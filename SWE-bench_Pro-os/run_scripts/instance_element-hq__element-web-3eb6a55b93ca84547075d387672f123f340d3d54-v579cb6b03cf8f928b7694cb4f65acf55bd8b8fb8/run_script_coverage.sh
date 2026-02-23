#!/bin/bash
set -e

# Coverage output directory
COVERAGE_DIR="/workspace/coverage"
mkdir -p "$COVERAGE_DIR"


run_all_tests() {
  echo "Running all tests..."
  
  export CI=true
  export NODE_ENV=test
  
  mkdir -p /tmp/test_results
  
  echo "Running Jest unit tests..."
  timeout 600 yarn test --verbose --passWithNoTests --testTimeout=30000 2>&1 || echo "Jest tests completed with code $?"
  
  echo "Running Playwright e2e tests..."
  if [ -d "playwright" ]; then
    yarn global add serve
    yarn build
    npx serve -p 8080 -s ./lib &
    SERVER_PID=$!
    
    sleep 15
    
    timeout 300 yarn test:playwright --reporter=line --timeout=30000 2>&1 || echo "Playwright tests completed with code $?"
    
    kill $SERVER_PID 2>/dev/null || true
    wait $SERVER_PID 2>/dev/null || true
  else
    echo "No Playwright tests found"
  fi
  

}

run_selected_tests() {
  local test_files=("$@")
  echo "Running selected tests: ${test_files[@]}"
  
  export CI=true
  export NODE_ENV=test
  
  mkdir -p /tmp/test_results
  
  for file in "${test_files[@]}"; do
    if [[ $file == *"playwright"* ]] || [[ $file == *"e2e"* ]]; then
      echo "Running Playwright test: $file"
      yarn global add serve
      yarn build
      npx serve -p 8080 -s ./lib &
      SERVER_PID=$!
      sleep 15
      timeout 300 yarn test:playwright --reporter=line "$file" 2>&1 || echo "Test completed with code $?"
      kill $SERVER_PID 2>/dev/null || true
      wait $SERVER_PID 2>/dev/null || true
    else
      echo "Running Jest test: $file"
      yarn test --coverage --coverageDirectory="$COVERAGE_DIR" --coverageReporters=json --verbose --testPathPattern="$file" --passWithNoTests 2>&1 || echo "Test completed with code $?"
    fi
  done

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
