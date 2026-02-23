"""
The script is used to evaluate the performance of the SWEAP Pro agent with Modal.

This evaluation script:
1. Takes a CSV file containing test cases and a JSON file containing patches
2. Runs each patch in a Modal sandbox environment using Docker Hub images
3. Executes the tests using local run scripts and collects results
4. Calculates overall accuracy based on test pass/fail status

Usage:
python sweap_pro_eval_modal.py \
    --raw_sample_path=data.csv \
    --patch_path={OUTPUT}/gold_patches.json \
    --output_dir={OUTPUT}/ \
    --scripts_dir=run_scripts \
    --num_workers=100 \
    --dockerhub_username=your-username

It expects:
- Local run scripts in run_scripts/{instance_id}/run_script.sh
- Local parser scripts in run_scripts/{instance_id}/parser.py
- CSV file with columns: instance_id, before_repo_set_cmd, selected_test_files_to_run, 
  base_commit, base_dockerfile, instance_dockerfile, FAIL_TO_PASS, PASS_TO_PASS

And the generated patch file (gold_patches.json) should have the following format:
[
    {
        "instance_id": "unique_id",
        "patch": "git patch content",
        "prefix": "optional_prefix"
    },
    ...
]
"""

import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import platform as py_platform
import re
import shutil
import sys
import time

import docker
import pandas as pd
from tqdm import tqdm

# Add util path for ResultManager
UTIL_PATH = Path(__file__).resolve().parent.parent.parent / "util"
sys.path.insert(0, str(UTIL_PATH))
from sweabs_utils.preds_manager import ResultManager

from helper_code.image_uri import get_dockerhub_image_uri
from utils.logging_utils import setup_global_logger
from utils.parser_util import str2bool, read_list_file, analyze_test_results
from utils.unified_log_parsers import parse_logs_with_unified_parser
from utils.coverage_parse_utils import compute_coverage
from utils.run_util import (
    load_base_docker,
    instance_docker,
    load_local_script,
    prepare_run,
    write_files_local,
    write_patch_snapshot,
    save_entryscript_copy
)
from utils.constants import (
    DOCKER_USER,
    DOCKER_WORKDIR,
    KEY_INSTANCE_ID,
    KEY_MODEL,
    KEY_PREDICTION,
    RUN_EVALUATION_LOG_DIR,
    KEY_MUTATION_THINKING,
    PASS_INIT_TEST,
    FAIL_INIT_TEST
)
global_logger = None



def create_entryscript(sample):
    before_repo_set_cmd = sample["before_repo_set_cmd"].strip().split("\n")[-1]
    selected_test_files_to_run = ",".join(eval(sample["selected_test_files_to_run"]))
    base_commit = sample["base_commit"]
    base_dockerfile = load_base_docker(sample["instance_id"])
    instance_dockerfile = instance_docker(sample["instance_id"])
    
    # Extract ENV commands from dockerfiles
    env_cmds = []
    for dockerfile_content in [base_dockerfile, instance_dockerfile]:
        for line in dockerfile_content.split("\n"):
            line = line.strip()
            if line.startswith("ENV"):
                # Convert ENV commands to export statements
                env_cmd = line.replace("ENV", "export", 1)
                env_cmds.append(env_cmd)
    
    env_cmds = "\n".join(env_cmds)

    entry_script = f"""
{env_cmds}
# apply patch
cd /app
git reset --hard {base_commit}
git checkout {base_commit}
git apply -v /workspace/patch.diff
{before_repo_set_cmd}
# run test and save stdout and stderr to separate files
bash /workspace/run_script.sh {selected_test_files_to_run} > /workspace/stdout.log 2> /workspace/stderr.log
# run parsing script
python /workspace/parser.py /workspace/stdout.log /workspace/stderr.log /workspace/output.json
"""
    return entry_script


def assemble_workspace_files(uid, scripts_dir, patch, sample):

    run_script = load_local_script(scripts_dir, uid, "run_script.sh")

    parser_script = load_local_script(scripts_dir, uid, "parser.py")
    entryscript_content = create_entryscript(sample)

    files = {
        "patch.diff": patch,
        "run_script.sh": run_script,
        "parser.py": parser_script,
        "entryscript.sh": entryscript_content,
    }
    return files, entryscript_content



