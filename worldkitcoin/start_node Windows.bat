@echo off
title WorldKitCoin Node
echo Installing dependencies...
pip install -r requirements.txt > nul
echo Starting node...
python main.py
pause