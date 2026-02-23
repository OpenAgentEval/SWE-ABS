# python src/minisweagent/run/extra/swebench_single_test.py \
#   --instance django__django-11141

run_instance=sympy__sympy-13852


# deepseek/deepseek-reasoner  deepseek/deepseek-chat  openai/gpt-5  zai/glm-4.7
model=openai/gpt-5
output=result/model_gen_test/debug_pipline

# swebenchpro swebench
benchmark=swebench
run_instance_file=re_run.yaml
redo_existing=false
temperature=1


python src/minisweagent/swe_abs_run/swebench_test.py \
  --benchmark $benchmark \
  --subset verified \
  --split test \
  --model $model \
  --output  $output \
  --redo_existing $redo_existing \
  --workers 2 \
  --temperature $temperature \
  --run_instance $run_instance \


  # --run_instance_file $run_instance_file \
  # --run_instance $run_instance \



# deepseek/deepseek-reasoner  deepseek/deepseek-chat  openai/gpt-5  zai/glm-4.7
# model=zai/glm-4.7
# output=result/model_gen_test/glm_50

# # swebenchpro swebench
# benchmark=swebench
# run_instance=sympy__sympy-19637
# run_instance_file=re_run.yaml


# python src/minisweagent/swe_abs_run/swebench_test.py \
#   --benchmark $benchmark \
#   --subset verified \
#   --split test \
#   --model $model \
#   --output  $output \
#   --workers 2 \
#   --repo_select_num 5 \
#   --run_instance $run_instance \
#   # --run_instance_file $run_instance_file \

