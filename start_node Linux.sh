#!/bin/bash
echo "Installing dependencies..."
pip install -r requirements.txt
echo "Starting node..."
python3 main.py