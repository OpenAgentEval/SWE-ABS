import concurrent.futures
import copy
import json
import os
import random
import re
import sys
import threading
import time
import traceback
from pathlib import Path
from collections import defaultdict
import typer
import yaml
from datasets import load_dataset
from jinja2 import StrictUndefined, Template
from rich.live import Live
import argparse
from minisweagent import Environment
from minisweagent.agents.default import DefaultAgent
from minisweagent.config import builtin_config_dir, get_config_path
from minisweagent.environments import get_environment
from minisweagent.models import get_model
from minisweagent.run.extra.utils.batch_progress import RunBatchProgressManager
from minisweagent.run.utils.save import save_traj
from minisweagent.utils.log import add_file_handler, logger

from minisweagent.constants import MAP_REPO_VERSION_TO_SPECS
from minisweagent.agents.single_step import SingleStepAgent
from minisweagent.utils.parser_utils import get_test_directives
from minisweagent.utils.constants import (
    BenchMarkType,
    validate_benchmark_type,
)
from minisweagent.utils.benchmark_util import (
    get_docker_image_name,
    get_dataset_path,
    get_workdir,
    get_test_command,
    get_sb_environment
)

# Import from sweabs_utils package
from sweabs_utils.preds_manager import ResultManager

_HELP_TEXT = """Run mini-SWE-agent on SWEBench instances.

[not dim]
More information about the usage: [bold green]https://mini-swe-agent.com/latest/usage/swebench/[/bold green]
[/not dim]
"""
app = typer.Typer(rich_markup_mode="rich", add_completion=False)


def judge_mutatation(agent: SingleStepAgent,instance,mutation_desr,mutation_patch,judge_equ=False):
    # mutation_desr = messages[-2]['content'].split('```bash')[0]

    issue = instance['problem_statement']
    gold_patch = instance['patch']
    test_patch = instance['test_patch']
    mutation_str = mutation_desr + '\n' + mutation_patch

    response = agent.run(issue=issue, gold_patch=gold_patch, test_patch=test_patch, mutation=mutation_str)
    response: str

    try:
        # Extract <Answer> ... </Answer>
        block = response.split("<Answer>")[1].split("</Answer>")[0].strip()

        is_rele = None
        is_valid = None

        for line in block.splitlines():
            line = line.strip()
            if not line:
                continue

            # Relevance
            if line.lower().startswith("relevance"):
                value = line.split(":")[1].strip().lower()
                is_rele = (value == "yes")

            # Mutation Validity
            elif line.lower().startswith("mutation validity"):
                value = line.split(":")[1].strip().lower()
                is_valid = (value == "yes")

            # Equivalent Mutation: if yes, the mutation is not valid but equivalent
            elif line.lower().startswith("equivalent mutation"):
                value = line.split(":")[1].strip().lower()
                is_valid = (value == "no")


        # If all fields are present
        if is_rele is not None and is_valid is not None:
            return is_rele, is_valid

        return "parse error", "parse error"

    except Exception as e:
        print(e)
        return "parse error", "parse error"

def majority_vote(bool_list):
    if not bool_list:  # If list is empty, return False by default or raise an exception
        return False
    true_count = sum(1 for item in bool_list if item is True)
    return true_count > (len(bool_list) / 2)






