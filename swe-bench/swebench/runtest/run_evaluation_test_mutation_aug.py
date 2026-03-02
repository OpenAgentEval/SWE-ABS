from __future__ import annotations
import argparse
from venv import logger

import docker
import json
import platform
import sys
import threading
import traceback
import copy
if platform.system() == "Linux":
    import resource
import os
from argparse import ArgumentParser, ArgumentDefaultsHelpFormatter
from pathlib import Path, PurePosixPath
from tqdm.auto import tqdm
import time
from swebench.harness.constants import (
    APPLY_PATCH_FAIL,
    APPLY_PATCH_PASS,
    DOCKER_PATCH,
    DOCKER_USER,
    DOCKER_WORKDIR,
    INSTANCE_IMAGE_BUILD_DIR,
    KEY_INSTANCE_ID,
    KEY_MODEL,
    KEY_PREDICTION,
    LOG_REPORT,
    LOG_INSTANCE,
    LOG_TEST_OUTPUT,
    RUN_EVALUATION_LOG_DIR,
    RUN_SWE_ABS_DIR,
    SUCCESS_STATUS,
    FAIL_STATUS,
    START_TEST_OUTPUT,
    END_TEST_OUTPUT,
    KEY_MODEL_TESTPATCH,
    UTF8,
    SWEbenchInstance
)
from swebench.harness.docker_utils import (
    clean_images,
    cleanup_container,
    copy_to_container,
    exec_run_with_timeout,
    list_images,
    remove_image,
    should_remove,
    copy_from_container
)
from swebench.harness.docker_build import (
    BuildImageError,
    build_container,
    build_env_images,
    close_logger,
    setup_logger,
    setup_global_logger,
    run_container
)
from swebench.harness.grading import get_eval_report, get_logs_eval
from swebench.harness.modal_eval import (
    run_instances_modal,
    validate_modal_credentials,
)
from swebench.harness.test_spec.test_spec import make_test_spec, TestSpec
from swebench.harness.utils import (
    EvaluationError,
    load_swebench_dataset,
    get_predictions_from_file,
    run_threadpool,
    str2bool,
    optional_str,
)

# ========== Add util path and import ResultManager ==========
UTIL_PATH = Path(__file__).resolve().parent.parent.parent.parent / "util"
if str(UTIL_PATH) not in sys.path:
    sys.path.insert(0, str(UTIL_PATH))

from sweabs_utils.preds_manager import ResultManager

global_logger = None
SAVE_DIR = "aug_mutation"

def analyze_eval_status_map(
    eval_status_map 
):
    fail = []
    for test_name, status in eval_status_map.items():
        if status.upper() not in ['OK',"PASSED","SKIPPED"]:
            fail.append(test_name)
    return fail


def run_instance(
    args,
    # data:dict | SWEbenchInstance,
    test_spec: TestSpec,
    prediction: dict,
    rm_image: bool,
    force_rebuild: bool,
    client: docker.DockerClient,
    run_id: str,
    timeout: int | None = None,
    rewrite_reports: bool = False,
    eval_gold_patch: bool = True,
) -> dict:
    """
    Run a single instance with the given prediction.

    Args:
    """
    # print("args:", args)
    # print("test_spec",test_spec)

    all_result = {}
    all_result['gold_state']={}
    all_result['pass'] = []
    all_result['fail'] = []
    all_result['error'] = []
    all_result['init_fail'] = []

    if test_spec.eval_script_list is None:
        all_result['test_none']=True
        return all_result

    instance_id = test_spec.instance_id
    log_dir = RUN_EVALUATION_LOG_DIR / SAVE_DIR / run_id  / instance_id

    if eval_gold_patch:
        each_log_dir = log_dir / "gold_patch"
        # print("eval_gold_patch:",eval_gold_patch)
        # model_name_or_path = pred.get(KEY_MODEL, "None").replace("/", "__")
        return_msg = run_container(
            args,
            test_spec, prediction[KEY_PREDICTION], rm_image, force_rebuild, client, run_id, each_log_dir, timeout
        )

        if return_msg.get("error_msg", None) is not None:
            all_result['gold_state']['error']=[('gold_patch', return_msg.get("error_msg", ""))]
            return all_result
        
        eval_status_map, _ = get_logs_eval(test_spec, return_msg["test_output_path"])

        if len(eval_status_map) == 0:
            all_result['gold_state']={
            'fail':["RUN TEST ERROR"],
            'eval_status_map':eval_status_map
            }

            # return all_result
        else:
            fail = analyze_eval_status_map(eval_status_map)
            all_result['gold_state']={
                'fail':fail,
                'eval_status_map':eval_status_map
            }

            # if fail:
            #     return all_result

    target_aug_key = prediction['aug_meta']['target_aug_key']
    need_aug_keys = prediction['mutation_info'][args.use_key]
    all_mutatation_patch = prediction['all_mutatation_patch']

    for mutation_key in tqdm(need_aug_keys, total=len(need_aug_keys), desc=instance_id):
        
        each_log_dir = log_dir / mutation_key
        mutatation_patch = all_mutatation_patch[mutation_key]
        model_key = mutation_key
        mutation_patch = mutatation_patch['model_patch']

        return_msg = run_container(
            args,
            test_spec, mutation_patch, rm_image, force_rebuild, client, run_id, each_log_dir, timeout
        )
        if return_msg.get("error_msg", None) is not None:
            all_result['error'].append((model_key, return_msg.get("error_msg", "")))
            continue

        eval_status_map, _ = get_logs_eval(test_spec, return_msg["test_output_path"])
        fail = analyze_eval_status_map(eval_status_map)

        all_result[model_key]=fail
        if fail:
            all_result['fail'].append(model_key)
            if model_key == target_aug_key:
                all_result['aug_success'] = True

        else:
            all_result['pass'].append(model_key)
            if model_key == target_aug_key:
                all_result['aug_success'] = False

    return all_result

