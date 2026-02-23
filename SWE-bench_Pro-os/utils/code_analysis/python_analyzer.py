# file: python_analyzer.py
"""
Python-specific code analyzer using tree-sitter.
"""

import tree_sitter_python as tspython
from tree_sitter import Language, Parser
from typing import Dict, Set, Tuple, Any, List

from .base import BaseLanguageAnalyzer, ScopeInfo, ScopeType


# Python executable node types
PYTHON_EXECUTABLE_NODES = {
    "assignment",
    "augmented_assignment",
    "expression_statement",
    "return_statement",
    "raise_statement",
    "assert_statement",
    "pass_statement",
    "break_statement",
    "continue_statement",
    "if_statement",
    "for_statement",
    "while_statement",
    "try_statement",
    "with_statement",
    "match_statement",
    "function_definition",
    "async_function_definition",
    "class_definition",
    "import_statement",
    "import_from_statement",
    "global_statement",
    "nonlocal_statement",
    "delete_statement",
}

# Python scope-defining nodes
PYTHON_SCOPE_NODES = {
    "function_definition": ScopeType.FUNCTION,
    "async_function_definition": ScopeType.FUNCTION,
    "class_definition": ScopeType.CLASS,
}

# Global statements that can be ignored (imports, simple assignments, docstrings)
PYTHON_GLOBAL_IGNORABLE_TYPES = {
    "import_statement",
    "import_from_statement",
    "comment",
}


