$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

Write-Host "Yungshiu setup started" -ForegroundColor Cyan

function Test-CommandExists {
    param([string]$Command)
    $null -ne (Get-Command $Command -ErrorAction SilentlyContinue)
}

if (-not (Test-CommandExists "python")) {
    Write-Host "ERROR: python command was not found. Please install Python 3.10+ first." -ForegroundColor Red
    exit 1
}

$pythonVersion = python -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
Write-Host "Python: $pythonVersion"

$versionOk = python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Python 3.10+ is required." -ForegroundColor Red
    exit 1
}

foreach ($dir in @("data", "exports", "logs", "static", "static\vendor", "templates")) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir | Out-Null
        Write-Host "Created directory: $dir"
    }
}

if (Test-Path "requirements.txt") {
    Write-Host "Installing Python dependencies..."
    python -m pip install -r requirements.txt
} else {
    Write-Host "WARNING: requirements.txt not found." -ForegroundColor Yellow
}

$chartPath = Join-Path $ScriptDir "static\vendor\chart.umd.min.js"
if (-not (Test-Path $chartPath)) {
    Write-Host "Downloading local Chart.js asset..."
    Invoke-WebRequest `
        -Uri "https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js" `
        -OutFile $chartPath
} else {
    Write-Host "Chart.js asset exists: static\vendor\chart.umd.min.js"
}

Write-Host "Checking Python syntax..."
python -m py_compile scraper.py app.py health_check.py

if (Test-Path "data\scraper.sqlite3") {
    Write-Host "Running health check..."
    python health_check.py
} else {
    Write-Host "Database not found yet. Run this to create data:" -ForegroundColor Yellow
    Write-Host "  python scraper.py --once --fx-history --fx-history-years 10 --export --report"
}

Write-Host "Setup finished" -ForegroundColor Green
