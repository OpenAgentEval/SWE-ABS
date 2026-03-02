from __future__ import annotations
import argparse
from collections import defaultdict
from venv import logger

import docker
import json
import platform
import sys
import threading
import traceback
import copy

from swebench.harness.coverage_utils import compute_coverage
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
    RUN_SWE_PLIS_DIR,
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

from swebench.harness.test_spec.test_spec import make_test_spec, TestSpec
from swebench.harness.utils import (
    EvaluationError,
    load_swebench_dataset,
    get_predictions_from_file,
    run_threadpool,
    str2bool,
    optional_str,
    remove_conflicting_chunks
)
import re

# Import from sweabs_utils package
from sweabs_utils.preds_manager import ResultManager

global_logger = None

SAVE_DIR = "eval"


def get_error_info(aug_test_log_file:Path):
    content = aug_test_log_file.read_text()
    test_content = content.split(START_TEST_OUTPUT)[1].split(END_TEST_OUTPUT)[0]
    return test_content

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
    mutation_instances: dict | None = None,
) -> dict:
    """
    Run a single instance with the given prediction.

    Args:
    """
    # print("args:", args)
    # print("test_spec",test_spec)

    all_result = {}
    all_result['gold_state']={}
    all_result['fail'] = []
    all_result['error'] = []
    all_result['init_fail'] = []
    all_result['pass'] = []
    if test_spec.eval_script_list is None:
        all_result['test_none']=True
        return all_result


    instance_id = test_spec.instance_id
    log_dir = RUN_EVALUATION_LOG_DIR / SAVE_DIR / run_id  / instance_id

    # Ensure gold patch passes the model-generated new test_spec
    if args.eval_gold_patch:
        each_log_dir = log_dir / "gold_patch"
        # print("eval_gold_patch:",eval_gold_patch)
        # model_name_or_path = pred.get(KEY_MODEL, "None").replace("/", "__")
        return_msg = run_container(
            args,
            test_spec, prediction[KEY_PREDICTION], rm_image, force_rebuild, client, run_id, each_log_dir, timeout
        )
        all_result['log_dir'] = str(log_dir.resolve())
        if return_msg.get("error_msg", None) is not None:
            all_result['gold_state']['error']=[('gold_patch', return_msg.get("error_msg", ""))]
            return all_result
        
        eval_status_map, _ = get_logs_eval(test_spec, return_msg["test_output_path"])


        if len(eval_status_map) == 0:
            all_result['gold_state']={
            'fail':["RUN TEST ERROR"],
            'eval_status_map':eval_status_map
            }
        else:
            fail = analyze_eval_status_map(eval_status_map)
            all_result['gold_state']={
                'fail':fail,
                'eval_status_map':eval_status_map
            }
        
    # # run mutation
    if args.mutation_paths:
        for mutation_key , mutation_instance in mutation_instances.items():
            # See data structure reference at /mini-swe-agent/数据结构
            each_log_dir = log_dir / mutation_key
            model_patch = mutation_instance['model_patch']

            return_msg = run_container(
                args,
                test_spec, model_patch, rm_image, force_rebuild, client, run_id, each_log_dir, timeout
            )
            if return_msg.get("error_msg", None) is not None:
                all_result['error'].append((mutation_key, return_msg.get("error_msg", "")))
                continue

            eval_status_map, _ = get_logs_eval(test_spec, return_msg["test_output_path"])
            fail = analyze_eval_status_map(eval_status_map)

            all_result[mutation_key]=fail
            if fail:
                all_result['fail'].append(mutation_key)
            else:
                all_result['pass'].append(mutation_key)


    # Used to evaluate patch results from other agents
    if  hasattr(args, "all_vaild_model_path") and args.all_vaild_model_path:
        all_vaild_model_path = args.all_vaild_model_path
        for path in tqdm(all_vaild_model_path, total=len(all_vaild_model_path), desc=instance_id):

            path:Path
            use_path = path / "logs" / instance_id

            each_log_dir = log_dir / path.name

            report_file = use_path / 'report.json'
            patch_file = use_path / 'patch.diff'

            # If report.json or patch.diff does not exist
            if not report_file.exists() or not patch_file.exists():
                all_result['init_fail'].append(path.name)
                # global_logger.error(f"{path}: report.json or patch.diff not exists")
                continue

            with open(use_path / 'report.json', "r") as f:
                report = json.load(f)

            # If resolved is False in report.json
            is_resolved = False
            if "resolved" in report:
                is_resolved = report["resolved"]
            elif instance_id in report:
                is_resolved = report[instance_id]["resolved"]
            

            if is_resolved is False:
                all_result['init_fail'].append(path.name)
                # global_logger.error(f"{path}: resolved is False")
                continue

            with open(use_path / 'patch.diff', "r") as f:
                model_patch = f.read()

            # Remove conflicting parts between model_patch and model_test_patch
            model_test_patch = prediction[KEY_MODEL_TESTPATCH]

            # with open("SWE-ABS/swe-bench/tool_script/patch.diff", "w") as f:
            #     f.write(model_patch)

            model_patch = remove_conflicting_chunks(model_patch, model_test_patch)

            if not model_patch.endswith("\n"):
                model_patch += "\n"

            return_msg = run_container(
                args,
                test_spec, model_patch, rm_image, force_rebuild, client, run_id, each_log_dir, timeout
            )
            if return_msg.get("error_msg", None) is not None:
                all_result['error'].append((path.name, return_msg.get("error_msg", "")))
                continue

            eval_status_map, _ = get_logs_eval(test_spec, return_msg["test_output_path"])
            fail = analyze_eval_status_map(eval_status_map)

            all_result[path.name]=fail
            if fail:
                all_result['fail'].append(path.name)
            else:
                all_result['pass'].append(path.name)
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
    all_mutation_instances: dict | None = None,
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
                all_mutation_instances[test_spec.instance_id] if all_mutation_instances else None,
            )
        )

    # run instances in parallel
    print(f"Running {len(predictions)} instances...")
    stats = {"✓": 0, "✖": 0}
    pbar = tqdm(total=len(payloads), desc="Evaluation", postfix=stats)
    results_dict = {}  # Store final results as {"instance_id": result}
    updataed_results = {}
    lock = threading.Lock()

    save_path = Path(RUN_SWE_PLIS_DIR) / SAVE_DIR / run_id / "final_results.json"
    save_path.parent.mkdir(parents=True, exist_ok=True)

    if save_path.exists():
        with open(save_path, "r") as f:
            results_dict = json.load(f)

    def run_evaluation_with_progress(*args_eval):
        result = run_instance(*args_eval)
        with lock:
            # eval agent mode: changed from overwrite to conditional append
            # if args.all_vaild_model_path and os.path.exists(save_path) and not args.agent_re_eval:
            #     update_agent_res(results_dict,result, args_eval[1].instance_id)
            # else:
            results_dict[args_eval[1].instance_id] = result
            updataed_results[args_eval[1].instance_id] = result
            pbar.update()
        return result

    run_threadpool(run_evaluation_with_progress, payloads, max_workers)

    with open(save_path, "w") as f:
        json.dump(results_dict, f, indent=2)

    print("All instances run.")

    return updataed_results

