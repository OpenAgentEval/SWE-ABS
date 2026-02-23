#!/usr/bin/env python3

"""Run mini-SWE-agent on SWE-bench instances in batch mode."""
# Read this first: https://mini-swe-agent.com/latest/usage/swebench/  (usage docs)

import concurrent.futures
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
from minisweagent.agents.mutation import MutationAgent

from minisweagent.config import builtin_config_dir, get_config_path
from minisweagent.models import get_model
from minisweagent.run.extra.utils.batch_progress import RunBatchProgressManager
from minisweagent.run.utils.save import save_traj
from minisweagent.utils.log import add_file_handler, logger
from minisweagent.utils.parser_utils import get_test_directives,read_list_file,filter_apply_diffs

from minisweagent.run.extra.utils.swe_bench import git_apply
from minisweagent.agents.single_step import SingleStepAgent
from minisweagent.utils.constants import (
    BenchMarkType,
    validate_benchmark_type,
)
from minisweagent.utils.benchmark_util import (
    get_docker_image_name,
    get_dataset_path,
    get_workdir,
    get_test_command,
    get_sb_environment,
    build_test_command_with_directives
)

# Import from sweabs_utils package
from sweabs_utils.preds_manager import ResultManager


_HELP_TEXT = """Run mini-SWE-agent on SWEBench instances.

[not dim]
More information about the usage: [bold green]https://mini-swe-agent.com/latest/usage/swebench/[/bold green]
[/not dim]
"""

app = typer.Typer(rich_markup_mode="rich", add_completion=False)


