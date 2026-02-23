# file: go_analyzer.py
"""
Go-specific code analyzer using tree-sitter.
"""

import tree_sitter_go as tsgo
from tree_sitter import Language, Parser
from typing import Dict, Set, Tuple, Any, List

from .base import BaseLanguageAnalyzer, ScopeInfo, ScopeType


# Go executable node types
GO_EXECUTABLE_NODES = {
    "short_var_declaration",
    "assignment_statement",
    "return_statement",
    "if_statement",
    "for_statement",
    "switch_statement",
    "type_switch_statement",
    "select_statement",
    "go_statement",
    "defer_statement",
    "expression_statement",
    "send_statement",
    "inc_statement",
    "dec_statement",
    "function_declaration",
    "method_declaration",
    "labeled_statement",
    "fallthrough_statement",
    "break_statement",
    "continue_statement",
    "goto_statement",
}

# Go scope-defining nodes
GO_SCOPE_NODES = {
    "function_declaration": ScopeType.FUNCTION,
    "method_declaration": ScopeType.METHOD,
    "func_literal": ScopeType.FUNCTION,  # Anonymous function
}

# Global statements that can be ignored
GO_GLOBAL_IGNORABLE_TYPES = {
    "import_declaration",
    "package_clause",
    "comment",
}


class GoAnalyzer(BaseLanguageAnalyzer):
    """Go code analyzer using tree-sitter."""

    def __init__(self):
        super().__init__("go")

    def _init_parser(self):
        """Initialize tree-sitter Go parser."""
        self._tree_sitter_language = Language(tsgo.language())
        self._parser = Parser(self._tree_sitter_language)

    def get_executable_lines(self, src: str, modified_lines: Set[int]) -> Tuple[Set[int], Set[int]]:
        """
        Identify executable lines and correct modified_lines.

        - Maps multi-line function signatures to function declaration line
        """
        tree = self.parse(src)
        lines = set()
        modified = modified_lines.copy()

        def visit(node):
            # Collect executable lines
            if node.type in GO_EXECUTABLE_NODES:
                lines.add(node.start_point[0] + 1)

            # Handle multi-line function/method signatures
            # Function signature lines are never executed and should be removed
            if node.type in ("function_declaration", "method_declaration"):
                func_start = node.start_point[0] + 1  # 1-indexed
                # Find body (block node)
                body = None
                for child in node.children:
                    if child.type == "block":
                        body = child
                        break
                if body:
                    # Go blocks start with {, find the first real statement
                    first_stmt = None
                    for child in body.children:
                        if child.type not in ("{", "}", "comment"):
                            first_stmt = child
                            break
                    if first_stmt:
                        # first_body_ln is the line number of the first body statement (1-indexed)
                        first_body_ln = first_stmt.start_point[0] + 1
                        sig_end = first_body_ln - 1
                    else:
                        sig_end = body.start_point[0]  # line containing { (0-indexed, as fallback)

                    # Remove all modified lines within the signature range
                    for m in list(modified):
                        if func_start <= m <= sig_end:
                            modified.discard(m)

            # Handle multi-line function calls
            # In multi-line calls, only the call's starting line gets executed
            if node.type == "call_expression":
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
        Build mapping from line number to scope.

        Go has functions and methods (no classes in the traditional sense).
        Methods are associated with receiver types.
        """
        tree = self.parse(src)
        scopes: List[ScopeInfo] = []

        def collect_scopes(node, parent_scope=None):
            if node.type == "function_declaration":
                # Get function name
                name = "<anonymous>"
                for child in node.children:
                    if child.type == "identifier":
                        name = child.text.decode("utf-8")
                        break

                scope_info = ScopeInfo(
                    scope_type=ScopeType.FUNCTION,
                    name=name,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    parent=parent_scope
                )
                scopes.append(scope_info)

                for child in node.children:
                    collect_scopes(child, scope_info)

            elif node.type == "method_declaration":
                # Get method name and receiver type
                method_name = "<anonymous>"
                receiver_type = ""

                for child in node.children:
                    if child.type == "identifier":
                        method_name = child.text.decode("utf-8")
                    elif child.type == "parameter_list":
                        # This is the receiver, extract type
                        receiver_type = self._extract_receiver_type(child)

                full_name = f"{receiver_type}.{method_name}" if receiver_type else method_name

                scope_info = ScopeInfo(
                    scope_type=ScopeType.METHOD,
                    name=full_name,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    parent=parent_scope
                )
                scopes.append(scope_info)

                for child in node.children:
                    collect_scopes(child, scope_info)

            elif node.type == "func_literal":
                # Anonymous function
                scope_info = ScopeInfo(
                    scope_type=ScopeType.FUNCTION,
                    name="<anonymous>",
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

        # Sort by scope size (smaller first)
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
                        # Global function: global.func_name
                        qualified_name = f"global.{scope.name}"
                    # Note: METHOD already has ReceiverType.method_name format

                    line2scope[i] = (scope.scope_type.value, qualified_name)
                    # Function/method scope has priority
                    if scope.scope_type in (ScopeType.FUNCTION, ScopeType.METHOD):
                        break

        return line2scope

    def _extract_receiver_type(self, param_list_node) -> str:
        """Extract receiver type from method receiver parameter list."""
        for child in param_list_node.children:
            if child.type == "parameter_declaration":
                # Find the type in the parameter
                for subchild in child.children:
                    if subchild.type == "pointer_type":
                        # *SomeType
                        for ptr_child in subchild.children:
                            if ptr_child.type == "type_identifier":
                                return "*" + ptr_child.text.decode("utf-8")
                    elif subchild.type == "type_identifier":
                        return subchild.text.decode("utf-8")
        return ""

    def build_def_use(self, src: str) -> Tuple[Dict[int, Set[str]], Dict[int, Set[str]]]:
        """
        Build def-use mappings for Go code.
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
                # Skip blank identifier
                if name == "_":
                    return
                if is_lvalue:
                    add(defs, line, name)
                else:
                    add(uses, line, name)
                return

            # Short variable declaration (:=)
            if node.type == "short_var_declaration":
                left = node.child_by_field_name("left")
                right = node.child_by_field_name("right")
                if left:
                    # Left side can be expression_list
                    analyze(left, is_lvalue=True)
                if right:
                    analyze(right, is_lvalue=False)
                return

            # Assignment statement
            if node.type == "assignment_statement":
                left = node.child_by_field_name("left")
                right = node.child_by_field_name("right")
                if left:
                    analyze(left, is_lvalue=True)
                if right:
                    analyze(right, is_lvalue=False)
                return

            # For statement with range
            if node.type == "for_statement":
                # Check for range clause
                for child in node.children:
                    if child.type == "range_clause":
                        left = child.child_by_field_name("left")
                        right = child.child_by_field_name("right")
                        if left:
                            analyze(left, is_lvalue=True)
                        if right:
                            analyze(right, is_lvalue=False)
                # Continue with body
                for child in node.children:
                    if child.type == "block":
                        analyze(child, is_lvalue=False)
                return

            # Var declaration
            if node.type == "var_declaration":
                for child in node.children:
                    if child.type == "var_spec":
                        # Get identifiers (definitions)
                        for spec_child in child.children:
                            if spec_child.type == "identifier":
                                add(defs, spec_child.start_point[0] + 1, spec_child.text.decode("utf-8"))
                            elif spec_child.type == "expression_list":
                                analyze(spec_child, is_lvalue=False)
                return

            # Const declaration
            if node.type == "const_declaration":
                for child in node.children:
                    if child.type == "const_spec":
                        for spec_child in child.children:
                            if spec_child.type == "identifier":
                                add(defs, spec_child.start_point[0] + 1, spec_child.text.decode("utf-8"))
                            elif spec_child.type == "expression_list":
                                analyze(spec_child, is_lvalue=False)
                return

            # Note: Function parameters are NOT recorded as definitions
            # to maintain compatibility with the old ast-based implementation.
            # The old DefUseAnalyzer only tracks variable assignments, not function parameters.
            if node.type == "parameter_list":
                # Skip processing parameters as definitions
                return

            # Recurse
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

        Filters:
        - Package clause
        - Import declarations
        - Simple const/var declarations
        - Type declarations (struct, interface definitions)
        """
        filtered = set()

        for ln in modified_lines:
            scope_type, _ = line2scope.get(ln, ("global", ""))

            # Non-global lines are always kept
            if scope_type != "global":
                filtered.add(ln)
                continue

            node = nodes_by_lineno.get(ln)
            if node is None:
                continue

            # Walk up to find the actual statement node
            while node and node.parent and node.parent.type != "source_file":
                node = node.parent

            if node is None:
                continue

            if self._is_ignorable_global_node(node):
                continue

            filtered.add(ln)

        return filtered

    def _is_ignorable_global_node(self, node) -> bool:
        """Check if a global-level node can be safely ignored."""
        # Package and imports
        if node.type in ("package_clause", "import_declaration"):
            return True

        # Type declarations (struct, interface)
        if node.type == "type_declaration":
            return True

        # Simple const declaration
        if node.type == "const_declaration":
            # Check if it's a simple constant (no complex expressions)
            for child in node.children:
                if child.type == "const_spec":
                    for spec_child in child.children:
                        if spec_child.type == "expression_list":
                            # Check if all expressions are simple literals
                            for expr in spec_child.children:
                                if expr.type not in (
                                    "int_literal", "float_literal", "rune_literal",
                                    "raw_string_literal", "interpreted_string_literal",
                                    "true", "false", "nil", "identifier", "iota"
                                ):
                                    return False
            return True

        # Simple var declaration
        if node.type == "var_declaration":
            for child in node.children:
                if child.type == "var_spec":
                    for spec_child in child.children:
                        if spec_child.type == "expression_list":
                            for expr in spec_child.children:
                                if expr.type not in (
                                    "int_literal", "float_literal", "rune_literal",
                                    "raw_string_literal", "interpreted_string_literal",
                                    "true", "false", "nil", "identifier",
                                    "composite_literal"  # Simple struct/slice literals
                                ):
                                    return False
            return True

        return False
