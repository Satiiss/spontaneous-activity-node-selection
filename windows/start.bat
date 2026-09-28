@echo off
setlocal
cd /d "%~dp0.."
if not defined OBSERVER_UI_PYTHON set "OBSERVER_UI_PYTHON=%CD%\.venv\Scripts\python.exe"
if not exist "%OBSERVER_UI_PYTHON%" (
  echo Create .venv and install windows\requirements.txt. See README.md.
  pause
  exit /b 1
)
"%OBSERVER_UI_PYTHON%" -m windows.app %*
if errorlevel 1 pause
