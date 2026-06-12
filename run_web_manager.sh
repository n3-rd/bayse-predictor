#!/bin/bash
# Activates the virtual environment and starts the unified web manager

# Get the script directory
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "=== Starting Bayse Unified Web Manager ==="
if [ ! -d "venv" ]; then
    echo "Error: Virtual environment 'venv' not found. Please create it first."
    exit 1
fi

# Run the web manager
./venv/bin/python web_manager.py
