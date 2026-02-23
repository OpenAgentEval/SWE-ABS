
instance_ids=matplotlib__matplotlib-20826
# instance_ids=pytest-dev__pytest-7490

use_coverage=False

python -m swebench.runtest.run_evaluation \
    --predictions_path gold \
    --max_workers 2 \
    --run_id validate-gold \
    --instance_ids  $instance_ids \
    --dataset_name princeton-nlp/SWE-bench_Verified \
    --use_coverage $use_coverage \
    # --rewrite_reports $rewrite_reports \
