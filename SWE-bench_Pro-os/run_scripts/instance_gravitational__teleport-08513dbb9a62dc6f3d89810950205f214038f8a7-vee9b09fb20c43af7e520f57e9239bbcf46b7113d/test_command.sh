#!/bin/bash
set -e
cd /app

CGO_ENABLED=1 go test -v ./... "$@"
