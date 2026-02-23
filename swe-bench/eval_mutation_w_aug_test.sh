# Run mutations that passed init_test and check if they pass aug_test

predictions_test_path=/path/to/preds.json
aug_test_eval_id=mutation_run_glm_100
mutation_paths=(
    /path/to/mutation_preds_set1.json
    /path/to/mutation_preds_set2.json
)

rewrite_preds=True
# Convert array to a comma-separated string
mutation_paths_str=$(IFS=,; echo "${mutation_paths[*]}")
re_run_eval=False

python -m swebench.runtest.run_evaluation_test \
    --predictions_test_path $predictions_test_path \
    --max_workers 12 \
    --timeout 120 \
    --run_id $aug_test_eval_id \
    --mutation_paths $mutation_paths_str \
    --rewrite_preds $rewrite_preds \
    --re_run_eval $re_run_eval \

