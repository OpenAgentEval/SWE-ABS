"""
    Unified test result parser

    Provides a unified parse_test_output function for each repository.
    Usage:
        from unified_parsers import get_parser_for_repo

        parser = get_parser_for_repo("ansible__ansible")
        results = parser(stdout_content, stderr_content)
"""

import re
import json
from typing import List, Callable, Optional
from enum import Enum
import dataclasses


class TestStatus(Enum):
    """The test status enum."""
    PASSED = 1
    FAILED = 2
    SKIPPED = 3
    ERROR = 4


@dataclasses.dataclass
class TestResult:
    """The test result dataclass."""
    name: str
    status: TestStatus


# =============================================================================
# ansible__ansible - Python pytest (with pytest-xdist)
# =============================================================================
def parse_ansible(stdout_content: str, stderr_content: str) -> List[TestResult]:
    """
        Parse pytest test output for ansible.
            Supported formats:
            - pytest-xdist: [gw0] [ 10%] PASSED test/units/xxx.py::TestClass::test_method
            - plain pytest: test/units/xxx.py::TestClass::test_method PASSED
            - XPASS/XFAIL statuses
    """
    results = []
    seen = set()
    combined_content = stdout_content + "\n" + stderr_content

    # Strip ANSI escape codes
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    combined_content = ansi_escape.sub('', combined_content)

    # Pattern 1: pytest-xdist format
    xdist_pattern = r'\[gw\d+\]\s*\[\s*\d+%\]\s+(PASSED|FAILED|SKIPPED|ERROR|XFAIL|XPASS)\s+(test/[^\s]+)'
    for match in re.finditer(xdist_pattern, combined_content):
        status_str, test_name = match.group(1), match.group(2)
        if test_name not in seen:
            seen.add(test_name)
            status = _map_pytest_status(status_str)
            results.append(TestResult(name=test_name, status=status))

    # Pattern 2: standard pytest format
    if not results:
        pytest_pattern = r'(test/[^\s]+\.py::[^\s]+)\s+(PASSED|FAILED|SKIPPED|ERROR|XFAIL|XPASS)'
        for match in re.finditer(pytest_pattern, combined_content):
            test_name, status_str = match.group(1), match.group(2)
            if test_name not in seen:
                seen.add(test_name)
                status = _map_pytest_status(status_str)
                results.append(TestResult(name=test_name, status=status))

    # Pattern 3: status appears first
    if not results:
        alt_pattern = r'(PASSED|FAILED|SKIPPED|ERROR|XFAIL|XPASS)\s+(test/[^\s]+\.py::[^\s]+)'
        for match in re.finditer(alt_pattern, combined_content):
            status_str, test_name = match.group(1), match.group(2)
            if test_name not in seen:
                seen.add(test_name)
                status = _map_pytest_status(status_str)
                results.append(TestResult(name=test_name, status=status))

    return results


def _map_pytest_status(status_str: str) -> TestStatus:
    """Map a pytest status string to TestStatus"""
    if status_str in ('PASSED', 'XPASS'):
        return TestStatus.PASSED
    elif status_str == 'FAILED':
        return TestStatus.FAILED
    elif status_str in ('SKIPPED', 'XFAIL'):
        return TestStatus.SKIPPED
    else:
        return TestStatus.ERROR


# =============================================================================
# qutebrowser__qutebrowser - Python pytest
# =============================================================================
def parse_qutebrowser(stdout_content: str, stderr_content: str) -> List[TestResult]:
    """
        Parse pytest test output for qutebrowser.
            Note: test parameter names may contain spaces, e.g. test_set[c.colors.hints.bg = "red"]
            Uses lookahead assertions to correctly match full test names.
    """
    results = []
    seen = set()
    combined_content = stdout_content + "\n" + stderr_content

    # Strip non-printable characters, preserve newlines
    combined_content = ''.join(char for char in combined_content if char.isprintable() or char == '\n')

    # Use lookahead assertion to match test names (which may contain spaces)
    # Format: tests/xxx.py::TestClass::test_method[params] PASSED [ xx%]
    pattern = r'^(tests/.*?)(?=\s+(PASSED|FAILED|SKIPPED|ERROR|XFAIL|XPASS))'

    for match in re.finditer(pattern, combined_content, re.MULTILINE):
        test_name = match.group(1).strip()
        # Find the status
        rest_of_line = combined_content[match.end():]
        status_match = re.match(r'\s+(PASSED|FAILED|SKIPPED|ERROR|XFAIL|XPASS)', rest_of_line)
        if status_match:
            status_str = status_match.group(1)
            if test_name not in seen:
                seen.add(test_name)
                # qutebrowser special case: XFAIL counts as PASSED, XPASS counts as FAILED
                if status_str in ('PASSED', 'XFAIL'):
                    status = TestStatus.PASSED
                elif status_str in ('FAILED', 'XPASS'):
                    status = TestStatus.FAILED
                elif status_str == 'SKIPPED':
                    status = TestStatus.SKIPPED
                else:
                    status = TestStatus.ERROR
                results.append(TestResult(name=test_name, status=status))

    return results


