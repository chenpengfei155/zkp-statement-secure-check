#!/bin/bash
set -e

echo "=== CirVerify Setup ==="

# Create venv if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate venv and install packages
echo "Installing packages..."
source venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt
pip install -q -e .

# Run demo
echo "Running demo.circom..."
cirverify demo.circom

echo ""
echo "Done! To analyze other circuits:"
echo "  source venv/bin/activate"
echo "  cirverify <circuit.circom>"

