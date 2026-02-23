instance_ids=instance_navidrome__navidrome-3f2d24695e9382125dfe5e6d6c8bbeb4a313a4f9

config=src/minisweagent/config/extra/swebench_aug_from_mutation.yaml

# no_equ_mutation_aug equ_mutation_aug
stage_name=no_equ_mutation_aug 
iteration=0
aug_test_file=result/model_gen_test/multi_run_10_15/preds_mutation.json

output=result/mutation_aug/pro_selecet_135_aug
temperature=0

# swebenchpro swebench
benchmark=swebench

# deepseek/deepseek-reasoner  deepseek/deepseek-chat  openai/gpt-5  zai/glm-4.7
model=openai/gpt-5
run_instance_file=aug_run.yaml


python src/minisweagent/swe_abs_run/swebench_aug_mutation.py \
  --aug_test_file $aug_test_file \
  --output  $output \
  --workers 2 \
  --config  $config \
  --model $model \
  --stage_name $stage_name \
  --iteration $iteration \
  --redo_fail_instance true \
  --temperature $temperature \
  --benchmark $benchmark \
  --instance_ids $instance_ids \



  # --run_instance_file $run_instance_file \
  # --instance_ids $instance_ids \