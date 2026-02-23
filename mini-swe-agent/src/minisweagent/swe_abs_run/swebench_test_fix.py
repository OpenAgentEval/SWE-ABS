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
from minisweagent.utils.parser_utils import get_test_directives,read_list_file,filter_apply_diffs

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




def get_error_info(aug_test_log_file:Path):

    content = aug_test_log_file.read_text()

    test_content = content.split(START_TEST_OUTPUT)[1].split(END_TEST_OUTPUT)[0]

    return test_content

def find_log_traj_path(instance,fix_type:FixType):

    # Find log and traj files for the instance_id from stage
    if 'stage' not in instance:
        raise ValueError("instance must have 'stage' key")

    more_info = None
    aug_test_traj_path = None

    stage_list = instance['stage']


    if fix_type == FixType.HARD_CODE_FIX: # Hard_Code_Fix
        more_info = None
        # Find the most recent patch_generation trajectory
        for stage in stage_list[::-1]:
            if stage['stage'] == 'patch_generation':
                aug_test_traj_path = stage['outputs']
                break
    elif fix_type == FixType.GOLD_FAIL_FIX: # Gold_Fail_Fix

        # Find the most recent patch_generation trajectory
        for stage in stage_list[::-1]:
            if stage['stage'] == FixType.HARD_CODE_FIX.value:
                aug_test_traj_path = stage['outputs']
                break
        more_info = stage['evaluation_info']['error_info']

    elif fix_type == FixType.COVERAGE_FIX: # Coverage_Fix
        # Find previous HARD_CODE_FIX or GOLD_FAIL_FIX trajectory
        for stage in stage_list[::-1]:
            if stage['stage'] == FixType.GOLD_FAIL_FIX.value or stage['stage'] == FixType.HARD_CODE_FIX.value:
                aug_test_traj_path = stage['outputs']
                break

        uncovered_lines = stage['evaluation_info']['uncovered_lines']
        more_info_parts = []  
        for file_name, lines in uncovered_lines.items():
            uncovered_lines_str = []
            for line_idx, line_content in lines:
                # Assume line_idx is int and line_content is str
                uncovered_lines_str.append(f"{line_idx}: {line_content}")
            # Concatenate all uncovered lines for this file
            more_info_parts.append(f"{file_name}:\n" + "\n".join(uncovered_lines_str))

        # Concatenate uncovered info across all files
        more_info = "\n\n".join(more_info_parts)

    return more_info, Path(aug_test_traj_path)

