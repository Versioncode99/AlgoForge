[CmdletBinding()]
param([switch]$NoBrowser)

$ErrorActionPreference = 'Stop'
$ForgeRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ForgeRoot '.venv\Scripts\python.exe'
$Runtime = Join-Path $ForgeRoot 'data\runtime'
$LogRoot = Join-Path $Runtime 'logs'
$WebUrl = 'http://127.0.0.1:5173'
$ApiHealthUrl = 'http://127.0.0.1:8765/api/v1/health'

try {
    $ApiHealth = Invoke-RestMethod -Uri $ApiHealthUrl -TimeoutSec 2
    $WebHealth = Invoke-WebRequest -Uri $WebUrl -UseBasicParsing -TimeoutSec 2
    if ($ApiHealth.data.status -eq 'ok' -and $WebHealth.StatusCode -eq 200) {
        Write-Host 'AlgoForge is already running.'
        if (-not $NoBrowser) { Start-Process $WebUrl }
        return
    }
} catch {
    # A failed health probe is expected when the local services are stopped.
}

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

# Vite needs a few seconds on a cold start. Opening the browser immediately lands
# the user on a connection-refused page, so wait for the port to answer first.
$Deadline = (Get-Date).AddSeconds(60)
$Ready = $false
while ((Get-Date) -lt $Deadline) {
    try {
        if ((Invoke-WebRequest -Uri $WebUrl -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200) {
            $Ready = $true
            break
        }
    } catch {
        Start-Sleep -Milliseconds 500
    }
}

if ($Ready) {
    Write-Host "Ready. Open $WebUrl - bundled market data is synthetic and uncalibrated."
    if (-not $NoBrowser) { Start-Process $WebUrl }
} else {
    Write-Warning "AlgoForge did not answer on $WebUrl within 60s. Check $LogRoot."
}
