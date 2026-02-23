# instance_ids=sphinx-doc__sphinx-7440
instance_ids=pylint-dev__pylint-6386

python -m swebench.runtest.get_line_number \
    --max_workers 8 \
    --run_id validate-gold \
    --dataset_name princeton-nlp/SWE-bench_Verified \
    # --instance_ids $instance_ids \

