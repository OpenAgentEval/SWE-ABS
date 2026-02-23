"""
This script evaluates whether model-generated test patches (model_test_patch) are valid.

It works by:
1. Taking a JSON/JSONL file containing instances with model_test_patch
2. Applying the gold patch to the codebase
3. Applying the model_test_patch (new tests)
4. Running the tests to see which ones fail

The goal is to verify if the model-generated test patch is effective -
a good test patch should pass when the gold patch is applied.

Usage:
python eval_model_test_patch.py \
    --input_path=predictions.jsonl \
    --output_dir={OUTPUT}/ \
    --scripts_dir=run_scripts \
    --num_workers=100 \
    --dockerhub_username=your-username \
    --use_local_docker

Input format (JSON/JSONL):
{
    "instance_id": "unique_id",
    "model_test_patch": "git patch for new tests",
    "patch": "gold patch",
    "base_commit": "...",
    "before_repo_set_cmd": "...",
    "selected_test_files_to_run": [...],
    "repo": "org/repo",
    ...
}

Output format:
{
    "instance_id": {
        "gold_state": {
            "fail": [...],  # list of failed test names
            "eval_status_map": {...}  # detailed status per test
        },
        "error": null or "error message",
        "log_dir": "path/to/logs"
    },
    ...
}
"""

import argparse
import concurrent.futures
import json
import os
import re
from pathlib import Path
import platform as py_platform
import time

try:
    import modal
except Exception:
    modal = None
try:
    import docker
except Exception:
    docker = None
import pandas as pd
from tqdm import tqdm

from helper_code.image_uri import get_dockerhub_image_uri
from utils.constants import (
    RUN_EVALUATION_LOG_DIR,
    RUN_SWE_PLIS_DIR
)

from utils.logging_utils import setup_global_logger
from utils.parser_util import str2bool, get_test_directives, analyze_test_results



global_logger = None


def load_base_docker(iid):
    with open(f"dockerfiles/base_dockerfile/{iid}/Dockerfile") as fp:
        return fp.read()


def instance_docker(iid):
    with open(f"dockerfiles/instance_dockerfile/{iid}/Dockerfile") as fp:
        return fp.read()


def load_local_script(scripts_dir, instance_id, script_name):
    """Load a script file from local scripts directory."""
    script_path = os.path.join(scripts_dir, instance_id, script_name)
    if not os.path.exists(script_path):
        raise FileNotFoundError(f"Script not found: {script_path}")

    with open(script_path, 'r') as f:
        return f.read()


