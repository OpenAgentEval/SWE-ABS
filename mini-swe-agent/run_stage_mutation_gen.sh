#!/bin/bash

# Stage 2 Mutation Pipeline - Quick Start Script
# This is a simple wrapper around run_stage2_mutation.py with common defaults

# ========== Configuration ==========
# Modify these variables to match your setup

# deepseek/deepseek-reasoner  deepseek/deepseek-chat  openai/gpt-5  zai/glm-4.7
MODEL=openai/gpt-5
WORKERS=2
BENCHMARK=swebench
OUTPUT=result/res_mutation  # Should contain Stage1's preds.json
RUN_ID=stage2_mutation_gen_debug_10

# Optional: Specify instances to run
# INSTANCE_IDS="django__django-11740,django__django-15280"
# Or use a file with instance IDs
# RUN_INSTANCE_FILE="select_100_instances_ids.yaml"
# django__django-11141
# ========== Run Stage2 Pipeline ==========

python run_stage_mutation_gen.py \
  --output "$OUTPUT" \
  --model "$MODEL" \
  --run-id "$RUN_ID" \
  --workers "$WORKERS" \
  --benchmark "$BENCHMARK" \
  --required-mutations 2 \
  --max-mutation-iterations 5 \
  --max-eval-workers 8 \
  --judge-times 3

# To resume from a specific phase, add:
# --start-from-phase init_test

# To run only specific instances, add:
# --instance-ids "$INSTANCE_IDS"
# Or:
# --run-instance-file "$RUN_INSTANCE_FILE"

# To use multiple models for judging, add:
# --judge-models "zai/glm-4.7,openai/gpt-5,deepseek/deepseek-chat"