def run_instances(
    args,
    predictions: dict,
    cache_level: str,
    clean: bool,
    force_rebuild: bool,
    max_workers: int,
    run_id: str,
    timeout: int,
    namespace: str | None = "swebench",
    instance_image_tag: str = "latest",
    env_image_tag: str = "latest",
    rewrite_reports: bool = False,
):
    """
    Run all instances for the given predictions in parallel.
    Args:
    """
    client = docker.from_env()
    test_specs = list(
        map(
            lambda instance: make_test_spec(
                args,
                instance,
                namespace=namespace,
                instance_image_tag=instance_image_tag,
                env_image_tag=env_image_tag,
            ),
            predictions.values(),
        )
    )

    # print number of existing instance images
    instance_image_ids = {x.instance_image_key for x in test_specs}
    existing_images = {
        tag
        for i in client.images.list(all=True)
        for tag in i.tags
        if tag in instance_image_ids
    }
    if not force_rebuild and len(existing_images):
        print(
            f"Found {len(existing_images)} existing instance images. Will reuse them."
        )

    # run instances in parallel


    payloads = []
    for test_spec in test_specs:
        payloads.append(
            (
                args,
                test_spec,
                predictions[test_spec.instance_id],
                should_remove(
                    test_spec.instance_image_key,
                    cache_level,
                    clean,
                    existing_images,
                ),
                force_rebuild,
                client,
                run_id,
                timeout,
                rewrite_reports,
            )
        )

    # run instances in parallel
    print(f"Running {len(predictions)} instances...")
    stats = {"✓": 0, "✖": 0}
    pbar = tqdm(total=len(payloads), desc="Evaluation", postfix=stats)
    results_dict = {}  # Store final results as {"instance_id": result}
    lock = threading.Lock()

    def run_evaluation_with_progress(*args):
        result = run_instance(*args)
        with lock:

            if len(result['fail'])>0:
                stats["✖"] += 1
            else:
                stats["✓"] += 1
            results_dict[args[1].instance_id] = result
            
            pbar.update()
        return result

    run_threadpool(run_evaluation_with_progress, payloads, max_workers)

    save_path = Path(RUN_SWE_ABS_DIR) / SAVE_DIR / run_id / "final_results.json"
    save_path.parent.mkdir(parents=True, exist_ok=True)

    final_results_manager = ResultManager(save_path)
    if not os.path.exists(save_path):
        final_results_manager.save(results_dict)
    else:
        # Update the original dict
        global_logger.info(f"Updating {save_path}")
        results_dict_old = final_results_manager.load()
        results_dict_old.update(results_dict)
        results_dict = results_dict_old
        final_results_manager.save(results_dict)

    print("All instances run.")
    return results_dict




def filter_run_instance(args,predictions_test):
    use_instance = {}
    for key,value in predictions_test.items():
        if 'aug_meta' in value:
            if 'dead_code' in value['aug_meta'] and value['aug_meta']['dead_code']:
                continue
            aug_meta = value['aug_meta']
            if aug_meta['stage'] != args.stage_name or aug_meta['iteration'] != args.iteration:
                continue
            use_instance[key]=value

    return use_instance




