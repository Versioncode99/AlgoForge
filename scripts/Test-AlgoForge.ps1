[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ForgeRoot = Split-Path -Parent $PSScriptRoot
Push-Location $ForgeRoot
try {
    & '.\.venv\Scripts\python.exe' -m ruff check packages apps tests
    & '.\.venv\Scripts\python.exe' -m mypy packages apps
    & '.\.venv\Scripts\python.exe' -m pytest
    & '.\.venv\Scripts\python.exe' scripts/check_wave_prompts.py
    npm run build:web
    npm run test:web
} finally {
    Pop-Location
}
