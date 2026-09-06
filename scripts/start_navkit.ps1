# DebugStudio launcher: start server (separate process) and open browser.
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts/start_navkit.ps1 [-Module treasure] [-Port 8765]
# Options:
#   -Module  module adapter id (default treasure; racing reserved), maps to server.py --module
#   -Port    HTTP port (default 8765)
#
# NOTE: keep this file ASCII-only. PowerShell 5.1 reads UTF-8-without-BOM scripts
# as ANSI/GBK, so any non-ASCII char breaks parsing.

param(
    [string]$Module = 'treasure',
    [int]$Port = 8765
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path $PSScriptRoot -Parent
$ServerPy = Join-Path $RepoRoot 'tools\navkit\server.py'
if (-not (Test-Path $ServerPy)) { throw "DebugStudio server not found: $ServerPy" }

$Url = "http://localhost:$Port"

# resolve python interpreter: prefer the project venv (has cv2/opencv), else PATH 'python'
$PyCandidates = @(
    (Join-Path $RepoRoot '.venv\Scripts\python.exe'),
    'python'
)
$PyExe = $null
foreach ($c in $PyCandidates) {
    if (Test-Path $c) { $PyExe = $c; break }
}
if (-not $PyExe) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $PyExe = $cmd.Source }
}
if (-not $PyExe) { throw "no python interpreter found (.venv or PATH)" }
$PyExeFull = (Resolve-Path $PyExe).Path

# TCP port probe (more reliable than HTTP request against localhost in PS 5.1)
function Test-UrlReady([int]$TimeoutSec = 20) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        $c = New-Object System.Net.Sockets.TcpClient
        try {
            $c.Connect('127.0.0.1', $Port)
            $c.Close()
            return $true
        } catch {
            Start-Sleep -Milliseconds 400
        } finally {
            $c.Dispose()
        }
    }
    return $false
}

# 1) reuse an already-listening instance
$existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "[DebugStudio] port $Port already in use; opening browser against existing instance." -ForegroundColor Yellow
    Start-Process $Url
    exit 0
}

# 2) start server as an independent process (survives this script exiting)
$serverOut = Join-Path $env:TEMP "navkit_$Port.out.log"
$serverErr = Join-Path $env:TEMP "navkit_$Port.err.log"
Write-Host "[DebugStudio] starting server (module=$Module) via $PyExeFull at $Url ..." -ForegroundColor Cyan
$proc = Start-Process -FilePath $PyExeFull `
    -ArgumentList @($ServerPy, "--module", $Module, "--port", "$Port") `
    -WorkingDirectory $RepoRoot -NoNewWindow -PassThru `
    -RedirectStandardOutput $serverOut -RedirectStandardError $serverErr

# 3) wait for readiness, then open browser
if (Test-UrlReady -TimeoutSec 20) {
    Write-Host "[DebugStudio] server ready. Opening browser ..." -ForegroundColor Green
    Start-Process $Url
    Write-Host "[DebugStudio] launched (PID=$($proc.Id)). To stop, kill the python process." -ForegroundColor Cyan
} else {
    Write-Warning "[DebugStudio] wait timed out; server may have failed to start."
    if (Test-Path $serverErr) { Write-Warning ("stderr tail:"); Get-Content $serverErr -Tail 15 }
    Write-Warning ("Run manually: $PyExeFull $ServerPy --module $Module --port $Port")
}