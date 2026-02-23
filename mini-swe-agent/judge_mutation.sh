judge_mutatation_config_spec=./src/minisweagent/config/extra/mutation_judge.yaml

mutation_res_file=res_mutation/mutation_run_glm100_set2/preds.json

# zai/glm-4.7  gemini/gemini-3-pro-preview    openai/gpt-5  deepseek/deepseek-chat
model=zai/glm-4.7
# swebenchpro swebench
benchmark=swebench
instance_ids=django__django-14608


python src/minisweagent/swe_abs_run/judge_vaild_mutation.py \
  --subset verified \
  --split test \
  --benchmark $benchmark \
  --mutation_res_file $mutation_res_file \
  --judge_mutatation_config_spec $judge_mutatation_config_spec \
  --workers 2 \
  --models $model \
  --instance_ids $instance_ids \

