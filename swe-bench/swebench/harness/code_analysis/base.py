# file: base.py
"""
Base classes and data structures for multi-language code analysis using tree-sitter.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Set, Tuple, Optional, Any
from enum import Enum


class ScopeType(Enum):
    """Types of code scopes across different languages."""
    GLOBAL = "global"
    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"       # For Go struct methods, JS/TS class methods
    INTERFACE = "interface" # For TypeScript/Go interfaces


@dataclass
class ScopeInfo:
    """Information about a code scope (function, class, etc.)."""
    scope_type: ScopeType
    name: str
    start_line: int
    end_line: int
    parent: Optional['ScopeInfo'] = None


@dataclass
class AnalysisResult:
    """Result of analyzing source code."""
    executable_lines: Set[int]
    modified_lines: Set[int]  # Corrected modified lines (mapped back from multi-line constructs)
    line_to_scope: Dict[int, Tuple[str, str]]  # line -> (scope_type, scope_name)
    defs: Dict[int, Set[str]]  # line -> defined variables
    uses: Dict[int, Set[str]]  # line -> used variables
    nodes_by_lineno: Dict[int, Any] = field(default_factory=dict)  # line -> tree-sitter node


class BaseLanguageAnalyzer(ABC):
    """
    Abstract base class for language-specific code analyzers.

    Each language (Python, Go, JavaScript, TypeScript) should implement this class
    to provide language-specific code analysis using tree-sitter.
    """

    def __init__(self, language: str):
        self.language = language
        self._parser = None
        self._tree_sitter_language = None

    @abstractmethod
    def _init_parser(self):
        """Initialize tree-sitter parser for this language."""
        pass

    @property
    def parser(self):
        """Lazy initialization of parser."""
        if self._parser is None:
            self._init_parser()
        return self._parser

    def parse(self, src: str):
        """Parse source code and return tree-sitter tree."""
        return self.parser.parse(bytes(src, "utf-8"))

    @abstractmethod
    def get_executable_lines(self, src: str, modified_lines: Set[int]) -> Tuple[Set[int], Set[int]]:
        """
        Identify executable lines and correct modified_lines for multi-line constructs.

        Args:
            src: Source code content
            modified_lines: Set of modified line numbers from patch

        Returns:
            Tuple of (executable_lines, corrected_modified_lines)
        """
        pass

    @abstractmethod
    def build_line_scope(self, src: str) -> Dict[int, Tuple[str, str]]:
        """
        Build mapping from line number to scope information.

        Args:
            src: Source code content

        Returns:
            Dict mapping line number to (scope_type, scope_name)
            scope_type is one of: "global", "function", "class", "method", "interface"
        """
        pass

    @abstractmethod
    def build_def_use(self, src: str) -> Tuple[Dict[int, Set[str]], Dict[int, Set[str]]]:
        """
        Build def-use mappings for variable analysis.

        Args:
            src: Source code content

        Returns:
            Tuple of (defs, uses) where:
            - defs: Dict[line, Set[variable_names]] for defined variables
            - uses: Dict[line, Set[variable_names]] for used variables
        """
        pass

    @abstractmethod
    def get_nodes_by_lineno(self, src: str) -> Dict[int, Any]:
        """
        Get tree-sitter nodes indexed by line number.

        Args:
            src: Source code content

        Returns:
            Dict mapping line number to tree-sitter node(s)
        """
        pass

    @abstractmethod
    def filtered_global_modified(
        self,
        line2scope: Dict[int, Tuple[str, str]],
        nodes_by_lineno: Dict[int, Any],
        modified_lines: Set[int]
    ) -> Set[int]:
        """
        Filter out insignificant global-level statements.

        Args:
            line2scope: Mapping from line to scope info
            nodes_by_lineno: Mapping from line to tree-sitter nodes
            modified_lines: Set of modified line numbers

        Returns:
            Filtered set of modified lines with semantic significance
        """
        pass

    def analyze(self, src: str, modified_lines: Set[int]) -> AnalysisResult:
        """
        Perform full analysis on source code.

        Args:
            src: Source code content
            modified_lines: Set of modified line numbers from patch

        Returns:
            AnalysisResult with all analysis data
        """
        executable_lines, corrected_modified = self.get_executable_lines(src, modified_lines.copy())
        line_to_scope = self.build_line_scope(src)
        defs, uses = self.build_def_use(src)
        nodes_by_lineno = self.get_nodes_by_lineno(src)

        return AnalysisResult(
            executable_lines=executable_lines,
            modified_lines=corrected_modified,
            line_to_scope=line_to_scope,
            defs=defs,
            uses=uses,
            nodes_by_lineno=nodes_by_lineno
        )
