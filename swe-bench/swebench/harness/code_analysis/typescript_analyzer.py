# file: typescript_analyzer.py
"""
TypeScript-specific code analyzer using tree-sitter.
Extends JavaScript analyzer with TypeScript-specific features.
"""

import tree_sitter_typescript as tsts
from tree_sitter import Language, Parser
from typing import Dict, Set, Tuple, Any

from .base import ScopeType
from .javascript_analyzer import JavaScriptAnalyzer, JS_EXECUTABLE_NODES, JS_SCOPE_NODES


# TypeScript extends JavaScript executable nodes
TS_EXECUTABLE_NODES = JS_EXECUTABLE_NODES | {
    "type_alias_declaration",
    "interface_declaration",
    "enum_declaration",
    "ambient_declaration",
    "module",  # namespace/module declarations
}

# TypeScript extends JavaScript scope nodes
TS_SCOPE_NODES = {
    **JS_SCOPE_NODES,
    "interface_declaration": ScopeType.INTERFACE,
    "module": ScopeType.FUNCTION,  # Treat namespace as function scope
}

# TypeScript type-only declarations that can be ignored
TS_TYPE_ONLY_NODES = {
    "type_alias_declaration",
    "interface_declaration",
    "ambient_declaration",
}


class TypeScriptAnalyzer(JavaScriptAnalyzer):
    """
    TypeScript code analyzer using tree-sitter.
    Extends JavaScript analyzer with TypeScript-specific features.
    """

    def __init__(self, use_tsx: bool = False):
        """
        Initialize TypeScript analyzer.

        Args:
            use_tsx: If True, use TSX parser for React TypeScript files
        """
        super().__init__()
        self.language = "typescript"
        self.use_tsx = use_tsx

    def _init_parser(self):
        """Initialize tree-sitter TypeScript parser."""
        if self.use_tsx:
            self._tree_sitter_language = Language(tsts.language_tsx())
        else:
            self._tree_sitter_language = Language(tsts.language_typescript())
        self._parser = Parser(self._tree_sitter_language)

    def _get_executable_nodes(self) -> Set[str]:
        """Get TypeScript executable node types."""
        return TS_EXECUTABLE_NODES

    def _get_scope_nodes(self) -> Dict[str, ScopeType]:
        """Get TypeScript scope node mappings."""
        return TS_SCOPE_NODES

    def filtered_global_modified(
        self,
        line2scope: Dict[int, Tuple[str, str]],
        nodes_by_lineno: Dict[int, Any],
        modified_lines: Set[int]
    ) -> Set[int]:
        """
        Filter out "ignorable" global-level statements.

        TypeScript-specific: also filters type-only declarations.
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
        """
        Check if a global-level node can be safely ignored.

        TypeScript-specific: type declarations have no runtime effect.
        """
        # Use parent class for common cases
        if super()._is_ignorable_global_node(node):
            return True

        # TypeScript type-only declarations
        if node.type in TS_TYPE_ONLY_NODES:
            return True

        # Enum declarations - they do have runtime effect, but are often just configuration
        # Keep them for now, can be filtered if needed
        # if node.type == "enum_declaration":
        #     return True

        return False


class TSXAnalyzer(TypeScriptAnalyzer):
    """TSX (TypeScript + JSX) code analyzer."""

    def __init__(self):
        super().__init__(use_tsx=True)
        self.language = "tsx"
