#   bash-only   topk_swe_data
vaild_model_path=$HOME/topk_swe_data

# vaild_model_name=20250522_tools_claude-4-sonnet,20250522_tools_claude-4-opus
# Get all filenames in the directory (non-hidden, non-directory), join as a comma-separated string
vaild_model_name=$(ls "$vaild_model_path" | tr '\n' ',' | sed 's/,$//')
predictions_test_path=/path/to/preds.json
run_id=top30_pipline
instance_ids=django__django-11206
re_run_eval=True

python -m swebench.runtest.run_evaluation_test \
    --predictions_test_path $predictions_test_path \
    --vaild_model_name $vaild_model_name \
    --vaild_model_path $vaild_model_path \
    --max_workers 12 \
    --timeout 120 \
    --run_id  $run_id \
    --re_run_eval $re_run_eval \
    # --instance_ids $instance_ids \