def process_instance(
    args: argparse.Namespace,
    instance: dict,
    output_dir: Path,
    config: dict,
    progress_manager: RunBatchProgressManager,
    fix_type: FixType
) -> None:
    """Process a single SWEBench instance."""
    benchmark_type = args.benchmark_type
    workdir = args.workdir

    benchmark_type: BenchMarkType
    workdir: str
    instance_id = instance["instance_id"]
    iteration = instance['meta']['iteration']

    # Create ResultManager
    result_manager = ResultManager(output_dir / "preds.json")

    traj_dir = output_dir / "traj"/ f"{fix_type.value}_{iteration}"
    traj_file = traj_dir / instance_id /f"{instance_id}.traj.json"
    # avoid inconsistent state if something here fails and there's leftover previous files
    traj_file.unlink(missing_ok=True)
    model = get_model(config=config.get("model", {}))
    # task = instance["problem_statement"]

    more_info, aug_test_traj_path = find_log_traj_path(instance,fix_type)

    progress_manager.on_instance_start(instance_id)
    progress_manager.update_instance_status(instance_id, "Pulling/starting docker")

    agent = None
    extra_info = None

    aug_test_traj_file = aug_test_traj_path / instance_id / f"{instance_id}.traj.json"
    with open(aug_test_traj_file, "r") as f:
        aug_test_traj = json.load(f)
    
    messages = aug_test_traj['messages']

    if len(messages) == 0:
        raise RuntimeError(f"aug_test_traj_file:{aug_test_traj_file} has empty messages")

    # Remove the last two instructions
    messages = messages[:-2]
    
    try:
        env = get_sb_environment(config, instance, benchmark_type)

        patch = filter_apply_diffs(instance['patch'], [])

        # Apply gold patch to the repo first to save context
        apply_files = git_apply(env, patch, workdir=workdir)
        if not apply_files:
            raise RuntimeError("Failed to apply gold patch to github repository")

        test_apply_files = git_apply(env, instance['model_test_patch'], workdir=workdir)
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
        # aug_test_log_file = aug_test_log_path / instance_id / "gold_patch" / "test_output.txt"
        # error_info = get_error_info(aug_test_log_file)

        if 'coverage_rate' in instance['meta']:
            coverage_rate = instance['meta']['coverage_rate']
        else:
            coverage_rate = None

        exit_status, result = agent.run_fix(messages,
                                            error_info=more_info,
                                            coverage_rate=coverage_rate,
                                            test_patch=instance["model_test_patch"],
                                            test_command=test_command,
                                            fix_type=fix_type,
                                            workdir=workdir
                                            )
        
    except Exception as e:
        logger.error(f"Error processing instance {instance_id}: {e}", exc_info=True)
        exit_status, result = type(e).__name__, str(e)
        extra_info = {"traceback": traceback.format_exc()}
    finally:
        # Save trajectory
        save_traj(
            None,
            agent,
            traj_file,
            exit_status=exit_status,
            result=result,
            extra_info=extra_info,
            instance_id=instance_id,
            print_fct=logger.info,
        )

        if exit_status == "Submitted":
            # Filter result to keep only the test case portion
            if isinstance(result, str):
                result = filter_apply_diffs(result,apply_files)

            # Update preds.json using ResultManager
            # Retrieve existing data or use the passed-in instance
            if result_manager.instance_exists(instance_id):
                existing = result_manager.get_instance(instance_id)

                # Update model_test_patch
                existing["model_test_patch"] = result

                # If HARD_CODE_FIX, update hard_code_status in meta
                if fix_type == FixType.HARD_CODE_FIX:
                    if 'meta' not in existing:
                        existing['meta'] = {}
                    existing['meta']['hard_code_status'] = "success"

                # Check if the last stage is fix_type; if so, it is a re-run
                # Use the passed-in use_instance (instance param here), as meta and stage info were already processed before passing
                if existing.get('stage') and existing['stage'][-1]['stage'] == fix_type.value:
                    # Use the passed-in instance, but update model_test_patch
                    instance['model_test_patch'] = result
                    existing = instance

                # Add a new stage
                if 'stage' not in existing:
                    existing['stage'] = []
                existing['stage'].append({
                    "stage": fix_type.value,
                    "outputs": str(traj_dir.resolve()),
                    "model_test_patch": result,
                    "status": "completed"
                })

                result_manager.update_instance(instance_id, existing, merge=False)
            else:
                # Instance does not exist, create using the passed-in instance
                instance["model_test_patch"] = result

                if fix_type == FixType.HARD_CODE_FIX:
                    if 'meta' not in instance:
                        instance['meta'] = {}
                    instance['meta']['hard_code_status'] = "success"

                if 'stage' not in instance:
                    instance['stage'] = []
                instance['stage'].append({
                    "stage": fix_type.value,
                    "outputs": str(traj_dir.resolve()),
                    "model_test_patch": result,
                    "status": "completed"
                })

                result_manager.update_instance(instance_id, instance)

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



def filter_instance_by_fix_type(aug_test_instances, fix_type: FixType):
    instances = []
    for key in aug_test_instances:
        instance = aug_test_instances[key]
        stage = instance['stage'][-1]  # Assume stage is a list, take the last element

        if fix_type == FixType.HARD_CODE_FIX:
            # Check meta, find instances that have not been hard code fixed

            if 'meta' in instance:
                meta = instance['meta']
                hard_code_status = meta.get('hard_code_status')
                if hard_code_status == SUCCESS_STATUS:
                    continue
                instances.append(instance)
            else:
                raise RuntimeError(f"instance id {key}: meta is missing")

        # elif fix_type == FixType.GOLD_FAIL_FIX:
        #     if 'evaluation_info' in stage:
        #         if stage['evaluation_info'].get('pass_gold_patch_status') == FAIL_STATUS:
        #             instances.append(instance)
        #         elif stage['evaluation_info'].get('pass_gold_patch_status') == UNKNOW_STATUS:
        #             raise RuntimeError(f"fix_type: {fix_type}, instance id {key}: instance not evaluated, pass_gold_patch_status is {UNKNOW_STATUS}")
        #         else:
        #             continue
        #     else:
        #         raise RuntimeError(f"instance id {key}: instance not evaluated, evaluation_info is missing")

        elif fix_type == FixType.COVERAGE_FIX:
            if 'meta' in instance:
                meta = instance['meta']
                pass_gold_patch_status = meta.get('pass_gold_patch_status')
                coverage_rate = meta.get('coverage_rate')

                # Indicates this has already been run
                if stage['stage'] == fix_type.value and stage['status'] == "completed":
                    continue

                if pass_gold_patch_status != SUCCESS_STATUS:
                    raise RuntimeError(f"fix_type: {fix_type}, instance id {key}: instance not pass gold patch") 

                # 0 is also an anomalous value
                if 0 < coverage_rate < 0.99:
                    instances.append(instance)
                else:
                    continue
            else:
                raise RuntimeError(f"instance id {key}: instance missing meta")

    return instances



