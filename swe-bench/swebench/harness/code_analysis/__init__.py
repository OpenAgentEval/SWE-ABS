# file: __init__.py
"""
Multi-language code analysis module using tree-sitter.

Provides unified API for analyzing code in Python, Go, JavaScript, and TypeScript.
Supports detecting executable lines, building scope mappings, and def-use analysis.

Usage:
    from swebench.harness.code_analysis import analyze_source, detect_language_from_path

    # Analyze source code
    result = analyze_source(source_code, "python", modified_lines)

    # Or detect language from file path
    language = detect_language_from_path("src/main.py")
    if language:
        result = analyze_source(source_code, language, modified_lines)
"""

from pathlib import Path
from typing import Dict, Optional, Set, Type

from .base import BaseLanguageAnalyzer, AnalysisResult, ScopeType, ScopeInfo
from .python_analyzer import PythonAnalyzer
from .go_analyzer import GoAnalyzer
from .javascript_analyzer import JavaScriptAnalyzer
from .typescript_analyzer import TypeScriptAnalyzer, TSXAnalyzer


# Registry of language analyzers
_ANALYZER_REGISTRY: Dict[str, Type[BaseLanguageAnalyzer]] = {
    # Python
    "python": PythonAnalyzer,
    "py": PythonAnalyzer,

    # Go
    "go": GoAnalyzer,
    "golang": GoAnalyzer,

    # JavaScript
    "javascript": JavaScriptAnalyzer,
    "js": JavaScriptAnalyzer,
    "jsx": JavaScriptAnalyzer,

    # TypeScript
    "typescript": TypeScriptAnalyzer,
    "ts": TypeScriptAnalyzer,
    "tsx": TSXAnalyzer,
}

# File extension to language mapping
_EXTENSION_MAP: Dict[str, str] = {
    ".py": "python",
    ".pyw": "python",
    ".go": "go",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "jsx",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
}


def register_analyzer(language: str, analyzer_class: Type[BaseLanguageAnalyzer]) -> None:
    """
    Register a new language analyzer.

    Args:
        language: Language identifier (e.g., "rust", "java")
        analyzer_class: Class that extends BaseLanguageAnalyzer

    Example:
        from swebench.harness.code_analysis import register_analyzer
        register_analyzer("rust", RustAnalyzer)
    """
    _ANALYZER_REGISTRY[language.lower()] = analyzer_class


def register_extension(extension: str, language: str) -> None:
    """
    Register a file extension to language mapping.

    Args:
        extension: File extension including dot (e.g., ".rs")
        language: Language identifier (e.g., "rust")

    Example:
        register_extension(".rs", "rust")
    """
    _EXTENSION_MAP[extension.lower()] = language.lower()


def get_analyzer(language: str) -> Optional[BaseLanguageAnalyzer]:
    """
    Get an analyzer instance for the specified language.

    Args:
        language: Language identifier (e.g., "python", "go", "js")

    Returns:
        Analyzer instance or None if language is not supported
    """
    language = language.lower()
    analyzer_class = _ANALYZER_REGISTRY.get(language)
    if analyzer_class:
        return analyzer_class()
    return None


def detect_language_from_path(file_path: str) -> Optional[str]:
    """
    Detect programming language from file path extension.

    Args:
        file_path: Path to the source file

    Returns:
        Language identifier or None if not recognized
    """
    ext = Path(file_path).suffix.lower()
    return _EXTENSION_MAP.get(ext)


def is_language_supported(language: str) -> bool:
    """
    Check if a language is supported.

    Args:
        language: Language identifier

    Returns:
        True if language is supported
    """
    return language.lower() in _ANALYZER_REGISTRY


def get_supported_languages() -> Set[str]:
    """
    Get set of supported language identifiers.

    Returns:
        Set of language names (deduplicated)
    """
    # Return unique language names (not aliases)
    unique_languages = set()
    seen_classes = set()
    for lang, cls in _ANALYZER_REGISTRY.items():
        if cls not in seen_classes:
            unique_languages.add(lang)
            seen_classes.add(cls)
    return unique_languages


def get_supported_extensions() -> Set[str]:
    """
    Get set of supported file extensions.

    Returns:
        Set of file extensions
    """
    return set(_EXTENSION_MAP.keys())


def analyze_source(
    src: str,
    language: str,
    modified_lines: Set[int]
) -> AnalysisResult:
    """
    Analyze source code and return comprehensive analysis result.

    Args:
        src: Source code content
        language: Language identifier (e.g., "python", "go", "js", "ts")
        modified_lines: Set of modified line numbers from patch

    Returns:
        AnalysisResult containing:
        - executable_lines: Set of executable line numbers
        - modified_lines: Corrected modified lines (mapped from multi-line constructs)
        - line_to_scope: Mapping from line to (scope_type, scope_name)
        - defs: Mapping from line to defined variable names
        - uses: Mapping from line to used variable names
        - nodes_by_lineno: Mapping from line to tree-sitter nodes

    Raises:
        ValueError: If language is not supported
    """
    analyzer = get_analyzer(language)
    if not analyzer:
        raise ValueError(
            f"Unsupported language: {language}. "
            f"Supported languages: {', '.join(sorted(get_supported_languages()))}"
        )

    return analyzer.analyze(src, modified_lines)


# Export public API
__all__ = [
    # Main API functions
    "analyze_source",
    "detect_language_from_path",
    "get_analyzer",
    "is_language_supported",
    "get_supported_languages",
    "get_supported_extensions",

    # Registration functions
    "register_analyzer",
    "register_extension",

    # Data classes
    "AnalysisResult",
    "ScopeType",
    "ScopeInfo",
    "BaseLanguageAnalyzer",

    # Analyzer classes (for extension)
    "PythonAnalyzer",
    "GoAnalyzer",
    "JavaScriptAnalyzer",
    "TypeScriptAnalyzer",
    "TSXAnalyzer",
]
