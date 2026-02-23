#!/usr/bin/env python3

"""Run mini-SWE-agent on SWE-bench instances in batch mode."""
# Read this first: https://mini-swe-agent.com/latest/usage/swebench/  (usage docs)

import argparse
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

from minisweagent import Environment
# from minisweagent.agents.default import DefaultAgent
from minisweagent.agents.multi_env import DefaultAgent, MultiEnvAgent

from minisweagent.config import builtin_config_dir, get_config_path
from minisweagent.environments import get_environment
from minisweagent.models import get_model
from minisweagent.run.extra.utils.batch_progress import RunBatchProgressManager
from minisweagent.run.utils.save import save_traj
from minisweagent.utils.log import add_file_handler, logger
from minisweagent.utils.parser_utils import read_list_file,filter_apply_diffs, remove_conflicting_chunks,str2bool

from minisweagent.run.extra.utils.swe_bench import git_apply
from minisweagent.utils.constants import (
    BenchMarkType,
    validate_benchmark_type
)
from minisweagent.utils.benchmark_util import (
    build_test_command_with_directives,
    get_workdir,
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


class ProgressTrackingMutationAgent(MultiEnvAgent):
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
    args,
    instance: dict,
    output_dir: Path,
    config: dict,
    progress_manager: RunBatchProgressManager,
) -> None:

    """Process a single SWEBench instance."""

    iteration = args.iteration
    stage_name = args.stage_name
    benchmark_type = args.benchmark_type
    workdir = args.workdir

    benchmark_type: BenchMarkType
    workdir: str

    save_name = f"{stage_name}_{iteration}"

    instance_id = instance["instance_id"]
    traj_folder = output_dir / "traj"/ save_name
    instance_file = traj_folder / instance_id / f"{instance_id}.traj.json"

    # Create ResultManager
    result_manager = ResultManager(output_dir / f"preds_{save_name}.json")

    # avoid inconsistent state if something here fails and there's leftover previous files
    # Clear model_test_patch to avoid inconsistent state
    if result_manager.instance_exists(instance_id):
        result_manager.update_instance(instance_id, {"model_test_patch": ""})
    instance_file.unlink(missing_ok=True)
    model = get_model(config=config.get("model", {}))
    task = instance["problem_statement"]

    progress_manager.on_instance_start(instance_id)
    progress_manager.update_instance_status(instance_id, "Pulling/starting docker")

    agent = None
    extra_info = None

    try:
        env_gold = get_sb_environment(config, instance, benchmark_type)
        env_mutation = get_sb_environment(config, instance, benchmark_type)
        
        env_dict = {
            'Gold': env_gold,
            'Mutated':env_mutation
        }

        if "mutation_aug_evaluation_info" in instance \
                and "mutation_info" in instance:
            mutation_info = instance['mutation_aug_evaluation_info']['mutation_info']
        elif "mutation_info" in instance:
            mutation_info = instance['mutation_info']

        need_aug_list = mutation_info[args.use_key]

        mutation_key = need_aug_list[0]
        mutation_instance = instance['all_mutatation_patch'][mutation_key]
        mutation_patch,mutation_thinking = mutation_instance['model_patch'],mutation_instance['mutation_thinking']

        patch = filter_apply_diffs(instance['patch'], [])

        patch = remove_conflicting_chunks(patch,instance['model_test_patch'])
        mutation_patch = remove_conflicting_chunks(mutation_patch,instance['model_test_patch'])

        # Apply gold patch to the repo first to save context
        apply_test_file = git_apply(env_gold, instance['model_test_patch'], workdir=workdir)
        apply_gold_file = git_apply(env_gold, patch, workdir=workdir)

        apply_test_file = git_apply(env_mutation, instance['model_test_patch'], workdir=workdir)
        apply_mutation_file = git_apply(env_mutation, mutation_patch, workdir=workdir)


        if not apply_test_file or not apply_gold_file or not apply_mutation_file:
            raise RuntimeError("Failed to apply patch to github repository")

        agent = ProgressTrackingMutationAgent(
            model,
            env_dict,
            progress_manager=progress_manager,
            instance_id=instance_id,
            **config.get("agent", {}),
        )

        test_command = build_test_command_with_directives(instance, benchmark_type)

        exit_status, result = agent.run(task,
                                        test_patch=instance["model_test_patch"],
                                        gold_patch=patch,
                                        mutation_patch=mutation_patch,
                                        test_command=test_command,
                                        mutation_thinking=mutation_thinking,
                                        workdir=workdir)
    except Exception as e:
        logger.error(f"Error processing instance {instance_id}: {e}", exc_info=True)
        exit_status, result = type(e).__name__, str(e)
        extra_info = {"traceback": traceback.format_exc()}
        mutation_instance = None
    finally:
        # Save trajectory
        save_traj(
            args,
            agent,
            instance_file,
            exit_status=exit_status,
            result=result,
            extra_info=extra_info,
            instance_id=instance_id,
            print_fct=logger.info,
            target_mutatation_patch = mutation_instance,
        )

        # Filter result to keep only test case portion, remove apply_gold_file
        if isinstance(result, str):
            result = filter_apply_diffs(result,apply_gold_file)
            # result = clean_full_diff(result)

        # Save results using ResultManager
        # Save the old model_test_patch
        instance['last_old_model_test_patch'] = instance['model_test_patch']
        instance['model_test_patch'] = result

        # Build aug_meta information
        aug_meta = {
            "stage": stage_name,
            "outputs": str(traj_folder.resolve()),
            "status": "completed",
            "iteration": iteration,
            "target_aug_key": mutation_key
        }

        instance['aug_meta'] = aug_meta

        # Remove mutation_aug_evaluation_info if it exists
        if 'mutation_aug_evaluation_info' in instance:
            del instance['mutation_aug_evaluation_info']

        # Update instance using ResultManager
        result_manager.update_instance(instance_id, instance, merge=False)

        progress_manager.on_instance_end(instance_id, exit_status)


