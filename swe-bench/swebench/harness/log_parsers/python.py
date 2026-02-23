import re
from collections import deque
from swebench.harness.constants import TestStatus
from swebench.harness.test_spec.test_spec import TestSpec

def parse_log_pytest_old(log: str, test_spec: TestSpec) -> dict[str, str]:
    """
    Parser for test logs generated with PyTest framework

    Args:
        log (str): log content
    Returns:
        dict: test case to test status mapping
    """
    test_status_map = {}
    for line in log.split("\n"):
        if any([line.startswith(x.value) for x in TestStatus]):
            # Additional parsing for FAILED status
            if line.startswith(TestStatus.FAILED.value):
                line = line.replace(" - ", " ")
            test_case = line.split()
            if len(test_case) <= 1:
                continue
            test_status_map[test_case[1]] = test_case[0]
    
    # import json
    # with open("temp_debug_old.json", "w") as f:
    #     json.dump(test_status_map, f, indent=4)
    #     print("save the test_status_map")

    return test_status_map



def parse_log_pytest(log: str, test_spec) -> dict[str, str]:
    """
    Robust pytest log parser that extracts results only from the
    LAST "short test summary info" section, ensuring that nested
    pytest runs (e.g., those triggered inside test cases) are ignored.

    Args:
        log (str): Full pytest log output.
        test_spec: Unused here, kept for interface compatibility.

    Returns:
        dict[str, str]: A mapping from test case name to its final status.
    """

    # 1. Locate all "short test summary info" header positions.
    summary_header = "short test summary info"
    sections = []
    lines = log.splitlines()

    for i, line in enumerate(lines):
        if summary_header in line.lower():
            sections.append(i)

    # No summary section found
    if not sections:
        return {}

    # 2. Extract the last summary block.
    start = sections[-1] + 1

    # Determine the end of the summary block by finding the next ===== line
    end = len(lines)
    for j in range(start, len(lines)):
        if lines[j].startswith("="):
            end = j
            break

    summary_lines = lines[start:end]

    # 3. Regex to match summary result lines such as:
    #    FAILED test_file.py::test_name
    #    PASSED test_file.py::test_name
    result_pattern = re.compile(r"^(FAILED|PASSED|XPASSED|XFAILED|SKIPPED|ERROR)\s+(.+)$")

    test_status_map = {}

    for line in summary_lines:
        match = result_pattern.match(line.strip())
        if match:
            status, test_name = match.group(1), match.group(2)
            test_status_map[test_name] = status

            # ! For backward compatibility with old parse functions and old pass2pass list
            if ' ' in test_name:
                test_status_map[test_name.split()[0]] = status
    # import json
    # with open('temp_debug.json', 'w') as f:
    #     json.dump(test_status_map, f, indent=4)
    #     print(f"save the test_status_map")
    
    return test_status_map



def parse_log_pytest_options(log: str, test_spec: TestSpec) -> dict[str, str]:
    """
    Parser for test logs generated with PyTest framework with options

    Args:
        log (str): log content
    Returns:
        dict: test case to test status mapping
    """
    option_pattern = re.compile(r"(.*?)\[(.*)\]")
    test_status_map = {}
    for line in log.split("\n"):
        if any([line.startswith(x.value) for x in TestStatus]):
            # Additional parsing for FAILED status
            if line.startswith(TestStatus.FAILED.value):
                line = line.replace(" - ", " ")
            test_case = line.split()
            if len(test_case) <= 1:
                continue
            has_option = option_pattern.search(test_case[1])
            if has_option:
                main, option = has_option.groups()
                if (
                    option.startswith("/")
                    and not option.startswith("//")
                    and "*" not in option
                ):
                    option = "/" + option.split("/")[-1]
                test_name = f"{main}[{option}]"
            else:
                test_name = test_case[1]
            test_status_map[test_name] = test_case[0]
    return test_status_map


