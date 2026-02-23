input_path=data.csv

# instance_element-hq__element-web-5e8488c2838ff4268f39db4a8cca7d74eecf5a7e-vnan
instance_ids=instance_gravitational__teleport-eefac60a350930e5f295f94a2d55b94c1988c04e-vee9b09fb20c43af7e520f57e9239bbcf46b7113d

run_id=exe_line_all
redo=True

python -m run_test.get_line_number \
    --input_path $input_path \
    --run_id $run_id \
    --redo $redo \
    --num_workers 4 \
    --dockerhub_username jefzda \
    # --instance_ids $instance_ids
