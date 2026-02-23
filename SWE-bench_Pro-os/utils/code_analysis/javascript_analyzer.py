# file: javascript_analyzer.py
"""
JavaScript-specific code analyzer using tree-sitter.
"""

import tree_sitter_javascript as tsjs
from tree_sitter import Language, Parser
from typing import Dict, Set, Tuple, Any, List

from .base import BaseLanguageAnalyzer, ScopeInfo, ScopeType


# JavaScript executable node types
JS_EXECUTABLE_NODES = {
    "variable_declaration",
    "lexical_declaration",  # let/const
    "expression_statement",
    "return_statement",
    "throw_statement",
    "break_statement",
    "continue_statement",
    "if_statement",
    "for_statement",
    "for_in_statement",
    "for_of_statement",
    "while_statement",
    "do_statement",
    "try_statement",
    "switch_statement",
    "with_statement",
    "labeled_statement",
    "function_declaration",
    "class_declaration",
    "import_statement",
    "export_statement",
    "debugger_statement",
}

# JavaScript scope-defining nodes
JS_SCOPE_NODES = {
    "function_declaration": ScopeType.FUNCTION,
    "function_expression": ScopeType.FUNCTION,
    "arrow_function": ScopeType.FUNCTION,
    "method_definition": ScopeType.METHOD,
    "class_declaration": ScopeType.CLASS,
    "class_expression": ScopeType.CLASS,
}

# Global statements that can be ignored
JS_GLOBAL_IGNORABLE_TYPES = {
    "import_statement",
    "comment",
}


