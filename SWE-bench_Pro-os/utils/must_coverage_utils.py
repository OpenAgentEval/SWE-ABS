# file: coverage_utils.py
"""
Patch analysis and coverage utilities.

Supports multi-language code analysis using tree-sitter for:
- Python, Go, JavaScript, TypeScript
"""

from collections import defaultdict
import copy
import json
import re
from pathlib import Path
from unidiff import PatchSet
from typing import Dict, Set, Tuple, Optional, Any

from utils.constants import (
    DOCKER_USER,
    DOCKER_WORKDIR,
)
from utils.code_analysis import (
    analyze_source,
    detect_language_from_path,
    is_language_supported,
    AnalysisResult,
)



def parse_patch_log(log_content: str) -> dict:
    # Regex: match hunk lines
    hunk_re = re.compile(
        r"Hunk\s+#(\d+)\s+succeeded\s+at\s+(\d+)\s+\(offset\s+([+-]?\d+)\s+lines?\)"
    )
    # Regex: match 'Checking patch' file lines
    checking_patch_re = re.compile(r"Checking patch (.+?)\.\.\.")


    result = {}
    current_file = None

    for line in log_content.splitlines():
        line = line.strip()
        if not line:
            continue

        # Check if line is "Checking patch xxx.py..." -> current file being processed
        checking_match = checking_patch_re.match(line)
        if checking_match:
            current_file = checking_match.group(1)
            if current_file not in result:
                result[current_file] = {}
            continue

        # Check if line is a hunk line -> extract hunk info
        hunk_match = hunk_re.search(line)
        if hunk_match and current_file:
            hunk_num = int(hunk_match.group(1))
            applied_line = int(hunk_match.group(2))
            offset = int(hunk_match.group(3))

            hunk_info = {
                "hunk": hunk_num,
                "applied_at_line": applied_line,
                "offset": offset
            }
            result[current_file][str(hunk_num)] = hunk_info
            continue

    return result


# ---------- diff file parsing ----------
def parse_modified_info(diff_text: str,offset_dict=None) -> Dict[str, Set[int]]:
    """
    Parse unified diff using unidiff library.
    Returns:
        modified_info: dict[str, set[int]]  # only added lines
    """
    patch = PatchSet(diff_text)
    modified_info: Dict[str, Set[int]] = {}

    for patched_file in patch:
        file_path = patched_file.path

        modified_info[file_path] = set()

        file_offset = None
        if offset_dict:
            file_offset = offset_dict.get(file_path)


        for idx, hunk in enumerate(patched_file):
            offset_num = 0
            if file_offset and str(idx+1) in file_offset:
                applied_at_line = file_offset[str(idx+1)]['applied_at_line']
                offset_num = applied_at_line - hunk.target_start

            for line in hunk:
                if line.is_added:
                    # target_line_no is the line number of the added line
                    modified_info[file_path].add(line.target_line_no+offset_num)

        # Skip if an empty file was newly added
        if not modified_info[file_path]:
            del modified_info[file_path]
            continue

    return modified_info



# ---------- container file reading ----------
def fetch_file_from_container(container, path_in_container: str) -> str | None:
    """
    Read file content from container using 'cat'.
    Returns None if file does not exist.
    """
    exec_result = container.exec_run(
        f"cat {path_in_container}",
        workdir=DOCKER_WORKDIR,
        user=DOCKER_USER
    )
    if exec_result.exit_code != 0:
        return None
    return exec_result.output.decode("utf-8", "ignore")


def dump_modified_files(container, modified_files: list[str], save_dir: Path):
    """
    Save full contents of modified files into save_dir/modified_files/
    Returns a list of tuples: (relative_path, content or None)
    """
    dumped = []
    for file_path in modified_files:

        inner_path = f"{DOCKER_WORKDIR}/{file_path}"
        content = fetch_file_from_container(container, inner_path)

        if content is None:
            continue

        dumped.append((file_path, content))
        save_name = file_path.replace("/", "__") + ".after"
        output_path = save_dir / "modified_files" / save_name
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")

    return dumped