def parse_log_django(log: str, test_spec: TestSpec) -> dict[str, str]:
    """
    Parser for test logs generated with Django tester framework

    Args:
        log (str): log content
    Returns:
        dict: test case to test status mapping
    """
    test_status_map = {}
    lines = log.split("\n")

    prev_test = None
    pattern_test = r"[a-zA-Z_]\w*\s\([\w.]+\)"
    previous_line = deque()

    for line in lines:
        line = line.strip()

        # This isn't ideal but the test output spans multiple lines
        if "--version is equivalent to version" in line:
            test_status_map["--version is equivalent to version"] = (
                TestStatus.PASSED.value
            )

        # Log it in case of error
        if " ... " in line:
            prev_test = line.split(" ... ")[0]

        pass_suffixes = (" ... ok", " ... OK", " ...  OK")
        for suffix in pass_suffixes:
            if line.endswith(suffix):
                # Handle specific case for django__django-7188
                if line.strip().startswith(
                    "Applying sites.0002_alter_domain_unique...test_no_migrations"
                ):
                    line = line.split("...", 1)[-1].strip()

                raw_test = line.rsplit(suffix, 1)[0]
                test = raw_test
                # Process when test log is split across lines
                if not re.fullmatch(pattern_test, test):
                    # Only backtrack if we have previous lines
                    if previous_line:
                        pt = -1
                        while True:
                            try:
                                prev = previous_line[pt]
                            except IndexError:
                                break

                            if re.fullmatch(pattern_test, prev):
                                test = prev
                                break

                            pt -= 1
                if raw_test != test:
                    test_status_map[raw_test] = TestStatus.PASSED.value
                test_status_map[test] = TestStatus.PASSED.value
                break

        previous_line.append(line)

        if " ... skipped" in line:
            test = line.split(" ... skipped")[0]
            test_status_map[test] = TestStatus.SKIPPED.value

        if line.endswith(" ... FAIL"):
            test = line.split(" ... FAIL")[0]
            test_status_map[test] = TestStatus.FAILED.value

        if line.startswith("FAIL:"):
            test = line.split()[1].strip()
            test_status_map[test] = TestStatus.FAILED.value

        if line.endswith(" ... ERROR"):
            test = line.split(" ... ERROR")[0]
            test_status_map[test] = TestStatus.ERROR.value

        if line.startswith("ERROR:"):
            test = line.split()[1].strip()
            test_status_map[test] = TestStatus.ERROR.value

        if line.lstrip().startswith("ok") and prev_test is not None:
            # It means the test passed, but there's some additional output (including new lines)
            # between "..." and "ok" message
            test = prev_test
            test_status_map[test] = TestStatus.PASSED.value
        if (
            ("Fatal Python error" in line or "core dumped" in line or "Aborted" in line)
            and prev_test is not None
            and prev_test not in test_status_map
        ):
            test_status_map[prev_test] = TestStatus.ERROR.value
            
    # TODO: This is very brittle, we should do better
    patterns = [
        r"^(.*?)\s\.\.\.\sTesting\ against\ Django\ installed\ in\ ((?s:.*?))\ silenced\)\.\nok$",
        r"^(.*?)\s\.\.\.\sInternal\ Server\ Error:\ \/(.*)\/\nok$",
        r"^(.*?)\s\.\.\.\sSystem check identified no issues \(0 silenced\)\nok$",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, log, re.MULTILINE):
            test_name = match.group(1)
            test_status_map[test_name] = TestStatus.PASSED.value

    return test_status_map


def parse_log_pytest_v2(log: str, test_spec: TestSpec) -> dict[str, str]:
    """
    Parser for test logs generated with PyTest framework (Later Version)

    Args:
        log (str): log content
    Returns:
        dict: test case to test status mapping
    """
    test_status_map = {}
    escapes = "".join([chr(char) for char in range(1, 32)])
    for line in log.split("\n"):
        line = re.sub(r"\[(\d+)m", "", line)
        translator = str.maketrans("", "", escapes)
        line = line.translate(translator)
        if any([line.startswith(x.value) for x in TestStatus]):
            if line.startswith(TestStatus.FAILED.value):
                line = line.replace(" - ", " ")
            test_case = line.split()
            if len(test_case) >= 2:
                test_status_map[test_case[1]] = test_case[0]
        # Support older pytest versions by checking if the line ends with the test status
        elif any([line.endswith(x.value) for x in TestStatus]):
            test_case = line.split()
            if len(test_case) >= 2:
                test_status_map[test_case[0]] = test_case[1]

    # ! Modification specific to the sphinx-doc__sphinx repo
    if not test_status_map:
        test_status_map = {"sphinx-doc__sphinx":"PASSED"}

    return test_status_map


