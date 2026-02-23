#!/bin/bash

# Stage 1 automation flow: Test Generation + Hard Code Fix + Gold Patch Evaluation + Coverage Fix

# ouput dir with preds.json
output=result/model_gen_test


# ["test_gen", "hard_code_fix", "gold_eval", "coverage_fix", "coverage_eval"]
start_from_phase=coverage_fix

# deepseek/deepseek-reasoner  deepseek/deepseek-chat  openai/gpt-5  zai/glm-4.7
model=openai/gpt-5
temperature=1.0
workers=2

# benchmark type
# swebench swebenchpro
benchmark=swebench

# dataset folder
subset=verified
split=test

# evaluation
run_id=stage1_auto_debug_10
eval_timeout=120
max_eval_workers=12

# retries
max_test_gen_retries=3
max_hard_code_fix_retries=3
max_combined_retries=2
max_coverage_fix_attempts=2

python run_stage1_auto.py \
  --output $output \
  --model $model \
  --benchmark $benchmark \
  --temperature $temperature \
  --workers $workers \
  --subset $subset \
  --split $split \
  --run-id $run_id \
  --eval-timeout $eval_timeout \
  --max-eval-workers $max_eval_workers \
  --max-test-gen-retries $max_test_gen_retries \
  --max-hard-code-fix-retries $max_hard_code_fix_retries \
  --max-combined-retries $max_combined_retries \
  --max-coverage-fix-attempts $max_coverage_fix_attempts \

  # optional args
  # --start-from-phase $start_from_phase
  # --skip-coverage-fix \
  # --fail-fast \
  # --must-cover-line-file /path/to/file.json \
