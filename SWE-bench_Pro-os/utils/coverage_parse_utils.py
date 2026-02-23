"""
Unified Coverage Parser for Multiple Languages

Supports:
- Python: coverage.py JSON format (coverage.json)
- Go: go test coverprofile format (coverage.out)
- JavaScript: Istanbul/nyc JSON format (coverage-final.json)
- TypeScript: V8 coverage format (v8-coverage/*.json)
  Note: V8 coverage collects compiled JS files, not source .ts files.
  For accurate TS coverage, use istanbul/nyc with ts-node or source maps.

Output format:
{
    "language": "python|go|javascript|typescript",
    "files": {
        "path/to/file.py": {
            "executed_lines": [1, 2, 3, ...],
            "missing_lines": [4, 5, 6, ...]
        },
        ...
    }
}

Usage:
    # Parse single instance
    from coverage_parse_utils import parse_coverage, compute_coverage

    coverage = parse_coverage("logs/.../instance_xxx")

    # With modified_related_lines from exe_line_all/final_results.json
    score, un_hit = compute_coverage(instance_dir, modified_related_lines)
"""

import json
import os
import re
from typing import Dict, List, Optional, Any, Set, Tuple
from dataclasses import dataclass, field


@dataclass
class FileCoverage:
    """Coverage information for a single file."""
    executed_lines: Set[int] = field(default_factory=set)
    missing_lines: Set[int] = field(default_factory=set)

    def to_dict(self) -> Dict[str, List[int]]:
        return {
            "executed_lines": sorted(self.executed_lines),
            "missing_lines": sorted(self.missing_lines)
        }


@dataclass
class CoverageResult:
    """Unified coverage result."""
    language: str
    files: Dict[str, FileCoverage] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "language": self.language,
            "files": {path: cov.to_dict() for path, cov in self.files.items()}
        }


def detect_language_from_instance(instance_dir: str) -> Optional[str]:
    """Detect language from instance directory structure."""
    workspace_dir = os.path.join(instance_dir, "workspace")
    coverage_dir = os.path.join(workspace_dir, "coverage")

    if not os.path.exists(coverage_dir):
        return None

    # Check for Python coverage
    if os.path.exists(os.path.join(coverage_dir, "coverage.json")):
        return "python"

    # Check for Go coverage
    if os.path.exists(os.path.join(coverage_dir, "coverage.out")):
        return "go"

    # Check for Istanbul (JS) coverage
    if os.path.exists(os.path.join(coverage_dir, "coverage-final.json")):
        return "javascript"

    # Check for V8 coverage (TS)
    v8_dir = os.path.join(coverage_dir, "v8-coverage")
    if os.path.exists(v8_dir) and os.listdir(v8_dir):
        return "typescript"

    return None


def parse_python_coverage(coverage_path: str, repo_prefix: str = "/app") -> CoverageResult:
    """
    Parse Python coverage.py JSON format.

    Format:
    {
        "files": {
            "/app/lib/module.py": {
                "executed_lines": [1, 2, 3],
                "missing_lines": [4, 5]
            }
        }
    }
    """
    result = CoverageResult(language="python")

    with open(coverage_path, 'r') as f:
        data = json.load(f)

    files_data = data.get("files", {})

    for file_path, file_info in files_data.items():
        # Normalize path - remove repo prefix
        normalized_path = file_path
        if repo_prefix and file_path.startswith(repo_prefix):
            normalized_path = file_path[len(repo_prefix):].lstrip("/")

        cov = FileCoverage()
        cov.executed_lines = set(file_info.get("executed_lines", []))
        cov.missing_lines = set(file_info.get("missing_lines", []))

        result.files[normalized_path] = cov

    return result