def collect_outputs_local(workspace_dir, log_dir, uid, prefix):
    def _copy_safe(src_name, dest_name):
        src_path = os.path.join(workspace_dir, src_name)
        dest_path = os.path.join(log_dir, dest_name)
        try:
            with open(src_path, "r") as f_in:
                content = f_in.read()
        except FileNotFoundError:
            content = ""
        with open(dest_path, "w") as f_out:
            f_out.write(content if content is not None else "")

    _copy_safe("stdout.log", f"{prefix}_stdout.log")
    _copy_safe("stderr.log", f"{prefix}_stderr.log")


    # Then try to read output.json
    try:
        with open(os.path.join(workspace_dir, "output.json"), "r") as f_in:
            output = json.load(f_in)
            # Add coverage data to output
            with open(os.path.join(log_dir, f"{prefix}_output.json"), "w") as f:
                json.dump(output, f)
            return output
    except FileNotFoundError:
        global_logger.info(
            f"Warning: output.json not found for {uid}. Check {prefix}_stdout.log and {prefix}_stderr.log for details"
        )
        return None





def eval_with_docker(patch, # model patch
                    sample, # instance_dict
                    output_dir,
                    dockerhub_username, scripts_dir, prefix="", redo=False, block_network=False, docker_platform=None, mem_limit="8g", timeout=1800):
    if docker is None:
        raise RuntimeError("docker SDK is not installed. Install via 'pip install docker' or run without --use_local_docker")
    uid = sample["instance_id"]
    log_dir = os.path.join(output_dir, uid)
    existing_output, output_path, workspace_dir = prepare_run(log_dir, prefix, redo)
    if existing_output is not None:
        return existing_output

    # global_logger.info(f"Running local-docker evaluation for {uid}")

    try:
        try:
            # One is a dict, the other is a str
            # entryscript_content appears to be the entry script
            files, entryscript_content = assemble_workspace_files(uid, scripts_dir, patch, sample)
        except FileNotFoundError as e:
            global_logger.info(f"Error loading scripts for {uid}: {e}")
            return None
        # Write some data to local files

        # Write files content to local: primarily dataset from the original dataset, stored in /output_dir/uid/workspace
        write_files_local(workspace_dir, files)
        # Write model_patch to local, stored at /output_dir/uid/patch.diff
        write_patch_snapshot(log_dir, prefix, patch)

        # Run container via Docker SDK
        dockerhub_image_uri = get_dockerhub_image_uri(uid, dockerhub_username, sample.get("repo", ""))
        # global_logger.info(f"Using Docker Hub image: {dockerhub_image_uri}")

        client = docker.from_env()
        try:
            if docker_platform:
                client.images.pull(dockerhub_image_uri, platform=docker_platform)
            else:
                client.images.pull(dockerhub_image_uri)
        except Exception as pull_err:
            # If pull fails, fall back to a local image if present; otherwise, fail this run
            try:
                client.images.get(dockerhub_image_uri)
                global_logger.info(f"Using locally available image: {dockerhub_image_uri}")
            except Exception:
                global_logger.info(f"Failed to pull or find image locally for {uid}: {pull_err}")
                return None

        abs_workspace_dir = os.path.abspath(workspace_dir)
        volumes = {abs_workspace_dir: {"bind": "/workspace", "mode": "rw"}}
        run_kwargs = {
            "volumes": volumes,
            "detach": True,
            "remove": False,
            "entrypoint": "/bin/bash",  # Override image entrypoint
            "command": ["-c", "bash /workspace/entryscript.sh"],
            # Resource limits: max 16 CPU cores and configurable memory
            # Use nano_cpus for stricter CPU limiting (16 cores = 16 * 10^9)
            "nano_cpus": 8 * 1000000000,  # 8 cores
            "mem_limit": mem_limit,
            "memswap_limit": mem_limit,  # Prevent swap usage beyond memory limit
        }
        if block_network:
            run_kwargs["network_mode"] = "none"
        # Optional platform override (useful on Apple Silicon)
        if docker_platform:
            run_kwargs["platform"] = docker_platform

        container = client.containers.run(
            dockerhub_image_uri,
            **run_kwargs,
        )

        try:
            result = container.wait(timeout=timeout)
            status_code = result.get("StatusCode", 1) if isinstance(result, dict) else 1
            if status_code != 0:
                global_logger.info(f"Entryscript failed for {uid} with return code: {status_code}")
        except Exception as wait_err:
            global_logger.info(f"Container timeout or wait error for {uid}: {wait_err}")
            try:
                container.remove()
            except Exception:
                pass
            status_code = 1
        # Collect outputs and logs, and save entryscript for reference
        output = collect_outputs_local(workspace_dir, log_dir, uid, prefix)
        if output is None:
            return None
        save_entryscript_copy(log_dir, prefix, entryscript_content)
        try:
            container.remove()
        except Exception:
            pass
        return output
    except Exception as e:
        global_logger.info(f"Error in eval_with_docker for {uid}: {repr(e)}")
        global_logger.info(f"Error type: {type(e)}")
        return None






