input_path=/path/to/preds.json

# instance_ids=instance_gravitational__teleport-1a77b7945a022ab86858029d30ac7ad0d5239d00-vee9b09fb20c43af7e520f57e9239bbcf46b7113d
# instance_ids=instance_ansible__ansible-5e369604e1930b1a2e071fecd7ec5276ebd12cb1-v0f01c69f1e2528b935359cfe578530722bca2c59
instance_ids=all

run_instance_file=fail_keys.txt
# instance_ids=instance_NodeBB__NodeBB-cfc237c2b79d8c731bbfc6cadf977ed530bfd57a-v0495b863a912fbff5749c67e860612b91825407c

run_id=pro_selecet_135
redo=True
use_coverage=False
rewrite_preds=False
coverage_eval=False
must_cover_line=swe_plus_res/extract_line_numbers/exe_line_all/final_results.json


python -m run_test.eval_model_test_patch \
    --input_path  $input_path \
    --scripts_dir run_scripts \
    --run_id $run_id \
    --redo $redo \
    --num_workers 6 \
    --must_cover_line $must_cover_line \
    --rewrite_preds $rewrite_preds \
    --use_coverage $use_coverage \
    --eval_gold_patch true \
    --coverage_eval $coverage_eval \
    --dockerhub_username jefzda \
    --use_local_docker \
    --mem_limit "4g" \
    --run_instance_file $run_instance_file \


    
    
    # --run_instance_file $run_instance_file \
    # --instance_ids $instance_ids \