# =============================================================================
# internetarchive__openlibrary - Python pytest
# =============================================================================
def parse_openlibrary(stdout_content: str, stderr_content: str) -> List[TestResult]:
    """Parse pytest test output for openlibrary"""
    results = []
    seen = set()
    combined_content = stdout_content + "\n" + stderr_content

    patterns = [
        (r'([^\s]+\.py::[^\s]+)\s+(PASSED|FAILED|SKIPPED|ERROR)', False),
        (r'(PASSED|FAILED|SKIPPED|ERROR)\s+([^\s]+\.py::[^\s]+)', True),
    ]

    for pattern, status_first in patterns:
        for match in re.finditer(pattern, combined_content, re.MULTILINE):
            if status_first:
                status_str, test_name = match.group(1), match.group(2)
            else:
                test_name, status_str = match.group(1), match.group(2)

            if test_name not in seen:
                seen.add(test_name)
                status = {'PASSED': TestStatus.PASSED, 'FAILED': TestStatus.FAILED,
                         'SKIPPED': TestStatus.SKIPPED, 'ERROR': TestStatus.ERROR}.get(status_str, TestStatus.ERROR)
                results.append(TestResult(name=test_name, status=status))

    return results


# =============================================================================
# Generic Go project parser (navidrome, teleport, vuls)
# =============================================================================
def parse_go_test(stdout_content: str, stderr_content: str) -> List[TestResult]:
    """
        Parse Go test output.
            Supports: standard format, JSON format, panic detection, compile error detection.
    """
    results = []
    seen = set()
    combined_content = stdout_content + "\n" + stderr_content

    # First detect compilation errors (in stderr)
    # Format: # package_path
    #       file.go:line:col: error message
    compile_error_pattern = re.compile(r'^#\s+(\S+)\s*$', re.MULTILINE)
    go_error_pattern = re.compile(r'^([^\s:]+\.go):(\d+):(\d+):\s*(.+)$', re.MULTILINE)

    for pkg_match in compile_error_pattern.finditer(stderr_content):
        pkg_name = pkg_match.group(1)
        # Look for specific error messages after this package name
        start_pos = pkg_match.end()
        for err_match in go_error_pattern.finditer(stderr_content[start_pos:start_pos+500]):
            error_file = err_match.group(1)
            error_line = err_match.group(2)
            # Use file basename only to avoid duplicating the package name
            file_basename = error_file.split('/')[-1] if '/' in error_file else error_file
            error_name = f"COMPILE_ERROR:{pkg_name}:{file_basename}:{error_line}"
            if error_name not in seen:
                seen.add(error_name)
                results.append(TestResult(name=error_name, status=TestStatus.ERROR))
            break  # Take only the first error

    # Detect [setup failed] (in stdout)
    # Format: FAIL	package_path [setup failed]
    setup_failed_pattern = re.compile(r'^FAIL\s+(\S+)\s+\[setup failed\]', re.MULTILINE)
    for match in setup_failed_pattern.finditer(stdout_content):
        pkg_name = match.group(1)
        error_name = f"SETUP_FAILED:{pkg_name}"
        if error_name not in seen:
            seen.add(error_name)
            results.append(TestResult(name=error_name, status=TestStatus.ERROR))

    # Try JSON format (go test -json)
    for line in stdout_content.strip().split('\n'):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            if 'Test' in data and 'Action' in data:
                test_name, action = data['Test'], data['Action']
                if test_name not in seen and action in ('pass', 'fail', 'skip'):
                    seen.add(test_name)
                    status = {'pass': TestStatus.PASSED, 'fail': TestStatus.FAILED,
                             'skip': TestStatus.SKIPPED}[action]
                    results.append(TestResult(name=test_name, status=status))
        except json.JSONDecodeError:
            continue

    if results:
        return results

    # Standard format: --- PASS: TestName (0.00s)
    test_pattern = re.compile(r'^\s*--- (PASS|FAIL|SKIP): ([^\s]+)', re.MULTILINE)
    for match in test_pattern.finditer(combined_content):
        status_str, test_name = match.groups()
        if test_name not in seen:
            seen.add(test_name)
            status = {'PASS': TestStatus.PASSED, 'FAIL': TestStatus.FAILED,
                     'SKIP': TestStatus.SKIPPED}.get(status_str, TestStatus.ERROR)
            results.append(TestResult(name=test_name, status=status))

    # Detect panic/fatal error
    last_run = None
    run_pattern = re.compile(r'^=== RUN\s+([^\s]+)')
    error_pattern = re.compile(r'(panic:|fatal error|SIGSEGV|SIGILL|SIGFPE|SIGBUS|build failed)', re.IGNORECASE)

    for line in combined_content.split('\n'):
        run_match = run_pattern.search(line)
        if run_match:
            last_run = run_match.group(1)
        if error_pattern.search(line):
            name = last_run or "BUILD_OR_RUNTIME_ERROR"
            if name not in seen:
                seen.add(name)
                results.append(TestResult(name=name, status=TestStatus.ERROR))

    return results


