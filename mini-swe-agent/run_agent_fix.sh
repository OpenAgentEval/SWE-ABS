
aug_test_file=result/model_gen_test/glm_100/preds.json
# task_name=multi_run_5_debug

# Hard_Code_Fix  Coverage_Fix
fix_type=Coverage_Fix
# instance_ids=scikit-learn__scikit-learn-10908,astropy__astropy-13033,pylint-dev__pylint-4661,sphinx-doc__sphinx-10466
instance_ids=sympy__sympy-19040,sympy__sympy-13852


# deepseek/deepseek-reasoner  deepseek/deepseek-chat  openai/gpt-5  zai/glm-4.7
model=zai/glm-4.7
temperature=0
# swebenchpro swebench
benchmark=swebench
run_instance_file=../SWE-bench_Pro-os/fail_keys.txt


python src/minisweagent/swe_abs_run/swebench_test_fix.py \
  --aug_test_file $aug_test_file \
  --workers 2 \
  --model $model \
  --temperature $temperature \
  --fix_type $fix_type \
  --benchmark $benchmark \
  # --instance_ids $instance_ids \

  # --run_instance_file $run_instance_file \

  # --instance_ids $instance_ids \



  # --run_instance_file $run_instance_file \
  # --instance_ids $instance_ids \


