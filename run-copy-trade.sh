#!/bin/bash
# Activates the virtual environment and starts the copy trading runner

# Get the script directory
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "=== Starting Bayse Copy Trade Bot ==="
if [ ! -d "venv" ]; then
    echo "Error: Virtual environment 'venv' not found. Please create it first."
    exit 1
fi

# Run copy trading runner
./venv/bin/python copy_trade_runner.py
