"""
Extract patch-related line numbers for coverage analysis.

This script:
1. Takes a dataset containing instances with patches
2. Applies the patch to the codebase in a Docker container
3. Extracts the modified files and computes must-coverage information
4. Saves the line numbers that should be covered by tests

Usage:
python get_line_number.py \
    --input_path=data.csv \
    --output_dir={OUTPUT}/ \
    --scripts_dir=run_scripts \
    --num_workers=10 \
    --dockerhub_username=your-username \
    --use_local_docker \
    --run_id=extract_lines_v1

Output format:
{
    "instance_id": {
        "file_path": {
            "exe_slice_lines_scope": [1, 2, 3, ...],
            "exe_slice_lines": [1, 2, 3, ...],
            "exe_modified_lines": [1, 2, 3, ...],
            "content": "file content...",
            "language": "python"
        },
        ...
    },
    ...
}
"""

import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import platform as py_platform
import time

try:
    import docker
except Exception:
    docker = None
import pandas as pd
from tqdm import tqdm

from helper_code.image_uri import get_dockerhub_image_uri
from utils.constants import (
    RUN_EVALUATION_LOG_DIR,
    RUN_SWE_PLIS_DIR,
)
from utils.logging_utils import setup_global_logger
from utils.parser_util import str2bool
from utils.must_coverage_utils import compute_must_coverage

RUN_SWE_PLIS_DIR=Path("swe_plus_res/")
RUN_EVALUATION_LOG_DIR=Path("logs/")

global_logger = None


def load_base_docker(iid):
    with open(f"dockerfiles/base_dockerfile/{iid}/Dockerfile") as fp:
        return fp.read()


def instance_docker(iid):
    with open(f"dockerfiles/instance_dockerfile/{iid}/Dockerfile") as fp:
        return fp.read()


def create_entryscript_for_line_extraction(sample):
    """
    Create entry script that applies the patch and prepares for file extraction.
    """
    before_repo_set_cmd = sample["before_repo_set_cmd"].strip().split("\n")[-1]
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

    entry_script = f"""
{env_cmds}
# apply patch
cd /app
git reset --hard {base_commit}
git checkout {base_commit}

echo "Applying patch..."
git apply -v /workspace/patch.diff 2>&1 | tee /workspace/apply_patch.log

if [ $? -ne 0 ]; then
    echo "Warning: git apply failed, trying with --reject..."
    git apply -v --reject /workspace/patch.diff 2>&1 | tee /workspace/apply_patch.log
fi

{before_repo_set_cmd}

echo "Patch applied successfully"
echo "READY_FOR_EXTRACTION" > /workspace/ready.flag

# Keep container running until signaled to stop
# Wait for stop signal (file /workspace/stop.flag)
while [ ! -f /workspace/stop.flag ]; do
    sleep 1
done
echo "Stop signal received, exiting..."
"""
    return entry_script


def prepare_run(uid, output_dir, redo):
    uid_dir = os.path.join(output_dir, uid)
    os.makedirs(uid_dir, exist_ok=True)
    output_path = os.path.join(uid_dir, "must_coverage.json")
    if not redo and os.path.exists(output_path):
        print(f"Skipping {uid} - output already exists")
        with open(output_path, "r") as f:
            return json.load(f), output_path, os.path.join(uid_dir, "workspace")
    workspace_dir = os.path.join(uid_dir, "workspace")
    os.makedirs(workspace_dir, exist_ok=True)
    return None, output_path, workspace_dir


def write_files_local(workspace_dir, files):
    for rel_path, content in files.items():
        dst = os.path.join(workspace_dir, rel_path)
        with open(dst, "w") as f:
            f.write(content)