# ---------- auto slicing ----------
def slice_engine_k(
    modified_lines,
    defs,
    uses,
    k,
    line_scope,
    direction="forward",
    limit_scope=False
):
    """
        General-purpose slicing engine:
              - forward: def → use
              - backward: use → def
            Supports k-hop and scope limiting (multi-scope propagation bug fixed).

            direction: "forward" or "backward"
    """
    if direction not in ("forward", "backward"):
        raise ValueError("direction must be 'forward' or 'backward'")

    if limit_scope:
        target_scopes = { line_scope[m] for m in modified_lines }
    else:
        target_scopes = None

    affected = set(modified_lines)
    frontier = set(modified_lines)

    for _ in range(k):
        next_frontier = set()

        # -----------------------------
        # Step 1: Collect def/use info for frontier
        # -----------------------------
        if direction == "forward":
            vars_of_interest = set()
            for ln in frontier:
                vars_of_interest |= defs.get(ln, set())
        else:
            vars_of_interest = set()
            for ln in frontier:
                vars_of_interest |= uses.get(ln, set())

        # -----------------------------
        # Step 2: Iterate all lines to find which new lines are affected by propagation
        # -----------------------------
        all_lines = set(defs.keys()) | set(uses.keys())

        for ln in all_lines:
            if ln in affected:
                continue

            if limit_scope and line_scope.get(ln) not in target_scopes:
                continue

            if direction == "forward":
                used_vars = uses.get(ln, set())
                if used_vars & vars_of_interest:
                    affected.add(ln)
                    next_frontier.add(ln)

            else:  # backward
                defined_vars = defs.get(ln, set())
                if defined_vars & vars_of_interest:
                    affected.add(ln)
                    next_frontier.add(ln)

        frontier = next_frontier
        if not frontier:
            break

    return affected


def compute_patch_slice_k(
    modified_lines: Set[int],
    src: str,
    k: int = 2,
    limit_scope: bool = True,
    analysis_result: Optional[AnalysisResult] = None,
    analyzer: Optional[Any] = None,
) -> Tuple[Set[int], Set[int]]:
    """
        Compute repair-influence slice with depth limit k.
            Supports forward/backward and scope limiting (function/class/global).

            Args:
                modified_lines: Set of modified line numbers
                src: Source code content
                k: Depth limit for slicing
                limit_scope: Whether to limit slicing to the same scope
                analysis_result: Pre-computed analysis result (optional)
                analyzer: Language analyzer instance (optional, for filtering)

            Returns:
                Tuple of (scoped_slice, full_slice)
    """
    # Use pre-computed analysis if available
    if analysis_result:
        line2scope = analysis_result.line_to_scope
        defs = analysis_result.defs
        uses = analysis_result.uses
        nodes_by_lineno = analysis_result.nodes_by_lineno
    else:
        # Fallback: analyze as Python (for backward compatibility)
        from utils.code_analysis import PythonAnalyzer
        py_analyzer = PythonAnalyzer()
        result = py_analyzer.analyze(src, modified_lines.copy())
        line2scope = result.line_to_scope
        defs = result.defs
        uses = result.uses
        nodes_by_lineno = result.nodes_by_lineno
        analyzer = py_analyzer

    # 3. Forward slice (full scope)
    fwd_full = slice_engine_k(
        modified_lines,
        defs,
        uses,
        k=k,
        line_scope=line2scope,
        direction="forward",
        limit_scope=False
    )

    # 4. Backward slice (full scope)
    bwd_full = slice_engine_k(
        modified_lines,
        defs,
        uses,
        k=k,
        line_scope=line2scope,
        direction="backward",
        limit_scope=False
    )

    # Filter global modified lines
    if analyzer:
        filtered_modified = analyzer.filtered_global_modified(
            line2scope, nodes_by_lineno, modified_lines
        )
    else:
        # No filtering if no analyzer
        filtered_modified = modified_lines

    # Scoped slicing with filtered modified lines
    fwd = slice_engine_k(
        filtered_modified,
        defs,
        uses,
        k=5,
        line_scope=line2scope,
        direction="forward",
        limit_scope=limit_scope
    )
    bwd = slice_engine_k(
        filtered_modified,
        defs,
        uses,
        k=5,
        line_scope=line2scope,
        direction="backward",
        limit_scope=limit_scope
    )


    # print("bwd:",sorted(list(bwd)))

    # 5. Merge results
    return fwd | bwd, fwd_full | bwd_full




