#!/usr/bin/env bash
# Stage 3 Augmented Test Pipeline
# Connects Stage 1 (aug tests) and Stage 2 (mutations) via aug gen + eval + retry.
#
# Usage:
#   bash run_stage3.sh
#
# Resume from a specific phase:
#   bash run_stage3.sh --start-from-phase aug_no_equ
#
# Specific instances only:
#   bash run_stage3.sh --instance-ids "django__django-7530,django__django-11740"

# ========== Required: adjust these paths ==========
stage1_preds=result/model_gen_test/stage1_auto_debug_10/preds.json
stage2_output=result/res_mutation/stage2_mutation_gen_debug_10  # contains set1/, set2/

# ========== Output ==========
output=result/mutation_aug
run_id=stage2_mutation_aug_debug_10

# ========== Model ==========
model=openai/gpt-5
temperature=0

# ========== Pipeline Settings ==========
required_mutations=2    # must match Stage2 required-mutations
max_aug_retries=2

# ========== Workers ==========
aug_workers=2
eval_workers=8

# ========== Benchmark ==========
benchmark=swebench      # swebench or swebenchpro


start_from_phase=

python run_stage_mutation_aug.py \
  --stage1-preds "$stage1_preds" \
  --stage2-output "$stage2_output" \
  --output "$output" \
  --run-id "$run_id" \
  --model "$model" \
  --temperature "$temperature" \
  --required-mutations "$required_mutations" \
  --max-aug-retries "$max_aug_retries" \
  --aug-workers "$aug_workers" \
  --eval-workers "$eval_workers" \
  --benchmark "$benchmark" \
  ${start_from_phase:+--start-from-phase "$start_from_phase"} \
  "$@"
