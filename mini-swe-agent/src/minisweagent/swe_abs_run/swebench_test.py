#!/usr/bin/env python3

"""Run mini-SWE-agent on SWE-bench instances in batch mode."""
# Read this first: https://mini-swe-agent.com/latest/usage/swebench/  (usage docs)

import concurrent.futures
from datetime import datetime
import json
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
import os

from minisweagent import Environment
from minisweagent.agents.default import DefaultAgent
from minisweagent.config import builtin_config_dir, get_config_path
from minisweagent.models import get_model
from minisweagent.run.extra.utils.batch_progress import RunBatchProgressManager
from minisweagent.run.utils.save import save_traj
from minisweagent.utils.log import add_file_handler, logger

from minisweagent.run.extra.utils.swe_bench import git_apply
from minisweagent.utils.parser_utils import (
    get_test_directives,
    read_list_file,
    filter_apply_diffs,
    str2bool
)
from minisweagent.constants import MAP_REPO_VERSION_TO_SPECS
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
    args: argparse.Namespace,
    instance: dict,
    output_dir: Path,
    gen_test_iter_num: int,
    config: dict,
    progress_manager: RunBatchProgressManager,
) -> None:
    """Process a single SWEBench instance."""
    benchmark_type = args.benchmark_type
    workdir = args.workdir

    benchmark_type: BenchMarkType
    workdir: str


    instance_id = instance["instance_id"]
    traj_folder = output_dir / "traj"/ f"gen_{gen_test_iter_num}"
    traj_dir = traj_folder / instance_id

    result_manager = ResultManager(output_dir / "preds.json")

    # avoid inconsistent state if something here fails and there's leftover previous files

    if result_manager.instance_exists(instance_id):
        result_manager.update_instance(instance_id, {"model_test_patch": ""})
    (traj_dir / f"{instance_id}.traj.json").unlink(missing_ok=True)
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

        apply_files = git_apply(env, patch, workdir=workdir)


        if not apply_files:
            raise RuntimeError("Failed to apply gold patch to github repository")

        agent = ProgressTrackingAgent(
            model,
            env,
            progress_manager=progress_manager,
            instance_id=instance_id,
            **config.get("agent", {}),
        )
        test_command = get_test_command(instance, benchmark_type)

        exit_status, result = agent.run(task,
                                        test_patch=test_patch,
                                        gold_patch=patch,
                                        test_command=test_command,
                                        workdir=workdir)


    except Exception as e:
        logger.error(f"Error processing instance {instance_id}: {e}", exc_info=True)
        exit_status, result = type(e).__name__, str(e)
        extra_info = {"traceback": traceback.format_exc()}
    finally:
        # Save trajectory
        save_traj(
            None,
            agent,
            traj_dir / f"{instance_id}.traj.json",
            exit_status=exit_status,
            result=result,
            extra_info=extra_info,
            instance_id=instance_id,
            print_fct=logger.info,
        )

        if exit_status != "Submitted":
            result = ""


        # Filter result to keep only the test case portion
        if isinstance(result, str):
            result = filter_apply_diffs(result,apply_files)

        # Save results using ResultManager
        status = "completed" if result != '' else 'incomplete'

        # Build stage information
        new_stage = {
            "stage": "patch_generation",
            "iteration": gen_test_iter_num,
            "model_test_patch": result,
            "outputs": str(traj_folder.resolve()),
            "status": status
        }

        # If instance already exists, append stage; otherwise create a new instance
        if result_manager.instance_exists(instance_id):
            # Retrieve existing data
            existing = result_manager.get_instance(instance_id)
            if 'stage' in existing and isinstance(existing['stage'], list):
                existing['stage'].append(new_stage)
            else:
                existing['stage'] = [new_stage]

            existing['model_test_patch'] = result

            # Update meta
            existing['meta'] = {
                "hard_code_status": "unknow",
                "pass_gold_patch_status": "unknow",
                "coverage_rate": "unknow",
                "iteration": gen_test_iter_num,
            }

            result_manager.update_instance(instance_id, existing, merge=False)
        else:
            # Create a new instance
            save_data = {
                "subset": args.subset if hasattr(args, 'subset') else 'verified',
                "model_test_patch": result,
                **instance
            }
            save_data['model_patch'] = save_data.get('patch', '')
            save_data['model_name_or_path'] = model.config.model_name
            save_data['stage'] = [new_stage]
            save_data['meta'] = {
                "hard_code_status": "unknow",
                "pass_gold_patch_status": "unknow",
                "coverage_rate": "unknow",
                "iteration": gen_test_iter_num,
            }

            result_manager.update_instance(instance_id, save_data)

        progress_manager.on_instance_end(instance_id, exit_status)