# =============================================================================
# flipt-io__flipt - Go test (parses --- PASS/FAIL/SKIP: format, no JSON parsing)
# =============================================================================
def parse_flipt(stdout_content: str, stderr_content: str) -> List[TestResult]:
    """
        Parse Go test output for flipt.
            Parses the --- PASS/FAIL/SKIP: format (does not parse JSON format).
            Also detects compile errors and setup failures.
    """
    results = []
    seen = set()

    # First detect compilation errors (in stderr)
    # Format: # package_path
    #       file.go:line:col: error message
    compile_error_pattern = re.compile(r'^#\s+(\S+)\s*$', re.MULTILINE)
    go_error_pattern = re.compile(r'^([^\s:]+\.go):(\d+):(\d+):\s*(.+)$', re.MULTILINE)

    compile_errors = []
    for pkg_match in compile_error_pattern.finditer(stderr_content):
        pkg_name = pkg_match.group(1)
        # Look for specific error messages after this package name
        start_pos = pkg_match.end()
        for err_match in go_error_pattern.finditer(stderr_content[start_pos:start_pos+500]):
            error_file = err_match.group(1)
            error_line = err_match.group(2)
            error_msg = err_match.group(4)
            # Use file basename only to avoid duplicating the package name
            file_basename = error_file.split('/')[-1] if '/' in error_file else error_file
            error_name = f"COMPILE_ERROR:{pkg_name}:{file_basename}:{error_line}"
            if error_name not in seen:
                seen.add(error_name)
                compile_errors.append(TestResult(name=error_name, status=TestStatus.ERROR))
            break  # Take only the first error

    # Detect [setup failed] (in stdout)
    # Format: FAIL	package_path [setup failed]
    setup_failed_pattern = re.compile(r'^FAIL\s+(\S+)\s+\[setup failed\]', re.MULTILINE)
    for match in setup_failed_pattern.finditer(stdout_content):
        pkg_name = match.group(1)
        error_name = f"SETUP_FAILED:{pkg_name}"
        if error_name not in seen:
            seen.add(error_name)
            compile_errors.append(TestResult(name=error_name, status=TestStatus.ERROR))

    # If compilation errors exist, add them to results
    results.extend(compile_errors)

    # Parse --- PASS/FAIL/SKIP: TestName (0.00s) format
    pattern = re.compile(r'^--- (PASS|FAIL|SKIP):\s*(\S+)\s*\(.*\)$', re.MULTILINE)
    for match in pattern.finditer(stdout_content):
        status_str, test_name = match.group(1), match.group(2)
        if test_name not in seen:
            seen.add(test_name)
            status = {'PASS': TestStatus.PASSED, 'FAIL': TestStatus.FAILED,
                     'SKIP': TestStatus.SKIPPED}.get(status_str, TestStatus.ERROR)
            results.append(TestResult(name=test_name, status=status))

    return results


