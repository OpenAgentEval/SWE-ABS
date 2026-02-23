#!/usr/bin/env python3

"""Run mini-SWE-agent on SWE-bench instances in batch mode."""
# Read this first: https://mini-swe-agent.com/latest/usage/swebench/  (usage docs)

import argparse
import concurrent.futures
import json
import os
import random
import re
import shutil
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
from enum import Enum
from minisweagent.utils.parser_utils import get_test_directives,read_list_file,filter_apply_diffs, remove_conflicting_chunks

from minisweagent import Environment
from minisweagent.agents.default import DefaultAgent
from minisweagent.config import builtin_config_dir, get_config_path
from minisweagent.environments import get_environment
from minisweagent.models import get_model
from minisweagent.run.extra.utils.batch_progress import RunBatchProgressManager
from minisweagent.run.utils.save import save_traj
from minisweagent.utils.log import add_file_handler, logger
from minisweagent.utils.constants import (
    SUCCESS_STATUS, 
    FAIL_STATUS, 
    UNKNOW_STATUS,
    START_TEST_OUTPUT,
    END_TEST_OUTPUT,
    FixType,
    BenchMarkType,
    validate_fix_type,
    validate_benchmark_type
)

from minisweagent.run.extra.utils.swe_bench import git_apply
from minisweagent.constants import MAP_REPO_VERSION_TO_SPECS

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


DATASET_MAPPING = {
    "full": "princeton-nlp/SWE-Bench",
    "verified": "princeton-nlp/SWE-Bench_Verified",
    "lite": "princeton-nlp/SWE-Bench_Lite",
    "multimodal": "princeton-nlp/SWE-Bench_Multimodal",
    "multilingual": "swe-bench/SWE-Bench_Multilingual",
    "smith": "SWE-bench/SWE-smith",
    "_test": "klieret/swe-bench-dummy-test-dataset",
}


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
    config: dict,
    progress_manager: RunBatchProgressManager,
) -> None:
    """Process a single SWEBench instance."""
    benchmark_type = args.benchmark_type
    workdir = args.workdir

    benchmark_type: BenchMarkType
    workdir: str

    instance_id = instance["instance_id"]
    traj_folder = output_dir / "traj"/ "overfiting_solve"
    traj_dir = traj_folder / instance_id

    # Create ResultManager
    result_manager = ResultManager(output_dir / "preds.json")

    # avoid inconsistent state if something here fails and there's leftover previous files
    # Clear model_test_patch to avoid inconsistent state
    if result_manager.instance_exists(instance_id):
        result_manager.update_instance(instance_id, {"model_test_patch": ""})
    (traj_dir / f"{instance_id}.traj.json").unlink(missing_ok=True)
    model = get_model(config=config.get("model", {}))
    task = instance["problem_statement"]

    progress_manager.on_instance_start(instance_id)
    progress_manager.update_instance_status(instance_id, "Pulling/starting docker")

    agent = None

    try:
        env = get_sb_environment(config, instance, benchmark_type)

        patch = filter_apply_diffs(instance['patch'], [])
        model_test_patch = filter_apply_diffs(instance['model_test_patch'], [])

        patch = remove_conflicting_chunks(patch,model_test_patch)
        apply_files = git_apply(env, patch, workdir=workdir)
        if not apply_files:
            raise RuntimeError("Failed to apply gold patch to github repository")

        test_apply_files = git_apply(env, model_test_patch, workdir=workdir)
        if not test_apply_files:
            raise RuntimeError(f"Failed to apply test patch to github repository")


        agent = ProgressTrackingAgent(
            model,
            env,
            progress_manager=progress_manager,
            instance_id=instance_id,
            **config.get("agent", {}),
        )

        test_command = build_test_command_with_directives(instance, benchmark_type)

        exit_status, result = agent.run(task,
                                        model_test_patch=model_test_patch,
                                        gold_patch=patch,
                                        test_command=test_command,
                                        workdir=workdir)

    except Exception as e:
        logger.error(f"Error processing instance {instance_id}: {e}", exc_info=True)
        exit_status, result = type(e).__name__, str(e)
    finally:
        # Save trajectory
        save_traj(
            None,
            agent,
            traj_dir / f"{instance_id}.traj.json",
            exit_status=exit_status,
            result=result,
            instance_id=instance_id,
            print_fct=logger.info,
        )

        if exit_status != "Submitted":
            result = ""

        # Filter result to keep only the test case portion
        if isinstance(result, str):
            result = filter_apply_diffs(result,apply_files)

        # Update results using ResultManager
        # If instance already exists, update it; otherwise create a new instance
        if result_manager.instance_exists(instance_id):
            result_manager.update_instance(instance_id, {
                "model_test_patch": result
            })
        else:
            # Create a new instance, preserving original data
            result_manager.update_instance(instance_id, {
                **instance,
                "model_test_patch": result
            })

        progress_manager.on_instance_end(instance_id, exit_status)