class PythonAnalyzer(BaseLanguageAnalyzer):
    """Python code analyzer using tree-sitter."""

    def __init__(self):
        super().__init__("python")

    def _init_parser(self):
        """Initialize tree-sitter Python parser."""
        self._tree_sitter_language = Language(tspython.language())
        self._parser = Parser(self._tree_sitter_language)

    def get_executable_lines(self, src: str, modified_lines: Set[int]) -> Tuple[Set[int], Set[int]]:
        """
        Identify executable lines and correct modified_lines.

        - Skips docstrings (string as first expression)
        - Maps multi-line function signatures to the function definition line
        - Maps multi-line call arguments to the call start line
        """
        tree = self.parse(src)
        lines = set()
        modified = modified_lines.copy()

        def visit(node):
            # Collect executable lines
            if node.type in PYTHON_EXECUTABLE_NODES:
                # Skip docstrings
                if node.type == "expression_statement":
                    if node.children and node.children[0].type == "string":
                        # Check if it's a docstring (first statement in function/class/module)
                        parent = node.parent
                        if parent and parent.type in ("block", "module"):
                            siblings = [c for c in parent.children if c.type != "comment"]
                            if siblings and siblings[0] == node:
                                # This is a docstring, skip it
                                for child in node.children:
                                    visit(child)
                                return
                lines.add(node.start_point[0] + 1)  # 0-indexed to 1-indexed

            # Handle multi-line function signatures
            # Function signature lines (from def to before body start) are never executed and must be removed
            if node.type in ("function_definition", "async_function_definition"):
                func_start = node.start_point[0] + 1  # 1-indexed
                # Find body (block node)
                body = None
                for child in node.children:
                    if child.type == "block":
                        body = child
                        break
                if body:
                    # Find the first real statement in the body
                    first_stmt = None
                    for child in body.children:
                        # Skip colons and comments, find the first actual statement
                        if child.type not in (":", "comment"):
                            first_stmt = child
                            break
                    if first_stmt:
                        # first_body_ln is the line number of the first body statement (1-indexed)
                        first_body_ln = first_stmt.start_point[0] + 1
                        # sig_end = first_body_ln - 1, consistent with old logic
                        sig_end = first_body_ln - 1
                    else:
                        sig_end = node.end_point[0] + 1  # 1-indexed

                    # Remove all modified lines within the signature range (including the def line itself)
                    for m in list(modified):
                        if func_start <= m <= sig_end:
                            modified.discard(m)

            # Handle multi-line calls
            # In multi-line calls, only the starting line gets executed; other lines must be mapped to it
            if node.type == "call":
                call_start = node.start_point[0] + 1
                call_end = node.end_point[0] + 1
                for m in list(modified):
                    if call_start <= m <= call_end:
                        modified.discard(m)
                        modified.add(call_start)

            for child in node.children:
                visit(child)

        visit(tree.root_node)
        return lines, modified

    def build_line_scope(self, src: str) -> Dict[int, Tuple[str, str]]:
        """
        Build mapping from line number to scope (function/class/global).

        Priority: function > class > global
        """
        tree = self.parse(src)
        scopes: List[ScopeInfo] = []

        def collect_scopes(node, parent_scope=None):
            if node.type in PYTHON_SCOPE_NODES:
                # Get function/class name
                name = "<anonymous>"
                for child in node.children:
                    if child.type == "identifier":
                        name = child.text.decode("utf-8")
                        break

                scope_info = ScopeInfo(
                    scope_type=PYTHON_SCOPE_NODES[node.type],
                    name=name,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    parent=parent_scope
                )
                scopes.append(scope_info)

                for child in node.children:
                    collect_scopes(child, scope_info)
            else:
                for child in node.children:
                    collect_scopes(child, parent_scope)

        collect_scopes(tree.root_node)

        # Sort by scope size (smaller first) so inner scopes override outer
        scopes.sort(key=lambda s: (s.end_line - s.start_line, s.start_line))

        total_lines = len(src.splitlines())
        line2scope: Dict[int, Tuple[str, str]] = {}

        for i in range(1, total_lines + 1):
            line2scope[i] = ("global", "__global__")

            for scope in scopes:
                if scope.start_line <= i <= scope.end_line:
                    # Build qualified name with prefix to distinguish same-name functions
                    qualified_name = scope.name
                    if scope.scope_type == ScopeType.FUNCTION:
                        if scope.parent and scope.parent.scope_type == ScopeType.CLASS:
                            # Method inside a class: ClassName.method_name
                            qualified_name = f"{scope.parent.name}.{scope.name}"
                        else:
                            # Global function: global.func_name
                            qualified_name = f"global.{scope.name}"

                    line2scope[i] = (scope.scope_type.value, qualified_name)
                    # Function scope has priority, so break if we hit a function
                    if scope.scope_type == ScopeType.FUNCTION:
                        break

        return line2scope

    def build_def_use(self, src: str) -> Tuple[Dict[int, Set[str]], Dict[int, Set[str]]]:
        """
        Build def-use mappings for Python code.

        Definitions: assignments, function parameters, for loop variables, etc.
        Uses: variable references in expressions
        """
        tree = self.parse(src)
        defs: Dict[int, Set[str]] = {}
        uses: Dict[int, Set[str]] = {}

        def add(mapping: Dict[int, Set[str]], line: int, name: str):
            if line not in mapping:
                mapping[line] = set()
            mapping[line].add(name)

        def analyze(node, is_lvalue=False):
            line = node.start_point[0] + 1

            if node.type == "identifier":
                name = node.text.decode("utf-8")
                if is_lvalue:
                    add(defs, line, name)
                else:
                    add(uses, line, name)
                return

            # Assignment: left side is def, right side is use
            if node.type == "assignment":
                left = node.child_by_field_name("left")
                right = node.child_by_field_name("right")
                if left:
                    analyze(left, is_lvalue=True)
                if right:
                    analyze(right, is_lvalue=False)
                return

            # Augmented assignment (+=, etc.): both def and use
            if node.type == "augmented_assignment":
                left = node.child_by_field_name("left")
                right = node.child_by_field_name("right")
                if left:
                    analyze(left, is_lvalue=True)
                    analyze(left, is_lvalue=False)  # Also a use
                if right:
                    analyze(right, is_lvalue=False)
                return

            # For loop: loop variable is a definition
            if node.type == "for_statement":
                left = node.child_by_field_name("left")
                right = node.child_by_field_name("right")
                if left:
                    analyze(left, is_lvalue=True)
                if right:
                    analyze(right, is_lvalue=False)
                # Continue with body
                for child in node.children:
                    if child.type == "block":
                        analyze(child, is_lvalue=False)
                return

            # Note: Function parameters are NOT recorded as definitions
            # to maintain compatibility with the old ast-based implementation.
            # The old DefUseAnalyzer only tracks ast.Name nodes, not ast.arg nodes.
            if node.type == "parameters":
                # Skip processing parameters as definitions
                return

            # Named expression (:= walrus)
            if node.type == "named_expression":
                name_node = node.child_by_field_name("name")
                value_node = node.child_by_field_name("value")
                if name_node:
                    analyze(name_node, is_lvalue=True)
                if value_node:
                    analyze(value_node, is_lvalue=False)
                return

            # Recurse for other nodes
            for child in node.children:
                analyze(child, is_lvalue)

        analyze(tree.root_node)
        return defs, uses

    def get_nodes_by_lineno(self, src: str) -> Dict[int, Any]:
        """Get tree-sitter nodes indexed by line number."""
        tree = self.parse(src)
        nodes_by_lineno: Dict[int, Any] = {}

        def collect(node):
            line = node.start_point[0] + 1
            if line not in nodes_by_lineno:
                nodes_by_lineno[line] = node
            for child in node.children:
                collect(child)

        collect(tree.root_node)
        return nodes_by_lineno

    def filtered_global_modified(
        self,
        line2scope: Dict[int, Tuple[str, str]],
        nodes_by_lineno: Dict[int, Any],
        modified_lines: Set[int]
    ) -> Set[int]:
        """
        Filter out "ignorable" global-level statements.

        Keeps:
        - All non-global scope lines
        - Semantically significant global statements

        Filters:
        - Import statements
        - Simple constant assignments
        - Docstrings
        """
        filtered = set()

        for ln in modified_lines:
            scope_type, _ = line2scope.get(ln, ("global", ""))

            # Non-global lines are always kept
            if scope_type != "global":
                filtered.add(ln)
                continue

            # Check the node at this line
            node = nodes_by_lineno.get(ln)
            if node is None:
                # Empty line or comment - skip
                continue

            # Walk up to find the actual statement node
            while node and node.parent and node.parent.type not in ("module", "block"):
                node = node.parent

            if node is None:
                continue

            # Check if this is an ignorable global statement
            if self._is_ignorable_global_node(node):
                continue

            filtered.add(ln)

        return filtered

    def _is_ignorable_global_node(self, node) -> bool:
        """
        Check if a global-level node can be safely ignored.

        Returns True for:
        - Import statements
        - Simple assignments (constant = value)
        - Docstrings
        """
        # Import statements
        if node.type in ("import_statement", "import_from_statement"):
            return True

        # Simple assignment
        if node.type == "assignment":
            # Check if it's a simple assignment (single target, simple value)
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            if left and left.type == "identifier":
                if right and right.type in (
                    "string", "integer", "float", "true", "false", "none",
                    "identifier", "attribute", "list", "dictionary", "tuple", "set"
                ):
                    return True
            return False

        # Expression statement (could be docstring)
        if node.type == "expression_statement":
            if node.children and node.children[0].type == "string":
                return True
            return False

        # Other statements have semantic significance
        return False
