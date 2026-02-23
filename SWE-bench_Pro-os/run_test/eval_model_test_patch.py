
import argparse
from collections import defaultdict
import concurrent.futures
import copy
import json
import os
import re
from pathlib import Path
import platform as py_platform
import sys
import time
import traceback

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

# Add util path for ResultManager
UTIL_PATH = Path(__file__).resolve().parent.parent.parent / "util"
sys.path.insert(0, str(UTIL_PATH))
from sweabs_utils.preds_manager import ResultManager

from helper_code.image_uri import get_dockerhub_image_uri
from utils.constants import (
    RUN_EVALUATION_LOG_DIR,
    RUN_SWE_PLIS_DIR,
    SUCCESS_STATUS,
    FAIL_STATUS
)

from utils.logging_utils import setup_global_logger
from utils.parser_util import str2bool, read_list_file, analyze_test_results
from utils.unified_log_parsers import parse_logs_with_unified_parser
from utils.coverage_parse_utils import compute_coverage, compute_coverage_batch
from utils.run_util import (
    assemble_workspace_files_for_test_patch,
    prepare_run,
    write_files_local,
    write_patch_snapshot,
    save_entryscript_copy,
    run_docker
)

global_logger = None

def get_coverage_instance(aug_test_instances):
    instances = {}
    for key in aug_test_instances:
        instance = aug_test_instances[key]
        # aug_test passed via coverage
        if 'meta' in instance and instance['meta']['pass_gold_patch_status']==SUCCESS_STATUS:
            if 0< instance['meta']['coverage_rate']<1.0:
                instances[key] = instance
        else:
            raise RuntimeError("Before running the coverage test, please judge the gold patch first.")
    return instances

def get_gold_fail_instance(aug_test_instances):
    instances = {}
    model_test_patch_none = 0
    for key in aug_test_instances:
        instance = aug_test_instances[key]
        # aug_test did not pass with gold_patch
        if "model_test_patch" in instance and instance['model_test_patch']!="":
            if 'meta' in instance and instance['meta']['pass_gold_patch_status']!=SUCCESS_STATUS:
                instances[key] = instance
        else:
            model_test_patch_none += 1
    global_logger.info(f"model_test_patch_none: {model_test_patch_none}")
    return instances


def get_gold_success_instance(aug_test_instances):
    instances = {}
    for key in aug_test_instances:
        instance = aug_test_instances[key]
        # aug_test passed with gold_patch
        if "model_test_patch" in instance and instance['model_test_patch']!="":
            if 'meta' in instance and instance['meta']['pass_gold_patch_status']==SUCCESS_STATUS:
                instances[key] = instance
            else:
                global_logger.error(f"model_test_patch no pass: {key}")
        else:
            global_logger.error(f"model_test_patch_none: {key}")

    return instances


def get_vaild_mutation(mutation_paths):
    # Merge mutations from multiple files for the same instance into a single dict
    use_instance = defaultdict(dict)
    for mutation_path in mutation_paths:
        mutation_key = Path(mutation_path).parent.name

        with open(mutation_path, "r") as f:
            mutation_instances = json.load(f)

        for key, value in mutation_instances.items():
            if 'evaluation_info' in value:
                # If init_test passes, then check judge info
                if value['evaluation_info']['pass_init_test_status'] == SUCCESS_STATUS:
                    if 'judge_info' in value:
                        if value['judge_info']['isrele'] is False:
                            continue
                    else:
                        raise RuntimeError(f"mutation_path {mutation_path}, instance {key}, mutation judge_info not found")

                    if mutation_key in use_instance[key]:
                        raise RuntimeError(f"Found the same mutation key: {mutation_key} in {key}")

                    # If in /res_equ_mutation/ and generated as equ_mutation but judged non-equivalent, skip
                    if 'res_equ_mutation' in mutation_path:
                        if value['judge_info']['isvalid']:
                            continue
                    use_instance[key][mutation_key]=value
            else:
                raise RuntimeError(f"mutation_path {mutation_path}, instance {key}, before judge mutation with aug test, please run the init test first.")

    return use_instance