def read_file(filepath: str):
    if filepath.endswith(".json"):
        with open(filepath) as f:
            return json.load(f)
    elif filepath.endswith(".jsonl"):
        return [json.loads(line) for line in open(filepath)]


def filter_gold_fail_instance(aug_test_instances):
    instances = []
    for key in aug_test_instances:
        instance = aug_test_instances[key]
        # aug_test did not pass gold_patch
        if 'meta' in instance: 
            if instance['meta']['pass_gold_patch_status']==UNKNOW_STATUS:
                raise RuntimeError(f"instance id {key}, instance donot evaluate,pass_gold_patch_status is {UNKNOW_STATUS}")
            elif instance['meta']['pass_gold_patch_status'] == FAIL_STATUS:
                instances.append(instance)

    return instances



# fmt: off
@app.command(help=_HELP_TEXT)
def main(
    benchmark: str = typer.Option("swebench", "--benchmark", help="Benchmark to run", rich_help_panel="Data selection",callback=validate_benchmark_type),
    aug_test_file: str = typer.Option("", "--aug_test_file", help="Augmented test file", rich_help_panel="Data selection"),
    instance_ids: str = typer.Option("","-i","--instance_ids",help="Instance IDs to run (comma separated, e.g. 'id1,id2,id3')",rich_help_panel="Data selection"),
    output: str = typer.Option("", "-o", "--output", help="Output directory", rich_help_panel="Basic"),
    workers: int = typer.Option(1, "-w", "--workers", help="Number of worker threads for parallel processing", rich_help_panel="Basic"),
    model: str | None = typer.Option(None, "-m", "--model", help="Model to use", rich_help_panel="Basic"),
    model_class: str | None = typer.Option(None, "-c", "--model-class", help="Model class to use (e.g., 'anthropic' or 'minisweagent.models.anthropic.AnthropicModel')", rich_help_panel="Advanced"),
    redo_existing: bool = typer.Option(False, "--redo-existing", help="Redo existing instances", rich_help_panel="Data selection"),
    config_spec: Path = typer.Option( builtin_config_dir / "extra" / "swebench_test.yaml", "-c", "--config", help="Path to a config file", rich_help_panel="Basic"),
    environment_class: str | None = typer.Option( None, "--environment-class", help="Environment type to use. Recommended are docker or singularity", rich_help_panel="Advanced"),
    fix_type: str = typer.Option("Hard_Code_Fix", "--fix_type", help="Fix type", rich_help_panel="Basic",callback=validate_fix_type),
    run_instance_file: str = typer.Option("", "--run_instance_file", help="Run a specific instance, stored in a file", rich_help_panel="Data selection"),
    temperature: float = typer.Option(None, "--temperature", help="Temperature for sampling", rich_help_panel="Advanced"),
) -> None:

    '''
        3 fix_type variants
            1. Hard_Code_Fix

            2. Gold_Fail_Fix

            3. Coverage_Fix
    '''

    # fmt: on
    # if benchmark == BenchMarkType.SWEBENCHPRO:
    #     subset = 'pro'
    workdir = get_workdir(benchmark)
    args = argparse.Namespace(
        benchmark_type = benchmark,
        workdir = workdir
    )

    output_path = Path(output)
    output_path.mkdir(parents=True, exist_ok=True)
    logger.info(f"Results will be saved to {output_path}")
    add_file_handler(output_path / "minisweagent.log")

    if not os.path.exists(aug_test_file):
        raise ValueError("aug_test_file must exist")

    aug_test_file = Path(aug_test_file)

    aug_test_instances = json.loads(aug_test_file.read_text())
    aug_test_instances:dict
    

    if instance_ids and run_instance_file:
        raise RuntimeError("Cannot specify both run_instance and run_instance_file")

    # Specify instance_ids to fix
    if instance_ids:
        instance_ids = instance_ids.split(",")
        instances = [aug_test_instances[instance_id] for instance_id in instance_ids if instance_id in aug_test_instances]
    elif run_instance_file:
        run_instance = read_list_file(run_instance_file)
        instances = [aug_test_instances[instance_id] for instance_id in aug_test_instances if instance_id in run_instance]
    else:
        instances = []
        for idx,key in enumerate(aug_test_instances.keys()):
            # if idx <50:
            #     continue
            instances.append(aug_test_instances[key])
            


    logger.info(f"Running on {len(instances)} instances...")
    if len(instances) == 0:
        logger.info("No instances to run.")
        return
    
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
    progress_manager = RunBatchProgressManager(len(instances), output_path / f"exit_statuses_{time.time()}.yaml")

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