def process_instance(
    args:argparse.Namespace,
    mutation_instance: str,
    instance: dict,
    output_dir: Path,
    config: dict,
    progress_manager: RunBatchProgressManager,
):

    models = args.models
    instance_id = instance["instance_id"]
    output_path = output_dir / "traj" / "judge" / instance_id
    output_path.mkdir(parents=True, exist_ok=True)

    # Create ResultManager
    result_manager = ResultManager(output_dir / "preds.json")

    isrele_list = []
    isvalid_list = []
    valid_isrele_list = []  # For majority voting only: filter out "parse error" results
    valid_isvalid_list = []

    exit_status = "Submitted"  # Default state
    extra_info = None

    for idx, model_name in enumerate(models):

        model_config = copy.deepcopy(config.get("model", {}))
        model_config["model_name"] = model_name

        model = get_model(config=model_config)
        agent = SingleStepAgent(model, **config.get("agent", {}))

        # Call judge_mutation to get the current model's verdict
        isrele, isvalid = judge_mutatation(
            agent, instance,
            mutation_instance['mutation_thinking'],
            mutation_instance['model_patch'],
            args.judge_equ
        )

        # Save raw result (may contain "parse error")
        isrele_list.append(isrele)
        isvalid_list.append(isvalid)

        # Check for parse error
        if isrele == "parse error" or isvalid == "parse error":
            logger.info(f"Model {model_name} returned parse error")
            exit_status = "parse error"
        else:
            # Only add non-parse-error results to the majority voting list
            valid_isrele_list.append(isrele)
            valid_isvalid_list.append(isvalid)

        # Save the current model's trajectory
        save_traj(
            args,
            agent,
            output_path / f"judge_{idx}.traj.json",
            exit_status= "Submitted" if not any(v == "parse error" for v in [isrele, isvalid]) else "parse error",
            result=(isrele, isvalid),
            extra_info=extra_info,
            print_fct=logger.info,
        )
        # time.sleep(0.1)
    # Mark this instance as done in progress
    progress_manager.on_instance_end(instance_id, exit_status)

    # ===== Start: majority vote to determine final isrele and isvalid =====
    def majority_vote(bool_list):
        if not bool_list:
            return False  # If no valid votes, return False by default or adjust as needed
        true_count = sum(1 for item in bool_list if item is True)
        return true_count > (len(bool_list) / 2)

    # Only perform majority vote when valid votes exist
    final_isrele = False
    final_isvalid = False

    if valid_isrele_list:
        final_isrele = majority_vote(valid_isrele_list)
    if valid_isvalid_list:
        final_isvalid = majority_vote(valid_isvalid_list)

    # Save results using ResultManager
    judge_info = {
        "isrele": final_isrele,
        "isvalid": final_isvalid,
        "isrele_list": isrele_list,
        "isvalid_list": isvalid_list,
        "outputs": str(output_path.parent.resolve())
    }

    # If instance already exists, update judge_info; otherwise create a new instance
    if result_manager.instance_exists(instance_id):
        result_manager.update_instance(instance_id, {"judge_info": judge_info})
    else:
        # Create a new instance (with judge_info)
        result_manager.update_instance(instance_id, {"judge_info": judge_info})
        
def filter_exist(mutation_res:dict):
    use_instances = {}

    for instance_id,value in mutation_res.items():
        # if value['model_patch'] == "" or value['mutation_thinking'] == '':
        #     raise RuntimeError(f"model_patch or mutation_thinking is empty for instance {instance_id}")

        if 'evaluation_info' in value:
            if value['evaluation_info']['pass_init_test_status'] == "success" and 'judge_info' not in value:
                use_instances[instance_id] = value
        else:
            raise RuntimeError(f"evaluation_info not found for instance {instance_id}, please first run init test to get evaluation_info")
                
        # if 'judge_info' in value:
        #     continue
        # else:
        #     use_instances[instance_id] = value
            
    return use_instances