def filter_instances_pipline(
    instances, output_path,
    gen_test_iter_num=0,
    redo_existing=False
):
    # Initial generation phase, filter by repository
    if gen_test_iter_num == 0:
        #! for test
        instances = instances[10:20]

    # Currently in the phase of regenerating certain tests
    elif gen_test_iter_num>0:
        if (output_path / "preds.json").exists():
            output_preds = json.loads((output_path / "preds.json").read_text())
            use_instances = []
            for key,value in output_preds.items():
                if value['meta']['pass_gold_patch_status'] == 'success':
                    continue
                elif value['meta']['pass_gold_patch_status'] == 'fail':
                    use_instances.append(key)
                # If some are unknown and some have ['max_patch_gen'] < gen_test_iter_num and ['pass_gold_patch_status'] == 'fail', it means certain failed cases need to be re-run
                elif value['meta']['pass_gold_patch_status'] == 'unknown':
                    continue
                else:
                    raise RuntimeError(f"Unknown status {value['meta']['pass_gold_patch_status']} for instance {key}")

            instances = [instance for instance in instances if instance["instance_id"] in use_instances]
        else:
            raise RuntimeError(f"{output_path}/preds.json not found with gen_test_iter_num={gen_test_iter_num}")

    # Re-run instances that failed in the current round, but skip already generated ones
    if not redo_existing  and (output_path / "preds.json").exists():
        output_preds = json.loads((output_path / "preds.json").read_text())
        existing_instances = set(output_preds.keys())

        for key,value in output_preds.items():
            # ? Not sure if this condition needs to be modified; could also use stage to determine
            if 'model_test_patch' in value and value['model_test_patch'] == "":
                existing_instances.remove(key)
            # If max gen rounds is less than current and gold patch not passed, it indicates
            # elif 'meta' in value and value['meta']['max_patch_gen'] < gen_test_iter_num and value['meta']['pass_gold_patch_status'] == 'fail':
            #     existing_instances.remove(key)

        logger.info(f"Skipping {len(existing_instances)} existing instances")
        instances = [instance for instance in instances if instance["instance_id"] not in existing_instances]

    return instances

    
# fmt: off
@app.command(help=_HELP_TEXT)
def main(
    benchmark: str = typer.Option("swebench", "--benchmark", help="Benchmark to run", rich_help_panel="Data selection",callback=validate_benchmark_type),
    subset: str = typer.Option("verified", "--subset", help="SWEBench subset to use or path to a dataset", rich_help_panel="Data selection"),
    split: str = typer.Option("test", "--split", help="Dataset split", rich_help_panel="Data selection"),
    output: str = typer.Option("", "-o", "--output", help="Output directory", rich_help_panel="Basic"),
    workers: int = typer.Option(1, "-w", "--workers", help="Number of worker threads for parallel processing", rich_help_panel="Basic"),
    model: str | None = typer.Option(None, "-m", "--model", help="Model to use", rich_help_panel="Basic"),
    model_class: str | None = typer.Option(None, "-c", "--model-class", help="Model class to use (e.g., 'anthropic' or 'minisweagent.models.anthropic.AnthropicModel')", rich_help_panel="Advanced"),
    redo_existing: str = typer.Option("false", "--redo_existing", help="Redo existing instances", rich_help_panel="Data selection"),
    config_spec: Path = typer.Option( builtin_config_dir / "extra" / "swebench_test.yaml", "-c", "--config", help="Path to a config file", rich_help_panel="Basic"),
    environment_class: str | None = typer.Option( None, "--environment-class", help="Environment type to use. Recommended are docker or singularity", rich_help_panel="Advanced"),
    run_instance: str = typer.Option("", "--run_instance", help="Run a specific instance, split by ','", rich_help_panel="Data selection"),
    run_instance_file: str = typer.Option("", "--run_instance_file", help="Run a specific instance, stored in a file", rich_help_panel="Data selection"),
    gen_test_iter_num: int = typer.Option(0, "--gen_test_iter_num", help="Number of times to generate test instances", rich_help_panel="Data selection"),
    temperature: float = typer.Option(None, "--temperature", help="Temperature for sampling", rich_help_panel="Advanced"),

) -> None:

    '''
        When gen_test_iter_num is 0, it indicates that the initial test_patch is being generated.
    '''

    redo_existing = str2bool(redo_existing)

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

    if run_instance:
        if run_instance_file:
            raise RuntimeError("Cannot specify both run_instance and run_instance_file")

        run_instance = run_instance.split(',')
        instances = [instance for instance in instances if instance["instance_id"] in run_instance]
        
    elif run_instance_file:
        run_instance = read_list_file(run_instance_file)
        instances = [instance for instance in instances if instance["instance_id"] in run_instance]
        if not redo_existing  and (output_path / "preds.json").exists():
            output_preds = json.loads((output_path / "preds.json").read_text())
            existing_instances = set(output_preds.keys())

            for key,value in output_preds.items():
                if 'model_test_patch' in value and value['model_test_patch'] == "":
                    existing_instances.remove(key)
                # If max gen rounds is less than current and gold patch not passed, it indicates
                # elif 'meta' in value and value['meta']['max_patch_gen'] < gen_test_iter_num and value['meta']['pass_gold_patch_status'] == 'fail':
                #     existing_instances.remove(key)

            logger.info(f"Skipping {len(existing_instances)} existing instances")
            instances = [instance for instance in instances if instance["instance_id"] not in existing_instances]
    else:
        instances = filter_instances_pipline(instances, output_path, gen_test_iter_num=gen_test_iter_num)

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

    config.setdefault("environment", {})["cwd"] = workdir

    # Create exit_statuses directory and save status file there with phase prefix
    exit_statuses_dir = output_path / "exit_statuses"
    exit_statuses_dir.mkdir(parents=True, exist_ok=True)
    progress_manager = RunBatchProgressManager(len(instances), exit_statuses_dir / f"test_gen_exit_statuses_{time.time()}.yaml")

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
                executor.submit(process_instance, args, instance, output_path, gen_test_iter_num,
                                 config, progress_manager): instance[
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
