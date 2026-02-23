predictions_test_path=/path/to/preds.json
instance_ids=django__django-11141,psf__requests-1724

must_cover_line_file=swe_plus_res/modified_raleted_lines/final_results.json
rewrite_preds=False
use_coverage=False
coverage_eval=False
run_id=swebench_53_gold

re_run_eval=True

python -m swebench.runtest.run_evaluation_test \
      --predictions_test_path "$predictions_test_path" \
      --max_workers 12 \
      --timeout 120 \
      --rewrite_preds $rewrite_preds \
      --run_id $run_id \
      --eval_gold_patch True \
      --re_run_eval $re_run_eval \
      --use_coverage $use_coverage \
      --must_cover_line $must_cover_line_file \
      --coverage_eval $coverage_eval \
      --instance_ids $instance_ids \

