#!/bin/bash
set -e

run_all_tests() {
  echo "Running all tests..."
  cd /app
  
  export NODE_ICU_DATA=/app/node_modules/full-icu
  
  echo "Running API tests..."
  cd test
  node --icu-data-dir=../node_modules/full-icu test api
  
  echo "Running client tests..."
  node --icu-data-dir=../node_modules/full-icu test client
}

run_selected_tests() {
  local test_files=("$@")
  echo "Running selected tests: ${test_files[@]}"
  
  cd /app
  export NODE_ICU_DATA=/app/node_modules/full-icu
  
  for test_path in "${test_files[@]}"; do
    if [[ "$test_path" == *"|"* ]]; then
      file_path=$(echo "$test_path" | cut -d'|' -f1 | xargs)
      test_name=$(echo "$test_path" | cut -d'|' -f2- | xargs)
      echo "Running specific test: $test_name in $file_path"
      cd /app/test
      if [[ "$file_path" == *"api"* ]]; then
        node --icu-data-dir=../node_modules/full-icu test api
      else
        node --icu-data-dir=../node_modules/full-icu test client
      fi
    else
      echo "Running test file: $test_path"
      cd /app/test
      if [[ "$test_path" == *"api"* ]]; then
        node --icu-data-dir=../node_modules/full-icu test api
      else
        node --icu-data-dir=../node_modules/full-icu test client
      fi
    fi
  done
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
