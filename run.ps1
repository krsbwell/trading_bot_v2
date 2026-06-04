# Trading Bot launcher — runs with the correct venv Python every time
# Usage:  .\run.ps1
$python = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
& $python "$PSScriptRoot\main.py"
