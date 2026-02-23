# file: patch_analysis.py

import ast
from collections import defaultdict
import copy
import json
import re
from pathlib import Path
from unidiff import PatchSet
from typing import List, Dict, Set, Tuple, Union, Iterable
from swebench.harness.constants import (
    DOCKER_USER,
    DOCKER_WORKDIR,
)



def filtered_global_modified(
    line2scope: dict[int, tuple[str, str]],
    nodes_by_lineno: dict[int, Union[ast.AST, Iterable[ast.AST]]],
    modified_lines: set[int],
) -> set[int]:
    """
        Filter out "ignorable global-level statements", retaining only modified lines with real semantic significance.
    """

    # ---- Helper: normalize single or multiple nodes into an iterable ----
    def iter_nodes(ln: int):
        n = nodes_by_lineno.get(ln)
        if n is None:
            return ()
        if isinstance(n, ast.AST):
            return (n,)
        try:
            return tuple(n)
        except TypeError:
            return (n,)

    # ---- Helper: check if expression is simple ----
    SIMPLE_EXPR_TYPES = (
        ast.Constant, ast.Name, ast.Attribute, ast.Call, ast.BinOp
    )

    def is_simple_expr(node: ast.AST) -> bool:
        return isinstance(node, SIMPLE_EXPR_TYPES)

    # ---- Check if a node belongs to the top-level whitelist ----
    def is_whitelisted_global_node(node: ast.AST) -> bool:
        """
            True = ignorable (discard)
                    False = has semantic meaning (keep)
        """

        # 1. import statements
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return True

        # 2. regular assignments
        if isinstance(node, ast.Assign):
            # single target with a simple rhs expression
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                if is_simple_expr(node.value):
                    return True
            return False

        # 3. annotated assignments
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                # X: int or X: int = <simple>
                if node.value is None or is_simple_expr(node.value):
                    return True
            return False

        # 4. Expr(...) top-level expression statements
        if isinstance(node, ast.Expr):
            v = node.value
            # bare constant (docstring or meaningless literal)
            if isinstance(v, ast.Constant):
                return True
            # other exprs (call, attribute, name, binop) are all semantically meaningful
            return False

        # 5. bare constant (some AST versions may yield Constant directly)
        if isinstance(node, ast.Constant):
            return True

        # everything else (If, Try, With, Call Expr, Decorator, FunctionDef, ClassDef, etc.)
        return False

    # ---- main loop ----
    filtered = set()

    for ln in modified_lines:
        scope_type, _ = line2scope.get(ln, ("global", ""))

        # non-global (inside function or class) -> always keep
        if scope_type != "global":
            filtered.add(ln)
            continue

        # global scope: check if all nodes are whitelisted
        ns = iter_nodes(ln)
        if not ns:
            # blank line / comment -> can be ignored
            continue

        # If any node is not whitelisted -> keep
        for node in ns:
            if not is_whitelisted_global_node(node):
                filtered.add(ln)
                break

        # If all nodes are whitelisted -> ignore (do not include)

    return filtered



def parse_patch_log(log_content: str) -> dict:
    # Regex: match hunk header lines
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

        # Check if line is a hunk header -> extract hunk info
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
            file_offset = offset_dict[file_path]

            
        for idx, hunk in enumerate(patched_file):
            offset_num = 0
            if file_offset and str(idx+1) in file_offset:
                applied_at_line = file_offset[str(idx+1)]['applied_at_line']
                offset_num = applied_at_line - hunk.target_start

            for line in hunk:
                if line.is_added:
                    # target_line_no is the line number of the added line
                    modified_info[file_path].add(line.target_line_no+offset_num)

        # Skip if an empty file was added
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


# ---------- AST executable line analysis ----------
EXECUTABLE_NODES = (
    ast.Assign, ast.AugAssign, ast.AnnAssign,
    ast.Return, ast.Raise, ast.Assert,
    ast.Expr,       # but skip docstring
    ast.If, ast.For, ast.While, ast.Try, ast.With,
    ast.FunctionDef, ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Import, ast.ImportFrom,
)


def build_line_scope(src: str):
    """
        Returns line -> ('function', func_name) / ('class', class_name) / ('global', '__global__')
            Priority: function > class > global
    """
    tree = ast.parse(src)
    line2scope = {}

    class StackFrame:
        def __init__(self, type_, name, start, end):
            self.type = type_  # 'function' or 'class'
            self.name = name
            self.start = start
            self.end = end

    # Collect the ranges of all functions and classes
    scopes = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scopes.append(StackFrame("function", node.name, node.lineno, node.end_lineno))
        elif isinstance(node, ast.ClassDef):
            scopes.append(StackFrame("class", node.name, node.lineno, node.end_lineno))

    # Sort by range length ascending (nested functions matched first)
    scopes.sort(key=lambda x: (x.end - x.start, x.start))

    lines = src.splitlines()
    for i in range(1, len(lines) + 1):
        # default to global scope
        line2scope[i] = ("global", "__global__")

        for s in scopes:
            if s.start <= i <= s.end:
                # range matched -> override global
                line2scope[i] = (s.type, s.name)
                # function scope takes priority, so break immediately
                if s.type == "function":
                    break
                
    return line2scope




