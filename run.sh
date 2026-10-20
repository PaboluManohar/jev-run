#!/bin/bash
# run.sh - Run all project commands in sequence

set -e  # Stop execution if any command fails

make clean-ports
sleep 0.1

make install
sleep 0.1

make build
sleep 0.1

make run