def create_entryscript_for_test_patch(sample):
    """
    Create entry script that:
    1. Applies the gold patch first
    2. Then applies the model_test_patch (new tests)
    3. Runs the tests
    """
    before_repo_set_cmd = sample["before_repo_set_cmd"].strip().split("\n")[-1]
    selected_test_files_to_run = ",".join(eval(sample["selected_test_files_to_run"]))

    test_directives = get_test_directives(sample, 'model_test_patch')
    repo_language = sample.get("repo_language", "python")
    base_commit = sample["base_commit"]
    base_dockerfile = load_base_docker(sample["instance_id"])
    instance_dockerfile = instance_docker(sample["instance_id"])

    # Extract ENV commands from dockerfiles
    env_cmds = []
    for dockerfile_content in [base_dockerfile, instance_dockerfile]:
        for line in dockerfile_content.split("\n"):
            line = line.strip()
            if line.startswith("ENV"):
                env_cmd = line.replace("ENV", "export", 1)
                env_cmds.append(env_cmd)

    env_cmds = "\n".join(env_cmds)

    # For Go projects, use the extracted test info (package paths + test names)
    if repo_language == "go" and isinstance(test_directives, dict):
        package_paths = " ".join(test_directives["package_paths"])
        test_names = test_directives["test_names"]

        # if test_names:
        #     # Build -run pattern: escape special regex chars and join with |
        #     # For Ginkgo Describe names with spaces, we need to escape them properly
        #     run_patterns = []
        #     for name in test_names:
        #         # Escape regex special characters and replace spaces with \s+ for flexibility
        #         escaped = re.escape(name)
        #         run_patterns.append(escaped)
        #     run_pattern = "|".join(run_patterns)
        #     test_command = f'go test -v -tags netgo {package_paths} -run "{run_pattern}" 2>&1 | tee /workspace/stdout.log'
        # else:
            # No specific test names, run all tests in the packages
        test_command = f"go test -v -tags netgo {package_paths} > /workspace/stdout.log 2> /workspace/stderr.log"
    else:
        # For other languages, use run_script.sh
        if isinstance(test_directives, dict):
            # Shouldn't happen, but handle gracefully
            selected_test_files_to_run = ','.join(test_directives.get("package_paths", []))
        else:
            selected_test_files_to_run = ','.join(test_directives)
        test_command = f"bash /workspace/run_script.sh {selected_test_files_to_run} > /workspace/stdout.log 2> /workspace/stderr.log"

    entry_script = f"""
{env_cmds}
# apply patches
cd /app
git reset --hard {base_commit}
git checkout {base_commit}

# First apply the gold patch (the actual fix)
echo "Applying gold patch..."
git apply -v /workspace/gold_patch.diff
if [ $? -ne 0 ]; then
    echo "ERROR: Failed to apply gold patch"
    exit 1
fi

# Then apply the model test patch (new tests generated by model)
echo "Applying model test patch..."
git apply -v /workspace/model_test_patch.diff
if [ $? -ne 0 ]; then
    echo "ERROR: Failed to apply model test patch"
    exit 1
fi

{before_repo_set_cmd}

# run test and save stdout and stderr to separate files
{test_command}

# run parsing script
python /workspace/parser.py /workspace/stdout.log /workspace/stderr.log /workspace/output.json
"""
    return entry_script


def prepare_run(uid, output_dir, prefix, redo):
    uid_dir = os.path.join(output_dir, uid)
    os.makedirs(uid_dir, exist_ok=True)
    output_path = os.path.join(uid_dir, f"{prefix}_output.json")
    if not redo and os.path.exists(output_path):
        print(f"Skipping {uid} - output already exists")
        with open(output_path, "r") as f:
            return json.load(f), output_path, os.path.join(uid_dir, "workspace")
    workspace_dir = os.path.join(uid_dir, "workspace")
    os.makedirs(workspace_dir, exist_ok=True)
    return None, output_path, workspace_dir


def write_patch_snapshot(output_dir, uid, prefix, patch, filename):
    with open(os.path.join(output_dir, uid, f"{prefix}_{filename}"), "w") as f:
        f.write(patch)


def assemble_workspace_files_for_test_patch(uid, scripts_dir, gold_patch, model_test_patch, sample):
    """
    Prepare workspace files for evaluating model test patch.
    """
    run_script = load_local_script(scripts_dir, uid, "run_script.sh")
    parser_script = load_local_script(scripts_dir, uid, "parser.py")
    entryscript_content = create_entryscript_for_test_patch(sample)

    files = {
        "gold_patch.diff": gold_patch,
        "model_test_patch.diff": model_test_patch,
        "run_script.sh": run_script,
        "parser.py": parser_script,
        "entryscript.sh": entryscript_content,
    }
    return files, entryscript_content


def write_files_local(workspace_dir, files):
    for rel_path, content in files.items():
        dst = os.path.join(workspace_dir, rel_path)
        with open(dst, "w") as f:
            f.write(content)


def save_entryscript_copy(output_dir, uid, prefix, entryscript_content):
    with open(os.path.join(output_dir, uid, f"{prefix}_entryscript.sh"), "w") as f:
        f.write(entryscript_content if entryscript_content is not None else "")