def get_executable_lines(src: str, modified_lines: Set[int]) -> Set[int]:
    """
    Return executable lines and correct modified_lines so that if a modified
    line falls inside a multi-line call or function signature, it maps back to
    the call/def start line.
    """
    tree = ast.parse(src)
    lines = set()

    for node in ast.walk(tree):

        # 1. Collect executable start lines (using EXECUTABLE_NODES logic)
        if isinstance(node, EXECUTABLE_NODES):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Str):
                continue
            lines.add(node.lineno)

        # ----------------------------
        # 2. Adjust argument ranges for def/async def function definitions
        # ----------------------------
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            def_start = node.lineno

            # Argument line range: from the def line to the line before the first body statement
            if node.body:
                first_body_ln = node.body[0].lineno
                sig_end = first_body_ln - 1
            else:
                sig_end = getattr(node, "end_lineno", def_start)

            for m in list(modified_lines):
                if def_start <= m <= sig_end:
                    modified_lines.remove(m)

        # ----------------------------
        # 3. Adjust argument ranges for ast.Call function calls
        # ----------------------------
        elif isinstance(node, ast.Call):
            call_start = node.lineno
            call_end = getattr(node, "end_lineno", call_start)

            for m in list(modified_lines):
                if call_start <= m <= call_end:
                    modified_lines.remove(m)
                    modified_lines.add(call_start)

    return lines,modified_lines


# ---------- Def-Use analysis ----------
class DefUseAnalyzer(ast.NodeVisitor):
    def __init__(self):
        self.defs = {}  # line -> set of variables defined
        self.uses = {}  # line -> set of variables used

    def add(self, mapping, lineno, name):
        mapping.setdefault(lineno, set()).add(name)

    def visit_Name(self, node: ast.Name):
        if isinstance(node.ctx, ast.Store):
            self.add(self.defs, node.lineno, node.id)
        elif isinstance(node.ctx, ast.Load):
            self.add(self.uses, node.lineno, node.id)
        self.generic_visit(node)


def build_def_use(src: str):
    """
    Returns defs, uses mapping for the source code.
    """
    tree = ast.parse(src)
    analyzer = DefUseAnalyzer()
    analyzer.visit(tree)
    return analyzer.defs, analyzer.uses


# ---------- automatic slicing ----------
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
        # 1. Collect def/use info for frontier lines
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
        # 2. Iterate over all lines to find which new lines are affected by propagation
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


def compute_patch_slice_k(modified_lines: set[int], 
                          src: str,
                          k: int = 2,
                          limit_scope: bool = True) -> set[int]:
    """
        Compute repair-influence slice with depth limit k.
            Supports forward/backward and scope limiting (function/class/global).
    """
    # 1. Build the scope mapping for each line
    line2scope = build_line_scope(src)

    # 2. Build defs / uses mappings
    defs, uses = build_def_use(src)

    # 3. Forward slice
    fwd_full = slice_engine_k(
        modified_lines,
        defs,
        uses,
        k=k,
        line_scope=line2scope,
        direction="forward",
        limit_scope=False
    ) 
    

    # print("fwd:",sorted(list(fwd)))
    # 4. Backward slice

    bwd_full = slice_engine_k(
        modified_lines,
        defs,
        uses,
        k=k,
        line_scope=line2scope,
        direction="backward",
        limit_scope=False
    )

    tree = ast.parse(src)
    nodes_by_lineno = {
        node.lineno: node
        for node in ast.walk(tree)
        if hasattr(node, "lineno")
    }

    filtered_modified = filtered_global_modified(line2scope, nodes_by_lineno, modified_lines)

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


    print("fwd:",sorted(list(fwd)))
    # 5. Merge results
    return fwd | bwd , fwd_full | bwd_full


def compute_must_coverage(container,patch,save_dir,logger,patch_log):

    offset_dict = parse_patch_log(patch_log)
    modified_info = parse_modified_info(patch,offset_dict)
    modified_info:dict[str, set[int]]
    logger.info(modified_info)
    must_coverage = {}
    dumps = dump_modified_files(container, list(modified_info.keys()), save_dir)

    for file_path, content in dumps:
        if not file_path.endswith(".py"):
            continue
        # Get executable lines and update line numbers in modified_info for params in def or call
        executable_lines, modified_lines = get_executable_lines(content,modified_info[file_path])

        # Compute slice using updated modified_info
        slice_region_scope, slice_region = compute_patch_slice_k(modified_lines, content, k=1)
        exe_slice_lines = slice_region & executable_lines
        exe_slice_lines_scope = slice_region_scope & executable_lines
        exe_modified_lines = modified_lines & executable_lines
        must_coverage[file_path] = {
            "exe_slice_lines_scope": sorted(exe_slice_lines_scope),
            "exe_slice_lines": sorted(exe_slice_lines),
            "exe_modified_lines": sorted(exe_modified_lines),
            "content": content
        }

    return must_coverage


