# SWE-abs Installation Guide

---

## Project Structure

```
SWE-abs/
├── sweabs_utils/              # Shared utilities (installed as a package)
│   ├── __init__.py
│   ├── preds_manager.py
│   └── parser_utils.py
├── mini-swe-agent/            # Agent framework and pipeline scripts
│   └── src/minisweagent/
├── swe-bench/                 # SWE-bench evaluation harness
└── pyproject.toml             # Root package config (installs sweabs_utils)
```

---

## Installation

### 1. Create environment

```bash
conda create -n swe-abs python=3.11
conda activate swe-abs
```

### 2. Install packages

```bash
cd /path/to/SWE-abs

# Install shared utilities
python -m pip install -e . --config-settings editable_mode=compat

# Install mini-swe-agent
cd mini-swe-agent
python -m pip install -e . --config-settings editable_mode=compat
cd ..

# Install swe-bench harness
cd swe-bench
python -m pip install -e . --config-settings editable_mode=compat
cd ..

# Install SWE-bench Pro (optional)
cd SWE-bench_Pro-os
python -m pip install -e . --config-settings editable_mode=compat
cd ..
```

> The `-e` flag installs in editable mode — changes to source files take effect immediately without reinstalling.

### 3. Verify installation

```bash
python test/test_cross_package_imports.py
```

Expected output: `Total: 5/5 tests passed`

---

## Troubleshooting

### `ModuleNotFoundError: No module named 'sweabs_utils'`

```bash
cd /path/to/SWE-abs
pip install -e .
```

### `ModuleNotFoundError: No module named 'swebench'`

```bash
cd /path/to/SWE-abs/swe-bench
pip install -e .
```

### Imports work from root but not from subdirectories

Verify the package is registered in your environment:

```bash
pip show sweabs-utils
pip show swebench
pip show minisweagent
```

If any are missing, reinstall from the corresponding directory.

### Still failing after reinstall

Check you are in the correct conda environment:

```bash
conda activate swe-abs
which python
```

---

**Version:** 0.2.0 | **Last Updated:** 2026-02-21