# =============================================================================
# element-hq__element-web - JavaScript Jest (supports JSON and plain format)
# =============================================================================
def parse_element_web(stdout_content: str, stderr_content: str) -> List[TestResult]:
    """
        Parse Jest test output.
            Supports two formats:
            1. JSON format (jest --json)
            2. Plain format (✓ ✕ ○)
    """
    results = []

    # Try parsing JSON format (jest --json)
    # JSON may be embedded in other output; need to locate the complete JSON object
    start = stdout_content.find('{"numFailed')
    if start == -1:
        start = stdout_content.find('{"numPassedTestSuites')
    if start == -1:
        start = stdout_content.find('{"testResults')

    if start >= 0:
        # Find the matching }
        depth = 0
        end = start
        for i in range(start, len(stdout_content)):
            if stdout_content[i] == '{':
                depth += 1
            elif stdout_content[i] == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break

        json_str = stdout_content[start:end]
        try:
            data = json.loads(json_str)
            for test_result in data.get('testResults', []):
                file_path = test_result.get('name', '')
                for assertion in test_result.get('assertionResults', []):
                    ancestors = assertion.get('ancestorTitles', [])
                    title = assertion.get('title', '')
                    status_str = assertion.get('status', 'passed')

                    # Build test name: file | ancestors... | title
                    parts = [file_path] + ancestors + [title]
                    full_name = ' | '.join(p for p in parts if p)

                    if status_str == 'passed':
                        status = TestStatus.PASSED
                    elif status_str == 'failed':
                        status = TestStatus.FAILED
                    elif status_str in ('pending', 'skipped'):
                        status = TestStatus.SKIPPED
                    else:
                        status = TestStatus.ERROR

                    results.append(TestResult(name=full_name, status=status))
            if results:
                return results
        except json.JSONDecodeError:
            pass

    # Fall back to plain format (checkmark, cross, circle symbols)
    def clean_text(text):
        if isinstance(text, bytes):
            text = text.decode('utf-8', errors='replace')
        return ''.join(char for char in text if char.isprintable() or char == '\n')

    lines = clean_text(stdout_content).splitlines()
    current_file, current_suite = None, None

    for line in lines:
        line = line.strip()

        if line.startswith(("PASS", "FAIL")) and ' ' in line:
            current_file = line.split()[1] if len(line.split()) >= 2 else None
            current_suite = None
            continue

        # Test suite name
        if line and not any(line.startswith(c) for c in ['\u2713', '\u2714', '\u25cb', '\u270E', '\u2715', '\u2716', 'PASS', 'FAIL', 'Test Suites:', 'Tests:', 'Snapshots:', 'Time:']):
            current_suite = line
            continue

        # Parse test cases
        status = None
        test_case = None

        if line.startswith(('\u2713', '\u2714')):  # passed
            test_case = line.lstrip('\u2713\u2714 ').split('(')[0].strip()
            status = TestStatus.PASSED
        elif line.startswith(('\u25cb', '\u270E')):  # skipped
            test_case = line.lstrip('\u25cb\u270E ').split('(')[0].strip()
            status = TestStatus.SKIPPED
        elif line.startswith(('\u2715', '\u2716')):  # failed
            test_case = line.lstrip('\u2715\u2716 ').split('(')[0].strip()
            status = TestStatus.FAILED

        if test_case and status:
            parts = [p for p in [current_file, current_suite, test_case] if p]
            full_name = " | ".join(parts)
            results.append(TestResult(name=full_name, status=status))

    return results


# =============================================================================
# NodeBB__NodeBB - JavaScript Mocha (JSON output)
# =============================================================================
def parse_nodebb(stdout_content: str, stderr_content: str) -> List[TestResult]:
    """Parse Mocha JSON test output"""
    results = []
    seen = set()

    # Used to match the file:: pattern in fullTitle
    test_file_pattern = re.compile(r'(\S+)::')

    # Locate the position of "passes": [, then search backward for the enclosing JSON object
    for match in re.finditer(r'"passes"\s*:\s*\[', stdout_content):
        passes_pos = match.start()

        # Search backward for the nearest unmatched {
        depth = 0
        json_start = None
        for j in range(passes_pos - 1, -1, -1):
            c = stdout_content[j]
            if c == '}':
                depth += 1
            elif c == '{':
                if depth == 0:
                    json_start = j
                    break
                depth -= 1

        if json_start is None:
            continue

        # Search forward for the matching }
        depth = 0
        json_end = None
        for i in range(json_start, len(stdout_content)):
            c = stdout_content[i]
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    json_end = i + 1
                    break

        if json_end is None:
            continue

        try:
            data = json.loads(stdout_content[json_start:json_end])
            for key, status in [('passes', TestStatus.PASSED), ('pending', TestStatus.SKIPPED), ('failures', TestStatus.FAILED)]:
                tests = data.get(key, [])
                if not isinstance(tests, list):
                    continue
                for test in tests:
                    file_path = test.get('file', '')
                    # Remove /app/ prefix
                    if file_path.startswith('/app/'):
                        file_path = file_path[5:]

                    full_title = test.get('fullTitle', '')

                    # Handle case where fullTitle contains file::
                    test_file_match = test_file_pattern.search(full_title)
                    if test_file_match:
                        file_path = test_file_match.group(1)
                    full_title = full_title.replace(f"{file_path}::", '')

                    name = f"{file_path} | {full_title}" if file_path else full_title
                    # Deduplicate
                    if name not in seen:
                        seen.add(name)
                        results.append(TestResult(name=name, status=status))
            # Stop once valid JSON is found
            if results:
                break
        except json.JSONDecodeError:
            continue

    return results


