@echo off
setlocal
cd /d "%~dp0.."
if not defined OBSERVER_SERVICE_PYTHON set "OBSERVER_SERVICE_PYTHON=%CD%\.venv\Scripts\python.exe"
if not exist "%OBSERVER_SERVICE_PYTHON%" (
  echo Create .venv and install linux\requirements.txt for the local mock service.
  pause
  exit /b 1
)
echo Local mock only. Token: local-observer-demo-2026
"%OBSERVER_SERVICE_PYTHON%" -m linux.service --mode mock --token local-observer-demo-2026
if errorlevel 1 pause
