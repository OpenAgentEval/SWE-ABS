
# This script verifies whether mutations pass the init test
mutation_predictions_paths=(
    /path/to/mutation_preds_set1.json
    /path/to/mutation_preds_set2.json
)
instance_ids=django__django-11740,django__django-15280,django__django-15037,django__django-9296

# Step 1: loop through the main evaluation, verify each mutation passes init_test, exit on failure
for mutation_predictions_path in "${mutation_predictions_paths[@]}"; do
    # Extract the second-to-last directory from the path as run_id
    run_id=$(basename "$(dirname "$mutation_predictions_path")")

    # run_id=mutation_run_django_all_gpt5_debug

    echo "🔍 使用预测文件路径: $mutation_predictions_path"
    python -m swebench.runtest.run_evaluation \
        --predictions_path "$mutation_predictions_path" \
        --max_workers 8 \
        --run_id "$run_id" \
        --dataset_name princeton-nlp/SWE-bench_Verified \
        --eval_mutation True \
        # --instance_ids $instance_ids 

    # Check the exit status of the last command
    if [ $? -ne 0 ]; then
        echo "❌ Error: swebench.runtest.run_evaluation failed for run_id '$run_id'. Aborting further steps."
        exit 1
    fi
done

