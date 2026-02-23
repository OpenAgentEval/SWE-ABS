"""
    parser_util for SWE-bench_Pro-os

    This file has been refactored:
    - Common functions have been extracted to SWE-PLUS/util/parser_utils.py
    - Only Pro-os-specific functions are retained here

    Version: v0.2.0
    Last updated: 2026-02-14
"""

import sys
from pathlib import Path

# ========== Add util path and import common functions ==========
UTIL_PATH = Path(__file__).resolve().parent.parent.parent / "util"
if str(UTIL_PATH) not in sys.path:
    sys.path.insert(0, str(UTIL_PATH))

# Import common functions from util
from parser_utils import (
    # Basic utilities
    str2bool,
    read_list_file,

    # Constants
    LANGUAGE_TEST_EXTENSIONS,

    # Go test utilities
    extract_go_test_info,
    get_test_directives,

    # Diff/Patch parsing utilities
    get_apply_files,
    remove_conflicting_chunks,
)


# ========== SWE-bench_Pro-os specific functions ==========

def analyze_test_results(output):
    """
        Analyze test output and return failed tests.

            This is a Pro-os-specific function for analyzing test results.

            Args:
                output: The parsed test output containing test results

            Returns:
                tuple: (failed_tests list, eval_status_map dict)
    """
    if output is None:
        return ["RUN TEST ERROR - No output"], {}

    if "tests" not in output:
        return ["RUN TEST ERROR - No tests in output"], {}

    failed_tests = []
    eval_status_map = {}

    for test in output.get("tests", []):
        test_name = test.get("name", "unknown")
        test_status = test.get("status", "UNKNOWN")
        eval_status_map[test_name] = test_status

        if test_status not in ["PASSED", "SKIPPED"]:
            failed_tests.append(test_name)

    return failed_tests, eval_status_map