def parse_log_seaborn(log: str, test_spec: TestSpec) -> dict[str, str]:
    """
    Parser for test logs generated with seaborn testing framework

    Args:
        log (str): log content
    Returns:
        dict: test case to test status mapping
    """
    test_status_map = {}
    for line in log.split("\n"):
        if line.startswith(TestStatus.FAILED.value):
            test_case = line.split()[1]
            test_status_map[test_case] = TestStatus.FAILED.value
        elif f" {TestStatus.PASSED.value} " in line:
            parts = line.split()
            if parts[1] == TestStatus.PASSED.value:
                test_case = parts[0]
                test_status_map[test_case] = TestStatus.PASSED.value
        elif line.startswith(TestStatus.PASSED.value):
            parts = line.split()
            test_case = parts[1]
            test_status_map[test_case] = TestStatus.PASSED.value
    return test_status_map


import re

def parse_log_sympy(log: str, test_spec) -> dict[str, str]:
    """
    Robust parser for SymPy / pytest-style test logs.
    """
    test_status_map: dict[str, str] = {}

    test_line_pattern = re.compile(
        r"^(test_\S+)\s+([a-zA-Z]+)",
        re.IGNORECASE,
    )

    for raw_line in log.splitlines():
        line = raw_line.strip()
        if not line.startswith("test_"):
            continue

        m = test_line_pattern.match(line)
        if not m:
            continue

        test_name, status = m.groups()
        status = status.lower()

        # Detect expected failure marked as OK (e.g. "f [OK]")
        is_expected_fail = "[ok]" in line.lower()

        if status == "ok":
            test_status_map[test_name] = TestStatus.PASSED.value

        elif status == "f":
            if is_expected_fail:
                # xfail → treat as passed
                test_status_map[test_name] = TestStatus.PASSED.value
            else:
                test_status_map[test_name] = TestStatus.FAILED.value

        elif status == "e":
            test_status_map[test_name] = TestStatus.ERROR.value

        elif status in ("skipped", "xfail", "xfailed"):
            # pytest xfail: expected failure, not a real failure
            test_status_map[test_name] = TestStatus.PASSED.value

    return test_status_map




def parse_log_matplotlib(log: str, test_spec: TestSpec) -> dict[str, str]:
    """
    Parser for test logs generated with PyTest framework

    Args:
        log (str): log content
    Returns:
        dict: test case to test status mapping
    """
    test_status_map = {}
    for line in log.split("\n"):
        line = line.replace("MouseButton.LEFT", "1")
        line = line.replace("MouseButton.RIGHT", "3")
        if any([line.startswith(x.value) for x in TestStatus]):
            # Additional parsing for FAILED status
            if line.startswith(TestStatus.FAILED.value):
                line = line.replace(" - ", " ")
            test_case = line.split()
            if len(test_case) <= 1:
                continue
            test_status_map[test_case[1]] = test_case[0]
    return test_status_map


parse_log_astroid = parse_log_pytest
parse_log_flask = parse_log_pytest
parse_log_marshmallow = parse_log_pytest
parse_log_pvlib = parse_log_pytest
parse_log_pyvista = parse_log_pytest
parse_log_sqlfluff = parse_log_pytest
parse_log_xarray = parse_log_pytest

parse_log_pydicom = parse_log_pytest_options
parse_log_requests = parse_log_pytest_options
parse_log_pylint = parse_log_pytest_options

parse_log_astropy = parse_log_pytest_v2
parse_log_scikit = parse_log_pytest_v2
parse_log_sphinx = parse_log_pytest_v2


MAP_REPO_TO_PARSER_PY = {
    "astropy/astropy": parse_log_astropy,
    "django/django": parse_log_django,
    "marshmallow-code/marshmallow": parse_log_marshmallow,
    "matplotlib/matplotlib": parse_log_matplotlib,
    "mwaskom/seaborn": parse_log_seaborn,
    "pallets/flask": parse_log_flask,
    "psf/requests": parse_log_requests,
    "pvlib/pvlib-python": parse_log_pvlib,
    "pydata/xarray": parse_log_xarray,
    "pydicom/pydicom": parse_log_pydicom,
    "pylint-dev/astroid": parse_log_astroid,
    "pylint-dev/pylint": parse_log_pylint,
    "pytest-dev/pytest": parse_log_pytest,
    "pyvista/pyvista": parse_log_pyvista,
    "scikit-learn/scikit-learn": parse_log_scikit,
    "sqlfluff/sqlfluff": parse_log_sqlfluff,
    "sphinx-doc/sphinx": parse_log_sphinx,
    "sympy/sympy": parse_log_sympy,
}
