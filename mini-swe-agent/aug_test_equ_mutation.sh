instance_ids=django__django-7530

config=src/minisweagent/config/extra/swebench_aug_from_equ_mutation.yaml

# no_equ_mutation_aug equ_mutation_aug
stage_name=equ_mutation_aug 
iteration=0
aug_test_file=result/mutation_aug/multi_run_all_aug/preds_no_equ_mutation_aug_1_fix_eval.json
output=result/mutation_aug/multi_run_all_aug
temperature=0

python src/minisweagent/swe_abs_run/swebench_aug_mutation.py \
  --aug_test_file $aug_test_file \
  --output  $output \
  --workers 2 \
  --config  $config \
  --stage_name $stage_name \
  --iteration $iteration \
  --redo_fail_instance true \
  --temperature $temperature \
  # --instance_ids $instance_ids \