def parse_trace_log(output_path: str):
    if not Path(output_path).exists():
        return {}
    with open(output_path, "r") as f:
        eval_output = f.readlines()

    coverage = {}

    for i, line in enumerate(eval_output):
        if line.strip() == "+ cat coverage.cover":
            break
    for line in eval_output[i+1:]:
        if not line.startswith("{\"/testbed"):
            continue

        try:
            d = json.loads(line.strip())
            for file_name, file_coverage in d.items():
                key = file_name.replace("/testbed/", "")
                exe_lines = set()
                if key in coverage:
                    exe_lines = coverage[key]["executed_lines"]
                for line_id, line_coverage in file_coverage.items():
                    if line_coverage>0:
                        exe_lines.add(int(line_id))
                
                coverage[key] = {"executed_lines": exe_lines}
        except json.JSONDecodeError:
            continue
    return coverage 


def compute_coverage(output_path, modified_related_lines, use_key = "exe_slice_lines_scope"):
    
    if len(modified_related_lines) == 0:
        return 1, {}
    
    trace_coverage = parse_trace_log(output_path)

    if len(trace_coverage) == 0:
        return 404, {}
    
    total_avg = 0
    un_hit_lines_content = defaultdict(list)
    for file_name in modified_related_lines:
        lines = set(modified_related_lines[file_name][use_key])
        if len(lines) == 0:
            continue
        trace_exe_lines = set(trace_coverage.get(file_name, {}).get('executed_lines', set()))
        un_hit_lines = lines - trace_exe_lines
        if len(un_hit_lines) == 0:
            total_avg += 1
            continue
        total_avg += (1 - len(un_hit_lines) / len(lines))
        content = modified_related_lines[file_name]["content"].split("\n")
        # Extract unexecuted lines
        for line in sorted(list(un_hit_lines)):
            un_hit_lines_content[file_name].append((line,content[line-1]))
    total_avg /= len(modified_related_lines)
    if len(un_hit_lines_content) == 0:
        return 1.0, {}

    return round(total_avg, 3), dict(un_hit_lines_content)



# ---------- Example usage ----------
if __name__ == "__main__":
    instance_id = 'sympy__sympy-24562'
    instance_path = Path(f"/home/ddq/CaoYang/SWE-PLUS/swe-bench/logs/extract_line_number/validate-gold/{instance_id}")
    

    patch_file = instance_path / "patch.diff"
    log_file = instance_path / "run_instance.log"
    patch = patch_file.read_text()
    file_path = "sympy__core__numbers.py"
    modified_file = instance_path / "modified_files" / f"{file_path}.after"



    offset_dict = parse_patch_log(log_file.read_text())

    file_key = file_path.replace("__", "/")
    content = modified_file.read_text()
    modified_info = parse_modified_info(patch,offset_dict)
    modified_info_copy = copy.deepcopy(modified_info)

    executable_lines,modified_lines = get_executable_lines(content,modified_info[file_key])
    slice_region_scope, slice_region = compute_patch_slice_k(modified_lines, content, k=1)


    # print(f"old Slice region (scoped): {sorted(slice_region_scope)}")
    # print(f"old Slice region (full): {sorted(slice_region)}")
    # print(f"old exe_modified_lines: {sorted(modified_lines)}")

    print(f"old Slice region (scoped): {sorted(slice_region_scope)}")
    # print(f'old lines (scoped):{old_lines[instance_id][file_key]["exe_slice_lines_scope"]}')

    # print(f"old Slice region (full): {sorted(slice_region)}")
    # print(f'old lines (full):{old_lines[instance_id][file_key]["exe_slice_lines"]}')

    print(f"old exe_modified_lines: {sorted(modified_lines)}")
    # print(f"exe_modified_lines: {sorted(executable_lines)}")

    # [3267, 3270, 3271, 3650, 3653, 3654]

    # slice_region,executable_lines = compute_patch_slice_k(diff_lines, src_code)

    # output = "/root/CaoYang/SWE-PLUS/swe-bench/logs/aug_test_center/eval_gold_patch/multi_run_5_hard_code_coverage/sympy__sympy-11618/gold_patch/test_output.txt"

    # coverage = parse_trace_log(output)
    # print(coverage)