def extract_line_numbers_with_docker(
    sample,
    output_dir,
    dockerhub_username,
    redo=False,
    docker_platform=None,
    mem_limit=None,
    timeout=None
):
    """
    Extract patch-related line numbers by:
    1. Applying the patch in a Docker container
    2. Fetching the modified files
    3. Computing must-coverage information using code analysis
    """
    if docker is None:
        raise RuntimeError("docker SDK is not installed. Install via 'pip install docker'")

    uid = sample["instance_id"]
    patch = sample.get("patch", "")

    result = {
        "instance_id": uid,
        "must_coverage": None,
        "error": None,
        "log_dir": None
    }

    # Check if we have a patch
    if not patch or not patch.strip():
        result["error"] = "No patch provided"
        return result

    existing_output, output_path, workspace_dir = prepare_run(uid, output_dir, redo)
    result["log_dir"] = os.path.join(output_dir, uid)

    if existing_output is not None:
        result["must_coverage"] = existing_output
        return result

    try:
        # Create entry script
        entryscript_content = create_entryscript_for_line_extraction(sample)

        files = {
            "patch.diff": patch,
            "entryscript.sh": entryscript_content,
        }

        # Write files to workspace
        write_files_local(workspace_dir, files)

        # Save patch for debugging
        with open(os.path.join(output_dir, uid, "patch.diff"), "w") as f:
            f.write(patch)

        # Get Docker image
        dockerhub_image_uri = get_dockerhub_image_uri(uid, dockerhub_username, sample.get("repo", ""))

        client = docker.from_env()
        try:
            if docker_platform:
                client.images.pull(dockerhub_image_uri, platform=docker_platform)
            else:
                client.images.pull(dockerhub_image_uri)
        except Exception as pull_err:
            try:
                client.images.get(dockerhub_image_uri)
                global_logger.info(f"Using locally available image: {dockerhub_image_uri}")
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
        if docker_platform:
            run_kwargs["platform"] = docker_platform
        if mem_limit:
            run_kwargs["mem_limit"] = mem_limit

        container = client.containers.run(
            dockerhub_image_uri,
            **run_kwargs,
        )

        # Wait for container to be ready (check for ready.flag file)
        ready_flag_path = os.path.join(workspace_dir, "ready.flag")
        wait_start = time.time()
        timed_out = False

        while not os.path.exists(ready_flag_path):
            time.sleep(0.5)
            if time.time() - wait_start > timeout:
                timed_out = True
                global_logger.error(f"Container timeout waiting for ready flag for {uid}")
                break
            # Also check if container exited unexpectedly
            container.reload()
            if container.status not in ("running", "created"):
                global_logger.error(f"Container exited unexpectedly with status: {container.status}")
                break

        if timed_out:
            result["error"] = f"Container timeout exceeded {timeout}s"
            try:
                container.kill()
                container.remove()
            except Exception:
                pass
            return result

        # Read the apply patch log
        apply_log_path = os.path.join(workspace_dir, "apply_patch.log")
        if os.path.exists(apply_log_path):
            with open(apply_log_path, "r") as f:
                patch_log = f.read()
        else:
            patch_log = ""

        # Compute must coverage using the running container
        try:
            save_dir = Path(output_dir) / uid
            must_coverage = compute_must_coverage(
                container=container,
                patch=patch,
                save_dir=save_dir,
                logger=global_logger,
                patch_log=patch_log
            )

            result["must_coverage"] = must_coverage

            # Save must_coverage to file
            with open(output_path, "w") as f:
                json.dump(must_coverage, f, indent=2, ensure_ascii=False)

        except Exception as e:
            result["error"] = f"Failed to compute must_coverage: {e}"
            global_logger.error(f"Failed to compute must_coverage for {uid}: {e}")

        # Signal container to stop by creating stop.flag
        stop_flag_path = os.path.join(workspace_dir, "stop.flag")
        with open(stop_flag_path, "w") as f:
            f.write("stop")

        # Wait for container to exit gracefully
        try:
            container.wait(timeout=10)
        except Exception:
            pass

        # Save raw container logs
        try:
            raw_logs = container.logs(stdout=True, stderr=True).decode("utf-8", errors="replace")
            raw_log_path = os.path.join(output_dir, uid, "raw_container.log")
            with open(raw_log_path, "w") as f:
                f.write(raw_logs)
        except Exception as log_err:
            global_logger.error(f"Failed to save raw container logs for {uid}: {log_err}")

        # Clean up container
        try:
            container.remove()
        except Exception:
            pass

        return result

    except Exception as e:
        result["error"] = f"Exception: {repr(e)}"
        global_logger.error(f"Error in extraction for {uid}: {repr(e)}")
        return result


def load_input_data(input_path):
    """Load input data from CSV, JSON, or JSONL file."""
    if input_path.endswith(".csv"):
        df = pd.read_csv(input_path)
        df = df.fillna("")
        return df.to_dict("records")
    elif input_path.endswith(".jsonl"):
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
                first_value = next(iter(data.values()), None)
                if isinstance(first_value, dict) and "instance_id" in first_value:
                    return list(data.values())
                return [data]
            return data