def read_file(filepath: str):
    if filepath.endswith(".json"):
        with open(filepath) as f:
            return json.load(f)
    elif filepath.endswith(".jsonl"):
        return [json.loads(line) for line in open(filepath)]



def filter_mutation_instance(aug_test_instances, use_key = 'run_success_no_equ',iteration = 0):
    instances = []
    for key in aug_test_instances:
        instance = aug_test_instances[key]
        if iteration == 0:
            if "mutation_info" in instance:
                    # Aug run_success_no_equ first, then batch aug run_fail_equ at the end
                    if len(instance['mutation_info'][use_key])>0:
                        instances.append(instance)
        else:
            # Use mutation_aug_evaluation_info and mutation_info if present, otherwise use original
            if "mutation_aug_evaluation_info" in instance \
                and "mutation_info" in instance:
                mutation_info = instance['mutation_aug_evaluation_info']['mutation_info']
            elif "mutation_info" in instance:
                mutation_info = instance['mutation_info']
            else:
                continue
            if len(mutation_info[use_key])>0:
                instances.append(instance)

    return instances





# fmt: off
@app.command(help=_HELP_TEXT)
def main(
    benchmark: str = typer.Option("swebench", "--benchmark", help="Benchmark to run", rich_help_panel="Data selection",callback=validate_benchmark_type),
    aug_test_file: str = typer.Option("", "--aug_test_file", help="Augmented test file", rich_help_panel="Data selection"),
    output: str = typer.Option("", "-o", "--output", help="Output directory", rich_help_panel="Basic"),
    workers: int = typer.Option(1, "-w", "--workers", help="Number of worker threads for parallel processing", rich_help_panel="Basic"),
    model: str | None = typer.Option(None, "-m", "--model", help="Model to use", rich_help_panel="Basic"),
    model_class: str | None = typer.Option(None, "-c", "--model-class", help="Model class to use (e.g., 'anthropic' or 'minisweagent.models.anthropic.AnthropicModel')", rich_help_panel="Advanced"),
    redo_existing: bool = typer.Option(False, "--redo-existing", help="Redo existing instances", rich_help_panel="Data selection"),
    config_spec: Path = typer.Option( builtin_config_dir / "extra" / "swebench_test.yaml", "-c", "--config", help="Path to a config file", rich_help_panel="Basic"),
    environment_class: str | None = typer.Option( None, "--environment-class", help="Environment type to use. Recommended are docker or singularity", rich_help_panel="Advanced"),
    instance_ids: str = typer.Option("","-i","--instance_ids",help="Instance IDs to run (comma separated, e.g. 'id1,id2,id3')",rich_help_panel="Data selection"),
    redo_fail_instance: str = typer.Option("false", "--redo_fail_instance", help="Redo failed instances", rich_help_panel="Data selection"),
    # use_key: str = typer.Option("run_success_no_equ", "--use_key", help="Use key to filter instances", rich_help_panel="Data selection"),
    stage_name: str = typer.Option("no_equ_mutation_aug", "--stage_name", help="Stage name to use", rich_help_panel="Data selection"),
    iteration: int = typer.Option(0, "--iteration", help="Iteration number to use", rich_help_panel="Data selection"),
    run_instance_file: str = typer.Option("", "--run_instance_file", help="Run a specific instance, stored in a file", rich_help_panel="Data selection"),
    temperature: float = typer.Option(None, "--temperature", help="Temperature for sampling", rich_help_panel="Advanced"),
) -> None:

    workdir = get_workdir(benchmark)

    redo_fail_instance = str2bool(redo_fail_instance)
    args = argparse.Namespace(
        stage_name=stage_name,
        iteration = iteration,
        benchmark_type = benchmark,
        workdir = workdir
    )
    save_name = f"{stage_name}_{iteration}"
    if stage_name == "no_equ_mutation_aug":
        args.use_key = "run_success_no_equ"
    elif stage_name == "equ_mutation_aug":
        args.use_key = "run_fail_equ"
    else:
        raise ValueError("stage_name must be no_equ_mutation_aug or equ_mutation_aug")



    if iteration>0 and 'preds_mutation' in aug_test_file:
        raise ValueError("iteration must be 0 when using /model_gen_test/pred_mutation.json")


    # fmt: on
    output_path = Path(output)
    output_path.mkdir(parents=True, exist_ok=True)
    logger.info(f"Results will be saved to {output_path}")
    add_file_handler(output_path / "minisweagent.log")

    if not os.path.exists(aug_test_file):
        raise ValueError("aug_test_file must exist")


    aug_test_instances = read_file(aug_test_file)
    aug_test_instances:dict


    instances = filter_mutation_instance(aug_test_instances,use_key = args.use_key,iteration = iteration)
    # logger.info("len(instances)",len(instances))
    # logger.info("len(aug_test_instances)",len(aug_test_instances))
    # raise RuntimeError("debug")

    # These are the instance_ids to re-run
    if instance_ids:
        if run_instance_file:
            raise RuntimeError("Cannot specify both run_instance and run_instance_file")
        instance_ids = instance_ids.split(",")
        logger.info(f"indicate instance_ids: {len(instance_ids)}")

        instances = [instance for instance in instances if instance["instance_id"] in instance_ids]
    elif run_instance_file:
        run_instance = read_list_file(run_instance_file)
        instances = [instance for instance in instances if instance["instance_id"] in run_instance]
    else:
        if not redo_existing and (output_path / f"preds_{save_name}.json").exists():
            # Also include instances where model_test_patch is empty
            output_preds = json.loads((output_path / f"preds_{save_name}.json").read_text())
            existing_instances = set(output_preds.keys())
            if redo_fail_instance:
                for key,value in output_preds.items():
                    if 'model_test_patch' in value and value['model_test_patch'] == "":
                        existing_instances.remove(key)

            if instance_ids:
                for instance_id in instance_ids:
                    if instance_id in existing_instances:
                        existing_instances.remove(instance_id)

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

        if model == 'zai/glm-4.7':
            config.setdefault("model", {})['model_kwargs']["reasoning_effort"] = 'low'
    
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
