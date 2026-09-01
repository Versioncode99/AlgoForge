[CmdletBinding()]
param(
    [switch]$Web,        # open in the browser instead of the desktop app
    [switch]$NoBrowser   # start services only, open nothing
)

$ErrorActionPreference = 'Stop'
$ForgeRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ForgeRoot '.venv\Scripts\python.exe'
$Electron = Join-Path $ForgeRoot 'apps\desktop\node_modules\.bin\electron.cmd'
$WebDist = Join-Path $ForgeRoot 'apps\web\dist\index.html'
$Runtime = Join-Path $ForgeRoot 'data\runtime'
$LogRoot = Join-Path $Runtime 'logs'
$ApiUrl = 'http://127.0.0.1:8765'
$ApiHealthUrl = "$ApiUrl/api/v1/health"

if (-not (Test-Path -LiteralPath $Python)) {
    throw 'Python environment missing. Run: uv sync --all-groups'
}
New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null

function Test-Api {
    try { return (Invoke-RestMethod -Uri $ApiHealthUrl -TimeoutSec 2).data.status -eq 'ok' }
    catch { return $false }
}

# ── Desktop app: it owns the API itself, so just launch it. ──────────────────
if (-not $Web) {
    if ((Test-Path -LiteralPath $Electron) -and (Test-Path -LiteralPath $WebDist)) {
        if (Get-Process electron -ErrorAction SilentlyContinue |
            Where-Object { $_.MainWindowTitle -eq 'AlgoForge' }) {
            Write-Host 'AlgoForge is already open.'
            return
        }
        Start-Process -FilePath $Electron -ArgumentList '.' `
            -WorkingDirectory (Join-Path $ForgeRoot 'apps\desktop') -WindowStyle Hidden
        Write-Host 'AlgoForge starting.'
        return
    }
    Write-Warning 'Desktop shell not built. Falling back to the browser.'
    Write-Warning 'To build it: npm --prefix apps/desktop install; npm run build:web'
}

# ── Browser fallback: API plus the Vite dev server. ──────────────────────────
if (Test-Api) {
    Write-Host 'AlgoForge is already running.'
    if (-not $NoBrowser) { Start-Process "$ApiUrl/" }
    return
}

$Api = Start-Process -FilePath $Python -ArgumentList @(
    '-m', 'uvicorn', 'forge_api.main:app', '--app-dir', (Join-Path $ForgeRoot 'apps\api'),
    '--host', '127.0.0.1', '--port', '8765'
) -WorkingDirectory $ForgeRoot -WindowStyle Hidden -PassThru `
  -RedirectStandardOutput (Join-Path $LogRoot 'api.out.log') `
  -RedirectStandardError (Join-Path $LogRoot 'api.err.log')

@{ api_pid = $Api.Id; started_at = (Get-Date).ToUniversalTime().ToString('o') } |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Runtime 'processes.json') -Encoding utf8

Write-Host "AlgoForge API PID $($Api.Id) started."

# The API serves the built interface, so wait for it before opening anything.
$Deadline = (Get-Date).AddSeconds(60)
$Ready = $false
while ((Get-Date) -lt $Deadline) {
    if (Test-Api) { $Ready = $true; break }
    Start-Sleep -Milliseconds 500
}

if ($Ready) {
    Write-Host "Ready. Open $ApiUrl - paper only; fills are modelled, not calibrated."
    if (-not $NoBrowser) { Start-Process "$ApiUrl/" }
} else {
    Write-Warning "AlgoForge did not answer within 60s. Check $LogRoot."
}