# =============================================================================
# protonmail__webclients - Jest (reads stderr)
# =============================================================================
def parse_protonmail(stdout_content: str, stderr_content: str) -> List[TestResult]:
    """
        Parse Jest test output for protonmail.
            Note: protonmail test output is written to stderr.
            Format: filename | [describe block] test description

            Also supports output wrapped by Yarn (YN0000), e.g.:
            ➤ YN0000: PASS ./chunk.extended.test.ts
    """
    results = []
    seen = set()

    # Use stderr content
    content = stderr_content if stderr_content.strip() else stdout_content

    # Strip Yarn YN0000 prefix: remove "YN0000: " or similar prefixes
    # Format: YN0000: actual content
    yarn_prefix_pattern = re.compile(r'^.*?➤\s*YN\d+:\s*', re.MULTILINE)
    content = yarn_prefix_pattern.sub('', content)

    file_pattern = re.compile(r'^(PASS|FAIL)\s+(.+?\.(?:test|spec)\.[jt]sx?)(?:\s|$)')
    test_pattern = re.compile(r'^\s*[✓✗×]\s+(.+?)(?:\s+\(\d+\s*m?s\))?$')
    skip_pattern = re.compile(r'^\s*○\s+(.+?)(?:\s+\(\d+\s*m?s\))?$')

    current_file = None
    current_describe = None

    lines = content.split('\n')

    for i, line in enumerate(lines):
        # Match file
        file_match = file_pattern.match(line.strip())
        if file_match:
            current_file = file_match.group(2)
            current_describe = None
            continue

        if not current_file:
            continue

        stripped = line.strip()

        # Check if this is a describe block (indented plain text line followed by test cases)
        if stripped and not re.match(r'^[✓✗×○]', stripped) and not stripped.startswith(('PASS', 'FAIL', 'Test Suites:', 'Tests:', 'Snapshots:', 'Time:', '(node:')):
            # Check if subsequent lines contain test cases
            is_describe = False
            for j in range(i + 1, min(i + 10, len(lines))):
                next_line = lines[j].strip()
                if re.match(r'^[✓✗×○]', next_line):
                    is_describe = True
                    break
                elif re.match(r'^(PASS|FAIL)\s', next_line):
                    break

            if is_describe:
                current_describe = stripped

        # Match test cases
        test_match = test_pattern.match(line)
        if test_match:
            test_name = test_match.group(1).strip()
            if current_describe:
                full_name = f"{current_file} | {current_describe} {test_name}"
            else:
                full_name = f"{current_file} | {test_name}"

            if full_name not in seen:
                seen.add(full_name)
                if line.strip().startswith('✓'):
                    results.append(TestResult(name=full_name, status=TestStatus.PASSED))
                else:
                    results.append(TestResult(name=full_name, status=TestStatus.FAILED))

        # Match skipped tests
        skip_match = skip_pattern.match(line)
        if skip_match:
            test_name = skip_match.group(1).strip()
            if current_describe:
                full_name = f"{current_file} | {current_describe} {test_name}"
            else:
                full_name = f"{current_file} | {test_name}"

            if full_name not in seen:
                seen.add(full_name)
                results.append(TestResult(name=full_name, status=TestStatus.SKIPPED))

    return results


