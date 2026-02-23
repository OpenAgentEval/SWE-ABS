import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import platform as py_platform
import sys
import time
import traceback

try:
    import docker
except Exception:
    docker = None
from tqdm import tqdm

# Add util path for ResultManager
UTIL_PATH = Path(__file__).resolve().parent.parent.parent / "util"
sys.path.insert(0, str(UTIL_PATH))
from sweabs_utils.preds_manager import ResultManager

from utils.constants import (
    RUN_EVALUATION_LOG_DIR,
    RUN_SWE_PLIS_DIR,
    SUCCESS_STATUS,
    FAIL_STATUS
)
from utils.logging_utils import setup_global_logger
from utils.parser_util import str2bool, read_list_file, analyze_test_results, remove_conflicting_chunks
from utils.run_util import prepare_run, run_docker

global_logger = None
SAVE_DIR = "aug_mutation"


# ============================================================================
# Result Processing Helpers
# ============================================================================

def process_test_output(output, timeout=None, timed_out=False):
    """
    Process Docker output and return standardized gold_state dict.

    Returns:
        dict: {"fail": list, "eval_status_map": dict}
    """
    if timed_out:
        return {
            "fail": [f"TIMEOUT - Container exceeded {timeout}s limit"],
            "eval_status_map": {}
        }

    if output is None:
        return {
            "fail": ["RUN TEST ERROR - No output generated"],
            "eval_status_map": {}
        }

    failed_tests, eval_status_map = analyze_test_results(output)
    if not eval_status_map:
        failed_tests = ["Return eval_status_map is empty"]

    return {
        "fail": failed_tests,
        "eval_status_map": eval_status_map
    }


def run_single_evaluation(args, uid, workspace_dir, log_dir, code_patch, test_patch, sample, repo_name):
    """
        Run a single Docker evaluation and return processed result.

            Docker-related parameters are taken directly from args.

            Returns:
                tuple: (gold_state_dict, error_message_or_none)
    """
    try:
        result_placeholder = {"gold_state": {}, "error": None, "log_dir": log_dir}

        output, timed_out = run_docker(
            args, uid, workspace_dir, log_dir, args.scripts_dir,
            code_patch, test_patch, sample, "aug_mutation", result_placeholder,
            repo_name, global_logger, args.dockerhub_username,
            block_network=args.block_network,
            docker_platform=args._docker_platform,
            mem_limit=args.mem_limit,
            timeout=args.timeout,
        )

        gold_state = process_test_output(output, args.timeout, timed_out)
        return gold_state, None

    except Exception as e:
        traceback.print_exc()
        return None, repr(e)


def _classify_mutation_result(all_result, mutation_key, target_aug_key, failed_tests):
    """Classify mutation result into pass/fail and check aug_success."""
    if failed_tests:
        all_result['fail'].append(mutation_key)
        if mutation_key == target_aug_key:
            all_result['aug_success'] = True
    else:
        all_result['pass'].append(mutation_key)
        if mutation_key == target_aug_key:
            all_result['aug_success'] = False


def _is_error_result(gold_state):
    """Check if the result indicates an error (timeout or run error)."""
    if not gold_state or "fail" not in gold_state:
        return False
    fail_list = gold_state["fail"]
    if not fail_list:
        return False
    first_fail = fail_list[0]
    return "TIMEOUT" in first_fail or "RUN TEST ERROR" in first_fail