def main(args):
    run_id = args.run_id

    global global_logger
    global_log_file = Path(RUN_SWE_PLIS_DIR) / "extract_line_numbers" / run_id / "global.log"
    global_log_file.parent.mkdir(parents=True, exist_ok=True)
    global_logger = setup_global_logger(global_log_file, add_stdout=True)
    final_results_save_file = Path(RUN_SWE_PLIS_DIR) / "extract_line_numbers" / run_id / "final_results.json"

    # Load input data
    samples = load_input_data(args.input_path)
    global_logger.info(f"Loaded {len(samples)} samples from {args.input_path}")

    if args.output_dir is None:
        args.output_dir = RUN_EVALUATION_LOG_DIR / "extract_line_numbers" / run_id

    # Create output directory
    os.makedirs(args.output_dir, exist_ok=True)

    # Filter by instance_ids if specified
    if args.instance_ids:
        samples = [s for s in samples if s.get("instance_id") in args.instance_ids]
        global_logger.info(f"Filtered to {len(samples)} samples based on instance_ids")

    else:
        if os.path.exists(final_results_save_file):
            with open(final_results_save_file, "r") as f:
                all_results = json.load(f)

            right_instance = []

            for key,value in all_results.items():
                if not value.get("error"):
                    right_instance.append(key)
  
            samples = [s for s in samples if s.get("instance_id") not in right_instance]
            global_logger.info(f"Filtered to {len(samples)} samples based on existing results")

    # Filter out samples without patch
    valid_samples = [s for s in samples if s.get("patch", "").strip()]
    global_logger.info(f"Found {len(valid_samples)} samples with valid patch")

    if not valid_samples:
        global_logger.info("No valid samples to process")
        return

    global_logger.info("Waiting 5 seconds before starting...")
    time.sleep(5)

    # Auto-detect platform for Apple Silicon
    detected_platform = None
    if args.docker_platform is None:
        try:
            if py_platform.machine().lower() in {"arm64", "aarch64"}:
                detected_platform = "linux/amd64"
        except Exception:
            detected_platform = None

    all_results = {}


    if os.path.exists(final_results_save_file):
        with open(final_results_save_file, "r") as f:
            all_results = json.load(f)

    # Statistics tracking
    stats = {
        "total": len(valid_samples),
        "success": 0,
        "error": 0,
    }

    # Use ThreadPoolExecutor for parallel execution
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.num_workers) as executor:
        future_to_sample = {
            executor.submit(
                extract_line_numbers_with_docker,
                sample,
                args.output_dir,
                args.dockerhub_username,
                redo=args.redo,
                docker_platform=(args.docker_platform or detected_platform),
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

                if result.get("error"):
                    stats["error"] += 1
                    all_results[instance_id] = {"error": result["error"]}
                elif result.get("must_coverage"):
                    stats["success"] += 1
                    all_results[instance_id] = result["must_coverage"]
                else:
                    stats["error"] += 1
                    all_results[instance_id] = {"error": "No must_coverage computed"}

            except Exception as exc:
                global_logger.error(f"Extraction for {instance_id} generated an exception: {exc}")
                all_results[instance_id] = {"error": str(exc)}
                stats["error"] += 1

            # Update progress bar
            pbar.set_description(
                f"Success: {stats['success']}, Error: {stats['error']}"
            )

    # Save results
    with open(final_results_save_file, "w") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)

    # Print summary
    global_logger.info("\n" + "="*50)
    global_logger.info("Extraction Summary")
    global_logger.info("="*50)
    global_logger.info(f"Total samples: {stats['total']}")
    global_logger.info(f"Success: {stats['success']} ({stats['success']/stats['total']*100:.1f}%)")
    global_logger.info(f"Error: {stats['error']} ({stats['error']/stats['total']*100:.1f}%)")
    global_logger.info(f"\nResults saved to: {final_results_save_file}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Extract patch-related line numbers for coverage analysis"
    )
    parser.add_argument(
        "--input_path",
        required=True,
        help="Path to CSV/JSON/JSONL file containing instances with patches"
    )
    parser.add_argument(
        "--output_dir",
        required=False,
        default=None,
        help="Directory to store extraction outputs"
    )
    parser.add_argument(
        "--dockerhub_username",
        required=True,
        help="Docker Hub username where images are located"
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
        help="Redo extraction even if output exists"
    )
    parser.add_argument(
        "--num_workers",
        type=int,
        default=10,
        help="Number of workers to run in parallel"
    )
    parser.add_argument(
        "--mem_limit",
        default="8g",
        help="Memory limit per container (e.g., '8g', '4g', '16g'). Default: 8g"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Timeout in seconds for each container. Default: 600 (10 minutes)"
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
        help="Run ID for this extraction job"
    )

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    main(args)