def format_gold_patch(raw_sample_df):
    """
    Format gold patches into a list compatible with evaluator input.

    Returns:
        list[dict]: [
            {
                "instance_id": str,
                "patch": str,
                "prefix": "gold"
            },
            ...
        ]
    """
    gold_patches = []

    # Auto-detect the patch column
    patch_col = "patch"

    for instance_id, row in raw_sample_df.iterrows():
        patch = row[patch_col]

        # Skip empty patches
        if not isinstance(patch, str) or patch.strip() == "":
            continue

        gold_patches.append({
            "instance_id": instance_id,
            "patch": patch,
            "prefix": "gold"
        })

    return gold_patches



def main(args):
    global global_logger

    # Create output directory at start
    os.makedirs(args.output_dir, exist_ok=True)

    # Initialize global logger
    global_log_file = Path(args.output_dir) / "global.log"
    global_logger = setup_global_logger(global_log_file, add_stdout=True)


    # Support both JSONL and CSV input files
    if args.raw_sample_path.endswith(".jsonl"):
        raw_sample_df = pd.read_json(args.raw_sample_path, lines=True)
    else:
        raw_sample_df = pd.read_csv(args.raw_sample_path)
    
    # Replace nulls with empty strings
    raw_sample_df = raw_sample_df.fillna("")
    
    # use instance_id as index
    raw_sample_df = raw_sample_df.set_index("instance_id", drop=False)

    # each patch sample is a dict with keys: instance_id, patch, prefix
    if args.patch_path == 'gold':
        patches_to_run = format_gold_patch(raw_sample_df)
    else:
        if os.path.exists(args.patch_path) and os.path.isfile(args.patch_path):
            with open(args.patch_path, "r") as f:
                patches_to_run = json.load(f)
            patches_to_run:dict
            patches_to_run = list(patches_to_run.values())
        else:
            raise FileNotFoundError(f"Patch file not found: {args.patch_path}")
    eval_results = {}


    # Filter patches to only include those with matching instance_ids in the raw sample data
    valid_patches = []
    missing_instances = []
    for patch_sample in patches_to_run:
        instance_id = patch_sample["instance_id"]
        if args.instance_ids and instance_id not in args.instance_ids:
            continue

        if instance_id in raw_sample_df.index:
            valid_patches.append(patch_sample)
        else:
            missing_instances.append(instance_id)
    global_logger.info(f"Runing {len(valid_patches)} valid patches out of {len(patches_to_run)} total patches,total raw sample {len(raw_sample_df)}")
    if missing_instances:
        global_logger.info(f"Warning: Found {len(missing_instances)} patch instances not in raw sample data:")
        for missing_id in missing_instances[:5]:  # Show first 5
            global_logger.info(f"  - {missing_id}")
        if len(missing_instances) > 5:
            global_logger.info(f"  ... and {len(missing_instances) - 5} more")
        global_logger.info(f"Proceeding with {len(valid_patches)} valid patches out of {len(patches_to_run)} total patches")

    global_logger.info("Wait for 5 seconds before Running evaluations...")
    time.sleep(5)

    # Select runtime
    # Auto-detect default platform if not provided: prefer linux/amd64 on Apple Silicon
    detected_platform = None
    if args.use_local_docker and args.docker_platform is None:
        try:
            if py_platform.machine().lower() in {"arm64", "aarch64"}:
                detected_platform = "linux/amd64"
        except Exception:
            detected_platform = None

    eval_fn = eval_with_docker

    # Use ThreadPoolExecutor to run evaluations in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.num_workers) as executor:
        # Create a dictionary mapping futures to their patch samples for progress tracking
        future_to_patch = {
            executor.submit(
                eval_fn,
                patch_sample.get("model_patch", patch_sample.get("patch", "")),
                raw_sample_df.loc[patch_sample["instance_id"]],
                args.output_dir,
                args.dockerhub_username,
                args.scripts_dir,
                prefix=patch_sample.get("prefix", ""),
                redo=args.redo,
                block_network=args.block_network,
                docker_platform=(args.docker_platform or detected_platform) if args.use_local_docker else None,
                mem_limit=args.mem_limit,
                timeout=args.timeout,
            ): patch_sample
            for patch_sample in valid_patches
        }

        # Track progress with tqdm and show running accuracy
        pbar = tqdm(concurrent.futures.as_completed(future_to_patch), total=len(valid_patches))
        for future in pbar:
            patch_sample = future_to_patch[future]
            try:
                # Get the result (if any error occurred, it will be raised here)
                output = future.result()
                if output is None:
                    global_logger.info(f'Evaluation for {patch_sample["instance_id"]} returned None')
                    eval_results[patch_sample["instance_id"]] = False
                else:
                    instance_id = patch_sample["instance_id"]
                    if instance_id not in raw_sample_df.index:
                        global_logger.info(f'Warning: Instance {instance_id} not found in raw sample data, skipping')
                        eval_results[instance_id] = False
                    else:
                        raw_sample = raw_sample_df.loc[instance_id]
                        passed_tests = {x["name"] for x in output["tests"] if x["status"] == "PASSED"}
                        f2p = set(eval(raw_sample["fail_to_pass"]))
                        p2p = set(eval(raw_sample["pass_to_pass"]))
                        result = (f2p | p2p) <= passed_tests
                        eval_results[instance_id] = result

                current_accuracy = sum(eval_results.values()) / len(eval_results)
                pbar.set_description(f"Accuracy: {current_accuracy:.2%}")
            except Exception as exc:
                global_logger.info(f'Evaluation for {patch_sample["instance_id"]} generated an exception: {exc}')
                eval_results[patch_sample["instance_id"]] = False
                # Update progress bar description with current accuracy
                current_accuracy = sum(eval_results.values()) / len(eval_results)
                pbar.set_description(f"Accuracy: {current_accuracy:.2%}")
    with open(os.path.join(args.output_dir, "eval_results.json"), "w") as f:
        json.dump(eval_results, f, indent=4, ensure_ascii=False)
    global_logger.info(
    "Overall accuracy: %f",
    sum(eval_results.values()) / len(eval_results)
    )

    output_dir = Path(args.output_dir)
    # Write init_patch eval results back to predictions
    if args.eval_mutation:
        # Initialize ResultManager
        result_manager = ResultManager(args.patch_path)

        for instance_id in eval_results:
            if eval_results[instance_id] == 'error':
                status = "uncompleted"
            else:
                status = "completed"

            # Use ResultManager to update evaluation_info
            result_manager.update_instance(instance_id, {
                'evaluation_info': {
                    "status": status,
                    "pass_init_test_status": PASS_INIT_TEST if eval_results[instance_id] else FAIL_INIT_TEST,
                    "outputs": str(output_dir.resolve()),
                }
            }, merge=True)

        # ! Find empty patches and set evaluation_info
        all_instances = result_manager.get_all_instances()
        for instance_id in all_instances:
            model_patch = all_instances[instance_id].get(KEY_PREDICTION)
            if model_patch is None or model_patch == '':
                result_manager.update_instance(instance_id, {
                    'evaluation_info': {
                        "status": "completed",
                        "pass_init_test_status": FAIL_INIT_TEST,
                    }
                }, merge=True)