@app.command(help=_HELP_TEXT)
def main(
    benchmark: str = typer.Option("swebench", "--benchmark", help="Benchmark to run", rich_help_panel="Data selection",callback=validate_benchmark_type),
    subset: str = typer.Option("verified", "--subset", help="SWEBench subset to use or path to a dataset", rich_help_panel="Data selection"),
    split: str = typer.Option("test", "--split", help="Dataset split", rich_help_panel="Data selection"),
    mutation_res_file: str = typer.Option("","-f","--mutation_res_file",help="mutation result file",rich_help_panel="Data selection"),
    judge_mutatation_config_spec: str = typer.Option("","-p","--judge_mutatation_config_spec",help="Judge config spec for mutation, default use the same config as the mutation config",rich_help_panel="Data selection"),
    models: str = typer.Option("","-m","--models",help="run (comma separated, e.g. 'model1,model2,model3')",rich_help_panel="Data selection"),
    judge_times: int = typer.Option(3,"-t","--judge_times",help="run (comma separated)",rich_help_panel="Data selection"),
    workers: int = typer.Option(1, "-w", "--workers", help="Number of worker threads for parallel processing", rich_help_panel="Basic"),
    output_path: str = typer.Option("", "-o", "--output", help="Output directory", rich_help_panel="Basic"),
    instance_ids: str = typer.Option("","-i","--instance_ids",help="Instance IDs to run (comma separated, e.g. 'id1,id2,id3')",rich_help_panel="Data selection"),
) -> None:
    '''
        Determines the relevance of a mutation to the issue and whether it is an equivalent mutation.
            Uses majority voting.

            Docstring for main.
            :param judge_mutatation_config_spec: config for running the judge
            :param models: list of models used by the judge
            :param judge_times: number of times to judge if using a single model from the config
    '''
    if 'judge_equ' in judge_mutatation_config_spec:
        print("use judge_equ")
        judge_equ = True
    else:
        judge_equ = False

    print(f"judge_equ: {judge_equ}")
    print(f"judge_mutatation_config_spec: {judge_mutatation_config_spec}")
    print("wait for 5 seconds before start")
    time.sleep(5)

    if benchmark == BenchMarkType.SWEBENCHPRO:
        subset = 'pro'

    args = argparse.Namespace(
        judge_equ=judge_equ
    )
    mutation_res_file = Path(mutation_res_file)
    if not output_path:
        output_path = mutation_res_file.parent
    else:
        output_path = Path(output_path)

    print(f"output_path: {output_path}")

    # raise RuntimeError("not support now")
    output_path.mkdir(parents=True, exist_ok=True)
    dataset_path = get_dataset_path(benchmark,subset=subset)
    instances = list(load_dataset(dataset_path, split=split))

    instances = {
        instance["instance_id"]: instance
        for instance in instances
        if "instance_id" in instance
    }


    judge_mutatation_config_spec = get_config_path(judge_mutatation_config_spec)
    judge_mutatation_config = yaml.safe_load(open(judge_mutatation_config_spec, "r"))
    config = judge_mutatation_config

    models = [m.strip() for m in models.split(",") if m.strip()]

    # If models not specified, repeat the default model from config judge_times times
    if not models or len(models) < 2:
        if len(models) == 1:
            model_name = models[0]
        else:
            model_name = judge_mutatation_config.get("model", {}).get("model_name", "")
        if not model_name:
            raise ValueError("No models specified and no default model found in config")

        models = [model_name] * judge_times
        judge_mutatation_config["model"]["model_kwargs"]['temperature'] = 1


    args.models = models

    mutation_res = json.load(mutation_res_file.open("r"))
    mutation_res:dict

    if instance_ids:
        instance_ids_list: list[str] = []
        instance_ids_list = instance_ids.split(",")
        mutation_res = {instance_id:mutation_res[instance_id] for instance_id in mutation_res if instance_id in instance_ids_list}
    else:  # If not specified, skip already evaluated cases
        mutation_res = filter_exist(mutation_res)
        # pass


    logger.info(f"Running {len(mutation_res)} instances")


    exit_statuses_dir = output_path / "exit_statuses"
    exit_statuses_dir.mkdir(parents=True, exist_ok=True)
    progress_manager = RunBatchProgressManager(len(mutation_res), exit_statuses_dir / f"judge_mutation_exit_statuses_{time.time()}.yaml")

    def process_futures(futures: dict[concurrent.futures.Future, str]):
        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()
            except concurrent.futures.CancelledError:
                pass
            except Exception as e:
                instance_id = futures[future]
                logger.error(f"Error in future for instance {instance_id}: {e}", exc_info=True)
                progress_manager.on_uncaught_exception(instance_id, e)

    with Live(progress_manager.render_group, refresh_per_second=4):
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(process_instance, args, mutation_res[instance_id], instances[instance_id], output_path, config, progress_manager): instance_id
                for instance_id in mutation_res
            }
            try:
                process_futures(futures)
            except KeyboardInterrupt:
                logger.info("Cancelling all pending jobs. Press ^C again to exit immediately.")
                for future in futures:
                    if not future.running() and not future.done():
                        future.cancel()
                process_futures(futures)

if __name__ == "__main__":
    app()

