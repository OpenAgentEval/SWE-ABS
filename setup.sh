#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[1/3] Installing sweabs_utils..."
pip install -e "$SCRIPT_DIR" --config-settings editable_mode=compat

echo "[2/3] Installing mini-swe-agent..."
pip install -e "$SCRIPT_DIR/mini-swe-agent" --config-settings editable_mode=compat

echo "[3/3] Installing swe-bench..."
pip install -e "$SCRIPT_DIR/swe-bench" --config-settings editable_mode=compat

echo "Done. Run 'python test/test_cross_package_imports.py' to verify."