# =============================================================================
# tutao__tutanota - ospec test framework (not Jest)
# =============================================================================
def parse_tutanota(stdout_content: str, stderr_content: str) -> List[TestResult]:
    """
        Parse ospec test output for tutanota.
            ospec output format: "All X assertions passed" or "X error(s)"
            Since ospec does not output individual test names, the list of test files is extracted from the beginning of the output.
    """
    results = []

    # Extract test file list from the beginning of stdout
    # Format: Running selected tests: test/tests/xxx.js test/tests/yyy.js ...
    test_files = []
    run_match = re.search(r'Running selected tests:\s*(.+?)(?:\n|$)', stdout_content)
    if run_match:
        files_str = run_match.group(1)
        # Match all test/tests/...js or .ts files
        test_files = re.findall(r'(test/tests/[^\s]+\.(?:js|ts))', files_str)

    # Check if all tests passed
    summary_pattern = r'All (\d+) assertions passed'
    error_pattern = r'(\d+) error\(s\)'

    all_passed = False
    has_errors = False

    for line in stdout_content.split('\n'):
        if re.search(summary_pattern, line):
            all_passed = True
            break
        if re.search(error_pattern, line):
            has_errors = True
            break

    if all_passed and test_files:
        # Create a PASSED result for each test file
        for test_file in test_files:
            test_name = f"{test_file} | test suite"
            results.append(TestResult(name=test_name, status=TestStatus.PASSED))
    elif has_errors:
        results.append(TestResult(name="test/tests/Suite.js | test execution", status=TestStatus.FAILED))
    else:
        # Check stderr for errors
        if stderr_content:
            for line in stderr_content.split('\n'):
                if any(err in line for err in ['Error:', 'TypeError:', 'ReferenceError:', 'failed with error code']):
                    results.append(TestResult(name=f"Build/Runtime Error: {line[:100]}", status=TestStatus.ERROR))
                    break

        if not results:
            results.append(TestResult(name="test/tests/Suite.js | all tests", status=TestStatus.PASSED))

    return results


# =============================================================================
# Mapping from repository name to parser function
# =============================================================================
REPO_PARSER_MAP: dict[str, Callable[[str, str], List[TestResult]]] = {
    'ansible__ansible': parse_ansible,
    'qutebrowser__qutebrowser': parse_qutebrowser,
    'internetarchive__openlibrary': parse_openlibrary,
    'flipt-io__flipt': parse_flipt,  # Parse --- PASS/FAIL/SKIP: format
    'navidrome__navidrome': parse_go_test,
    'gravitational__teleport': parse_go_test,
    'future-architect__vuls': parse_go_test,
    'element-hq__element-web': parse_element_web,
    'NodeBB__NodeBB': parse_nodebb,
    'protonmail__webclients': parse_protonmail,
    'tutao__tutanota': parse_tutanota,
}


# List of all supported repository names
ALL_REPOS = list(REPO_PARSER_MAP.keys())


def get_repo_from_instance_id(instance_id: str) -> str:
    """Extract the repository name from an instance_id"""
    for repo in ALL_REPOS:
        if repo in instance_id:
            return repo
    return None


def parse_logs_with_unified_parser(repo_name: str ,instance_id: str, stdout_content: str, stderr_content: str) -> dict:
    """
        Parse test logs using the unified parser.

            Args:
                instance_id: Instance ID
                stdout_content: stdout content
                stderr_content: stderr content

            Returns:
                dict: Parsed result compatible with the original output.json format
    """
    if repo_name is None:
        repo_name = get_repo_from_instance_id(instance_id)

    if repo_name is None:
        return {"error": f"Cannot determine repo from instance_id: {instance_id}"}

    try:
        test_results = parse_test_output(repo_name, stdout_content, stderr_content)

        if test_results is None or len(test_results) == 0:
            return {"tests": []}

        # Convert TestResult list to dict format
        output = {
            "tests": [
                {"name": tr.name, "status": tr.status.name}
                for tr in test_results
            ]
        }
        return output

    except ValueError as e:
        return {"error": f"Unsupported repo: {repo_name}"}
    except Exception as e:
        return {"error": f"Parse error: {repr(e)}"}




def get_parser_for_repo(repo_name: str) -> Optional[Callable[[str, str], List[TestResult]]]:
    """
        Get the corresponding parser function for a given repository name.

            Args:
                repo_name: Repository name, e.g. "ansible__ansible"

            Returns:
                The parser function, or None if the repository is not supported.
    """
    return REPO_PARSER_MAP.get(repo_name)