def eval_aug_model_test_patch_with_docker(args, sample, eval_gold_patch=True):
    """
        Evaluate model test patch with mutation testing.

            Args:
                args: Command-line arguments containing all configuration (Docker, paths, etc.)
                sample: Sample data
                eval_gold_patch: Whether to evaluate the gold patch first
    """
    if docker is None:
        raise RuntimeError(
            "docker SDK is not installed. "
            "Install via 'pip install docker' or run without --use_local_docker"
        )

    uid = sample["instance_id"]
    gold_patch = sample.get("patch", "")
    model_test_patch = sample.get("model_test_patch", "")
    repo_name = sample["repo"].replace("/", "__")

    target_aug_key = sample.get('aug_meta', {}).get('target_aug_key', None)
    need_aug_keys = sample.get('mutation_info', {}).get(args.use_key, [])
    all_mutatation_patch = sample.get('all_mutatation_patch', {})

    all_result = {
        "gold_state": {},
        "pass": [],
        "fail": [],
        "error": [],
        "init_fail": [],
        "details": {},
    }

    if not model_test_patch or not model_test_patch.strip():
        all_result["error"].append("No model_test_patch provided")
        return all_result

    base_output_dir = Path(args.output_dir) / uid
    os.makedirs(base_output_dir, exist_ok=True)

    # Step 1: Evaluate gold_patch (optional)
    if eval_gold_patch:
        log_dir = str(base_output_dir / "gold_patch")
        existing_output, _, workspace_dir = prepare_run(log_dir, "aug_mutation", args.redo)

        if existing_output is not None:
            gold_state = process_test_output(existing_output)
        else:
            gold_state, error = run_single_evaluation(
                args, uid, workspace_dir, log_dir, gold_patch, model_test_patch, sample, repo_name
            )
            if error:
                global_logger.error(f"Error in gold_patch eval for {uid}: {error}")
                gold_state = {"error": [("gold_patch", error)]}

        all_result["gold_state"] = gold_state

        # If gold patch has an error, return immediately
        if "error" in gold_state:
            return all_result

        # If gold patch test fails, the test itself is problematic; no need to continue evaluating mutations
        if gold_state.get("fail"):
            global_logger.warning(f"Gold patch failed for {uid}, skipping mutation evaluation")
            return all_result

    # Step 2: Iterate over mutation patches
    for mutation_key in need_aug_keys:
        if mutation_key not in all_mutatation_patch:
            all_result['error'].append((mutation_key, "mutation_key not found"))
            continue

        model_patch = all_mutatation_patch[mutation_key].get('model_patch', '')

        model_patch = remove_conflicting_chunks(model_patch, model_test_patch)
        if not model_patch or not model_patch.strip():
            all_result['error'].append((mutation_key, "No model_patch provided"))
            continue

        log_dir = str(base_output_dir / mutation_key)
        existing_output, _, workspace_dir = prepare_run(log_dir, "aug_mutation", args.redo)
        result = {"gold_state": {}, "error": None, "log_dir": log_dir}

        if existing_output is not None:
            gold_state = process_test_output(existing_output)
        else:
            gold_state, error = run_single_evaluation(
                args, uid, workspace_dir, log_dir, model_patch, model_test_patch, sample, repo_name
            )
            if error:
                result["error"] = f"Exception: {error}"
                global_logger.error(f"Error in eval for {mutation_key}/{uid}: {error}")
                all_result['error'].append(mutation_key)
                all_result['details'][mutation_key] = result
                continue

        result["gold_state"] = gold_state

        if _is_error_result(gold_state):
            all_result['error'].append(mutation_key)
        else:
            _classify_mutation_result(all_result, mutation_key, target_aug_key, gold_state["fail"])

        all_result['details'][mutation_key] = result

    return all_result




# ============================================================================
# Parallel Execution
# ============================================================================

def _detect_docker_platform(args):
    """Auto-detect Docker platform for Apple Silicon."""
    if args.use_local_docker and args.docker_platform is None:
        try:
            if py_platform.machine().lower() in {"arm64", "aarch64"}:
                return "linux/amd64"
        except Exception:
            pass
    return args.docker_platform


def _update_stats(stats, result):
    """
        Update statistics based on evaluation result.

            Stats breakdown:
            - pass/fail/error: test results against gold_patch
            - aug_success: whether augmentation against the mutation succeeded (target mutation was detected by the tests)
    """
    # Count aug_success (mutation augmentation succeeded)
    if result.get("aug_success", False):
        stats["aug_success"] += 1

    # Count gold_patch test results
    gold_state = result.get("gold_state", {})

    if result.get("error") or "error" in gold_state:
        # gold patch execution error
        stats["error"] += 1
    elif not gold_state.get("fail"):
        # gold patch all tests passed (no failed tests)
        stats["pass"] += 1
    else:
        # gold patch has test failures
        stats["fail"] += 1


def _get_progress_description(stats):
    """Generate progress bar description."""
    return (
        f"Pass: {stats['pass']}, Fail: {stats['fail']}, "
        f"Error: {stats['error']}, AugSuccess: {stats['aug_success']}"
    )