def eval_model_test_patch_with_docker(
    args,
    sample,
    output_dir,
    dockerhub_username,
    scripts_dir,
    prefix="gold_with_model_test",
    redo=False,
    block_network=False,
    docker_platform=None,
    mem_limit=None,
    timeout=None,
    **kwargs
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
    repo_name = sample["repo"].replace("/", "__")

    # Build log_dir: output_dir/uid
    log_dir = os.path.join(output_dir, uid)

    result = {
        "instance_id": uid,
        "gold_state": {},
        "error": None,
        "log_dir": log_dir,
    }

    # Check if we have required patches
    if not model_test_patch or not model_test_patch.strip():
        result["error"] = "No model_test_patch provided"
        return result

    if not gold_patch or not gold_patch.strip():
        result["error"] = "No gold patch provided"
        return result

    existing_output, output_path, workspace_dir = prepare_run(log_dir, prefix, redo)

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
        output, timed_out = run_docker(
            args,
            uid,
            workspace_dir,
            log_dir,
            scripts_dir,
            gold_patch,
            model_test_patch,
            sample,
            prefix,
            result,
            repo_name,
            dockerhub_username,
            block_network=block_network,
            docker_platform=docker_platform,
            mem_limit=mem_limit,
            timeout=timeout,
            use_coverage=args.use_coverage
        )

        # Handle timeout case
        if timed_out:
            result["gold_state"] = {
                "fail": [f"TIMEOUT - Container exceeded {timeout}s limit"],
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


def eval_agent_with_model_test_patch_with_docker(
    args,
    sample,
    output_dir,
    dockerhub_username,
    scripts_dir,
    prefix="agent_with_model_test",
    redo=False,
    block_network=False,
    docker_platform=None,
    mem_limit=None,
    timeout=None,
    **kwargs
):
    """
    Evaluate whether agent-generated patches can pass model-generated test patches.

    For each agent's patch:
    1. Apply the agent's patch (instead of gold patch)
    2. Apply model_test_patch (the generated tests)
    3. Run tests and record results

    Args:
        args: Arguments containing all_vaild_model_path
        sample: Sample containing instance_id, model_test_patch, repo, etc.
        output_dir: Directory to store evaluation outputs
        dockerhub_username: Docker Hub username
        scripts_dir: Directory containing run scripts
        prefix: Prefix for output files
        redo: Whether to redo evaluations even if output exists
        block_network: Whether to block network in container
        docker_platform: Docker platform override
        mem_limit: Memory limit per container
        timeout: Timeout in seconds

    Returns:
        dict: Results for all agents with keys:
            - pass: list of agent names that passed all tests
            - fail: list of agent names that failed some tests
            - error: list of agent names that had errors
            - init_fail: list of agent names that were skipped (no patch or not resolved)
            - details: dict mapping agent_name to detailed result
    """
    if docker is None:
        raise RuntimeError("docker SDK is not installed. Install via 'pip install docker' or run without --use_local_docker")

    uid = sample["instance_id"]
    model_test_patch = sample.get("model_test_patch", "")
    repo_name = sample["repo"].replace("/", "__")

    # Initialize result structure
    all_result = {
        "instance_id": uid,
        "pass": [],
        "fail": [],
        "error": [],
        "init_fail": [],
        "details": {},
    }

    # Check if model_test_patch is valid
    if not model_test_patch or not model_test_patch.strip():
        all_result["error"].append("No model_test_patch provided")
        return all_result

    all_vaild_model_path = args.all_vaild_model_path
    # Base directory: output_dir/uid
    base_output_dir = Path(output_dir) / uid
    os.makedirs(base_output_dir, exist_ok=True)

    # Iterate over each agent's output path
    for path in tqdm(all_vaild_model_path, total=len(all_vaild_model_path), desc=uid[:20]):
        path = Path(path)
        agent_name = path.name

        if not path.exists():
            global_logger.warning(f"Path {path} does not exist, skipping agent {agent_name}")
            all_result['init_fail'].append(agent_name)
            continue

        # Check for required files
        use_path = path / "eval" / uid
        report_file = use_path / 'report.json'
        patch_file = use_path / '_patch.diff'

        if not report_file.exists() or not patch_file.exists():
            all_result['init_fail'].append(agent_name)
            continue

        # Load report and check if resolved
        try:
            with open(report_file, 'r') as f:
                report = json.load(f)
        except Exception as e:
            global_logger.error(f"Failed to load report for {agent_name}/{uid}: {e}")
            all_result['error'].append(agent_name)
            continue

        if report.get('resolved') is False:
            all_result['init_fail'].append(agent_name)
            continue

        # Load agent's patch
        try:
            with open(patch_file, 'r') as f:
                agent_patch = f.read()
        except Exception as e:
            global_logger.error(f"Failed to load patch for {agent_name}/{uid}: {e}")
            all_result['error'].append(agent_name)
            continue

        if not agent_patch or not agent_patch.strip():
            all_result['init_fail'].append(agent_name)
            continue

        # Build log_dir: output_dir/uid/agent_name
        log_dir = str(base_output_dir / agent_name)

        # Prepare run environment
        existing_output, output_path, workspace_dir = prepare_run(log_dir, prefix, redo)

        result = {
            "agent_name": agent_name,
            "gold_state": {},
            "error": None,
            "log_dir": log_dir,
        }

        # Check if we already have results
        if existing_output is not None:
            failed_tests, eval_status_map = analyze_test_results(existing_output)
            if eval_status_map == {}:
                failed_tests = ["Return eval_status_map is empty"]

            result["gold_state"] = {
                "fail": failed_tests,
                "eval_status_map": eval_status_map
            }

            # Classify result
            if not failed_tests:
                all_result['pass'].append(agent_name)
            else:
                all_result['fail'].append(agent_name)
            all_result['details'][agent_name] = result
            continue

        # Run Docker evaluation
        try:
            output, timed_out = run_docker(
                args,
                uid,
                workspace_dir,
                log_dir,  # Use the already-assembled log_dir
                scripts_dir,
                agent_patch,  # Use agent's patch instead of gold patch
                model_test_patch,
                sample,
                prefix,
                result,
                repo_name,
                dockerhub_username,
                block_network=block_network,
                docker_platform=docker_platform,
                mem_limit=mem_limit,
                timeout=timeout,
                use_coverage=args.use_coverage
            )

            # Handle timeout case
            if timed_out:
                result["gold_state"] = {
                    "fail": [f"TIMEOUT - Container exceeded {timeout}s limit"],
                    "eval_status_map": {}
                }
                all_result['error'].append(agent_name)
                all_result['details'][agent_name] = result
                continue

            # Analyze results
            if output is None:
                result["gold_state"] = {
                    "fail": ["RUN TEST ERROR - No output generated"],
                    "eval_status_map": {}
                }
                all_result['error'].append(agent_name)
            else:
                failed_tests, eval_status_map = analyze_test_results(output)
                if eval_status_map == {}:
                    failed_tests = ["Return eval_status_map is empty"]
                result["gold_state"] = {
                    "fail": failed_tests,
                    "eval_status_map": eval_status_map
                }

                # Classify result based on failed tests
                if not failed_tests:
                    all_result['pass'].append(agent_name)
                else:
                    all_result['fail'].append(agent_name)

            all_result['details'][agent_name] = result

        except Exception as e:
            traceback.print_exc()
            result["error"] = f"Exception: {repr(e)}"
            global_logger.error(f"Error in eval for {agent_name}/{uid}: {repr(e)}")
            all_result['error'].append(agent_name)
            all_result['details'][agent_name] = result

    return all_result

def eval_mutataion_with_model_test_patch_with_docker(
    args,
    sample,
    output_dir,
    dockerhub_username,
    scripts_dir,
    prefix="mutataion_with_model_test",
    redo=False,
    block_network=False,
    docker_platform=None,
    mem_limit=None,
    timeout=None,
    **kwargs
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
    model_test_patch = sample.get("model_test_patch", "")
    repo_name = sample["repo"].replace("/", "__")


    mutation_instance = kwargs.get("mutation_instance", {})
    # Initialize result structure
    all_result = {
        "pass": [],
        "fail": [],
        "error": [],
        "init_fail": [],
        "details": {},
    }

    # Check if model_test_patch is valid
    if not model_test_patch or not model_test_patch.strip():
        all_result["error"].append("No model_test_patch provided")
        return all_result

    # Base directory: output_dir/uid
    base_output_dir = Path(output_dir) / uid
    os.makedirs(base_output_dir, exist_ok=True)

    # Iterate over each agent's output path
    for mutation_key , mutation_instance in mutation_instance.items():
        # Build log_dir: output_dir/uid/agent_name
        log_dir = str(base_output_dir / mutation_key)
        
        # print(mutation_instance)
        
        model_patch = mutation_instance['model_patch']
        
        # Prepare run environment
        existing_output, output_path, workspace_dir = prepare_run(log_dir, prefix, redo)

        result = {
            "gold_state": {},
            "error": None,
            "log_dir": log_dir,
        }

        # Check if we already have results
        if existing_output is not None:
            failed_tests, eval_status_map = analyze_test_results(existing_output)
            if eval_status_map == {}:
                failed_tests = ["Return eval_status_map is empty"]

            result["gold_state"] = {
                "fail": failed_tests,
                "eval_status_map": eval_status_map
            }

            # Classify result
            if not failed_tests:
                all_result['pass'].append(mutation_key)
            else:
                all_result['fail'].append(mutation_key)
            all_result['details'][mutation_key] = result
            continue

        # Run Docker evaluation
        try:
            output, timed_out = run_docker(
                args,
                uid,
                workspace_dir,
                log_dir,  # Use the already-assembled log_dir
                scripts_dir,
                model_patch,  # Use agent's patch instead of gold patch
                model_test_patch,
                sample,
                prefix,
                result,
                repo_name,
                dockerhub_username,
                block_network=block_network,
                docker_platform=docker_platform,
                mem_limit=mem_limit,
                timeout=timeout,
                use_coverage=args.use_coverage
            )

            # Handle timeout case
            if timed_out:
                result["gold_state"] = {
                    "fail": [f"TIMEOUT - Container exceeded {timeout}s limit"],
                    "eval_status_map": {}
                }
                all_result['error'].append(mutation_key)
                all_result['details'][mutation_key] = result
                continue

            # Analyze results
            if output is None:
                result["gold_state"] = {
                    "fail": ["RUN TEST ERROR - No output generated"],
                    "eval_status_map": {}
                }
                all_result['error'].append(mutation_key)
            else:
                failed_tests, eval_status_map = analyze_test_results(output)
                if eval_status_map == {}:
                    failed_tests = ["Return eval_status_map is empty"]
                result["gold_state"] = {
                    "fail": failed_tests,
                    "eval_status_map": eval_status_map
                }

                # Classify result based on failed tests
                if not failed_tests:
                    all_result['pass'].append(mutation_key)
                else:
                    all_result['fail'].append(mutation_key)

            all_result['details'][mutation_key] = result

        except Exception as e:
            traceback.print_exc()
            result["error"] = f"Exception: {repr(e)}"
            global_logger.error(f"Error in eval for {mutation_key}/{uid}: {repr(e)}")
            all_result['error'].append(mutation_key)
            all_result['details'][mutation_key] = result

    return all_result



def run_instances(args,final_results_save_file,valid_samples,**kwargs):

    all_mutation_instance = kwargs.get('all_mutation_instance', {})
    # Auto-detect platform for Apple Silicon
    detected_platform = None
    if args.use_local_docker and args.docker_platform is None:
        try:
            if py_platform.machine().lower() in {"arm64", "aarch64"}:
                detected_platform = "linux/amd64"
        except Exception:
            detected_platform = None

    all_results = {}

    run_results = {}

    if os.path.exists(final_results_save_file):
        with open(final_results_save_file, "r") as f:
            all_results = json.load(f)

    # Statistics tracking
    stats = {
        "total": len(valid_samples),
        "pass": 0,  # No failed tests
        "fail": 0,  # Has failed tests
        "error": 0,  # Evaluation error
    }



    if hasattr(args, "all_vaild_model_path") and args.all_vaild_model_path:
        global_logger.info(f"Using eval_agent_with_model_test_patch_with_docker")
        run_func = eval_agent_with_model_test_patch_with_docker
    elif args.mutation_paths:
        global_logger.info(f"Using eval_mutataion_with_model_test_patch_with_docker")
        run_func = eval_mutataion_with_model_test_patch_with_docker
    else:
        global_logger.info(f"Using eval_model_test_patch_with_docker")
        run_func = eval_model_test_patch_with_docker
    
    # Use ThreadPoolExecutor for parallel execution
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.num_workers) as executor:
        future_to_sample = {
            executor.submit(
                run_func,
                args,
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
                mutation_instance=all_mutation_instance.get(sample["instance_id"], {}),
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
                run_results[instance_id] = result
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
                run_results[instance_id] = all_results[instance_id]
                stats["error"] += 1
                print(traceback.format_exc())
            # Update progress bar
            pbar.set_description(
                f"Pass: {stats['pass']}, Fail: {stats['fail']}, Error: {stats['error']}"
            )

    # Save results
    # output_file = os.path.join(args.output_dir, "eval_model_test_results.json")
    with open(final_results_save_file, "w") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)

    return run_results, stats


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
    with open(args.input_path, "r") as f:
        predictions_test = json.load(f)


    global_logger.info(f"Loaded {len(predictions_test)} samples from {args.input_path}")

    if args.output_dir is None:
        args.output_dir = RUN_EVALUATION_LOG_DIR / SAVE_DIR / run_id

    if args.coverage_eval:
        assert args.eval_gold_patch, "coverage_eval must be used with eval_gold_patch"
        assert args.must_cover_line, "coverage_eval must be used with must_cover_line"
        assert args.use_coverage, "coverage_eval must be used with use_coverage"


    all_predictions_test = copy.deepcopy(predictions_test)
    # Create output directory
    os.makedirs(args.output_dir, exist_ok=True)
    init_length  = len(predictions_test)
    # Filter by instance_ids if specified
    if args.instance_ids:
        if args.instance_ids[0] == "all":
            samples = list(predictions_test.values())
        else:
            samples = [predictions_test[instance_ids] for instance_ids in predictions_test if instance_ids in args.instance_ids]
        global_logger.info(f"Filtered to {len(samples)} samples based on instance_ids")
    elif args.run_instance_file:
        run_instance = read_list_file(args.run_instance_file)
        samples = [predictions_test[instance_ids] for instance_ids in predictions_test if instance_ids in run_instance]
        global_logger.info(f"Filtered to {len(samples)} samples based on run_instance_file")
    
    elif args.eval_gold_patch:
    # In eval_gold_patch mode, filter out instances where gold_patch succeeded
        # Filter out instances with coverage_rate < 1
        if args.coverage_eval:
            predictions_test = get_coverage_instance(predictions_test)
        else:
            predictions_test = get_gold_fail_instance(predictions_test)
        global_logger.info(f"Run eval_gold_patch mode, init_length:{init_length},get_gold_fail_instance_length:{len(predictions_test)}")
        if len(predictions_test) == 0:
            return
        samples = list(predictions_test.values())
    else:
        samples = list(predictions_test.values())

    if args.instance_ids and args.run_instance_file:
        raise ValueError("Cannot specify both instance_ids and run_instance_file")


    if args.vaild_model_path:        
        if args.vaild_model_name:
            all_vaild_model_path = [Path(args.vaild_model_path,vaild_name) 
                        for vaild_name in args.vaild_model_name]
        else:
            # Get all files/folders in the directory and convert to a list of Path objects
            # all_vaild_model_path = list(Path(vaild_model_path).iterdir()) 
            raise RuntimeError(f"vaild_model_name must be provided with the vaild_model_path:{args.vaild_model_path}")

        global_logger.info(f"all_vaild_model_path_length:{len(all_vaild_model_path)}")
        args.all_vaild_model_path = all_vaild_model_path



    all_mutation_instance = {}
    # Get instances where mutation ran successfully
    if args.mutation_paths:
        # print(args.mutation_paths)
        all_mutation_instance = get_vaild_mutation(args.mutation_paths)
        mutation_instance_keys = set(all_mutation_instance.keys())
        samples = [s for s in samples if s.get("instance_id") in mutation_instance_keys]
        global_logger.info(f"mutation_paths:{args.mutation_paths}")
        global_logger.info(f"all_mutation_instance_length:{len(mutation_instance_keys)}")

    # Filter out samples without model_test_patch
    valid_samples = [s for s in samples if s.get("model_test_patch", "").strip()]

    global_logger.info(f"Found {len(valid_samples)} samples with valid model_test_patch")

    if not valid_samples:
        global_logger.info("No valid samples to evaluate")
        return

    global_logger.info("Waiting 5 seconds before starting evaluations...")
    time.sleep(5)


    # ! ============================= run docker stage =================================
    results_dict, stats = run_instances(args,final_results_save_file, valid_samples,all_mutation_instance = all_mutation_instance)


    # Print summary
    if not args.rewrite_preds:
        global_logger.info("\n" + "="*50)
        global_logger.info("Evaluation Summary")
        global_logger.info("="*50)
        global_logger.info(f"Total samples: {stats['total']}")
        global_logger.info(f"Pass (all tests passed): {stats['pass']} ({stats['pass']/stats['total']*100:.1f}%)")
        global_logger.info(f"Fail (some tests failed): {stats['fail']} ({stats['fail']/stats['total']*100:.1f}%)")
        global_logger.info(f"Error (evaluation failed): {stats['error']} ({stats['error']/stats['total']*100:.1f}%)")
        global_logger.info(f"\nResults saved to: {final_results_save_file}")
        return

    # ! ============================= post-processing and save stage =================================

    predictions_test_path = args.input_path
    dirname, filename = os.path.split(predictions_test_path)
    name, ext = os.path.splitext(filename)


    # Mark init_fail for each aug_test
    if args.eval_gold_patch:

        # If use_coverage is specified and the must_cover_line file path is provided
        if args.use_coverage and args.must_cover_line:
            with open(args.must_cover_line) as f:
                modified_related_lines = json.load(f)

        # gold_fail_num = 0
        gold_fail_list = []
        gold_error_list = []
        no_cover_list = []


        log_dir = RUN_EVALUATION_LOG_DIR / SAVE_DIR / run_id

        # Batch compute coverage
        coverage_results = {}
        if args.use_coverage and args.must_cover_line:
            coverage_results = compute_coverage_batch(str(log_dir), modified_related_lines)

        # Initialize ResultManager
        result_manager = ResultManager(predictions_test_path)

        # todo: complex details inside results_dict should be handled before returning
        for instance_id,value in results_dict.items():
            if "gold_state" in value and "fail" in value['gold_state']:
                error_info = ""
                if len(value['gold_state']['fail'])>0:
                    pass_gold_patch_status = FAIL_STATUS
                    gold_fail_list.append(instance_id)
                else:
                    pass_gold_patch_status = SUCCESS_STATUS

                if (args.use_coverage and args.must_cover_line \
                    and instance_id in coverage_results \
                    and pass_gold_patch_status == SUCCESS_STATUS):
                    coverage_rate, uncovered_lines = coverage_results[instance_id]
                    if 0 < coverage_rate < 1.0:
                        no_cover_list.append(instance_id)
                else:
                    coverage_rate,uncovered_lines = 'unknow',{}

                # Use ResultManager to update meta fields
                result_manager.update_instance_nested(instance_id, {
                    'meta.pass_gold_patch_status': pass_gold_patch_status,
                    'meta.coverage_rate': coverage_rate,
                    'meta.uncovered_lines': uncovered_lines,
                })

                evaluation_info = {
                    "status": "completed",
                    "pass_gold_patch_status": pass_gold_patch_status,
                    "outputs": str(log_dir.resolve()),
                    "error_info": error_info,
                    "coverage_rate": coverage_rate,
                    "uncovered_lines": uncovered_lines,
                }

                # Update the last stage's evaluation_info
                instance_data = result_manager.get_instance(instance_id)
                if instance_data and 'stage' in instance_data and instance_data['stage']:
                    instance_data['stage'][-1]['evaluation_info'] = evaluation_info
                    result_manager.update_instance(instance_id, instance_data, merge=False)

            elif "gold_state" in value and "error" in value['gold_state']:
                gold_error_list.append(instance_id)


        global_logger.info(f"predictions_test_path_with_gold save in:{predictions_test_path}")
        global_logger.info(f"gold_fail_num:{len(gold_fail_list)}")
        global_logger.info(f"gold_fail_list:{gold_fail_list}")
        global_logger.info(f"gold_error_num:{len(gold_error_list)}")
        global_logger.info(f"gold_error_list:{gold_error_list}")
        global_logger.info(f"no_cover_num:{len(no_cover_list)}")
        global_logger.info(f"no_cover_list:{no_cover_list}")



    # Collect usable pred_with_mutation entries
    if args.mutation_paths:
        # Append _mutation to the name
        new_name = f"{name}_mutation{ext}"
        # Construct the new path
        predictions_test_path_with_mutation = os.path.join(dirname, new_name)

        # Initialize ResultManager for mutation output
        mutation_result_manager = ResultManager(predictions_test_path_with_mutation)

        need_aug_case = {
            'run_fail_equ':set(),
            'run_success_no_equ':set(),
        }

        for instance_id,value in results_dict.items():
            mutation_info = {
                "run_success_equ":[],
                "run_fail_equ":[],
                "run_success_no_equ":[],
                "run_fail_no_equ":[],
                "run_error":[],
            }

            all_mutatation_patch = {}
            for mutation_key in all_mutation_instance[instance_id]:
                mutation_instance = all_mutation_instance[instance_id][mutation_key]
                isequ = not mutation_instance['judge_info']['isvalid']
                mutation_path_dict = {
                    "mutation_key":mutation_key,
                    "model_patch": mutation_instance['model_patch'],
                    "mutation_thinking": mutation_instance['mutation_thinking'],
                    # "outputs": mutation_instance['outputs'],
                    "isequ": isequ,
                }
                all_mutatation_patch[mutation_key] = mutation_path_dict

                if mutation_key in value['fail']:
                    if isequ:
                        mutation_info['run_fail_equ'].append(mutation_key)
                        need_aug_case['run_fail_equ'].add(instance_id)
                    # non-equivalent
                    else:
                        if mutation_key.startswith("equ"):
                            continue
                        mutation_info['run_fail_no_equ'].append(mutation_key)
                elif mutation_key in value['pass']:
                    if isequ:
                        mutation_info['run_success_equ'].append(mutation_key)
                    # non-equivalent
                    else:
                        if mutation_key.startswith("equ"):
                            continue
                        mutation_info['run_success_no_equ'].append(mutation_key)
                        need_aug_case['run_success_no_equ'].add(instance_id)
                else:
                    mutation_info['run_error'].append(mutation_key)

            # Get instance data and remove meta/stage, then add mutation_info
            instance_data = all_predictions_test[instance_id].copy()
            if 'meta' in instance_data:
                del instance_data['meta']
            if 'stage' in instance_data:
                del instance_data['stage']
            instance_data['mutation_info'] = mutation_info
            instance_data['all_mutatation_patch'] = all_mutatation_patch

            # Use ResultManager to update instance
            mutation_result_manager.update_instance(instance_id, instance_data, merge=False)

        need_aug_case_length = len(need_aug_case['run_fail_equ'] | need_aug_case['run_success_no_equ'])
        global_logger.info(f"predictions_test_path_with_mutation save into:{predictions_test_path_with_mutation}")
        global_logger.info(f"need_aug_case_length:{need_aug_case_length},total nums:{len(results_dict)}")
        global_logger.info(f"need_aug_case:{need_aug_case}")




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
        default=480,
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
        "--vaild_model_name",
        # type=str,
        # nargs="+",
        type=lambda s: s.split(","),
        help="the model name generate patch e.g trae ",
        required=False,  # Changed to optional
        default=[],  # Default value is an empty list
    )

    parser.add_argument(
        "--vaild_model_path",
        type=str,
        help="path to store vaild model patch log",
        required=False,
        default=None, 
    )
    parser.add_argument(
        "--use_coverage",
        type=str2bool,
        default=False,
    )
    parser.add_argument(
        "--must_cover_line",
        type=str,
        help="Path to load must cover line file",
        required=False,
        default="",
    )
    parser.add_argument(
        "--rewrite_preds",
        type=str2bool,
        default=False,
    )
    parser.add_argument(
        "--run_instance_file",
        type=str,
        help="path to store instance_id to run",
        required=False,
        default=None, 
    )

    parser.add_argument(
        "--coverage_eval",
        type=str2bool,
        default=False,
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    main(args)