def parse_args():
    parser = argparse.ArgumentParser(description="Run SWEAP Pro evaluations using Modal or local Docker with Docker Hub images and local scripts")
    parser.add_argument("--raw_sample_path", required=True, help="Path to the raw sample CSV file")
    parser.add_argument(
        "--patch_path", required=True, help="Path to the JSON file containing patches"
    )
    parser.add_argument("--output_dir", required=True, help="Directory to store evaluation outputs")
    parser.add_argument(
        "--dockerhub_username", required=True, help="Docker Hub username where sweap-images repository is located"
    )
    parser.add_argument(
        "--scripts_dir", required=True, help="Directory containing local run scripts (e.g., scripts/run_scripts)"
    )
    parser.add_argument(
        "--use_local_docker", action="store_true", help="Run locally with Docker instead of Modal"
    )
    parser.add_argument(
        "--docker_platform",
        default=None,
        help="Docker platform override, e.g., linux/amd64; defaults to auto-detect",
    )
    parser.add_argument(
        "--redo", action="store_true", help="Redo evaluations even if output exists"
    )
    parser.add_argument(
        "--num_workers",
        type=int,
        default=50,
        help="Number of workers to run evaluations in parallel",
    )
    parser.add_argument(
        "--block_network", action="store_true", help="Block network access inside container"
    )
    parser.add_argument(
        "-i",
        "--instance_ids",
        type=lambda s: s.split(","),
        help="Instance IDs to run (space separated)",
    )
    parser.add_argument(
        "--mem_limit",
        default="8g",
        help="Memory limit per container (e.g., '8g', '4g', '16g'). Default: 8g"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=480,
        help="Timeout in seconds for each container. Default: 1800 (30 minutes)"
    )
    parser.add_argument(
        "--eval_mutation",
        type=str2bool,
        default=False,
    )
    return parser.parse_args()

if __name__ == "__main__":
    args = parse_args()
    main(args)
