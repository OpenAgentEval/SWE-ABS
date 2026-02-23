
instance_ids=instance_tutao__tutanota-1ff82aa365763cee2d609c9d19360ad87fdf2ec7-vc4e41fd0029957297843cb9dec4a25c7c756f029

config=./src/minisweagent/config/extra/swebench_mutation.yaml

# anthropic/claude-sonnet-4-5-20250929    zai/glm-4.7  openai/gpt-5  deepseek/deepseek-chat
model=zai/glm-4.7
temperature=1
run_instance_file=select_100_instances_ids.yaml
# swebenchpro swebench
benchmark=swebench
output=res_mutation/mutation_run_glm100_set2


python src/minisweagent/swe_abs_run/swebench_mutation.py \
  --benchmark $benchmark \
  --subset verified \
  --split test \
  --output  $output \
  --workers 2 \
  --model $model \
  --config  $config \
  --repo_select_num 5 \
  --temperature $temperature \
  --run_instance_file $run_instance_file \



  # --run_instance_file $run_instance_file \
  # --instance_ids $instance_ids \