class ProgressTrackingAgent(DefaultAgent):
    """Simple wrapper around DefaultAgent that provides progress updates."""

    def __init__(self, *args, progress_manager: RunBatchProgressManager, instance_id: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.progress_manager: RunBatchProgressManager = progress_manager
        self.instance_id = instance_id

    def step(self) -> dict:
        """Override step to provide progress updates."""
        self.progress_manager.update_instance_status(
            self.instance_id, f"Step {self.model.n_calls + 1:3d} (${self.model.cost:.2f})"
        )
        return super().step()





def process_instance(
    args:argparse.Namespace,
    instance: dict,
    output_dir: Path,
    config: dict,
    progress_manager: RunBatchProgressManager,
) -> None:
    """Process a single SWEBench instance."""

    benchmark_type = args.benchmark_type
    workdir = args.workdir

    benchmark_type: BenchMarkType
    workdir: str


    instance_id = instance["instance_id"]


    traj_dir = output_dir / "traj"/ "mutation"
    traj_file = traj_dir / instance_id / f"{instance_id}.traj.json"

    # Create ResultManager
    result_manager = ResultManager(output_dir / "preds.json")

    # avoid inconsistent state if something here fails and there's leftover previous files
    # Clear model_patch to avoid inconsistent state
    if result_manager.instance_exists(instance_id):
        result_manager.update_instance(instance_id, {"model_patch": ""})
    (traj_file).unlink(missing_ok=True)
    model = get_model(config=config.get("model", {}))
    task = instance["problem_statement"]

    progress_manager.on_instance_start(instance_id)
    progress_manager.update_instance_status(instance_id, "Pulling/starting docker")

    agent = None
    extra_info = None

    try:
        env = get_sb_environment(config, instance, benchmark_type)
        
        patch = filter_apply_diffs(instance['patch'], [])
        test_patch = filter_apply_diffs(instance['test_patch'], [])


        # Apply gold patch to the repo first to save context
        apply_files = git_apply(env, test_patch, workdir=workdir)
        if not apply_files:
            raise RuntimeError("Failed to apply test patch to github repository")

        gold_apply_files = git_apply(env, patch, workdir=workdir)
        if not gold_apply_files:
            raise RuntimeError("Failed to apply gold patch to github repository")


        agent = ProgressTrackingAgent(
            model,
            env,
            progress_manager=progress_manager,
            instance_id=instance_id,
            **config.get("agent", {}),
        )
        
        # test_command = get_test_command(instance, benchmark_type)

        # test_command = " ".join(
        #     [   test_command,
        #         *get_test_directives(instance),
        #     ]
        # )

        test_command = build_test_command_with_directives(instance, benchmark_type, test_patch_key='test_patch')


        exit_status, result = agent.run(task,
                                        test_patch=test_patch,
                                        gold_patch=patch,
                                        test_command=test_command,
                                        workdir=workdir)
        
        # config:dict,instance,messages,mutation_patch
        
        if exit_status == 'Submitted':
            # raise RuntimeError(f"Agent exited with status {exit_status}")

            try:
                mutation_thinking = agent.messages[-2]['content'].split('```bash')[0]
            except Exception as e:
                mutation_thinking = ""
            
            # if args.judge_model_mutatation:
            #     judge_res,judge_messages = judge_mutatation(args.judge_mutatation_config,instance,mutation_thinking,result)
            # else:
            #     judge_res,judge_messages = None,None
        else:
            mutation_thinking = ""
            # judge_res,judge_messages = None,None

    except Exception as e:
        logger.error(f"Error processing instance {instance_id}: {e}", exc_info=True)
        exit_status, result = type(e).__name__, str(e)
        extra_info = {"traceback": traceback.format_exc()}
        mutation_thinking = ""
        # judge_res,judge_messages = None,None
    finally:
        # Filter result to keep only the test case portion
        if isinstance(result, str):
            result = filter_apply_diffs(result,apply_files)

        if result.strip() == '' and exit_status == 'Submitted':
            exit_status = 'Empty_Patch'


        # Save trajectory
        save_traj(
            args,
            agent,
            traj_file,
            # judge_res=judge_res,
            # judge_messages=judge_messages,
            exit_status=exit_status,
            result=result,
            extra_info=extra_info,
            print_fct=logger.info,
        )

        # Save results using ResultManager
        save_data = {
            "instance_id": instance["instance_id"],
            "subset": "verified",
            "model_patch": result,
            "mutation_thinking": mutation_thinking,
            "outputs": str(traj_dir.resolve()),
            "model_name_or_path": model.config.model_name
        }
        result_manager.update_instance(instance_id, save_data)

        progress_manager.on_instance_end(instance_id, exit_status)


def filter_instances(
    instances: list[dict], *, filter_spec: str, slice_spec: str = "", shuffle: bool = False
) -> list[dict]:
    """Filter and slice a list of SWEBench instances."""
    if shuffle:
        instances = sorted(instances.copy(), key=lambda x: x["instance_id"])
        random.seed(42)
        random.shuffle(instances)
    before_filter = len(instances)
    instances = [instance for instance in instances if re.match(filter_spec, instance["instance_id"])]
    if (after_filter := len(instances)) != before_filter:
        logger.info(f"Instance filter: {before_filter} -> {after_filter} instances")
    if slice_spec:
        values = [int(x) if x else None for x in slice_spec.split(":")]
        instances = instances[slice(*values)]
        if (after_slice := len(instances)) != before_filter:
            logger.info(f"Instance slice: {before_filter} -> {after_slice} instances")
    return instances


# fmt: off
@app.command(help=_HELP_TEXT)
def main(
    benchmark: str = typer.Option("swebench", "--benchmark", help="Benchmark to run", rich_help_panel="Data selection",callback=validate_benchmark_type),
    subset: str = typer.Option("verified", "--subset", help="SWEBench subset to use or path to a dataset", rich_help_panel="Data selection"),
    split: str = typer.Option("test", "--split", help="Dataset split", rich_help_panel="Data selection"),
    slice_spec: str = typer.Option("", "--slice", help="Slice specification (e.g., '0:5' for first 5 instances)", rich_help_panel="Data selection"),
    filter_spec: str = typer.Option("", "--filter", help="Filter instance IDs by regex", rich_help_panel="Data selection"),
    shuffle: bool = typer.Option(False, "--shuffle", help="Shuffle instances", rich_help_panel="Data selection"),
    output: str = typer.Option("", "-o", "--output", help="Output directory", rich_help_panel="Basic"),
    workers: int = typer.Option(1, "-w", "--workers", help="Number of worker threads for parallel processing", rich_help_panel="Basic"),
    model: str | None = typer.Option(None, "-m", "--model", help="Model to use", rich_help_panel="Basic"),
    model_class: str | None = typer.Option(None, "--model-class", help="Model class to use (e.g., 'anthropic' or 'minisweagent.models.anthropic.AnthropicModel')", rich_help_panel="Advanced"),
    redo_existing: bool = typer.Option(False, "--redo-existing", help="Redo existing instances", rich_help_panel="Data selection"),
    config_spec: Path = typer.Option( builtin_config_dir / "extra" / "swebench_test.yaml", "--config", help="Path to a config file", rich_help_panel="Basic"),
    environment_class: str | None = typer.Option( None, "--environment-class", help="Environment type to use. Recommended are docker or singularity", rich_help_panel="Advanced"),
    repo_select_num: int = typer.Option(2, "--repo_select_num", help="Number of instances to select from each repository", rich_help_panel="Data selection"),
    instance_ids: str = typer.Option("","-i","--instance_ids",help="Instance IDs to run (comma separated, e.g. 'id1,id2,id3')",rich_help_panel="Data selection"),
    redo_instance: str = typer.Option("", "--redo_instance", help="Redo a specific instance, split by ','", rich_help_panel="Data selection"),
    run_instance_file: str = typer.Option("", "--run_instance_file", help="Run a specific instance, stored in a file", rich_help_panel="Data selection"),
    temperature: float = typer.Option(None, "--temperature", help="Temperature for sampling", rich_help_panel="Advanced"),
) -> None:

    if benchmark == BenchMarkType.SWEBENCHPRO:
        subset = 'pro'
    workdir = get_workdir(benchmark)
    args = argparse.Namespace(
        benchmark_type = benchmark,
        workdir = workdir
    )

    # fmt: on
    output_path = Path(output)
    output_path.mkdir(parents=True, exist_ok=True)
    logger.info(f"Results will be saved to {output_path}")
    add_file_handler(output_path / "minisweagent.log")

    dataset_path = get_dataset_path(benchmark,subset=subset)


    logger.info(f"Loading dataset {dataset_path}, split {split}...")
    instances = list(load_dataset(dataset_path, split=split))

    
    if instance_ids:
        if run_instance_file:
            raise RuntimeError("Cannot specify both run_instance and run_instance_file")
        instance_ids_list: list[str] = []
        instance_ids_list = instance_ids.split(",")
        instances = [instance for instance in instances if instance["instance_id"] in instance_ids_list]

    elif run_instance_file:
        run_instance = read_list_file(run_instance_file)
        instances = [instance for instance in instances if instance["instance_id"] in run_instance]
        if not redo_existing and (output_path / "preds.json").exists():
            output_preds = json.loads((output_path / "preds.json").read_text())
            existing_instances = set(output_preds.keys())
            for key,value in output_preds.items():
                if 'model_patch' in value and value['model_patch'] == "":
                    existing_instances.remove(key)

            if redo_instance:
                redo_instance = redo_instance.split(',')
                existing_instances = existing_instances - set(redo_instance)

            logger.info(f"Skipping {len(existing_instances)} existing instances")
            instances = [instance for instance in instances if instance["instance_id"] not in existing_instances]
    else:
    
        instances = instances[10:20]
        if not redo_existing and (output_path / "preds.json").exists():
            output_preds = json.loads((output_path / "preds.json").read_text())
            existing_instances = set(output_preds.keys())
            for key,value in output_preds.items():
                if 'model_patch' in value and value['model_patch'] == "":
                    existing_instances.remove(key)

            if redo_instance:
                redo_instance = redo_instance.split(',')
                existing_instances = existing_instances - set(redo_instance)

            logger.info(f"Skipping {len(existing_instances)} existing instances")
            instances = [instance for instance in instances if instance["instance_id"] not in existing_instances]

    logger.info(f"Running on {len(instances)} instances...")
    logger.info(f"Waiting 5 seconds before starting...")
    time.sleep(5)
    
    config_path = get_config_path(config_spec)
    logger.info(f"Loading agent config from '{config_path}'")
    config = yaml.safe_load(config_path.read_text())
    if environment_class is not None:
        config.setdefault("environment", {})["environment_class"] = environment_class
    if model is not None:
        config.setdefault("model", {})["model_name"] = model
    if model_class is not None:
        config.setdefault("model", {})["model_class"] = model_class
    if temperature is not None:
        logger.info(f"Setting temperature to {temperature}")
        config.setdefault("model", {})['model_kwargs']["temperature"] = temperature


    exit_statuses_dir = output_path / "exit_statuses"
    exit_statuses_dir.mkdir(parents=True, exist_ok=True)
    progress_manager = RunBatchProgressManager(len(instances), exit_statuses_dir / f"exit_statuses_{time.time()}.yaml")

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
                executor.submit(process_instance, args, instance, output_path, config, progress_manager): instance[
                    "instance_id"
                ]
                for instance in instances
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