def get_gold_fail_instance(aug_test_instances):
    instances = {}
    model_test_patch_none = 0
    for key in aug_test_instances:
        instance = aug_test_instances[key]
        # aug_test did not pass gold_patch
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
        # aug_test passed gold_patch
        if "model_test_patch" in instance and instance['model_test_patch']!="":
            if 'meta' in instance and instance['meta']['pass_gold_patch_status']==SUCCESS_STATUS:
                instances[key] = instance
            else:
                global_logger.error(f"model_test_patch no pass: {key}")
        else:
            global_logger.error(f"model_test_patch_none: {key}")

    return instances


def get_coverage_instance(aug_test_instances):
    instances = {}
    for key in aug_test_instances:
        instance = aug_test_instances[key]
        # aug_test passed coverage check
        if 'meta' in instance and instance['meta']['pass_gold_patch_status']==SUCCESS_STATUS:
            if instance['meta']['coverage_rate']<1.0:
                instances[key] = instance
        else:
            raise RuntimeError("Before running the coverage test, please judge the gold patch first.")
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
                # If init_test passed, check judge info next
                if value['evaluation_info']['pass_init_test_status'] == SUCCESS_STATUS:
                    if 'judge_info' in value:
                        if value['judge_info']['isrele'] is False:
                            continue
                    else:
                        raise RuntimeError(f"mutation_path {mutation_path}, instance {key}, mutation judge_info not found")

                    if mutation_key in use_instance[key]:
                        raise RuntimeError(f"Found the same mutation key: {mutation_key} in {key}")

                    # If from /res_equ_mutation/ but judged as non-equivalent, skip
                    if 'res_equ_mutation' in mutation_path:
                        if value['judge_info']['isvalid']:
                            continue
                    use_instance[key][mutation_key]=value
            else:
                raise RuntimeError(f"mutation_path {mutation_path}, instance {key}, before judge mutation with aug test, please run the init test first.")




    return dict(use_instance)


