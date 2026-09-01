[CmdletBinding()]
param([switch]$NoBrowser)

$ErrorActionPreference = 'Stop'
$ForgeRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ForgeRoot '.venv\Scripts\python.exe'
$Runtime = Join-Path $ForgeRoot 'data\runtime'
$LogRoot = Join-Path $Runtime 'logs'

if (-not (Test-Path -LiteralPath $Python)) {
    throw 'Python environment missing. Run: uv sync --all-groups'
}
New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null

$Api = Start-Process -FilePath $Python -ArgumentList @(
    '-m', 'uvicorn', 'forge_api.main:app', '--app-dir', (Join-Path $ForgeRoot 'apps\api'),
    '--host', '127.0.0.1', '--port', '8765'
) -WorkingDirectory $ForgeRoot -WindowStyle Hidden -PassThru `
  -RedirectStandardOutput (Join-Path $LogRoot 'api.out.log') `
  -RedirectStandardError (Join-Path $LogRoot 'api.err.log')

$Web = Start-Process -FilePath 'npm.cmd' -ArgumentList @(
    '--prefix', (Join-Path $ForgeRoot 'apps\web'), 'run', 'dev', '--', '--host', '127.0.0.1'
) -WorkingDirectory $ForgeRoot -WindowStyle Hidden -PassThru `
  -RedirectStandardOutput (Join-Path $LogRoot 'web.out.log') `
  -RedirectStandardError (Join-Path $LogRoot 'web.err.log')

@{ api_pid = $Api.Id; web_pid = $Web.Id; started_at = (Get-Date).ToUniversalTime().ToString('o') } |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Runtime 'processes.json') -Encoding utf8

Write-Host "AlgoForge API PID $($Api.Id) and web PID $($Web.Id) started."
Write-Host 'Open http://127.0.0.1:5173 - all bundled output is sample and uncalibrated.'
if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:5173' }
