#!/bin/bash
# setup.sh - Unix/Linux/Git Bash setup script

echo "Creating virtual environment..."
python -m venv .venv

echo "Activating virtual environment..."
source .venv/Scripts/activate || source .venv/bin/activate

echo "Installing dependencies..."
pip install -r requirements.txt

echo "Installing Playwright Chromium..."
playwright install chromium

echo "Setup complete!"