def run_instances(args, final_results_save_file, valid_samples, **kwargs):
    """Run evaluations for all samples in parallel."""
    # Pre-process docker_platform and store in args for use by eval functions
    detected_platform = _detect_docker_platform(args)
    args._docker_platform = detected_platform if args.use_local_docker else None

    # Load existing results if available
    all_results = {}
    if os.path.exists(final_results_save_file):
        with open(final_results_save_file, "r") as f:
            all_results = json.load(f)

    stats = {
        "total": len(valid_samples),
        "pass": 0,
        "fail": 0,
        "error": 0,
        "aug_success": 0,
    }

    global_logger.info("Using eval_aug_model_test_patch_with_docker")

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.num_workers) as executor:
        # Simplify argument passing: only pass args and sample
        future_to_sample = {
            executor.submit(eval_aug_model_test_patch_with_docker, args, sample): sample
            for sample in valid_samples
        }

        pbar = tqdm(concurrent.futures.as_completed(future_to_sample), total=len(valid_samples))
        for future in pbar:
            sample = future_to_sample[future]
            instance_id = sample.get("instance_id", "unknown")

            try:
                result = future.result()
                all_results[instance_id] = result
                _update_stats(stats, result)
            except Exception as exc:
                global_logger.error(f"Evaluation for {instance_id} generated an exception: {exc}")
                all_results[instance_id] = {
                    "instance_id": instance_id,
                    "gold_state": {},
                    "error": str(exc),
                    "log_dir": None
                }
                stats["error"] += 1
                traceback.print_exc()

            pbar.set_description(_get_progress_description(stats))

    with open(final_results_save_file, "w") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)

    return all_results, stats


# ============================================================================
# Result Analysis and Saving
# ============================================================================

def _analyze_results(results_dict):
    """Analyze evaluation results and return statistics."""
    aug_success = []
    gold_fail = []
    aug_fail = []

    for key, value in results_dict.items():
        # Check if gold patch failed
        if value.get('gold_state', {}).get('fail'):
            gold_fail.append(key)

        # Check if aug succeeded
        if value.get('aug_success'):
            aug_success.append(key)
        else:
            aug_fail.append(key)

    return {
        'aug_success': aug_success,
        'gold_fail': gold_fail,
        'aug_fail': aug_fail,
    }


def _build_mutation_evaluation_info(value, log_dir, aug_meta, all_mutatation_patch):
    """Build mutation_evaluation_info dict for a single instance."""
    mutation_eval_info = {
        'outputs': str(log_dir.resolve()),
        'eval_stage': aug_meta.get('stage'),
        'iteration': aug_meta.get('iteration'),
    }

    gold_state = value.get('gold_state', {})

    # Handle error cases
    if 'error' in gold_state:
        mutation_eval_info['status'] = "incompleted"
        mutation_eval_info['error_info'] = gold_state['error']
        return mutation_eval_info, None

    mutation_eval_info['status'] = "completed"

    # Check if gold patch passed
    if gold_state.get('fail'):
        mutation_eval_info['pass_gold_patch_status'] = FAIL_STATUS
        return mutation_eval_info, None

    mutation_eval_info['pass_gold_patch_status'] = SUCCESS_STATUS

    # Build mutation_info
    mutation_info = {
        "run_success_equ": [],
        "run_fail_equ": [],
        "run_success_no_equ": [],
        "run_fail_no_equ": [],
        "run_error": [],
    }

    still_need_aug = {
        'run_fail_equ': False,
        'run_success_no_equ': False,
        'error': False,
    }

    # Categorize failed mutations
    for mutation_key in value.get('fail', []):
        mutation_patch = all_mutatation_patch.get(mutation_key, {})
        if mutation_patch.get('isequ'):
            still_need_aug['run_fail_equ'] = True
            mutation_info['run_fail_equ'].append(mutation_key)
        else:
            mutation_info['run_fail_no_equ'].append(mutation_key)

    # Categorize passed mutations
    for mutation_key in value.get('pass', []):
        mutation_patch = all_mutatation_patch.get(mutation_key, {})
        if not mutation_patch.get('isequ'):
            still_need_aug['run_success_no_equ'] = True
            mutation_info['run_success_no_equ'].append(mutation_key)
        else:
            mutation_info['run_success_equ'].append(mutation_key)

    # Categorize errored mutations
    for mutation_key in value.get('error', []):
        still_need_aug['error'] = True
        mutation_info['run_error'].append(mutation_key)

    mutation_eval_info['mutation_info'] = mutation_info
    return mutation_eval_info, still_need_aug


