#!/bin/bash
set -e

# Coverage output directory
COVERAGE_DIR="/workspace/coverage"
mkdir -p "$COVERAGE_DIR"


run_all_tests() {
  echo "Running all tests..."
  
  Xvfb :99 -screen 0 1024x768x24 > /dev/null 2>&1 &
  export DISPLAY=:99
  
  echo "Running Jest unit tests..."
  yarn test --coverage --coverageDirectory="$COVERAGE_DIR" --coverageReporters=json --verbose --passWithNoTests
  
  echo "Running e2e tests..."
  cd /app/test/end-to-end-tests
  
  if [ ! -d "node_modules" ]; then
    ./install.sh
  fi
  
  ./run.sh --app-url http://localhost:8080

    # Search and copy coverage files from default locations if not in COVERAGE_DIR
    if [ ! -f "$COVERAGE_DIR/coverage-final.json" ]; then
        echo "Searching for coverage files in default locations..."
        # Find all coverage-final.json files in the project
        for cov_file in $(find /app -name "coverage-final.json" -type f 2>/dev/null | head -5); do
            echo "Found coverage file: $cov_file"
            cp "$cov_file" "$COVERAGE_DIR/" 2>/dev/null || true
            # Also copy coverage directory contents if exists
            cov_dir=$(dirname "$cov_file")
            if [ -f "$cov_dir/lcov.info" ]; then
                cp "$cov_dir/lcov.info" "$COVERAGE_DIR/" 2>/dev/null || true
            fi
            if [ -f "$cov_dir/clover.xml" ]; then
                cp "$cov_dir/clover.xml" "$COVERAGE_DIR/" 2>/dev/null || true
            fi
        done
    fi
    # Final check
    if [ -f "$COVERAGE_DIR/coverage-final.json" ]; then
        echo "Coverage file generated successfully"
    elif [ -f "$COVERAGE_DIR/clover.xml" ]; then
        echo "Coverage generated in clover format"
    fi
}

run_selected_tests() {
  local test_files=("$@")
  echo "Running selected tests: ${test_files[@]}"
  
  Xvfb :99 -screen 0 1024x768x24 > /dev/null 2>&1 &
  export DISPLAY=:99
  
  yarn test --coverage --coverageDirectory="$COVERAGE_DIR" --coverageReporters=json --verbose --passWithNoTests "${test_files[@]}"

    # Search and copy coverage files from default locations if not in COVERAGE_DIR
    if [ ! -f "$COVERAGE_DIR/coverage-final.json" ]; then
        echo "Searching for coverage files in default locations..."
        # Find all coverage-final.json files in the project
        for cov_file in $(find /app -name "coverage-final.json" -type f 2>/dev/null | head -5); do
            echo "Found coverage file: $cov_file"
            cp "$cov_file" "$COVERAGE_DIR/" 2>/dev/null || true
            # Also copy coverage directory contents if exists
            cov_dir=$(dirname "$cov_file")
            if [ -f "$cov_dir/lcov.info" ]; then
                cp "$cov_dir/lcov.info" "$COVERAGE_DIR/" 2>/dev/null || true
            fi
            if [ -f "$cov_dir/clover.xml" ]; then
                cp "$cov_dir/clover.xml" "$COVERAGE_DIR/" 2>/dev/null || true
            fi
        done
    fi
    # Final check
    if [ -f "$COVERAGE_DIR/coverage-final.json" ]; then
        echo "Coverage file generated successfully"
    elif [ -f "$COVERAGE_DIR/clover.xml" ]; then
        echo "Coverage generated in clover format"
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