def main(
    instance_ids: list,
    predictions_test_path: str,

    max_workers: int,
    force_rebuild: bool,
    cache_level: str,
    clean: bool,
    open_file_limit: int,
    run_id: str,
    timeout: int,
    namespace: str | None,

    re_run_eval: bool = False,
    report_dir: str = ".",
    **kwargs,
):
    args = argparse.Namespace(**kwargs)

    global global_logger
    global_log_file = Path(RUN_SWE_ABS_DIR) / SAVE_DIR / run_id / "global.log"
    global_logger = setup_global_logger(global_log_file)
    """

    """

    if args.stage_name == "no_equ_mutation_aug":
        args.use_key = "run_success_no_equ"
    elif args.stage_name == "equ_mutation_aug":
        args.use_key = "run_fail_equ"


    # set open file limit
    assert len(run_id) > 0, "Run ID must be provided"
    if report_dir is not None:
        report_dir = Path(report_dir)
        if not report_dir.exists():
            report_dir.mkdir(parents=True)
    if force_rebuild and namespace is not None:
        raise ValueError("Cannot force rebuild and use a namespace at the same time.")

    if predictions_test_path.endswith(".json"):
        result_manager = ResultManager(predictions_test_path)
        predictions_test = result_manager.load()
    else:
        raise ValueError("Invalid predictions_test_path")

    predictions_test:dict
    # key:instance_id, value:prediction

    all_predictions_test = copy.deepcopy(predictions_test)


    if instance_ids:
        predictions_test = {k: v for k, v in predictions_test.items() if k in instance_ids}
    else:
        predictions_test = filter_run_instance(args,predictions_test)
        # predictions_test = {}
        
    # run instances locally
    if platform.system() == "Linux":
        resource.setrlimit(resource.RLIMIT_NOFILE, (open_file_limit, open_file_limit))


    if len(predictions_test) == 0:
        global_logger.info("No instances to run.")
        return


    save_file = Path(RUN_SWE_ABS_DIR) / SAVE_DIR / run_id / "final_results.json"
    if os.path.exists(save_file) and re_run_eval == False:
        save_file_manager = ResultManager(save_file)
        results_dict = save_file_manager.load()
    else:
        results_dict = run_instances(args,
                    predictions_test,
                    cache_level,
                    clean,
                    force_rebuild,
                    max_workers,
                    run_id,
                    timeout,
                    )


    aug_success = []
    gold_fail = []
    aug_fail = []


    
    # Parse results_dict
    for key,value in results_dict.items():
        if 'fail' in value['gold_state'] and value['gold_state']['fail']:
            gold_fail.append(key)

        if 'aug_success' in value and value['aug_success']:
            aug_success.append(key)
        else:
            aug_fail.append(key)
    global_logger.info(f"total:{len(results_dict)}")
    global_logger.info(f"gold_fail:{gold_fail}")
    global_logger.info(f"gold_fail length:{len(gold_fail)}")
    global_logger.info(f"aug_fail:{aug_fail}")
    global_logger.info(f"aug_fail length:{len(aug_fail)}")
    global_logger.info(f"aug_success length:{len(aug_success)}")
    global_logger.info(f"aug_success_rate:{len(aug_success)/len(results_dict)}")



    if not args.rewrite_preds:
        global_logger.info("Skip saving results to predictions_test_path, use --rewrite_preds to overwrite")
        return


    # Append _eval to the original .json filename

    if not predictions_test_path.endswith(".json"):
        raise ValueError("Invalid predictions_test_path")
    new_predictions_test_path = predictions_test_path[:-5] + "_eval.json"

    still_need_aug = {
        'run_fail_equ':set(),
        'run_success_no_equ':set(),
        'error':set(),
    }
    log_dir = RUN_EVALUATION_LOG_DIR / SAVE_DIR / run_id
    for key,value in results_dict.items():
        '''
        "mutation_aug_evaluation_info": {
            "status": "incompleted",
            "outputs": "",
            "error_info": "",
            "aug_success": true,
            "eval_stage":"no_equ_mutation_aug",
            "iteration": 0,
            'mutation_info':{
                "run_success_equ":[],
                "run_fail_equ":[],
                "run_success_no_equ":[],
                "run_fail_no_equ":[],
                "run_error":[],
            }
        }
        '''
        # Handle gold patch eval related logic
        mutation_evaluation_info = {}
        mutation_evaluation_info['outputs'] = str(log_dir.resolve())
        aug_meta = all_predictions_test[key]['aug_meta']

        mutation_evaluation_info['eval_stage'] = aug_meta['stage']
        mutation_evaluation_info['iteration'] = aug_meta['iteration']

        if 'error' in value['gold_state']:
            mutation_evaluation_info['status'] = "incompleted"
            mutation_evaluation_info['error_info'] = value['gold_state']['error']
            all_predictions_test[key]['mutation_evaluation_info'] = mutation_evaluation_info
            continue
        
        mutation_evaluation_info['status'] = "completed"

        if len(value['gold_state']['fail']) > 0:
            mutation_evaluation_info['pass_gold_patch_status'] = FAIL_STATUS
            continue
        else:
            mutation_evaluation_info['pass_gold_patch_status'] = SUCCESS_STATUS

        mutation_info = {
            "run_success_equ":[],
            "run_fail_equ":[],
            "run_success_no_equ":[],
            "run_fail_no_equ":[],
            "run_error":[],
        }

        all_mutatation_patch = all_predictions_test[key]['all_mutatation_patch']
        for mutation_key in value['fail']:
            mutation_patch = all_mutatation_patch[mutation_key]

            if mutation_patch['isequ']:
                still_need_aug["run_fail_equ"].add(key)
                mutation_info['run_fail_equ'].append(mutation_key)
            else:
                mutation_info['run_fail_no_equ'].append(mutation_key)
        for mutation_key in value['pass']:
            mutation_patch = all_mutatation_patch[mutation_key]

            if not mutation_patch['isequ']:
                still_need_aug["run_success_no_equ"].add(key)
                mutation_info['run_success_no_equ'].append(mutation_key)

            else:
                mutation_info['run_success_equ'].append(mutation_key)
        for mutation_key in value['error']:
            still_need_aug["error"].add(key)
            mutation_info['run_error'].append(mutation_key)

        mutation_evaluation_info['mutation_info'] = mutation_info
        all_predictions_test[key]['mutation_aug_evaluation_info'] = mutation_evaluation_info
        # all_predictions_test[key]['new_mutation_info'] = mutation_info
    
    still_need_aug_length = len(still_need_aug["run_fail_equ"]) + len(still_need_aug["run_success_no_equ"])
    global_logger.info(f"still_need_aug_length:{still_need_aug_length}")
    global_logger.info(f"still_need_aug:{still_need_aug}")

    new_preds_manager = ResultManager(new_predictions_test_path)
    new_preds_manager.save(all_predictions_test)



