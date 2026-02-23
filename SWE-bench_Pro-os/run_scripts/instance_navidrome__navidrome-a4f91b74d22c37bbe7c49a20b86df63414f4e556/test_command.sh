#!/bin/bash
set -e
cd /app

go test -v ./... "$@"
