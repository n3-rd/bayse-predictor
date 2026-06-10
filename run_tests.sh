#!/bin/bash
# Activates the virtual environment and runs the offline test suite and backtester

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "=== Running Bayse Predictor Bot Verification ==="
if [ ! -d "venv" ]; then
    echo "Error: Virtual environment 'venv' not found."
    exit 1
fi

./venv/bin/python run_tests.py