def compute_must_coverage(container, patch, save_dir, logger, patch_log):
    """
    Compute must-coverage information for a patch.

    Supports multiple languages: Python, Go, JavaScript, TypeScript.

    Args:
        container: Docker container to fetch files from
        patch: Patch diff content
        save_dir: Directory to save modified files
        logger: Logger instance
        patch_log: Log content from patch application

    Returns:
        Dictionary mapping file paths to coverage information
    """
    from utils.code_analysis import get_analyzer

    offset_dict = parse_patch_log(patch_log)
    modified_info = parse_modified_info(patch, offset_dict)
    modified_info: Dict[str, Set[int]]
    # logger.info(modified_info)
    must_coverage = {}
    dumps = dump_modified_files(container, list(modified_info.keys()), save_dir)

    for file_path, content in dumps:
        # Detect language from file extension
        language = detect_language_from_path(file_path)

        if language is None:
            logger.warning(f"Unsupported file type, skipping: {file_path}")
            continue

        if not is_language_supported(language):
            logger.warning(f"Language '{language}' not supported, skipping: {file_path}")
            continue

        try:
            # Get the analyzer for this language
            analyzer = get_analyzer(language)
            if analyzer is None:
                logger.warning(f"No analyzer for language '{language}', skipping: {file_path}")
                continue

            # Analyze source code
            analysis_result = analyze_source(content, language, modified_info[file_path].copy())

            # Get executable lines and corrected modified lines
            executable_lines = analysis_result.executable_lines
            modified_lines = analysis_result.modified_lines

            # Compute slicing with the analysis result
            slice_region_scope, slice_region = compute_patch_slice_k(
                modified_lines,
                content,
                k=1,
                analysis_result=analysis_result,
                analyzer=analyzer
            )

            exe_slice_lines = slice_region & executable_lines
            exe_slice_lines_scope = slice_region_scope & executable_lines
            exe_modified_lines = modified_lines & executable_lines

            must_coverage[file_path] = {
                "exe_slice_lines_scope": sorted(exe_slice_lines_scope),
                "exe_slice_lines": sorted(exe_slice_lines),
                "exe_modified_lines": sorted(exe_modified_lines),
                "content": content,
                "language": language,
            }

        except Exception as e:
            logger.error(f"Failed to analyze {file_path} ({language}): {e}")
            continue

    return must_coverage


# ---------- example usage ----------
if __name__ == "__main__":

    log_file = Path("/home/ddq/CaoYang/SWE-PLUS/SWE-bench_Pro-os/logs/aug_test_center/eval_gold_patch/debug_go1/instance_gravitational__teleport-eefac60a350930e5f295f94a2d55b94c1988c04e-vee9b09fb20c43af7e520f57e9239bbcf46b7113d/raw_container.log")
    offset_dict = parse_patch_log(log_file.read_text())
    
    
    from utils.code_analysis import get_analyzer

    instance_id = 'sympy__sympy-24562'
    instance_path = Path(f"logs/extract_line_number/validate-gold/{instance_id}")

    patch_file = instance_path / "patch.diff"
    log_file = instance_path / "run_instance.log"
    patch = patch_file.read_text()
    file_path = "sympy__core__numbers.py"
    modified_file = instance_path / "modified_files" / f"{file_path}.after"

    offset_dict = parse_patch_log(log_file.read_text())

    file_key = file_path.replace("__", "/")
    content = modified_file.read_text()
    modified_info = parse_modified_info(patch, offset_dict)
    modified_info_copy = copy.deepcopy(modified_info)

    # Detect language and get analyzer
    language = detect_language_from_path(file_key)
    print(f"Detected language: {language}")

    if language:
        analyzer = get_analyzer(language)
        analysis_result = analyze_source(content, language, modified_info[file_key].copy())

        executable_lines = analysis_result.executable_lines
        modified_lines = analysis_result.modified_lines

        slice_region_scope, slice_region = compute_patch_slice_k(
            modified_lines, content, k=1,
            analysis_result=analysis_result,
            analyzer=analyzer
        )

        print(f"Slice region (scoped): {sorted(slice_region_scope)}")
        print(f"exe_modified_lines: {sorted(modified_lines)}")
    else:
        print(f"Unsupported file type: {file_key}")
