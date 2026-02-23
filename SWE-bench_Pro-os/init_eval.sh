# instance_future-architect__vuls-0ec945d0510cdebf92cdd8999f94610772689f14
# instance_tutao__tutanota-fe240cbf7f0fdd6744ef7bef8cb61676bcdbb621-vc4e41fd0029957297843cb9dec4a25c7c756f029

instance_ids=instance_future-architect__vuls-407407d306e9431d6aa0ab566baa6e44e5ba2904

patch_path=/path/to/preds.json
OUTPUT=logs/mutation_center/mutation_pro_selecet_135
use_coverage=false
eval_mutation=true

python -m run_test.init_eval \
    --raw_sample_path data.csv \
    --patch_path $patch_path \
    --output_dir $OUTPUT \
    --eval_mutation $eval_mutation \
    --scripts_dir run_scripts \
    --num_workers 4 \
    --use_local_docker \
    --dockerhub_username jefzda \
    # --instance_ids $instance_ids \

# docker run -it -v log/result/instance_gravitational__teleport-eefac60a350930e5f295f94a2d55b94c1988c04e-vee9b09fb20c43af7e520f57e9239bbcf46b7113d/workspace:/workspace --entrypoint /bin/bash jefzda/sweap-images:gravitational.teleport-gravitational__teleport-eefac60a350930e5f295f94a2d55b94c1988c04e-vee9b09fb20c43af7e520f57e9239bbcf46b7113
# jefzda/sweap-images:gravitational.teleport-gravitational__teleport-eefac60a350930e5f295f94a2d55b94c1988c04e-vee9b09fb20c43af7e520f57e9239bbcf46b7113


# docker run -d --name minisweagent-7414aa7b -w /app --rm --entrypoint /bin/bash jefzda/sweap-images:gravitational.teleport-gravitational__teleport-eefac60a350930e5f295f94a2d55b94c1988c04e-vee9b09fb20c43af7e520f57e9239bbcf46b7113 -c 'while true; do sleep 2h; done'


