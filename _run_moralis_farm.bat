@echo off
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
python -X utf8 -u moralis_key_farmer.py signup -w 5 < NUL >> moralis_farm.log 2>&1