def update_agent_res(results_dict,result,instance_id):
    if instance_id in results_dict:
        results_dict[instance_id]['fail'].extend(result['fail'])
        results_dict[instance_id]['init_fail'].extend(result['init_fail'])
        results_dict[instance_id]['pass'].extend(result['pass'])
        results_dict[instance_id]['error'].extend(result['error'])
    else:
        results_dict[instance_id] = result





def filter_agent_exist(final_results_save_file, vaild_model_name):
    with open(final_results_save_file, "r") as f:
        results_dict = json.load(f)

    exist_keys = []
    vaild_model_name = set(vaild_model_name)
    first_value = next(iter(results_dict.values()))
    
    exist_keys.extend(first_value['fail'])
    exist_keys.extend(first_value['init_fail'])
    exist_keys.extend(first_value['pass'])

    if len(first_value['error'])>0:
        for (key,_) in first_value['error']:
            exist_keys.append(key)
    exist_keys = set(exist_keys)
    vaild_model_name = vaild_model_name - exist_keys

    return vaild_model_name
    



def main(
    instance_ids: list,
    predictions_test_path: str,
    vaild_model_name: list,
    vaild_model_path: str,

    max_workers: int,
    force_rebuild: bool,
    cache_level: str,
    clean: bool,
    open_file_limit: int,
    run_id: str,
    
    timeout: int,
    namespace: str | None,

    re_run_eval:bool = False,
    report_dir: str = ".",
    **kwargs,
):

    global SAVE_DIR
    global global_logger
    args = argparse.Namespace(**kwargs)
    if args.eval_gold_patch:
        SAVE_DIR = "eval_gold_patch"
    elif args.mutation_paths:
        SAVE_DIR = "eval_mutation"
    elif vaild_model_path:
        SAVE_DIR = "eval_agent"

    global_log_file = Path(RUN_SWE_PLIS_DIR) / SAVE_DIR / run_id / "global.log"
    global_logger = setup_global_logger(global_log_file)
    final_results_save_file = Path(RUN_SWE_PLIS_DIR) / SAVE_DIR / run_id / "final_results.json"
    
    """
    evaluate model gen test
    """
    # set open file limit
    assert len(run_id) > 0, "Run ID must be provided"
    if report_dir is not None:
        report_dir = Path(report_dir)
        if not report_dir.exists():
            report_dir.mkdir(parents=True)
    if force_rebuild and namespace is not None:
        raise ValueError("Cannot force rebuild and use a namespace at the same time.")

    if predictions_test_path.endswith(".json"):
        with open(predictions_test_path, "r") as f:
            all_predictions_test = json.load(f)
    else:
        raise ValueError("Invalid predictions_test_path")


    predictions_test = copy.deepcopy(all_predictions_test)
    predictions_test:dict
    # key:instance_id, value:prediction
    init_length  = len(predictions_test)


    if args.coverage_eval:
        assert args.eval_gold_patch, "coverage_eval must be used with eval_gold_patch"
        assert args.must_cover_line, "coverage_eval must be used with must_cover_line"
        assert args.use_coverage, "coverage_eval must be used with use_coverage"

    # Filter instances to run based on the current task
    if instance_ids:
        if instance_ids[0] == "all":
            global_logger.info(f"indicated run all instances,init_length:{init_length},indicated_length:{len(predictions_test)}")
        else:    
            predictions_test = {k: v for k, v in predictions_test.items() if k in instance_ids}
            global_logger.info(f"indicated instance_ids,init_length:{init_length},indicated_length:{len(predictions_test)}")
    elif args.eval_gold_patch:
        # In eval_gold_patch mode, filter out instances where gold_patch already succeeded
            # Filter instances with coverage_rate < 1
            if args.coverage_eval:
                predictions_test = get_coverage_instance(predictions_test)
            else:
                predictions_test = get_gold_fail_instance(predictions_test)
            global_logger.info(f"Run eval_gold_patch mode, init_length:{init_length},get_gold_fail_instance_length:{len(predictions_test)}")
            if len(predictions_test) == 0:
                return 
    else:
        # In all other cases, filter for instances where gold_patch succeeded
        predictions_test = get_gold_success_instance(predictions_test) 
        global_logger.info(f"Run eval_agent mode, vaild_model_path:{vaild_model_path},init_length:{init_length},get_gold_success_instance_length:{len(predictions_test)}")


    all_mutation_instance = None
    # Get instances where mutation ran successfully
    if args.mutation_paths:
        all_mutation_instance = get_vaild_mutation(args.mutation_paths)
        mutation_instance_keys = set(all_mutation_instance.keys())
        predictions_test = {k: v for k, v in predictions_test.items() if k in mutation_instance_keys}

        global_logger.info(f"mutation_paths:{args.mutation_paths}")
        global_logger.info(f"all_mutation_instance_length:{len(mutation_instance_keys)}")

    # Process patches generated by other agents
    if vaild_model_path:        
        if vaild_model_name:

            all_vaild_model_path = [Path(vaild_model_path,vaild_name) 
                        for vaild_name in vaild_model_name]
        else:
            # List all files/folders in the directory and convert to Path objects
            # all_vaild_model_path = list(Path(vaild_model_path).iterdir()) 
            raise RuntimeError(f"vaild_model_name must be provided with the vaild_model_path:{vaild_model_path}")

        global_logger.info(f"all_vaild_model_path_length:{len(all_vaild_model_path)}")
    else:
        all_vaild_model_path = None
    args.all_vaild_model_path = all_vaild_model_path


    # run instances locally
    if platform.system() == "Linux":
        resource.setrlimit(resource.RLIMIT_NOFILE, (open_file_limit, open_file_limit))

    # ! ============================= Execution Phase =================================

    

    if os.path.exists(final_results_save_file) and re_run_eval == False:
        with open(final_results_save_file, "r") as f:
            results_dict = json.load(f)
        global_logger.info("skip run instances, load results from final_results.json directly")
        global_logger.info(f"load results from {final_results_save_file}")

    else:
        results_dict = run_instances(args,
                    predictions_test,
                    cache_level,
                    clean,
                    force_rebuild,
                    max_workers,
                    run_id,
                    timeout,
                    all_mutation_instance
                    )

    dirname, filename = os.path.split(predictions_test_path)
    name, ext = os.path.splitext(filename)

    # ! ============================= Post-processing & Save Phase =================================

    if not args.rewrite_preds:
        if args.eval_gold_patch:
            gold_fail_list = []
            gold_error_list = []
            for instance_id,value in results_dict.items():
                if "gold_state" in value and "fail" in value['gold_state']:
                    if len(value['gold_state']['fail'])>0:
                        gold_fail_list.append(instance_id)
                        # Read error_info from log_dir
                elif "gold_state" in value and "error" in value['gold_state']:
                    gold_error_list.append(instance_id)
            global_logger.info(f"gold_fail_num: {len(gold_fail_list)}")
            global_logger.info(f"gold_fail_list: {gold_fail_list}")
            global_logger.info(f"gold_error_num: {len(gold_error_list)}")
            global_logger.info(f"gold_error_list: {gold_error_list}")

        global_logger.info("Skip saving results to predictions_test_path, use --rewrite_preds to overwrite")
        return

    # Mark init_fail for each aug_test
    if args.eval_gold_patch:

        # If use_coverage is set and must_cover_line file path is provided
        if args.use_coverage and args.must_cover_line:
            with open(args.must_cover_line) as f:
                modified_related_lines = json.load(f)

        # gold_fail_num = 0
        gold_fail_list = []
        gold_error_list = []
        no_cover_list = []

        # Initialize ResultManager
        result_manager = ResultManager(predictions_test_path)

        # todo: complex details inside results_dict should be processed before returning
        for instance_id,value in results_dict.items():
            if "gold_state" in value and "fail" in value['gold_state']:

                log_dir = RUN_EVALUATION_LOG_DIR / SAVE_DIR / run_id

                error_info = ""
                if len(value['gold_state']['fail'])>0:
                    pass_gold_patch_status = FAIL_STATUS
                    gold_fail_list.append(instance_id)
                    # Read error_info from log_dir
                    try:
                        error_info = get_error_info(log_dir/instance_id/"gold_patch"/"test_output.txt")
                    except:
                        error_info = "Parse Error"
                else:
                    pass_gold_patch_status = SUCCESS_STATUS

                if (args.use_coverage and args.must_cover_line \
                    and instance_id in modified_related_lines \
                    and pass_gold_patch_status == SUCCESS_STATUS):
                    log_file = log_dir / instance_id / "gold_patch" / "test_output.txt"
                    coverage_rate,uncovered_lines = compute_coverage(log_file,modified_related_lines[instance_id])
                    if coverage_rate<1.0:
                        no_cover_list.append(instance_id)
                else:
                    coverage_rate,uncovered_lines = 'unknow',{}

                evaluation_info = {
                    "status": "completed",
                    "pass_gold_patch_status": pass_gold_patch_status,
                    "outputs": str(log_dir.resolve()),
                    "error_info": error_info,
                    "coverage_rate": coverage_rate,
                    "uncovered_lines": uncovered_lines,
                }

                # Update instance data using ResultManager
                result_manager.update_instance_nested(instance_id, {
                    "meta.pass_gold_patch_status": pass_gold_patch_status,
                    "meta.coverage_rate": coverage_rate,
                    "meta.uncovered_lines": uncovered_lines,
                    "stage.-1.evaluation_info": evaluation_info,
                })
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
        # if os.path.exists(predictions_test_path_with_mutation):
        #     with open(predictions_test_path_with_mutation, "r") as f:
        #         all_predictions_test = json.load(f)

        need_aug_case = {
            'run_fail_equ':set(),
            'run_success_no_equ':set(),
        }

        for instance_id,value in results_dict.items():
            # Initialize if not present
            if 'mutation_info' not in all_predictions_test[instance_id]:
                mutation_info = {
                    "run_success_equ":[],
                    "run_fail_equ":[],
                    "run_success_no_equ":[],
                    "run_fail_no_equ":[],
                    "run_error":[],
                }
            else:
                mutation_info = all_predictions_test[instance_id]['mutation_info']


            all_mutatation_patch = {}
            for mutation_key in all_mutation_instance[instance_id]:
                mutation_instance = all_mutation_instance[instance_id][mutation_key]
                isequ = not mutation_instance['judge_info']['isvalid']
                mutation_path_dict = {
                    "mutation_key":mutation_key,
                    "model_patch": mutation_instance['model_patch'],
                    "mutation_thinking": mutation_instance['mutation_thinking'],
                    "outputs": mutation_instance['outputs'],
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
            del all_predictions_test[instance_id]['meta']
            del all_predictions_test[instance_id]['stage']
            all_predictions_test[instance_id]['mutation_info'] = mutation_info
            all_predictions_test[instance_id]['all_mutatation_patch'] = all_mutatation_patch


        with open(predictions_test_path_with_mutation,"w") as f:
            json.dump(all_predictions_test,f,ensure_ascii=False,indent=4)

        need_aug_case_length = len(need_aug_case['run_fail_equ'] | need_aug_case['run_success_no_equ'])
        global_logger.info(f"predictions_test_path_with_mutation save into:{predictions_test_path_with_mutation}")
        global_logger.info(f"need_aug_case_length:{need_aug_case_length},total nums:{len(results_dict)}")
        global_logger.info(f"need_aug_case:{need_aug_case}")



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

    parser.add_argument(
        "--mutation_paths",  
        type=lambda s: s.split(","),
        help="the model name generate patch e.g trae ",
        required=False,  # Changed to optional
        default=[],  # Default value is an empty list
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
        "--instance_image_tag", type=str, default="latest", help="Instance image tag"
    )
    parser.add_argument(
        "--env_image_tag", type=str, default="latest", help="Environment image tag"
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
        "--use_coverage",
        type=str2bool,
        default=False,
    )

    parser.add_argument(
        "--eval_mutation_aug",
        type=str2bool,
        default=False,
    )

    parser.add_argument(
        "--eval_gold_patch",
        type=str2bool,
        default=False,
    )

    parser.add_argument(
        "--rewrite_preds",
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
        "--coverage_eval",
        type=str2bool,
        default=False,
    )

    parser.add_argument(
        "--re_run_eval",
        type=str2bool,
        default=False,
    )

    parser.add_argument(
        "--agent_re_eval",
        type=str2bool,
        default=False,
    )

    # Modal execution args
    parser.add_argument("--modal", type=str2bool, default=False, help="Run on Modal")

    args = parser.parse_args()
    main(**vars(args))