def parse_test_output(repo_name: str, stdout_content: str, stderr_content: str) -> List[TestResult]:
    """
        Parse test output for a given repository name.

            Args:
                repo_name: Repository name
                stdout_content: stdout content
                stderr_content: stderr content

            Returns:
                List of TestResult
    """
    parser = get_parser_for_repo(repo_name)
    if parser is None:
        raise ValueError(f"Unsupported repo: {repo_name}. Supported repos: {list(REPO_PARSER_MAP.keys())}")
    return parser(stdout_content, stderr_content)


def list_supported_repos() -> List[str]:
    """Return a list of all supported repositories"""
    return list(REPO_PARSER_MAP.keys())


def validate_parser_against_gold(logs_dir: str = "logs/valid_gold") -> dict:
    """
        Validate the consistency between the unified parser and the native gold_output.json.

            Args:
                logs_dir: Path to the valid_gold log directory

            Returns:
                A dict of validation results containing info about successful/failed instances
    """
    from pathlib import Path

    logs_path = Path(logs_dir)
    eval_results_path = logs_path / "eval_results.json"

    if not eval_results_path.exists():
        raise FileNotFoundError(f"eval_results.json not found at {eval_results_path}")

    with open(eval_results_path, 'r') as f:
        eval_results = json.load(f)

    # Only process successful instances
    successful_instances = [name for name, success in eval_results.items() if success]

    validation_results = {
        "total": len(successful_instances),
        "matched": 0,
        "mismatched": 0,
        "unsupported_repo": 0,
        "parse_error": 0,
        "details": []
    }

    for instance_name in successful_instances:
        instance_path = logs_path / instance_name
        gold_output_path = instance_path / "gold_output.json"
        gold_stdout_path = instance_path / "gold_stdout.log"
        gold_stderr_path = instance_path / "gold_stderr.log"

        # Extract repository name from instance name
        # Format: instance_owner__repo-commit-version or instance_org-name__repo-name-commit-version
        # Repo name format: owner__repo, separated by double underscore
        # Example: instance_element-hq__element-web-72a8f8f03... -> element-hq__element-web
        name_without_prefix = instance_name.replace("instance_", "")
        # Find the double underscore position; repo name is the part before the commit hash
        match = re.match(r'^([^_]+__[^-]+-?[^-]*)', name_without_prefix)
        if match:
            # Look up the matching repo name in REPO_PARSER_MAP
            repo_name = None
            for known_repo in REPO_PARSER_MAP.keys():
                if name_without_prefix.startswith(known_repo):
                    repo_name = known_repo
                    break
            if repo_name is None:
                # Fall back to regex matching the owner__repo format
                repo_match = re.match(r'^([a-zA-Z0-9_-]+__[a-zA-Z0-9_-]+)', name_without_prefix)
                repo_name = repo_match.group(1) if repo_match else name_without_prefix.split("-")[0]
        else:
            repo_name = name_without_prefix.split("-")[0]

        result = {
            "instance": instance_name,
            "repo": repo_name,
            "status": "unknown",
            "message": ""
        }

        # Check if the repository is supported
        if repo_name not in REPO_PARSER_MAP:
            result["status"] = "unsupported_repo"
            result["message"] = f"Repo '{repo_name}' not in REPO_PARSER_MAP"
            validation_results["unsupported_repo"] += 1
            validation_results["details"].append(result)
            continue

        # Check if files exist
        if not gold_output_path.exists() or not gold_stdout_path.exists():
            result["status"] = "missing_files"
            result["message"] = "gold_output.json or gold_stdout.log not found"
            validation_results["parse_error"] += 1
            validation_results["details"].append(result)
            continue

        try:
            # Read raw parsing results
            with open(gold_output_path, 'r') as f:
                gold_output = json.load(f)

            # Read stdout
            with open(gold_stdout_path, 'r') as f:
                stdout_content = f.read()

            # Read stderr (if it exists)
            stderr_content = ""
            if gold_stderr_path.exists():
                with open(gold_stderr_path, 'r') as f:
                    stderr_content = f.read()

            # Parse using the unified parser
            parsed_results = parse_test_output(repo_name, stdout_content, stderr_content)

            # Convert parsing results to a comparable format
            parsed_dict = {r.name: r.status.name for r in parsed_results}
            gold_dict = {t["name"]: t["status"] for t in gold_output.get("tests", [])}

            # Compare results
            if parsed_dict == gold_dict:
                result["status"] = "matched"
                result["message"] = f"Matched: {len(parsed_dict)} tests"
                validation_results["matched"] += 1
            else:
                result["status"] = "mismatched"

                # Detailed diff analysis
                parsed_names = set(parsed_dict.keys())
                gold_names = set(gold_dict.keys())

                only_in_parsed = parsed_names - gold_names
                only_in_gold = gold_names - parsed_names
                common_names = parsed_names & gold_names

                status_diff = []
                for name in common_names:
                    if parsed_dict[name] != gold_dict[name]:
                        status_diff.append({
                            "name": name,
                            "parsed": parsed_dict[name],
                            "gold": gold_dict[name]
                        })

                result["message"] = {
                    "parsed_count": len(parsed_dict),
                    "gold_count": len(gold_dict),
                    "only_in_parsed": list(only_in_parsed),
                    "only_in_gold": list(only_in_gold),
                    "status_diff": status_diff
                }
                validation_results["mismatched"] += 1

        except Exception as e:
            result["status"] = "parse_error"
            result["message"] = str(e)
            validation_results["parse_error"] += 1

        validation_results["details"].append(result)

    return validation_results


