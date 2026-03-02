"""
    parser_utils for mini-swe-agent

    This file has been refactored:
    - Common functions have been extracted to SWE-PLUS/util/parser_utils.py
    - Only Python-specific functions used exclusively by mini-swe-agent are retained here

    Version: v0.2.0
    Last updated: 2026-02-14
"""

import re
import difflib
import sys
from pathlib import Path

# Import public functions from sweabs_utils package
from sweabs_utils.parser_utils import (
    # Basic utilities
    str2bool,
    read_list_file,

    # Constants
    NON_TEST_EXTS,
    LANGUAGE_TEST_EXTENSIONS,
    FILTER_DIRS,
    FILTER_FILES,
    FILTER_EXTS,

    # Go test utilities
    extract_go_test_info,
    get_test_directives,

    # Diff/Patch parsing utilities
    parse_diff_path,
    should_filter_path,
    is_binary_diff_block,
    split_diff_blocks,
    extract_added_content,
    generate_newfile_diff_block,
    filter_apply_diffs,
    get_apply_files,
    remove_conflicting_chunks,
)

# ========== mini-swe-agent specific functions (Python-related) ==========

# New, strict regex for definition detection
FUNC_NAME_RE = re.compile(r'\+def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(')
CLASS_NAME_RE = re.compile(r'\+class\s+([A-Za-z_][A-Za-z0-9_]*)')


def is_strict_def_or_class(line):
    """
        Recognizes only new definitions that strictly begin with +def or +class, and returns ('func'/'class', name).

            This is a Python-specific function for identifying newly added function/class definitions in a diff.
    """
    if line.startswith("+def "):
        m = FUNC_NAME_RE.match(line)
        if m:
            return ("func", m.group(1))
    if line.startswith("+class "):
        m = CLASS_NAME_RE.match(line)
        if m:
            return ("class", m.group(1))
    return None


def is_new_py_file(block: str) -> bool:
    """
        Determine whether a diff block represents a newly added Python file.

            Args:
                block: The diff block content

            Returns:
                True if it's a new Python file
    """
    return (
        "new file mode" in block
        and "--- /dev/null" in block
        and re.search(r"\+{3} b/.*\.py", block) is not None
    )


def clean_diff_block_keep_last(block_text: str) -> str:
    """
        Clean a Python diff block, keeping only the last occurrence of each function/class definition.

            When the same function/class is defined multiple times in the diff, all earlier occurrences are removed and only the last one is kept.

            Args:
                block_text: The diff block text

            Returns:
                The cleaned diff block
    """
    lines = block_text.splitlines(keepends=True)

    # A. Find all function/class definition locations, only record strict +def/+class
    def_positions = {}  # name -> list of line indices
    for i, line in enumerate(lines):
        info = is_strict_def_or_class(line)
        if info:
            _, name = info
            def_positions.setdefault(name, []).append(i)

    # B. For duplicate definitions: remove earlier occurrences, keep the last one
    to_remove = []

    for name, positions in def_positions.items():
        if len(positions) <= 1:
            continue

        for pos in positions[:-1]:  # Remove only the earlier ones
            start = pos
            end = pos + 1

            # Expand the entire function body (until the next strict +def or +class)
            while end < len(lines):
                if is_strict_def_or_class(lines[end]):
                    break
                end += 1

            to_remove.append((start, end))

    # C. Delete in reverse order to avoid index shifting
    to_remove.sort(reverse=True)
    for start, end in to_remove:
        del lines[start:end]

    return "".join(lines)


def clean_full_diff(diff_text: str) -> str:
    """
        Clean a complete Python diff by handling duplicate function/class definitions.

            For each newly added Python file, removes duplicate function/class definitions.

            Args:
                diff_text: The complete diff text

            Returns:
                The cleaned diff text
    """
    blocks = split_diff_blocks(diff_text)
    new_blocks = []

    for block in blocks:
        if not is_new_py_file(block):
            new_blocks.append(block)
            continue

        # Extract & clean
        cleaned = clean_diff_block_keep_last(block)

        if cleaned == block:
            new_blocks.append(block)
            continue

        # Keep as plain content, strip the leading + and header
        cleaned = extract_added_content(cleaned)
        # Generate new diff block
        new_block = generate_newfile_diff_block(block, cleaned)
        new_blocks.append(new_block)

    return "".join(new_blocks)


# ========== Maintain backward compatibility (if other code uses these directly) ==========
# This section can be kept or removed as needed

if __name__ == "__main__":
    # Test code remains unchanged
    import json

    file = 'SWE-ABS/mini-swe-agent/result/mutation_aug/pro_selecet_135_aug/preds_no_equ_mutation_aug_1.json'
    instance_id = 'instance_navidrome__navidrome-3f2d24695e9382125dfe5e6d6c8bbeb4a313a4f9'

    with open(file, "r") as f:
        preds = json.load(f)

    instance = preds[instance_id]
    model_test_patch = instance["model_test_patch"]
    patch = instance["patch"]

    model_test_patch = filter_apply_diffs(patch, [])
    preds[instance_id]["model_test_patch"] = patch
