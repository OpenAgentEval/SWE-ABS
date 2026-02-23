#!/bin/bash
set -e
cd /app

npx jest --verbose "$@"
