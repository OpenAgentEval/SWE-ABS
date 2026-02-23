# SWE-PLUS Common Utility Library

This is the shared utility library for the SWE-PLUS project, used across three sub-repositories:
- `mini-swe-agent/`
- `swe-bench/`
- `SWE-bench_Pro-os/`

---

## 📁 Directory Structure

```
util/
├── __init__.py           # Module initialization
├── preds_manager.py      # preds.json management tool
├── parser_utils.py       # Patch/Diff parsing utilities
└── README.md             # This file
```

---

## 📦 Module Overview

### `preds_manager.py`

Provides unified read/write management for `preds.json`.

**Main class**:
- `ResultManager`: primary class for preds.json operations

**Methods**:

| Method | Description |
|--------|-------------|
| `load()` | Load preds.json |
| `save(data)` | Save data to preds.json |
| `update_instance(instance_id, updates, merge=True)` | Update a single instance |
| `update_instance_nested(instance_id, nested_updates)` | Update using nested keys (supports `meta.coverage_rate` format) |
| `get_instance(instance_id)` | Get data for a single instance |
| `get_all_instances()` | Get all instances |
| `get_failed_test_gen()` | Get instances where test generation failed |
| `get_gold_patch_failures()` | Get instances where the gold patch failed |
| `get_low_coverage_instances(threshold=1.0)` | Get low-coverage instances |
| `instance_exists(instance_id)` | Check whether an instance exists |
| `delete_instance(instance_id)` | Delete an instance |
| `get_statistics()` | Get summary statistics |

**Data format**:
- Dict format: `{instance_id: instance_data}`
- Not list format (to avoid confusion)

---

### `parser_utils.py`

Provides utility functions for Patch/Diff parsing.

**Main features**:
- File reading (supports JSON / JSONL / TXT / YAML)
- Diff/Patch parsing and processing
- Test directive extraction (supports Python / Go / JavaScript / TypeScript)
- File path filtering

**Functions**:

| Function | Description |
|----------|-------------|
| `str2bool()` | Convert string to boolean |
| `read_list_file()` | Read files in multiple formats |
| `extract_go_test_info()` | Extract Go test information |
| `get_test_directives()` | Get test directives from a patch |
| `get_apply_files()` | Extract file paths from a patch |
| `remove_conflicting_chunks()` | Remove conflicting patch chunks |
| `parse_diff_path()` | Parse file path from a diff line |
| `should_filter_path()` | Check if a path should be filtered |
| `is_binary_diff_block()` | Check if a diff block is binary |
| `filter_apply_diffs()` | Filter specific files from a diff |
| `split_diff_blocks()` | Split a diff into individual blocks |
| `extract_added_content()` | Extract added lines from a diff |
| `generate_newfile_diff_block()` | Generate a diff block for a new file |

**Constants**:

| Constant | Description |
|----------|-------------|
| `LANGUAGE_TEST_EXTENSIONS` | Test file extensions by language |
| `FILTER_DIRS` | Directories to filter out |
| `FILTER_FILES` | Filenames to filter out |
| `FILTER_EXTS` | File extensions to filter out |

---

## 🔧 Usage

### Importing in each repository

Add the following path configuration at the top of your scripts:

```python
import sys
from pathlib import Path

# Add SWE-PLUS/util to the Python path
UTIL_PATH = Path(__file__).resolve().parent.parent.parent / "util"  # adjust depth as needed
if str(UTIL_PATH) not in sys.path:
    sys.path.insert(0, str(UTIL_PATH))

# Now import
from parser_utils import str2bool, read_list_file, get_test_directives
from sweabs_utils.preds_manager import ResultManager
```

### Path depth reference

The relative depth from each repository to `util/` differs:

| Repository | Example file | Parent levels | UTIL_PATH calculation |
|------------|-------------|---------------|-----------------------|
| **mini-swe-agent** | `src/minisweagent/swe_abs_run/xxx.py` | 5 | `parent.parent.parent.parent.parent / "util"` |
| **swe-bench** | `swebench/runtest/xxx.py` | 4 | `parent.parent.parent.parent / "util"` |
| **Pro-os** | `utils/xxx.py` | 3 | `parent.parent.parent / "util"` |

**Note**: `Path(__file__).resolve()` returns the absolute path of the file, unaffected by the current working directory.

---

## 📝 Examples

### Example 1: Managing preds.json with ResultManager