class JavaScriptAnalyzer(BaseLanguageAnalyzer):
    """JavaScript code analyzer using tree-sitter."""

    def __init__(self):
        super().__init__("javascript")

    def _init_parser(self):
        """Initialize tree-sitter JavaScript parser."""
        self._tree_sitter_language = Language(tsjs.language())
        self._parser = Parser(self._tree_sitter_language)

    def _get_executable_nodes(self) -> Set[str]:
        """Get the set of executable node types. Override in subclass for TypeScript."""
        return JS_EXECUTABLE_NODES

    def _get_scope_nodes(self) -> Dict[str, ScopeType]:
        """Get the scope node mappings. Override in subclass for TypeScript."""
        return JS_SCOPE_NODES

    def get_executable_lines(self, src: str, modified_lines: Set[int]) -> Tuple[Set[int], Set[int]]:
        """
        Identify executable lines and correct modified_lines.
        """
        tree = self.parse(src)
        lines = set()
        modified = modified_lines.copy()
        executable_nodes = self._get_executable_nodes()

        def visit(node):
            # Collect executable lines
            if node.type in executable_nodes:
                lines.add(node.start_point[0] + 1)

            # Handle multi-line function signatures
            # Function signature lines are never executed and must be removed
            if node.type in ("function_declaration", "function_expression", "arrow_function"):
                func_start = node.start_point[0] + 1  # 1-indexed
                # Find body
                body = None
                for child in node.children:
                    if child.type in ("statement_block", "expression"):
                        body = child
                        break
                if body and body.type == "statement_block":
                    # JS statement_block starts with {
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
                        sig_end = body.start_point[0]  # fallback

                    for m in list(modified):
                        if func_start <= m <= sig_end:
                            modified.discard(m)

            # Handle multi-line method definitions
            # Method signature lines are never executed
            if node.type == "method_definition":
                method_start = node.start_point[0] + 1  # 1-indexed
                body = None
                for child in node.children:
                    if child.type == "statement_block":
                        body = child
                        break
                if body:
                    first_stmt = None
                    for child in body.children:
                        if child.type not in ("{", "}", "comment"):
                            first_stmt = child
                            break
                    if first_stmt:
                        first_body_ln = first_stmt.start_point[0] + 1
                        sig_end = first_body_ln - 1
                    else:
                        sig_end = body.start_point[0]

                    for m in list(modified):
                        if method_start <= m <= sig_end:
                            modified.discard(m)

            # Handle multi-line call expressions
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
        """
        tree = self.parse(src)
        scopes: List[ScopeInfo] = []
        scope_nodes = self._get_scope_nodes()

        def collect_scopes(node, parent_scope=None):
            if node.type in scope_nodes:
                # Get function/class/method name
                name = "<anonymous>"

                if node.type in ("function_declaration", "class_declaration"):
                    for child in node.children:
                        if child.type == "identifier":
                            name = child.text.decode("utf-8")
                            break

                elif node.type == "method_definition":
                    # Method name is the property_identifier
                    for child in node.children:
                        if child.type == "property_identifier":
                            name = child.text.decode("utf-8")
                            break

                elif node.type == "function_expression":
                    # Named function expression
                    for child in node.children:
                        if child.type == "identifier":
                            name = child.text.decode("utf-8")
                            break

                # For arrow functions, try to get name from parent assignment
                elif node.type == "arrow_function":
                    parent = node.parent
                    if parent:
                        if parent.type == "variable_declarator":
                            for sib in parent.children:
                                if sib.type == "identifier":
                                    name = sib.text.decode("utf-8")
                                    break
                        elif parent.type == "pair":
                            for sib in parent.children:
                                if sib.type in ("property_identifier", "string"):
                                    name = sib.text.decode("utf-8").strip('"\'')
                                    break

                scope_info = ScopeInfo(
                    scope_type=scope_nodes[node.type],
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
                        if scope.parent and scope.parent.scope_type == ScopeType.CLASS:
                            # Method inside a class: ClassName.method_name
                            qualified_name = f"{scope.parent.name}.{scope.name}"
                        else:
                            # Global function: global.func_name
                            qualified_name = f"global.{scope.name}"
                    elif scope.scope_type == ScopeType.METHOD:
                        # Method definition inside class
                        if scope.parent and scope.parent.scope_type == ScopeType.CLASS:
                            qualified_name = f"{scope.parent.name}.{scope.name}"

                    line2scope[i] = (scope.scope_type.value, qualified_name)
                    if scope.scope_type in (ScopeType.FUNCTION, ScopeType.METHOD):
                        break

        return line2scope

    def build_def_use(self, src: str) -> Tuple[Dict[int, Set[str]], Dict[int, Set[str]]]:
        """
        Build def-use mappings for JavaScript code.
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

            # Variable declarator: let x = value
            if node.type == "variable_declarator":
                name_node = node.child_by_field_name("name")
                value_node = node.child_by_field_name("value")
                if name_node:
                    analyze(name_node, is_lvalue=True)
                if value_node:
                    analyze(value_node, is_lvalue=False)
                return

            # Assignment expression
            if node.type == "assignment_expression":
                left = node.child_by_field_name("left")
                right = node.child_by_field_name("right")
                if left:
                    analyze(left, is_lvalue=True)
                if right:
                    analyze(right, is_lvalue=False)
                return

            # Augmented assignment (+=, etc.)
            if node.type == "augmented_assignment_expression":
                left = node.child_by_field_name("left")
                right = node.child_by_field_name("right")
                if left:
                    analyze(left, is_lvalue=True)
                    analyze(left, is_lvalue=False)  # Also used
                if right:
                    analyze(right, is_lvalue=False)
                return

            # Update expression (++, --)
            if node.type == "update_expression":
                for child in node.children:
                    if child.type == "identifier":
                        name = child.text.decode("utf-8")
                        add(defs, child.start_point[0] + 1, name)
                        add(uses, child.start_point[0] + 1, name)
                return

            # For...in/of: for (let x of arr)
            if node.type in ("for_in_statement", "for_of_statement"):
                left = node.child_by_field_name("left")
                right = node.child_by_field_name("right")
                body = node.child_by_field_name("body")
                if left:
                    # Could be variable declaration or identifier
                    analyze(left, is_lvalue=True)
                if right:
                    analyze(right, is_lvalue=False)
                if body:
                    analyze(body, is_lvalue=False)
                return

            # Note: Function parameters are NOT recorded as definitions
            # to maintain compatibility with the old ast-based implementation.
            # The old DefUseAnalyzer only tracks variable assignments, not function parameters.
            if node.type == "formal_parameters":
                # Skip processing parameters as definitions
                return

            # Object/Array destructuring
            if node.type in ("object_pattern", "array_pattern"):
                for child in node.children:
                    analyze(child, is_lvalue=True)
                return

            if node.type == "shorthand_property_identifier_pattern":
                name = node.text.decode("utf-8")
                add(defs, line, name)
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
        """
        filtered = set()

        for ln in modified_lines:
            scope_type, _ = line2scope.get(ln, ("global", ""))

            if scope_type != "global":
                filtered.add(ln)
                continue

            node = nodes_by_lineno.get(ln)
            if node is None:
                continue

            # Walk up to find statement node
            while node and node.parent and node.parent.type != "program":
                node = node.parent

            if node is None:
                continue

            if self._is_ignorable_global_node(node):
                continue

            filtered.add(ln)

        return filtered

    def _is_ignorable_global_node(self, node) -> bool:
        """Check if a global-level node can be safely ignored."""
        # Import statements
        if node.type == "import_statement":
            return True

        # Re-export statements
        if node.type == "export_statement":
            # Check if it's a re-export: export { x } from 'y'
            for child in node.children:
                if child.type == "string":  # from 'module'
                    return True
            # export default or export const x = ... is not ignorable
            return False

        # Simple const/let/var declarations with literals
        if node.type in ("variable_declaration", "lexical_declaration"):
            for child in node.children:
                if child.type == "variable_declarator":
                    value = child.child_by_field_name("value")
                    if value and value.type not in (
                        "string", "number", "true", "false", "null", "undefined",
                        "identifier", "array", "object"
                    ):
                        return False
            return True

        return False
