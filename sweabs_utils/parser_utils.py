"""
SWE-PLUS Common Patch Analysis Tool

Includes:
- File reading tool
- Diff/Patch analysis tool
- Test command extraction tool
"""

import re
import json
import difflib
from pathlib import Path
from typing import Any, List, Union
from argparse import ArgumentTypeError

try:
    import yaml
except ImportError:
    yaml = None


# ========== Basic Utility Functions ==========

def str2bool(v):
    """
    Minor helper function to convert string to boolean
    """
    if isinstance(v, bool):
        return v
    if v.lower() in ("yes", "true", "t", "y", "1"):
        return True
    elif v.lower() in ("no", "false", "f", "n", "0"):
        return False
    else:
        raise ArgumentTypeError("Boolean value expected.")


def read_list_file(filepath: Union[str, Path]) -> Any:
    """
    Read a file and return parsed content based on file extension.

    Supported formats:
    - .json   -> return parsed JSON object
    - .jsonl  -> return List[dict]
    - .txt    -> return List[str]
    - .yaml/.yml -> return parsed YAML object

    Args:
        filepath: file path

    Returns:
        Parsed file content

    Raises:
        ValueError: unsupported file extension
        FileNotFoundError: file does not exist
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"File not found: {filepath}")

    suffix = filepath.suffix.lower()

    if suffix == ".json":
        with filepath.open("r", encoding="utf-8") as f:
            return json.load(f)

    elif suffix == ".jsonl":
        with filepath.open("r", encoding="utf-8") as f:
            return [
                json.loads(line)
                for line in f
                if line.strip()
            ]

    elif suffix == ".txt":
        with filepath.open("r", encoding="utf-8") as f:
            return [
                line.rstrip("\n")
                for line in f
                if line.strip()
            ]

    elif suffix in {".yaml", ".yml"}:
        if yaml is None:
            raise ImportError("PyYAML is not installed. Install it with: pip install pyyaml")
        with filepath.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    else:
        raise ValueError(f"Unsupported file type: {suffix}")


# ========== Constants ==========

# Non-test file extensions
NON_TEST_EXTS = [
    ".json",
    ".png",
    "csv",
    ".txt",
    ".md",
    ".jpg",
    ".jpeg",
    ".pkl",
    ".yml",
    ".yaml",
    ".toml",
]

# Test file extensions by language
LANGUAGE_TEST_EXTENSIONS = {
    "python": [".py"],
    "go": [".go"],
    "js": [".ts", ".tsx", ".js", ".jsx", ".mjs"],
    "ts": [".ts", ".tsx", ".js", ".jsx", ".mjs"],
}

# Directories to filter out
FILTER_DIRS = (
    "/public/",
    "/dist/",
    "/build/",
    "/assets/",
    "/static/",
)

# Filenames to filter out
FILTER_FILES = (
    "yarn.lock",
    "package-lock.json",
    "go.sum",
    "go.work",
    "go.work.sum",
    "base.rdb",
)

# File extensions to filter out
FILTER_EXTS = (
    ".orig",
    ".out",
    ".min.js",
    ".rej",
    ".bak",
)


# ========== Go Test Utilities ==========

def extract_go_test_info(patch_content: str) -> dict:
    """
    Extract Go test information from patch content.

    Returns a dict with:
    - package_paths: list of package paths (e.g., ["./persistence", "./scanner"])
    - test_names: list of test function names or Ginkgo Describe names for -run flag
    - is_ginkgo: whether the patch uses Ginkgo framework

    Args:
        patch_content: The patch content (diff format)

    Returns:
        dict with package_paths, test_names, is_ginkgo
    """
    result = {
        "package_paths": [],
        "test_names": [],
        "is_ginkgo": False
    }

    # Extract file paths from diff headers
    diff_pattern = r"diff --git a/.* b/(.*_test\.go)"
    test_files = re.findall(diff_pattern, patch_content)

    # Convert file paths to package paths (directory paths)
    package_paths = set()
    for file_path in test_files:
        dir_path = "/".join(file_path.split("/")[:-1])
        if dir_path:
            package_paths.add("./" + dir_path)
        else:
            package_paths.add(".")
    result["package_paths"] = list(package_paths)

    # Check if it's a Ginkgo test
    is_ginkgo = 'onsi/ginkgo' in patch_content
    result["is_ginkgo"] = is_ginkgo

    # Extract test names
    test_names = []

    # Pattern 1: Standard Go test functions (added lines)
    std_patterns = [
        r"^\+func\s+(Test\w+)\s*\(",
        r"^\+func\s+(Fuzz\w+)\s*\(",
        r"^\+func\s+(Benchmark\w+)\s*\(",
        r"^\+func\s+(Example\w*)\s*\(",
    ]
    for line in patch_content.split('\n'):
        for pattern in std_patterns:
            matches = re.findall(pattern, line)
            test_names.extend(matches)

    # Pattern 2: Hunk header context for modified test functions
    hunk_pattern = r"@@.*@@\s*func\s+(Test\w+|Fuzz\w+|Benchmark\w+|Example\w*)\s*\("
    hunk_matches = re.findall(hunk_pattern, patch_content)
    test_names.extend(hunk_matches)

    # Pattern 3: Ginkgo Describe blocks (for -run flag matching)
    if is_ginkgo:
        ginkgo_describe_pattern = r'^\+.*Describe\s*\(\s*"([^"]+)"'
        for line in patch_content.split('\n'):
            matches = re.findall(ginkgo_describe_pattern, line)
            test_names.extend(matches)

    # Remove duplicates while preserving order
    seen = set()
    unique_names = []
    for name in test_names:
        if name not in seen:
            seen.add(name)
            unique_names.append(name)
    result["test_names"] = unique_names

    return result["package_paths"]


def get_test_directives(instance, test_patch_key='test_patch') -> list:
    """
    Get test directives from the test_patch of a task instance

    Args:
        instance (dict): task instance
    Returns:
        directives (list): List of test directives
    """
    # For seq2seq code repos, testing command is fixed
    if instance["repo"] == "swe-bench/humaneval":
        return ["test.py"]

    # Get repo language and determine valid test file extensions
    repo_language = instance.get("repo_language", "python")
    valid_extensions = LANGUAGE_TEST_EXTENSIONS.get(repo_language, [".py"])

    # Get test directives from test patch and remove non-test files
    diff_pat = r"diff --git a/.* b/(.*)"
    test_patch = instance[test_patch_key]
    directives = re.findall(diff_pat, test_patch)

    # Filter test files by language
    directives = [
        d for d in directives if any(d.endswith(ext) for ext in valid_extensions)
    ]

    # For Python, exclude __init__.py files
    if repo_language == "python":
        directives = [d for d in directives if not d.endswith("__init__.py")]

    # For Django tests, remove extension + "tests/" prefix and convert slashes to dots (module referencing)
    if instance["repo"] == "django/django":
        directives_transformed = []
        for d in directives:
            d = d[: -len(".py")] if d.endswith(".py") else d
            d = d[len("tests/") :] if d.startswith("tests/") else d
            d = d.replace("/", ".")
            directives_transformed.append(d)
        directives = directives_transformed

    if repo_language == "go":
        directives = extract_go_test_info(test_patch)

    return directives


# ========== Diff/Patch Parsing Utilities ==========

def parse_diff_path(line: str) -> str:
    """
    Parse file path from a diff line.

    Args:
        line: diff --git a/foo/bar.js b/foo/bar.js

    Returns:
        File path (without 'a/' prefix)
    """
    parts = line.split()
    if len(parts) >= 4:
        return parts[2][2:]  # Strip 'a/' prefix
    return ""


def should_filter_path(path: str) -> bool:
    """
    Check if a path should be filtered out.

    Args:
        path: file path

    Returns:
        True if should be filtered
    """
    # Directory-level filter
    if any(d in path for d in FILTER_DIRS):
        return True

    # Filename-level filter
    filename = path.split("/")[-1]
    if filename in FILTER_FILES:
        return True

    # Extension-level filter
    if any(path.endswith(ext) for ext in FILTER_EXTS):
        return True

    return False


def is_binary_diff_block(block: str) -> bool:
    """
    Judge if the diff block is a binary file

    Args:
        block: diff block

    Returns:
        True if binary
    """
    if "GIT binary patch" in block:
        return True

    if block.lstrip().startswith("Binary files"):
        return True

    if "\n@@" not in block:
        return True

    return False


def split_diff_blocks(diff_text: str) -> list[str]:
    """
    Split diff into blocks by 'diff --git'.

    Args:
        diff_text: diff content
    Returns:
        diff blocks
    """
    blocks = []
    current = []
    for line in diff_text.splitlines(keepends=True):
        if line.startswith("diff --git"):
            if current:
                blocks.append("".join(current))
                current = []
        current.append(line)
    if current:
        blocks.append("".join(current))
    return blocks


def extract_added_content(block: str) -> list[str]:
    """
    Extract added content (lines starting with +) from a diff block.

    Args:
        block: diff block

    Returns:
        List of added lines
    """
    lines = []
    for line in block.splitlines():
        # '+++ b/file.py' is not actual added content
        if line.startswith("+++"):
            continue
        if line.startswith('+'):
            lines.append(line[1:])
    return lines


def generate_newfile_diff_block(block: str, new_lines: list[str]) -> str:
    """
    Generate a unified diff using difflib based on the file path info from the original block.

    Args:
        block: original diff block
        new_lines: content lines of the new file

    Returns:
        Generated diff block
    """
    # Extract target filepath
    m = re.search(r"\+{3} b/(.*)", block)
    filepath = m.group(1) if m else "unknown.py"

    diff_body = difflib.unified_diff(
        [],  # old file empty
        new_lines,
        fromfile="/dev/null",
        tofile=f"b/{filepath}",
        lineterm=""
    )

    # Reconstruct full block header
    header = []
    for line in block.splitlines():
        if line.startswith('---'):
            break
        header.append(line)

    return "\n".join(header) + "\n" + "\n".join(diff_body) + "\n"


def filter_apply_diffs(diff_text: str, apply_files: list[str], keep_apply_files=False) -> str:
    """
    Filter specific files from a diff.

    Args:
        diff_text: complete diff text
        apply_files: list of files to filter (full diff lines, e.g., "diff --git a/foo.py b/foo.py")
        keep_apply_files: if True, keep only files in apply_files; if False, exclude them

    Returns:
        Filtered diff text
    """
    kept_blocks = []

    current_block = []
    current_path = ""
    keep_current = False

    def flush_current():
        nonlocal current_block, current_path, keep_current
        if not current_block:
            return

        block_text = "".join(current_block)

        # ① Filter binary diffs (most critical)
        if is_binary_diff_block(block_text):
            current_block = []
            return

        # ② Keep
        if keep_current:
            kept_blocks.append(block_text)

        current_block = []

    for line in diff_text.splitlines(keepends=True):
        if line.startswith("diff --git"):
            # Process the previous block
            flush_current()

            current_block = [line]
            current_path = parse_diff_path(line)

            # apply_files control
            if keep_apply_files:
                keep_current = line.strip() in apply_files
            else:
                keep_current = line.strip() not in apply_files

            # Force path-level filter
            if should_filter_path(current_path):
                keep_current = False

        else:
            if current_block:
                current_block.append(line)

    # Handle the last block
    flush_current()

    return "".join(kept_blocks)


def get_apply_files(code_patch: str) -> list[str]:
    """
    Extract file paths involved in the patch.

    Args:
        code_patch: patch content

    Returns:
        List of file paths (e.g., 'diff --git a/foo.py b/foo.py' -> 'foo.py')
    """
    apply_files = []
    for line in code_patch.splitlines():
        if line.startswith("diff --git"):
            parts = line.strip().split()
            # parts[2] is 'a/xxx', strip the 'a/' prefix
            file_path = parts[2][2:]
            apply_files.append(file_path)
    return apply_files


def remove_conflicting_chunks(model_patch: str, model_test_patch: str) -> str:
    """
    Remove all diff chunks in model_patch that involve files present in model_test_patch.
    Compatible with:
    1) diff --git
    2) unified diff (modified / added / deleted)

    Args:
        model_patch: code patch generated by the model
        model_test_patch: test patch generated by the model

    Returns:
        Filtered model_patch
    """
    model_test_patch_files = get_apply_files(model_test_patch)
    result_chunks = []

    if "diff --git a/" in model_patch:
        # ---------- Case 1: Standard git diff ----------
        pattern = r"(diff --git a/(?P<file>.+?) b/.+?)(?=diff --git a/|\Z)"
        for m in re.finditer(pattern, model_patch, flags=re.DOTALL):
            chunk = m.group(0)
            file_path = m.group("file")
            if file_path not in model_test_patch_files:
                result_chunks.append(chunk)

    else:
        # ---------- Case 2: Unified diff (including /dev/null) ----------
        pattern = (
            r"(--- (?P<old>.+?)\n"
            r"\+\+\+ (?P<new>.+?)\n"
            r".+?)(?=\n--- |\Z)"
        )
        for m in re.finditer(pattern, model_patch, flags=re.DOTALL):
            chunk = m.group(0)

            old_path = m.group("old")
            new_path = m.group("new")

            # Real file path: use whichever is not /dev/null
            if old_path == "/dev/null":
                file_path = new_path
            else:
                file_path = old_path

            # Strip 'a/' or 'b/' prefix
            if file_path.startswith(("a/", "b/")):
                file_path = file_path[2:]

            if file_path not in model_test_patch_files:
                result_chunks.append(chunk)

    return "\n".join(result_chunks)
