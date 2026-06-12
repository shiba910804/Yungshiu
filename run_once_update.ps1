$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

python scraper.py --once --history-years 10 --fx-history-days 14 --export --report