def handle_instance_indicate(aug_test_instances:list, fix_type: FixType):
    '''
        If specific instances are specified to run,
            and there is an existing stage corresponding to the fix_type, remove those stages and restore model_test_patch to the one from the previous stage.
    '''

    instances = []
    for instance in aug_test_instances:
        key = instance['instance_id']
        last_stage = instance['stage'][-1]  # Assume stage is a list, take the last element

        if fix_type == FixType.HARD_CODE_FIX:
            # Check meta, find instances that have not been hard code fixed

            if 'meta' in instance:
                meta = instance['meta']
                hard_code_status = meta.get('hard_code_status')
                if hard_code_status == SUCCESS_STATUS:
                    # Restore model_test_patch from the previous stage, remove the stage, restore meta
                    for stage in instance['stage'][::-1]:
                        if stage['stage'] == FixType.Patch_GENERATION.value: 
                            use_model_test_patch = stage['model_test_patch']
                            break
                    instance['model_test_patch'] = use_model_test_patch

                    instance['stage'].pop()
                    instance['meta']['hard_code_status'] = "unknow"
                    instance['meta']['pass_gold_patch_status'] = "unknow"

                instances.append(instance)
            else:
                raise RuntimeError(f"instance id {key}: meta is missing")
        elif fix_type == FixType.COVERAGE_FIX:
            if 'meta' in instance:
                if last_stage['stage'] == fix_type.value:
                    # Restore to the previous state
                    for stage in instance['stage'][::-1]:
                        if stage['stage'] == FixType.HARD_CODE_FIX.value: 
                            use_model_test_patch = stage['model_test_patch']
                            evaluation_info = stage['evaluation_info']
                            break
                    instance['stage'].pop()
                    instance['meta']['pass_gold_patch_status'] = evaluation_info['pass_gold_patch_status']
                    instance['meta']['coverage_rate'] = evaluation_info['coverage_rate']
                    instance['meta']['uncovered_lines'] = evaluation_info['uncovered_lines']
                    instance['model_test_patch'] = use_model_test_patch

                meta = instance['meta']
                pass_gold_patch_status = meta.get('pass_gold_patch_status')
                coverage_rate = meta.get('coverage_rate') 
                
                if pass_gold_patch_status != SUCCESS_STATUS:
                    raise RuntimeError(f"fix_type: {fix_type}, instance id {key}: instance not pass gold patch") 

                if coverage_rate < 0.99:
                    instances.append(instance)
                else:
                    continue
            else:
                raise RuntimeError(f"instance id {key}: instance missing meta")

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


    output_path = Path(aug_test_file).parent
    output_path.mkdir(parents=True, exist_ok=True)
    logger.info(f"Results will be saved to {output_path}")
    add_file_handler(output_path / "minisweagent.log")

    if not os.path.exists(aug_test_file):
        raise ValueError("aug_test_file must exist")

    aug_test_file = Path(aug_test_file)

    aug_test_instances = json.loads(aug_test_file.read_text())
    aug_test_instances:dict
    
    # Specify instance_ids to fix
    if instance_ids:
        if run_instance_file:
            raise RuntimeError("Cannot specify both run_instance and run_instance_file")

        instance_ids = instance_ids.split(",")
        instances = [aug_test_instances[instance_id] for instance_id in instance_ids if instance_id in aug_test_instances]
        instances = handle_instance_indicate(instances, fix_type)
    elif run_instance_file:
        run_instance = read_list_file(run_instance_file)
        instances = [aug_test_instances[instance_id] for instance_id in aug_test_instances if instance_id in run_instance]
        instances = handle_instance_indicate(instances, fix_type)
    else:
        instances = filter_instance_by_fix_type(aug_test_instances,fix_type)

    logger.info(f"Running on {len(instances)} instances...")
    if len(instances) == 0:
        logger.info("No instances to run.")
        return
    
    logger.info(f"Waiting 5 seconds before starting...")
    time.sleep(5)
    # aug_test_log_path, aug_test_traj_path = find_log_traj_path(instances)

    # config_spec = CONFIG_MAP[fix_type]

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

    # Create exit_statuses directory and save status file there with fix_type prefix
    exit_statuses_dir = output_path / "exit_statuses"
    exit_statuses_dir.mkdir(parents=True, exist_ok=True)
    fix_type_prefix = fix_type.value.lower()  # Convert to lowercase (e.g., Hard_Code_Fix -> hard_code_fix)
    progress_manager = RunBatchProgressManager(len(instances), exit_statuses_dir / f"{fix_type_prefix}_exit_statuses_{time.time()}.yaml")

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
                executor.submit(process_instance, args, instance, output_path, config, progress_manager, fix_type): instance[
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
