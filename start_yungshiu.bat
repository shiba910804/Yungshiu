@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo Yungshiu dashboard and crawler launcher
echo ========================================
echo.

where python.exe >nul 2>nul
if errorlevel 1 (
  echo ERROR: Python was not found. Please install Python or add it to PATH.
  pause
  exit /b 1
)

echo [1/4] Checking Python packages...
python.exe -c "import flask" >nul 2>nul
if errorlevel 1 (
  echo Flask is not installed. Installing packages from requirements.txt...
  python.exe -m pip install -r requirements.txt
  if errorlevel 1 (
    echo ERROR: Failed to install Python packages.
    pause
    exit /b 1
  )
)

echo.
echo [2/4] Starting dashboard if it is not already running...
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "$project=(Resolve-Path '.').Path; $running=Get-CimInstance Win32_Process -Filter \"name like 'python%%'\" | Where-Object { $_.CommandLine -like '*app.py*' }; if ($running) { Write-Host 'Dashboard already running.' } else { Start-Process -FilePath 'python.exe' -ArgumentList 'app.py' -WorkingDirectory $project -WindowStyle Hidden | Out-Null; Write-Host 'Dashboard started.' }"
if errorlevel 1 (
  echo ERROR: Failed to start dashboard.
  pause
  exit /b 1
)

echo.
echo [3/4] Ensuring hourly crawler task is enabled...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_realtime_task.ps1"
if errorlevel 1 (
  echo ERROR: Failed to create or start the crawler task.
  pause
  exit /b 1
)

echo.
echo [4/4] Opening dashboard...
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:5000/"

echo.
echo Dashboard:
echo   Computer: http://127.0.0.1:5000/
echo.
echo Phone on the same Wi-Fi:
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' -and $_.PrefixOrigin -ne 'WellKnown' } | ForEach-Object { Write-Host ('  http://' + $_.IPAddress + ':5000/  (' + $_.InterfaceAlias + ')') }"

echo.
echo Done. You can close this window; dashboard and crawler will keep running.
pause
