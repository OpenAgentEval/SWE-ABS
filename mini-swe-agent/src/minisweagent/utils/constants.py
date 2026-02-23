
# Constants - Logging
from enum import Enum

import typer


APPLY_PATCH_FAIL = ">>>>> Patch Apply Failed"
APPLY_PATCH_PASS = ">>>>> Applied Patch"
INSTALL_FAIL = ">>>>> Init Failed"
INSTALL_PASS = ">>>>> Init Succeeded"
INSTALL_TIMEOUT = ">>>>> Init Timed Out"
RESET_FAILED = ">>>>> Reset Failed"
TESTS_ERROR = ">>>>> Tests Errored"
TESTS_FAILED = ">>>>> Some Tests Failed"
TESTS_PASSED = ">>>>> All Tests Passed"
TESTS_TIMEOUT = ">>>>> Tests Timed Out"
START_TEST_OUTPUT = ">>>>> Start Test Output"
END_TEST_OUTPUT = ">>>>> End Test Output"

# Constants - Patch Status
SUCCESS_STATUS = "success"
FAIL_STATUS = "fail"
UNKNOW_STATUS = "unknown"



KEY_MODEL_TESTPATCH = "model_test_patch"


class FixType(Enum):
    Patch_GENERATION = 'patch_generation'
    HARD_CODE_FIX = 'Hard_Code_Fix'
    GOLD_FAIL_FIX = 'Gold_Fail_Fix'
    COVERAGE_FIX = 'Coverage_Fix'


class BenchMarkType(Enum):
    SWEBENCH = 'swebench'
    SWEBENCHPRO = 'swebenchpro'


SWEBENCH_DATASET_MAPPING = {
    "full": "princeton-nlp/SWE-Bench",
    "verified": "princeton-nlp/SWE-Bench_Verified",
    "lite": "princeton-nlp/SWE-Bench_Lite",
    "multimodal": "princeton-nlp/SWE-Bench_Multimodal",
    "multilingual": "swe-bench/SWE-Bench_Multilingual",
    "smith": "SWE-bench/SWE-smith",
    "_test": "klieret/swe-bench-dummy-test-dataset",
    
}
SWEBENCHPRO_DATASET_MAPPING={
    "pro":"ScaleAI/SWE-bench_Pro"
}

BENCHMARK_WORKDIR_MAPPING = {
    BenchMarkType.SWEBENCH: "/testbed",
    BenchMarkType.SWEBENCHPRO: "/app",
}


def validate_fix_type(value: str) -> FixType:
    try:
        # Convert string to FixType enum
        return FixType(value)
    except ValueError:
        # Build a friendly error message listing all valid fix_type enum values
        allowed_values = ', '.join([e.value for e in FixType])
        raise typer.BadParameter(f"fix_type 必须是以下之一: {allowed_values}")

def validate_benchmark_type(value: str) -> BenchMarkType:
    try:
        # Convert string to FixType enum
        return BenchMarkType(value)
    except ValueError:
        # Build a friendly error message listing all valid fix_type enum values
        allowed_values = ', '.join([e.value for e in BenchMarkType])
        raise typer.BadParameter(f"benchmark_type 必须是以下之一: {allowed_values}")

