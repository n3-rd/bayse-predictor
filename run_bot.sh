#!/bin/bash
# Activates the virtual environment and starts the trading bot

# Get the script directory
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "=== Starting Bayse Predictor Bot ==="
if [ ! -d "venv" ]; then
    echo "Error: Virtual environment 'venv' not found. Please create it first."
    exit 1
fi

# Run main bot application
./venv/bin/python main.py
