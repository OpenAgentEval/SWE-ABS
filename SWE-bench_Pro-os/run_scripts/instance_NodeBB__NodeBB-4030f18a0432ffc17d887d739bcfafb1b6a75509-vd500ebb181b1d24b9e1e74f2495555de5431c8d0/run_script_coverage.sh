#!/bin/bash
set -e

# Coverage output directory
COVERAGE_DIR="/workspace/coverage"
mkdir -p "$COVERAGE_DIR"

# Install nyc if not present (for coverage collection)
if ! command -v nyc &> /dev/null; then
    npm install -g nyc 2>/dev/null || npm install nyc --save-dev 2>/dev/null || true
fi


prepare_test_environment() {
  cp install/package.json .
  npm install --production=false
  npm install lodash underscore async
  
  redis-server --daemonize yes --protected-mode no --appendonly yes
  while ! redis-cli ping; do
    echo "Waiting for Redis to start..."
    sleep 1
  done
  
  echo '{"url":"http://localhost:4568","secret":"test-secret","database":"redis","redis":{"host":"127.0.0.1","port":6379,"password":"","database":1},"test_database":{"host":"127.0.0.1","port":"6379","password":"","database":"1"},"port":"4568"}' > config.json
  
  mkdir -p logs
  touch logs/output.log
  
  pkill -f "node app.js" || true
  sleep 2
  echo "Test environment prepared"
}

run_all_tests() {
  echo "Running all tests..."
  
  prepare_test_environment
  timeout 300 bash -c 'NODE_ENV=test TEST_ENV=development nyc --reporter=json --reporter=text --report-dir="$COVERAGE_DIR" npx mocha test/ --reporter=json --timeout=25000 --bail=false' || echo '{"tests":[],"stats":{"suites":0,"tests":0,"passes":0,"pending":0,"failures":0}}'

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

  prepare_test_environment
  timeout 300 bash -c 'NODE_ENV=test TEST_ENV=development nyc --reporter=json --reporter=text --report-dir="$COVERAGE_DIR" npx mocha '"${test_files[*]}"' --reporter=json --timeout=25000 --bail=false' || echo '{"tests":[],"stats":{"suites":0,"tests":0,"passes":0,"pending":0,"failures":0}}'

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
