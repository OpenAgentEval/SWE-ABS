
instance_ids=instance_gravitational__teleport-c335534e02de143508ebebc7341021d7f8656e8f


# run_instance_file=fail_keys.txt
input_path=/path/to/preds.json
run_id=pro_selecet_135_aug_1
redo=True
rewrite_preds=True


use_key=run_success_no_equ
stage_name=no_equ_mutation_aug
iteration=1



python -m run_test.eval_model_test_patch_aug \
    --input_path $input_path \
    --scripts_dir run_scripts \
    --run_id $run_id \
    --redo $redo \
    --num_workers 6 \
    --use_key $use_key \
    --stage_name $stage_name \
    --iteration $iteration \
    --dockerhub_username jefzda \
    --use_local_docker \
    --mem_limit "4g" \
    --rewrite_preds $rewrite_preds \
    # --instance_ids $instance_ids \



    # --instance_ids $instance_ids \
    # --run_instance_file $run_instance_file \


