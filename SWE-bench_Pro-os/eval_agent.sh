# Path to your model predictions file
input_path=/path/to/preds.json

instance_ids=instance_ansible__ansible-5e369604e1930b1a2e071fecd7ec5276ebd12cb1-v0f01c69f1e2528b935359cfe578530722bca2c59

vaild_model_path=$HOME/swe-pro-data

# Get all filenames in the directory (non-hidden, non-directory), join as comma-separated string
vaild_model_name=$(ls "$vaild_model_path" | tr '\n' ',' | sed 's/,$//')
# vaild_model_name=claude-45haiku-10222025


run_id=pro_selecet_135
redo=True


python -m run_test.eval_model_test_patch \
    --input_path  $input_path \
    --scripts_dir run_scripts \
    --run_id $run_id \
    --redo $redo \
    --num_workers 6 \
    --vaild_model_name $vaild_model_name \
    --vaild_model_path $vaild_model_path \
    --dockerhub_username jefzda \
    --use_local_docker \
    --mem_limit "4g" \
    # --instance_ids $instance_ids \