def analyze_and_save_results(args, results_dict, predictions_test):
    """Analyze results and optionally save back to predictions file."""
    # Analyze results
    analysis = _analyze_results(results_dict)

    global_logger.info(f"total: {len(results_dict)}")
    global_logger.info(f"gold_fail: {analysis['gold_fail']}")
    global_logger.info(f"gold_fail length: {len(analysis['gold_fail'])}")
    global_logger.info(f"aug_fail: {analysis['aug_fail']}")
    global_logger.info(f"aug_fail length: {len(analysis['aug_fail'])}")
    global_logger.info(f"aug_success length: {len(analysis['aug_success'])}")
    if results_dict:
        global_logger.info(f"aug_success_rate: {len(analysis['aug_success']) / len(results_dict):.4f}")

    if not args.rewrite_preds:
        global_logger.info("Skip saving results to predictions file, use --rewrite_preds to enable")
        return

    # Validate input paths
    predictions_test_path = args.input_path
    if not predictions_test_path.endswith(".json"):
        raise ValueError("Invalid predictions_test_path: must end with .json")

    new_predictions_test_path = predictions_test_path[:-5] + "_eval.json"
    log_dir = Path(RUN_EVALUATION_LOG_DIR) / SAVE_DIR / args.run_id

    # Initialize ResultManager for the output file
    result_manager = ResultManager(new_predictions_test_path)

    still_need_aug_keys = {
        'run_fail_equ': set(),
        'run_success_no_equ': set(),
        'error': set(),
    }

    for key, value in results_dict.items():
        if key not in predictions_test:
            global_logger.warning(f"Key {key} not found in predictions_test, skipping")
            continue

        aug_meta = predictions_test[key].get('aug_meta', {})
        all_mutatation_patch = predictions_test[key].get('all_mutatation_patch', {})

        mutation_eval_info, still_need = _build_mutation_evaluation_info(
            value, log_dir, aug_meta, all_mutatation_patch
        )

        # Use ResultManager to update instance with mutation_aug_evaluation_info
        result_manager.update_instance(key, {
            **predictions_test[key],
            'mutation_aug_evaluation_info': mutation_eval_info
        }, merge=False)

        # Collect keys that need further augmentation
        if still_need:
            if still_need['run_fail_equ']:
                still_need_aug_keys['run_fail_equ'].add(key)
            if still_need['run_success_no_equ']:
                still_need_aug_keys['run_success_no_equ'].add(key)
            if still_need['error']:
                still_need_aug_keys['error'].add(key)

    still_need_aug_length = len(still_need_aug_keys['run_fail_equ']) + len(still_need_aug_keys['run_success_no_equ'])
    global_logger.info(f"still_need_aug_length: {still_need_aug_length}")
    global_logger.info(f"still_need_aug: run_fail_equ={len(still_need_aug_keys['run_fail_equ'])}, "
                       f"run_success_no_equ={len(still_need_aug_keys['run_success_no_equ'])}, "
                       f"error={len(still_need_aug_keys['error'])}")

    global_logger.info(f"Saved evaluation results to {new_predictions_test_path}")


# ============================================================================
# Main Entry Point
# ============================================================================

def _setup_paths(run_id):
    """Setup logging and result file paths."""
    base_dir = Path(RUN_SWE_PLIS_DIR) / SAVE_DIR / run_id
    return {
        "log_file": base_dir / "global.log",
        "results_file": base_dir / "final_results.json",
    }


