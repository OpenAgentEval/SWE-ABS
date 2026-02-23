# Run mutations that passed init_test to check if they can pass aug_test

input_path=/path/to/preds.json

run_id=mutation_run_pro_selecet_135
mutation_paths=(
    /path/to/mutation_preds_set1.json
    /path/to/mutation_preds_set2.json
)
rewrite_preds=True
# Convert array to comma-separated string
mutation_paths_str=$(IFS=,; echo "${mutation_paths[*]}")
redo=True
instance_ids=instance_navidrome__navidrome-31799662706fedddf5bcc1a76b50409d1f91d327,instance_navidrome__navidrome-3977ef6e0f287f598b6e4009876239d6f13b686d,instance_future-architect__vuls-8659668177f1feb65963db7a967347a79c5f9c40

python -m run_test.eval_model_test_patch \
    --input_path  $input_path \
    --scripts_dir run_scripts \
    --run_id $run_id \
    --redo $redo \
    --num_workers 4 \
    --mutation_paths $mutation_paths_str \
    --rewrite_preds $rewrite_preds \
    --dockerhub_username jefzda \
    --use_local_docker \
    --mem_limit "6g" \
    --instance_ids $instance_ids \

