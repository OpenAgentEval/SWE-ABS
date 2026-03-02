from pathlib import Path


RUN_EVALUATION_LOG_DIR = Path("logs/aug_test_center")
RUN_SWE_ABS_DIR = Path("swe_plus_res/")

# Docker constants
DOCKER_USER = "root"
DOCKER_WORKDIR = "/app"


SUCCESS_STATUS = "success"
FAIL_STATUS = "fail"


KEY_INSTANCE_ID = "instance_id"
KEY_MODEL = "model_name_or_path"
KEY_PREDICTION = "model_patch"
KEY_TESTPATCH = "test_patch"
KEY_GOLD_TESTPATCH = "gold_test_patch"
KEY_MODEL_TESTPATCH = "model_test_patch"
KEY_MUTATION_THINKING = "mutation_thinking"


PASS_INIT_TEST = "success"
FAIL_INIT_TEST = "fail"

SUCCESS_STATUS = "success"
FAIL_STATUS = "fail"