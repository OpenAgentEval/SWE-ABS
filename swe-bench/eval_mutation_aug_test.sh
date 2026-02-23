
# This script evaluates whether aug_tests after mutation augmentation pass the gold patch and mutation patch

instance_ids=django__django-7530

predictions_test_path=/path/to/preds.json
run_id=glm_50_aug_no_equ_mutation_aug_1

# no_equ_mutation_aug equ_mutation_aug
stage_name=no_equ_mutation_aug
iteration=1
rewrite_preds=True
re_run_eval=True

python -m swebench.runtest.run_evaluation_test_mutation_aug \
    --predictions_test_path "$predictions_test_path" \
    --max_workers 8 \
    --timeout 120 \
    --stage_name $stage_name \
    --iteration $iteration \
    --re_run_eval $re_run_eval \
    --run_id  "$run_id" \
    --rewrite_preds $rewrite_preds \
    # --instance_ids $instance_ids \