def print_validation_report(results: dict, verbose: bool = False):
    """Print the validation report"""
    print("=" * 80)
    print("统一解析器验证报告")
    print("=" * 80)
    print(f"总实例数:       {results['total']}")
    print(f"匹配成功:       {results['matched']}")
    print(f"匹配失败:       {results['mismatched']}")
    print(f"不支持的仓库:   {results['unsupported_repo']}")
    print(f"解析错误:       {results['parse_error']}")
    print("=" * 80)

    if verbose:
        # Group statistics by repository
        repo_stats = {}
        for detail in results["details"]:
            repo = detail["repo"]
            if repo not in repo_stats:
                repo_stats[repo] = {"matched": 0, "mismatched": 0, "unsupported": 0, "error": 0}

            if detail["status"] == "matched":
                repo_stats[repo]["matched"] += 1
            elif detail["status"] == "mismatched":
                repo_stats[repo]["mismatched"] += 1
            elif detail["status"] == "unsupported_repo":
                repo_stats[repo]["unsupported"] += 1
            else:
                repo_stats[repo]["error"] += 1

        print("\n按仓库统计:")
        print("-" * 80)
        for repo, stats in sorted(repo_stats.items()):
            total = sum(stats.values())
            print(f"  {repo:<35} 匹配: {stats['matched']:3}/{total:3}  "
                  f"失败: {stats['mismatched']:3}  不支持: {stats['unsupported']:3}  错误: {stats['error']:3}")

        # Show failure details
        mismatched = [d for d in results["details"] if d["status"] == "mismatched"]
        if mismatched:
            print("\n" + "=" * 80)
            print("失败详情:")
            print("=" * 80)
            for detail in mismatched[:10]:  # Show only the first 10
                print(f"\n实例: {detail['instance']}")
                msg = detail["message"]
                if isinstance(msg, dict):
                    print(f"  解析数量: {msg['parsed_count']}, 原生数量: {msg['gold_count']}")
                    if msg["only_in_parsed"]:
                        print(f"  仅在解析结果中: {msg['only_in_parsed'][:3]}...")
                    if msg["only_in_gold"]:
                        print(f"  仅在原生结果中: {msg['only_in_gold'][:3]}...")
                    if msg["status_diff"]:
                        print(f"  状态不一致: {msg['status_diff'][:3]}...")
                else:
                    print(f"  {msg}")

            if len(mismatched) > 10:
                print(f"\n  ... 还有 {len(mismatched) - 10} 个失败实例未显示")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="统一测试解析器")
    parser.add_argument("--validate", action="store_true", help="验证解析器与 gold_output.json 的一致性")
    parser.add_argument("--logs-dir", default="logs/valid_gold", help="日志目录路径")
    parser.add_argument("-v", "--verbose", action="store_true", help="显示详细信息")
    parser.add_argument("--save", type=str, help="将验证结果保存到指定 JSON 文件")

    args = parser.parse_args()

    if args.validate:
        print(f"正在验证... 日志目录: {args.logs_dir}")
        results = validate_parser_against_gold(args.logs_dir)
        print_validation_report(results, verbose=args.verbose)

        if args.save:
            with open(args.save, 'w') as f:
                json.dump(results, f, indent=2, ensure_ascii=False)
            print(f"\n验证结果已保存到: {args.save}")
    else:
        print("支持的仓库及其解析器:")
        print("=" * 60)
        for repo, parser_func in REPO_PARSER_MAP.items():
            print(f"  {repo:<35} -> {parser_func.__name__}")
        print("\n使用 --validate 参数来验证解析器")