def collect_outputs_local(workspace_dir, output_dir, uid, prefix):
    def _copy_safe(src_name, dest_name):
        src_path = os.path.join(workspace_dir, src_name)
        dest_path = os.path.join(output_dir, uid, dest_name)
        try:
            with open(src_path, "r") as f_in:
                content = f_in.read()
        except FileNotFoundError:
            content = ""
        with open(dest_path, "w") as f_out:
            f_out.write(content if content is not None else "")

    _copy_safe("stdout.log", f"{prefix}_stdout.log")
    _copy_safe("stderr.log", f"{prefix}_stderr.log")

    try:
        with open(os.path.join(workspace_dir, "output.json"), "r") as f_in:
            output = json.load(f_in)
            with open(os.path.join(output_dir, uid, f"{prefix}_output.json"), "w") as f:
                json.dump(output, f, indent=4, ensure_ascii=False)
            return output
    except FileNotFoundError:
        print(
            f"Warning: output.json not found for {uid}. Check {prefix}_stdout.log and {prefix}_stderr.log for details"
        )
        return None




def eval_model_test_patch_with_docker(
    sample,
    output_dir,
    dockerhub_username,
    scripts_dir,
    prefix="gold_with_model_test",
    redo=False,
    block_network=False,
    docker_platform=None,
    mem_limit=None,
    timeout=None
):
    """
    Evaluate model test patch by:
    1. Applying gold patch
    2. Applying model test patch
    3. Running tests and recording failures
    """
    if docker is None:
        raise RuntimeError("docker SDK is not installed. Install via 'pip install docker' or run without --use_local_docker")

    uid = sample["instance_id"]
    gold_patch = sample.get("patch", "")
    model_test_patch = sample.get("model_test_patch", "")

    result = {
        "instance_id": uid,
        "gold_state": {},
        "error": None,
        "log_dir": None
    }

    # Check if we have required patches
    if not model_test_patch or not model_test_patch.strip():
        result["error"] = "No model_test_patch provided"
        return result

    if not gold_patch or not gold_patch.strip():
        result["error"] = "No gold patch provided"
        return result

    existing_output, output_path, workspace_dir = prepare_run(uid, output_dir, prefix, redo)
    result["log_dir"] = os.path.join(output_dir, uid)

    if existing_output is not None:
        # Analyze existing output
        failed_tests, eval_status_map = analyze_test_results(existing_output)

        if eval_status_map == {}:
            failed_tests = ["Return eval_status_map is empty"]

        result["gold_state"] = {
            "fail": failed_tests,
            "eval_status_map": eval_status_map
        }
        return result

    # print(f"Running evaluation for {uid}")

    try:
        try:
            files, entryscript_content = assemble_workspace_files_for_test_patch(
                uid, scripts_dir, gold_patch, model_test_patch, sample
            )
        except FileNotFoundError as e:
            result["error"] = f"Error loading scripts: {e}"
            global_logger.error(f"Error loading scripts for {uid}: {e}")
            return result

        # Write files to workspace
        write_files_local(workspace_dir, files)

        # Save patches for debugging
        write_patch_snapshot(output_dir, uid, prefix, gold_patch, "gold_patch.diff")
        write_patch_snapshot(output_dir, uid, prefix, model_test_patch, "model_test_patch.diff")

        # Get Docker image
        dockerhub_image_uri = get_dockerhub_image_uri(uid, dockerhub_username, sample.get("repo", ""))
        # print(f"Using Docker Hub image: {dockerhub_image_uri}")

        client = docker.from_env()
        try:
            if docker_platform:
                client.images.pull(dockerhub_image_uri, platform=docker_platform)
            else:
                client.images.pull(dockerhub_image_uri)
        except Exception as pull_err:
            try:
                client.images.get(dockerhub_image_uri)
                global_logger.error(f"Using locally available image: {dockerhub_image_uri}")
            except Exception:
                result["error"] = f"Failed to pull or find image: {pull_err}"
                global_logger.error(f"Failed to pull or find image locally for {uid}: {pull_err}")
                return result

        abs_workspace_dir = os.path.abspath(workspace_dir)
        volumes = {abs_workspace_dir: {"bind": "/workspace", "mode": "rw"}}
        run_kwargs = {
            "volumes": volumes,
            "detach": True,
            "remove": False,
            "entrypoint": "/bin/bash",
            "command": ["-c", "bash /workspace/entryscript.sh"],
        }
        if block_network:
            run_kwargs["network_mode"] = "none"
        if docker_platform:
            run_kwargs["platform"] = docker_platform
        if mem_limit:
            run_kwargs["mem_limit"] = mem_limit
            run_kwargs['memswap_limit'] = mem_limit
            
        container = client.containers.run(
            dockerhub_image_uri,
            **run_kwargs,
        )

        timed_out = False
        oom_killed = False
        try:
            container_result = container.wait(timeout=timeout)
            status_code = container_result.get("StatusCode", 1) if isinstance(container_result, dict) else 1

            # Check if container was OOM killed (exit code 137 = 128 + 9 SIGKILL)
            if status_code == 137:
                # Double check by inspecting container state
                try:
                    container.reload()
                    state = container.attrs.get("State", {})
                    oom_killed = state.get("OOMKilled", False)
                except Exception:
                    # If exit code is 137, assume OOM even if we can't confirm
                    oom_killed = True
                if oom_killed:
                    global_logger.error(f"Container OOM killed for {uid}")

        except Exception as wait_err:
            # Timeout or other wait error
            timed_out = True
            status_code = -1
            global_logger.error(f"Container timeout or wait error for {uid}: {wait_err}")
            try:
                container.kill()
            except Exception:
                pass
            try:
                container.remove(force=True)
            except Exception:
                pass
        
        # Save raw container logs (stdout + stderr from Docker)
        try:
            raw_logs = container.logs(stdout=True, stderr=True).decode("utf-8", errors="replace")
            raw_log_path = os.path.join(output_dir, uid, f"raw_container.log")
            with open(raw_log_path, "w") as f:
                f.write(raw_logs)
        except Exception as log_err:
            global_logger.error(f"Failed to save raw container logs for {uid}: {log_err}")


        if status_code != 0 and not timed_out:
            global_logger.error(f"Entryscript failed for {uid} with return code: {status_code}")

        # Collect outputs
        output = collect_outputs_local(workspace_dir, output_dir, uid, prefix)
        save_entryscript_copy(output_dir, uid, prefix, entryscript_content)

        # Clean up container
        try:
            container.remove()
        except Exception:
            pass

        # Handle timeout case
        if timed_out:
            result["gold_state"] = {
                "fail": [f"TIMEOUT - Container exceeded {timeout}s limit"],
                "eval_status_map": {}
            }
            return result

        # Handle OOM case
        if oom_killed:
            result["gold_state"] = {
                "fail": [f"OOM_KILLED - Container exceeded memory limit ({mem_limit})"],
                "eval_status_map": {}
            }
            return result

        # Analyze results
        if output is None:
            result["gold_state"] = {
                "fail": ["RUN TEST ERROR - No output generated"],
                "eval_status_map": {}
            }
        else:
            failed_tests, eval_status_map = analyze_test_results(output)
            if eval_status_map == {}:
                failed_tests = ["Return eval_status_map is empty"]
            result["gold_state"] = {
                "fail": failed_tests,
                "eval_status_map": eval_status_map
            }

        return result

    except Exception as e:
        result["error"] = f"Exception: {repr(e)}"
        global_logger.error(f"Error in eval for {uid}: {repr(e)}")
        return result



