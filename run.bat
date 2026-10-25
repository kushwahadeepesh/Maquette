@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    call setup.bat --nopause
    if errorlevel 1 exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "%~dp0maquette.py" %*