def parse_go_coverage(coverage_path: str, module_prefix: str = "") -> CoverageResult:
    """
    Parse Go coverprofile format.

    Format (mode: set):
    mode: set
    github.com/org/repo/pkg/file.go:28.84,29.61 1 0
    github.com/org/repo/pkg/file.go:29.61,31.3 1 1

    Format explanation:
    file:startLine.startCol,endLine.endCol numStatements count

    count > 0 means executed, count == 0 means not executed
    """
    result = CoverageResult(language="go")

    # Track line coverage per file
    file_coverage: Dict[str, Dict[int, bool]] = {}  # file -> line -> executed

    with open(coverage_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("mode:"):
                continue

            # Parse: file:startLine.startCol,endLine.endCol numStatements count
            match = re.match(r'^(.+):(\d+)\.(\d+),(\d+)\.(\d+)\s+(\d+)\s+(\d+)$', line)
            if not match:
                continue

            file_path = match.group(1)
            start_line = int(match.group(2))
            end_line = int(match.group(4))
            count = int(match.group(7))

            # Normalize path - extract relative path from module
            if module_prefix and file_path.startswith(module_prefix):
                normalized_path = file_path[len(module_prefix):].lstrip("/")
            else:
                # Try to extract from common patterns like github.com/org/repo/...
                parts = file_path.split("/")
                if len(parts) > 3 and parts[0] in ("github.com", "gitlab.com", "bitbucket.org"):
                    # Remove github.com/org/repo prefix
                    normalized_path = "/".join(parts[3:])
                else:
                    normalized_path = file_path

            if normalized_path not in file_coverage:
                file_coverage[normalized_path] = {}

            # Mark all lines in the range
            for line_num in range(start_line, end_line + 1):
                # If count > 0, it's executed; otherwise it's not
                if line_num not in file_coverage[normalized_path]:
                    file_coverage[normalized_path][line_num] = count > 0
                elif count > 0:
                    # If any range covering this line has count > 0, it's executed
                    file_coverage[normalized_path][line_num] = True

    # Convert to FileCoverage objects
    for file_path, lines in file_coverage.items():
        cov = FileCoverage()
        for line_num, executed in lines.items():
            if executed:
                cov.executed_lines.add(line_num)
            else:
                cov.missing_lines.add(line_num)
        result.files[file_path] = cov

    return result


def parse_istanbul_coverage(coverage_path: str, repo_prefix: str = "/app") -> CoverageResult:
    """
    Parse Istanbul/nyc coverage-final.json format.

    Format:
    {
        "/app/src/file.js": {
            "path": "/app/src/file.js",
            "statementMap": {
                "0": {"start": {"line": 1, "column": 0}, "end": {"line": 1, "column": 10}},
                ...
            },
            "s": {"0": 1, "1": 0, ...},  // statement execution counts
            "fnMap": {...},
            "f": {...},  // function execution counts
            "branchMap": {...},
            "b": {...}  // branch execution counts
        }
    }
    """
    result = CoverageResult(language="javascript")

    with open(coverage_path, 'r') as f:
        data = json.load(f)

    for file_path, file_info in data.items():
        # Normalize path
        normalized_path = file_path
        if repo_prefix and file_path.startswith(repo_prefix):
            normalized_path = file_path[len(repo_prefix):].lstrip("/")

        cov = FileCoverage()

        # Get all lines from statementMap
        statement_map = file_info.get("statementMap", {})
        statement_counts = file_info.get("s", {})

        for stmt_id, stmt_info in statement_map.items():
            start_line = stmt_info.get("start", {}).get("line")
            end_line = stmt_info.get("end", {}).get("line")

            if start_line is None:
                continue

            if end_line is None:
                end_line = start_line

            count = statement_counts.get(stmt_id, 0)

            for line_num in range(start_line, end_line + 1):
                if count > 0:
                    cov.executed_lines.add(line_num)
                else:
                    cov.missing_lines.add(line_num)

        # Remove executed lines from missing (in case of overlap)
        cov.missing_lines -= cov.executed_lines

        result.files[normalized_path] = cov

    return result


def parse_v8_coverage(coverage_dir: str, repo_prefix: str = "/app") -> CoverageResult:
    """
    Parse V8 coverage format (multiple JSON files in v8-coverage directory).

    Each JSON file format:
    {
        "result": [
            {
                "scriptId": "123",
                "url": "file:///app/src/file.ts" or "/app/src/file.ts",
                "functions": [
                    {
                        "functionName": "foo",
                        "ranges": [
                            {"startOffset": 0, "endOffset": 100, "count": 1},
                            {"startOffset": 50, "endOffset": 75, "count": 0}
                        ],
                        "isBlockCoverage": true
                    }
                ]
            }
        ]
    }

    V8 coverage uses byte offsets, so we need to read the source file to convert to lines.
    For simplicity, we'll mark functions/ranges as covered or not.
    """
    result = CoverageResult(language="typescript")

    # Collect all coverage data
    file_offsets: Dict[str, List[Tuple[int, int, int]]] = {}  # path -> [(start, end, count)]

    # Read all coverage files
    for filename in os.listdir(coverage_dir):
        if not filename.endswith(".json"):
            continue

        filepath = os.path.join(coverage_dir, filename)
        try:
            with open(filepath, 'r') as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError):
            continue

        for script in data.get("result", []):
            url = script.get("url", "")

            # Skip node internal modules
            if url.startswith("node:") or not url:
                continue

            # Normalize path
            file_path = url
            if url.startswith("file://"):
                file_path = url[7:]

            # Skip node_modules
            if "node_modules" in file_path:
                continue

            normalized_path = file_path
            if repo_prefix and file_path.startswith(repo_prefix):
                normalized_path = file_path[len(repo_prefix):].lstrip("/")

            if normalized_path not in file_offsets:
                file_offsets[normalized_path] = []

            for func in script.get("functions", []):
                for range_info in func.get("ranges", []):
                    start = range_info.get("startOffset", 0)
                    end = range_info.get("endOffset", 0)
                    count = range_info.get("count", 0)
                    file_offsets[normalized_path].append((start, end, count))

    # Convert offsets to lines (simplified approach)
    # Note: For accurate conversion, we'd need the source files
    # This simplified version estimates lines based on average line length
    for file_path, offsets in file_offsets.items():
        cov = FileCoverage()

        # Estimate line numbers from offsets
        # Assume average line is ~50 characters
        AVG_LINE_LEN = 50

        for start, end, count in offsets:
            start_line = max(1, start // AVG_LINE_LEN + 1)
            end_line = max(start_line, end // AVG_LINE_LEN + 1)

            for line_num in range(start_line, end_line + 1):
                if count > 0:
                    cov.executed_lines.add(line_num)
                else:
                    cov.missing_lines.add(line_num)

        # Remove executed from missing
        cov.missing_lines -= cov.executed_lines

        if cov.executed_lines or cov.missing_lines:
            result.files[file_path] = cov

    return result


def parse_v8_coverage_with_source(coverage_dir: str, source_dir: str, repo_prefix: str = "/app") -> CoverageResult:
    """
    Parse V8 coverage with actual source files for accurate line mapping.

    Args:
        coverage_dir: Path to v8-coverage directory
        source_dir: Path to source code directory (for reading files)
        repo_prefix: Prefix to strip from paths
    """
    result = CoverageResult(language="typescript")

    # Build a cache of source file line offsets
    def get_line_offsets(filepath: str) -> List[int]:
        """Get byte offset of each line start."""
        offsets = [0]
        try:
            with open(filepath, 'rb') as f:
                content = f.read()
            offset = 0
            for char in content:
                offset += 1
                if char == ord('\n'):
                    offsets.append(offset)
        except (IOError, OSError):
            pass
        return offsets

    def offset_to_line(offsets: List[int], byte_offset: int) -> int:
        """Convert byte offset to line number."""
        for i, line_offset in enumerate(offsets):
            if line_offset > byte_offset:
                return max(1, i)
        return max(1, len(offsets))

    # Collect all coverage data
    file_ranges: Dict[str, List[Tuple[int, int, int]]] = {}

    for filename in os.listdir(coverage_dir):
        if not filename.endswith(".json"):
            continue

        filepath = os.path.join(coverage_dir, filename)
        try:
            with open(filepath, 'r') as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError):
            continue

        for script in data.get("result", []):
            url = script.get("url", "")

            if url.startswith("node:") or not url:
                continue

            file_path = url
            if url.startswith("file://"):
                file_path = url[7:]

            if "node_modules" in file_path:
                continue

            normalized_path = file_path
            if repo_prefix and file_path.startswith(repo_prefix):
                normalized_path = file_path[len(repo_prefix):].lstrip("/")

            if normalized_path not in file_ranges:
                file_ranges[normalized_path] = []

            for func in script.get("functions", []):
                for range_info in func.get("ranges", []):
                    start = range_info.get("startOffset", 0)
                    end = range_info.get("endOffset", 0)
                    count = range_info.get("count", 0)
                    file_ranges[normalized_path].append((start, end, count))

    # Convert ranges to lines
    for file_path, ranges in file_ranges.items():
        cov = FileCoverage()

        # Try to find the source file
        source_path = os.path.join(source_dir, file_path)
        if os.path.exists(source_path):
            offsets = get_line_offsets(source_path)

            for start, end, count in ranges:
                start_line = offset_to_line(offsets, start)
                end_line = offset_to_line(offsets, end)

                for line_num in range(start_line, end_line + 1):
                    if count > 0:
                        cov.executed_lines.add(line_num)
                    else:
                        cov.missing_lines.add(line_num)
        else:
            # Fallback to estimation
            AVG_LINE_LEN = 50
            for start, end, count in ranges:
                start_line = max(1, start // AVG_LINE_LEN + 1)
                end_line = max(start_line, end // AVG_LINE_LEN + 1)

                for line_num in range(start_line, end_line + 1):
                    if count > 0:
                        cov.executed_lines.add(line_num)
                    else:
                        cov.missing_lines.add(line_num)

        cov.missing_lines -= cov.executed_lines

        if cov.executed_lines or cov.missing_lines:
            result.files[file_path] = cov

    return result


def parse_coverage(instance_dir: str, language: Optional[str] = None,
                   source_dir: Optional[str] = None) -> Optional[CoverageResult]:
    """
    Parse coverage from an instance directory.

    Args:
        instance_dir: Path to instance directory (containing workspace/coverage)
        language: Override language detection (python, go, javascript, typescript)
        source_dir: Path to source directory for accurate line mapping (optional)

    Returns:
        CoverageResult or None if parsing fails
    """
    workspace_dir = os.path.join(instance_dir, "workspace")
    coverage_dir = os.path.join(workspace_dir, "coverage")

    if not os.path.exists(coverage_dir):
        return None

    # Detect language if not specified
    if language is None:
        language = detect_language_from_instance(instance_dir)

    if language is None:
        return None

    try:
        if language == "python":
            coverage_path = os.path.join(coverage_dir, "coverage.json")
            return parse_python_coverage(coverage_path)

        elif language == "go":
            coverage_path = os.path.join(coverage_dir, "coverage.out")
            return parse_go_coverage(coverage_path)

        elif language == "javascript":
            coverage_path = os.path.join(coverage_dir, "coverage-final.json")
            return parse_istanbul_coverage(coverage_path)

        elif language == "typescript":
            v8_dir = os.path.join(coverage_dir, "v8-coverage")
            if source_dir:
                return parse_v8_coverage_with_source(v8_dir, source_dir)
            else:
                return parse_v8_coverage(v8_dir)

        else:
            return None

    except Exception as e:
        print(f"Error parsing coverage for {instance_dir}: {e}")
        return None


def compare_coverage(coverage: CoverageResult,
                     required_lines: Dict[str, List[int]]) -> Dict[str, Any]:
    """
    Compare coverage with required lines to find uncovered lines.

    Args:
        coverage: Parsed coverage result
        required_lines: Dict mapping file paths to list of lines that need coverage

    Returns:
        {
            "covered": {"file1.py": [1, 2, 3], ...},
            "uncovered": {"file1.py": [4, 5], ...},
            "summary": {
                "total_required": 100,
                "total_covered": 80,
                "coverage_percent": 80.0
            }
        }
    """
    covered = {}
    uncovered = {}
    total_required = 0
    total_covered = 0

    for file_path, required in required_lines.items():
        file_cov = coverage.files.get(file_path)

        if file_cov is None:
            # No coverage data for this file
            uncovered[file_path] = required
            total_required += len(required)
            continue

        file_covered = []
        file_uncovered = []

        for line in required:
            if line in file_cov.executed_lines:
                file_covered.append(line)
            else:
                file_uncovered.append(line)

        if file_covered:
            covered[file_path] = file_covered
        if file_uncovered:
            uncovered[file_path] = file_uncovered

        total_required += len(required)
        total_covered += len(file_covered)

    return {
        "covered": covered,
        "uncovered": uncovered,
        "summary": {
            "total_required": total_required,
            "total_covered": total_covered,
            "coverage_percent": (total_covered / total_required * 100) if total_required > 0 else 0.0
        }
    }


def parse_all_instances(instances_dir: str) -> Dict[str, CoverageResult]:
    """
    Parse coverage for all instances in a directory.

    Args:
        instances_dir: Directory containing instance_* subdirectories

    Returns:
        Dict mapping instance names to coverage results
    """
    results = {}

    for entry in os.listdir(instances_dir):
        if not entry.startswith("instance_"):
            continue

        instance_dir = os.path.join(instances_dir, entry)
        if not os.path.isdir(instance_dir):
            continue

        coverage = parse_coverage(instance_dir)
        if coverage:
            results[entry] = coverage

    return results


# ============ Compatibility with existing workflow ============

def compute_coverage(
    instance_dir: str,
    modified_related_lines: Dict[str, Any],
    use_key: str = "exe_slice_lines_scope"
) -> Tuple[float, Dict[str, List[Tuple[int, str]]]]:
    """
    Compute coverage score for an instance against required lines.

    This function is compatible with the existing workflow that uses
    modified_related_lines from swe_plus_res/extract_line_numbers/exe_line_all/final_results.json

    Args:
        instance_dir: Path to instance directory (containing workspace/coverage)
        modified_related_lines: Dict with format:
            {
                "file.py": {
                    "exe_slice_lines_scope": [1, 2, 3],
                    "exe_slice_lines": [...],
                    "exe_modified_lines": [...],
                    "content": "file content..."
                }
            }
        use_key: Which key to use for required lines (default: "exe_slice_lines_scope")

    Returns:
        Tuple of:
        - coverage_score: float between 0.0 and 1.0 (or 404 if no coverage data)
        - un_hit_lines_content: Dict mapping file paths to list of (line_num, line_content)
    """
    if len(modified_related_lines) == 0:
        return 1.0, {}

    # Parse coverage from instance
    coverage_result = parse_coverage(instance_dir)

    if coverage_result is None or len(coverage_result.files) == 0:
        return 404, {}

    total_avg = 0.0
    un_hit_lines_content: Dict[str, List[Tuple[int, str]]] = {}

    for file_name, file_info in modified_related_lines.items():
        lines = set(file_info.get(use_key, []))
        if len(lines) == 0:
            continue

        # Get executed lines from coverage
        file_cov = coverage_result.files.get(file_name)
        trace_exe_lines = set(file_cov.executed_lines) if file_cov else set()

        un_hit_lines = lines - trace_exe_lines

        if len(un_hit_lines) == 0:
            total_avg += 1
            continue

        total_avg += (1 - len(un_hit_lines) / len(lines))

        # Extract content for unhit lines
        content = file_info.get("content", "").split("\n")
        for line in sorted(list(un_hit_lines)):
            if 0 < line <= len(content):
                if file_name not in un_hit_lines_content:
                    un_hit_lines_content[file_name] = []
                un_hit_lines_content[file_name].append((line, content[line - 1]))

    if len(modified_related_lines) > 0:
        total_avg /= len(modified_related_lines)

    if len(un_hit_lines_content) == 0:
        return 1.0, {}

    return round(total_avg, 3), un_hit_lines_content




def compute_coverage_batch(
    instances_dir: str,
    all_modified_related_lines: Dict[str, Dict[str, Any]],
    use_key: str = "exe_slice_lines_scope"
) -> Dict[str, Tuple[float, Dict[str, List[Tuple[int, str]]]]]:
    """
    Compute coverage for all instances in a directory.

    Args:
        instances_dir: Directory containing instance_* subdirectories
        all_modified_related_lines: Dict mapping instance_id to modified_related_lines
            Format from swe_plus_res/extract_line_numbers/exe_line_all/final_results.json
        use_key: Which key to use for required lines

    Returns:
        Dict mapping instance_id to (coverage_score, un_hit_lines_content)
    """
    results = {}

    for entry in os.listdir(instances_dir):
        if not entry.startswith("instance_"):
            continue

        instance_dir = os.path.join(instances_dir, entry)
        if not os.path.isdir(instance_dir):
            continue

        # Get modified_related_lines for this instance
        modified_related_lines = all_modified_related_lines.get(entry, {})

        if not modified_related_lines:
            continue

        score, un_hit = compute_coverage(instance_dir, modified_related_lines, use_key)
        results[entry] = (score, un_hit)

    return results


# ============ Main entry point ============

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Parse coverage from multiple languages")
    parser.add_argument("path", help="Instance directory or instances parent directory")
    parser.add_argument("--language", "-l", choices=["python", "go", "javascript", "typescript"],
                        help="Override language detection")
    parser.add_argument("--output", "-o", help="Output JSON file path")
    parser.add_argument("--all", "-a", action="store_true",
                        help="Parse all instances in directory")
    parser.add_argument("--compare", "-c", help="JSON file with required lines to compare")

    args = parser.parse_args()

    if args.all:
        results = parse_all_instances(args.path)
        output = {name: cov.to_dict() for name, cov in results.items()}
    else:
        coverage = parse_coverage(args.path, args.language)
        if coverage is None:
            print(f"Failed to parse coverage from {args.path}")
            exit(1)

        output = coverage.to_dict()

        # Compare with required lines if specified
        if args.compare:
            with open(args.compare, 'r') as f:
                required = json.load(f)
            comparison = compare_coverage(coverage, required)
            output["comparison"] = comparison

    # Output result
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(output, f, indent=2)
        print(f"Coverage saved to {args.output}")
    else:
        print(json.dumps(output, indent=2))