if __name__ == "__main__":
    parser = ArgumentParser(
        description="Run evaluation harness for the given dataset and predictions.",
        formatter_class=ArgumentDefaultsHelpFormatter,
    )
    # Common args
    parser.add_argument(
        "-d",
        "--dataset_name",
        default="SWE-bench/SWE-bench_Lite",
        type=str,
        help="Name of dataset or path to JSON file.",
    )
    parser.add_argument(
        "-s", "--split", type=str, default="test", help="Split of the dataset"
    )
    parser.add_argument(
        "-i",
        "--instance_ids",
        type=lambda s: s.split(","),
        help="Instance IDs to run (space separated)",
    )
    parser.add_argument(
        "--predictions_test_path",
        type=str,
        help="Path to predictions test file",
        required=True,
    )
    # Local execution args
    parser.add_argument(
        "--max_workers",
        type=int,
        default=4,
        help="Maximum number of workers (should be <= 75%% of CPU cores)",
    )
    parser.add_argument(
        "--open_file_limit", type=int, default=4096, help="Open file limit"
    )
    parser.add_argument(
        "-t",
        "--timeout",
        type=int,
        default=1_800,
        help="Timeout (in seconds) for running tests for each instance",
    )
    parser.add_argument(
        "--force_rebuild",
        type=str2bool,
        default=False,
        help="Force rebuild of all images",
    )
    parser.add_argument(
        "--cache_level",
        type=str,
        choices=["none", "base", "env", "instance"],
        help="Cache level - remove images above this level",
        default="env",
    )
    # if clean is true then we remove all images that are above the cache level
    # if clean is false, we only remove images above the cache level if they don't already exist
    parser.add_argument(
        "--clean", type=str2bool, default=False, help="Clean images above cache level"
    )
    parser.add_argument(
        "-id", "--run_id", type=str, required=True, help="Run ID - identifies the run"
    )
    parser.add_argument(
        "-n",
        "--namespace",
        type=optional_str,
        default="swebench",
        help='Namespace for images. (use "none" to use no namespace)',
    )
    parser.add_argument(
        "--rewrite_reports",
        type=str2bool,
        default=False,
        help="Doesn't run new instances, only writes reports for instances with existing test outputs",
    )
    parser.add_argument(
        "--report_dir", type=str, default=".", help="Directory to write reports to"
    )


    parser.add_argument(
        "--stage_name",
        type=str,
        required=True,
        default="no_equ_mutation_aug",
    )
    parser.add_argument(
        "--iteration",
        type=int,
        required=True,
        default=0,
    )

    parser.add_argument(
        "--rewrite_preds",
        type=str2bool,
        default=False,
    )
    parser.add_argument(
        "--re_run_eval",
        type=str2bool,
        default=False,
    )
    # Modal execution args
    parser.add_argument("--modal", type=str2bool, default=False, help="Run on Modal")

    args = parser.parse_args()
    args.use_coverage = False
    main(**vars(args))