def load_input_data(input_path):
    """Load input data from JSON or JSONL file."""
    if input_path.endswith(".jsonl"):
        data = []
        with open(input_path, "r") as f:
            for line in f:
                if line.strip():
                    data.append(json.loads(line))
        return data
    else:
        with open(input_path, "r") as f:
            data = json.load(f)
            if isinstance(data, dict):
                # Check if it's a dict of dicts (keyed by instance_id)
                first_value = next(iter(data.values()), None)
                if isinstance(first_value, dict) and "instance_id" in first_value:
                    # It's a dict of dicts, return the values as a list
                    return list(data.values())
                # Otherwise it's a single sample dict, wrap it in a list
                return [data]
            return data


def main(args):
    if args.eval_gold_patch:
        SAVE_DIR = "eval_gold_patch"
    elif args.mutation_paths:
        SAVE_DIR = "eval_mutation"
    elif args.vaild_model_path:
        SAVE_DIR = "eval_agent"

    run_id = args.run_id

    global global_logger
    global_log_file = Path(RUN_SWE_PLIS_DIR) / SAVE_DIR / run_id / "global.log"
    global_logger = setup_global_logger(global_log_file, add_stdout=True)
    final_results_save_file = Path(RUN_SWE_PLIS_DIR) / SAVE_DIR / run_id / "final_results.json"
    

    # Load input data
    samples = load_input_data(args.input_path)
    global_logger.info(f"Loaded {len(samples)} samples from {args.input_path}")


    if args.output_dir is None:
        args.output_dir = RUN_EVALUATION_LOG_DIR / SAVE_DIR / run_id


    # Create output directory
    os.makedirs(args.output_dir, exist_ok=True)

    # Filter by instance_ids if specified
    if args.instance_ids:
        samples = [s for s in samples if s.get("instance_id") in args.instance_ids]
        global_logger.info(f"Filtered to {len(samples)} samples based on instance_ids")

    # Filter out samples without model_test_patch
    valid_samples = [s for s in samples if s.get("model_test_patch", "").strip()]

    # Filter by language if specified
    if args.only_languages:
        valid_samples = [s for s in valid_samples if s.get("repo_language", "") in args.only_languages]
        global_logger.info(f"Filtered to languages: {args.only_languages}")
    elif args.exclude_languages:
        valid_samples = [s for s in valid_samples if s.get("repo_language", "") not in args.exclude_languages]
        global_logger.info(f"Excluded languages: {args.exclude_languages}")

    global_logger.info(f"Found {len(valid_samples)} samples with valid model_test_patch")

    if not valid_samples:
        global_logger.info("No valid samples to evaluate")
        return

    global_logger.info("Waiting 5 seconds before starting evaluations...")
    time.sleep(5)

    # Auto-detect platform for Apple Silicon
    detected_platform = None
    if args.use_local_docker and args.docker_platform is None:
        try:
            if py_platform.machine().lower() in {"arm64", "aarch64"}:
                detected_platform = "linux/amd64"
        except Exception:
            detected_platform = None

    all_results = {}

    # Statistics tracking
    stats = {
        "total": len(valid_samples),
        "pass": 0,  # No failed tests
        "fail": 0,  # Has failed tests
        "error": 0,  # Evaluation error
    }

    # Use ThreadPoolExecutor for parallel execution
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.num_workers) as executor:
        future_to_sample = {
            executor.submit(
                eval_model_test_patch_with_docker,
                sample,
                args.output_dir,
                args.dockerhub_username,
                args.scripts_dir,
                prefix="gold_with_model_test",
                redo=args.redo,
                block_network=args.block_network,
                docker_platform=(args.docker_platform or detected_platform) if args.use_local_docker else None,
                mem_limit=args.mem_limit,
                timeout=args.timeout,
            ): sample
            for sample in valid_samples
        }

        pbar = tqdm(concurrent.futures.as_completed(future_to_sample), total=len(valid_samples))
        for future in pbar:
            sample = future_to_sample[future]
            instance_id = sample.get("instance_id", "unknown")

            try:
                result = future.result()
                all_results[instance_id] = result

                # Update statistics
                if result.get("error"):
                    stats["error"] += 1
                elif not result.get("gold_state", {}).get("fail", []):
                    stats["pass"] += 1
                else:
                    stats["fail"] += 1

            except Exception as exc:
                global_logger.error(f"Evaluation for {instance_id} generated an exception: {exc}")
                all_results[instance_id] = {
                    "instance_id": instance_id,
                    "gold_state": {},
                    "error": str(exc),
                    "log_dir": None
                }
                stats["error"] += 1

            # Update progress bar
            pbar.set_description(
                f"Pass: {stats['pass']}, Fail: {stats['fail']}, Error: {stats['error']}"
            )

    # Save results
    # output_file = os.path.join(args.output_dir, "eval_model_test_results.json")
    with open(final_results_save_file, "w") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)

    # Print summary
    global_logger.info("\n" + "="*50)
    global_logger.info("Evaluation Summary")
    global_logger.info("="*50)
    global_logger.info(f"Total samples: {stats['total']}")
    global_logger.info(f"Pass (all tests passed): {stats['pass']} ({stats['pass']/stats['total']*100:.1f}%)")
    global_logger.info(f"Fail (some tests failed): {stats['fail']} ({stats['fail']/stats['total']*100:.1f}%)")
    global_logger.info(f"Error (evaluation failed): {stats['error']} ({stats['error']/stats['total']*100:.1f}%)")
    global_logger.info(f"\nResults saved to: {final_results_save_file}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Evaluate model-generated test patches using gold patch + model test patch"
    )
    parser.add_argument(
        "--input_path",
        required=True,
        help="Path to JSON/JSONL file containing instances with model_test_patch"
    )
    parser.add_argument(
        "--output_dir",
        required=False,
        default=None,
        help="Directory to store evaluation outputs"
    )
    parser.add_argument(
        "--dockerhub_username",
        required=True,
        help="Docker Hub username where sweap-images repository is located"
    )
    parser.add_argument(
        "--scripts_dir",
        required=True,
        help="Directory containing local run scripts"
    )
    parser.add_argument(
        "--use_local_docker",
        action="store_true",
        help="Run locally with Docker instead of Modal"
    )
    parser.add_argument(
        "--docker_platform",
        default=None,
        help="Docker platform override, e.g., linux/amd64"
    )
    parser.add_argument(
        "--redo",
        default=False,
        type=str2bool,
        help="Redo evaluations even if output exists"
    )
    parser.add_argument(
        "--num_workers",
        type=int,
        default=50,
        help="Number of workers to run evaluations in parallel"
    )
    parser.add_argument(
        "--block_network",
        action="store_true",
        help="Block network access inside container"
    )
    parser.add_argument(
        "--mem_limit",
        default="8g",
        help="Memory limit per container (e.g., '8g', '4g', '16g'). Default: 8g"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=1800,
        help="Timeout in seconds for each container. Default: 1800 (30 minutes)"
    )
    parser.add_argument(
        "-i",
        "--instance_ids",
        type=lambda s: s.split(","),
        help="Instance IDs to run (comma separated)"
    )

    parser.add_argument(
        "--run_id",
        required=True,
        help="Run ID"
    )

    parser.add_argument(
        "--eval_gold_patch",
        default=False,
        type=str2bool,
        help="Evaluate gold patch",

    )

    parser.add_argument(
        "--mutation_paths",
        type=lambda s: s.split(","),
        help="Comma separated list of mutation paths"
    )
    parser.add_argument(
        "--vaild_model_path",
        type=lambda s: s.split(","),
        help="Comma separated list of valid model paths"
    )

    parser.add_argument(
        "--only_languages",
        type=lambda s: set(s.split(",")),
        default=None,
        help="Only run instances with these languages (comma separated, e.g., 'js,ts')"
    )

    parser.add_argument(
        "--exclude_languages",
        type=lambda s: set(s.split(",")),
        default=None,
        help="Exclude instances with these languages (comma separated, e.g., 'js,ts')"
    )


    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    main(args)
