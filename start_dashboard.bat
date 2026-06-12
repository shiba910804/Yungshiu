@echo off
cd /d "%~dp0"
start "Yungshiu Dashboard Server" /B python app.py
timeout /t 2 /nobreak >nul
start "" http://127.0.0.1:5000
echo Dashboard is running at http://127.0.0.1:5000
pause
