#!/bin/bash
set -e

# Coverage output directory
COVERAGE_DIR="/workspace/coverage"
mkdir -p "$COVERAGE_DIR"


run_all_tests() {
  echo "Running all tests..."
  
  export NODE_ENV=test
  export CI=true
  export DISPLAY=:99
  export CHROME_BIN=/usr/bin/google-chrome
  export PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true
  
  Xvfb :99 -screen 0 1024x768x24 > /dev/null 2>&1 &
  XVFB_PID=$!
  
  sleep 2
  
  cd /app
  
  echo "Running Jest unit tests..."
  yarn test --coverage --coverageDirectory="$COVERAGE_DIR" --coverageReporters=json --verbose --passWithNoTests --ci --maxWorkers=2 2>&1
  JEST_EXIT_CODE=$?
  
  echo "Jest tests completed with exit code: $JEST_EXIT_CODE"
  
  echo "Running Cypress e2e tests..."
  
  yarn start > /dev/null 2>&1 &
  SERVER_PID=$!
  
  echo "Waiting for development server to start..."
  sleep 30
  
  if ! curl -s http://localhost:8080 > /dev/null; then
    echo "Development server failed to start, skipping Cypress tests"
    kill $SERVER_PID 2>/dev/null || true
    kill $XVFB_PID 2>/dev/null || true
    return $JEST_EXIT_CODE
  fi
  
  yarn test:cypress --headless --browser chrome --config video=false,screenshotOnRunFailure=false 2>&1
  CYPRESS_EXIT_CODE=$?
  
  echo "Cypress tests completed with exit code: $CYPRESS_EXIT_CODE"
  
  kill $SERVER_PID 2>/dev/null || true
  kill $XVFB_PID 2>/dev/null || true
  
  if [ $JEST_EXIT_CODE -ne 0 ]; then
    return $JEST_EXIT_CODE
  else
    return $CYPRESS_EXIT_CODE
  fi

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
  
  export NODE_ENV=test
  export CI=true
  export DISPLAY=:99
  export CHROME_BIN=/usr/bin/google-chrome
  export PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true
  
  cd /app
  
  if [[ "${test_files[0]}" == *"cypress"* ]] || [[ "${test_files[0]}" == *".spec."* ]]; then
    echo "Running Cypress tests..."
    
    Xvfb :99 -screen 0 1024x768x24 > /dev/null 2>&1 &
    XVFB_PID=$!
    sleep 2
    
    yarn start > /dev/null 2>&1 &
    SERVER_PID=$!
    sleep 30
    
    yarn test:cypress --headless --browser chrome --spec "${test_files[@]}" --config video=false,screenshotOnRunFailure=false 2>&1
    EXIT_CODE=$?
    
    kill $SERVER_PID 2>/dev/null || true
    kill $XVFB_PID 2>/dev/null || true
    
    return $EXIT_CODE
  else
    echo "Running Jest tests..."
    yarn test --coverage --coverageDirectory="$COVERAGE_DIR" --coverageReporters=json --verbose --passWithNoTests --ci --maxWorkers=2 "${test_files[@]}" 2>&1
    return $?
  fi

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