def _filter_samples(predictions_test, args):
    """Filter samples based on instance_ids or run_instance_file."""
    if args.instance_ids and args.run_instance_file:
        raise ValueError("Cannot specify both instance_ids and run_instance_file")

    if args.instance_ids:
        samples = [
            predictions_test[iid]
            for iid in predictions_test
            if iid in args.instance_ids
        ]
        global_logger.info(f"Filtered to {len(samples)} samples based on instance_ids")
    elif args.run_instance_file:
        run_instance = read_list_file(args.run_instance_file)
        samples = [
            predictions_test[iid]
            for iid in predictions_test
            if iid in run_instance
        ]
        global_logger.info(f"Filtered to {len(samples)} samples based on run_instance_file")
    else:
        samples = list(predictions_test.values())

    return samples


def main(args):
    """Main entry point for the evaluation script."""
    global global_logger

    # Setup paths and logging
    paths = _setup_paths(args.run_id)
    global_logger = setup_global_logger(paths["log_file"], add_stdout=True)

    # Load input data
    with open(args.input_path, "r") as f:
        predictions_test = json.load(f)
    global_logger.info(f"Loaded {len(predictions_test)} samples from {args.input_path}")

    # Setup output directory
    if args.output_dir is None:
        args.output_dir = RUN_EVALUATION_LOG_DIR / SAVE_DIR / args.run_id
    os.makedirs(args.output_dir, exist_ok=True)

    # Filter samples
    samples = _filter_samples(predictions_test, args)
    valid_samples = [s for s in samples if s.get("model_test_patch", "").strip()]

    global_logger.info(f"Found {len(valid_samples)} samples with valid model_test_patch")

    if not valid_samples:
        global_logger.info("No valid samples to evaluate")
        return

    global_logger.info("Waiting 5 seconds before starting evaluations...")
    time.sleep(5)

    # Run evaluations
    results_dict, stats = run_instances(args, paths["results_file"], valid_samples)

    # Analyze and save results
    analyze_and_save_results(args, results_dict, predictions_test)




# ============================================================================
# Argument Parsing
# ============================================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description="Evaluate model-generated test patches with mutation testing"
    )

    # Required arguments
    required = parser.add_argument_group("required arguments")
    required.add_argument("--input_path", required=True,
                          help="Path to JSON file containing instances with model_test_patch")
    required.add_argument("--dockerhub_username", required=True,
                          help="Docker Hub username for sweap-images repository")
    required.add_argument("--scripts_dir", required=True,
                          help="Directory containing local run scripts")
    required.add_argument("--run_id", required=True,
                          help="Unique identifier for this run")
    required.add_argument("--stage_name", required=True,
                          help="Stage name for filtering (e.g., 'no_equ_mutation_aug')")
    required.add_argument("--iteration", type=int, required=True,
                          help="Iteration number for filtering")

    # Docker configuration
    docker_group = parser.add_argument_group("docker configuration")
    docker_group.add_argument("--use_local_docker", action="store_true",
                              help="Run locally with Docker instead of Modal")
    docker_group.add_argument("--docker_platform", default=None,
                              help="Docker platform override (e.g., linux/amd64)")
    docker_group.add_argument("--block_network", action="store_true",
                              help="Block network access inside container")
    docker_group.add_argument("--mem_limit", default="8g",
                              help="Memory limit per container (default: 8g)")
    docker_group.add_argument("--timeout", type=int, default=480,
                              help="Timeout in seconds per container (default: 480)")

    # Execution options
    exec_group = parser.add_argument_group("execution options")
    exec_group.add_argument("--output_dir", default=None,
                            help="Directory to store evaluation outputs")
    exec_group.add_argument("--num_workers", type=int, default=50,
                            help="Number of parallel workers (default: 50)")
    exec_group.add_argument("--redo", type=str2bool, default=False,
                            help="Redo evaluations even if output exists")
    exec_group.add_argument("--rewrite_preds", type=str2bool, default=False,
                            help="Write evaluation results back to predictions file")

    # Instance filtering
    filter_group = parser.add_argument_group("instance filtering")
    filter_group.add_argument("-i", "--instance_ids", type=lambda s: s.split(","),
                              help="Instance IDs to run (comma separated)")
    filter_group.add_argument("--run_instance_file", type=str, default=None,
                              help="File containing instance IDs to run")
    filter_group.add_argument("--use_key", type=str, default="need_aug_keys",
                              help="Key in mutation_info dict for mutation keys")

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    main(args)