```python
from sweabs_utils.preds_manager import ResultManager

# Create ResultManager
manager = ResultManager("result/model_gen_test/preds.json")

# Update a single instance
manager.update_instance("django-11141", {
    "model_test_patch": "diff --git ...",
    "meta": {
        "pass_gold_patch_status": "success",
        "coverage_rate": 0.95
    }
})

# Update using nested keys
manager.update_instance_nested("django-11141", {
    "meta.pass_gold_patch_status": "success",
    "meta.coverage_rate": 0.95,
    "stage.-1.evaluation_info": {
        "status": "completed",
        "outputs": "/path/to/outputs"
    }
})

# Get a single instance
instance = manager.get_instance("django-11141")

# Query failed instances
failed_ids = manager.get_failed_test_gen()
gold_failures = manager.get_gold_patch_failures()
low_coverage = manager.get_low_coverage_instances(threshold=0.9)

# Get statistics
stats = manager.get_statistics()
print(f"Total instances: {stats['total_instances']}")
print(f"Successful instances: {stats['successful_instances']}")
```

### Example 2: Reading files

```python
from parser_utils import read_list_file

# Read a JSON file
data = read_list_file("config.json")

# Read a JSONL file
lines = read_list_file("data.jsonl")

# Read a YAML file
config = read_list_file("config.yaml")
```

### Example 3: Parsing a patch

```python
from parser_utils import get_test_directives, get_apply_files

instance = {
    "repo": "django/django",
    "repo_language": "python",
    "test_patch": "diff --git a/tests/test_foo.py b/tests/test_foo.py\n..."
}

# Get test directives
directives = get_test_directives(instance)
# Output: ["foo.test_bar", ...]

# Get involved files
files = get_apply_files(instance["test_patch"])
# Output: ["tests/test_foo.py"]
```

### Example 4: Filtering conflicting patches

```python
from parser_utils import remove_conflicting_chunks

model_patch = "diff --git a/foo.py b/foo.py\n..."
model_test_patch = "diff --git a/test_foo.py b/test_foo.py\n..."

# Remove parts of model_patch that conflict with model_test_patch
cleaned_patch = remove_conflicting_chunks(model_patch, model_test_patch)
```

---

## 🧪 Running Tests

Test scripts are located in the `test/` directory at the project root:

```bash
cd /path/to/SWE-PLUS

# Test parser_utils refactoring
python test/test_parser_utils.py

# Test preds_manager refactoring
python test/test_preds_manager.py
```

Tests cover:
- ✅ util/parser_utils.py imports successfully
- ✅ util/preds_manager.py imports successfully
- ✅ mini-swe-agent's parser_utils imports successfully
- ✅ Pro-os's parser_util imports successfully
- ✅ All functions work correctly

---

## 📊 Refactoring Impact

### parser_utils.py

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| **Duplicate code** | ~250 lines | 0 lines | -100% |
| **mini-swe-agent/parser_utils.py** | 577 lines | ~150 lines | -74% |
| **Pro-os/parser_util.py** | 321 lines | ~70 lines | -78% |
| **Total** | ~898 lines | ~670 lines | -25% |

### preds_manager.py

| Repository | Files refactored | Notes |
|------------|-----------------|-------|
| **mini-swe-agent** | 6 scripts | All scripts under swe_abs_run/ |
| **swe-bench** | 2 scripts | run_evaluation_test.py and variants |
| **SWE-bench_Pro-os** | 3 scripts | Evaluation scripts under run_test/ |

**Benefits**:
- ✅ Unified preds.json management interface
- ✅ Process-safe file operations (built-in file locking)
- ✅ Nested key updates (e.g., `meta.coverage_rate`)
- ✅ Negative array indexing (e.g., `stage.-1.evaluation_info`)
- ✅ Convenient query methods (failed instances, low coverage, etc.)
- ✅ Reduced code duplication by 200+ lines

---

## 🔄 Maintenance Guide

### Adding a new shared function

1. Add the function to `util/parser_utils.py`
2. Import it in each repository's parser_utils
3. Update this README
4. Run tests to verify

### Modifying an existing function

1. Modify the function in `util/parser_utils.py`
2. Ensure backward compatibility, or update all call sites simultaneously
3. Run tests to verify

### Version management

- Current version: v0.1.0
- Bump the version number on breaking changes
- Record the last update date in the file header comment

---

## 📚 Related Documentation

- [CODE_DUPLICATION_ANALYSIS.md](../CODE_DUPLICATION_ANALYSIS.md) - Code duplication analysis report
- [ARCHITECTURE_REFACTORING_PLAN_V3_UTIL.md](../ARCHITECTURE_REFACTORING_PLAN_V3_UTIL.md) - Refactoring plan

---

**Version**: v0.1.0
**Last updated**: 2026-02-14
**Maintainer**: SWE-PLUS Team
