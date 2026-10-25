@echo off
setlocal
cd /d "%~dp0"
title Maquette setup
echo.
echo  ===== Maquette setup =====
echo.

set "PY="
for %%V in (3.12 3.11 3.13 3.10) do (
    if not defined PY (
        py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
    )
)
if not defined PY (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo  Python was not found.
    echo  Install Python 3.12 from https://www.python.org/downloads/
    echo  and tick "Add python.exe to PATH" during install.
    echo  Or run this in a terminal:  winget install Python.Python.3.12
    echo.
    pause
    exit /b 1
)
echo  Using: %PY%

if not exist ".venv\Scripts\python.exe" (
    echo  Creating virtual environment...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo  Failed to create the virtual environment.
        pause
        exit /b 1
    )
)

echo  Installing packages - this takes a minute the first time...
".venv\Scripts\python.exe" -m pip install --upgrade pip --disable-pip-version-check -q
".venv\Scripts\python.exe" -m pip install -r requirements.txt --disable-pip-version-check
if errorlevel 1 (
    echo.
    echo  Package install failed. Check your internet connection and try again.
    pause
    exit /b 1
)
if not exist "models" mkdir "models"
echo.
echo  Done! Double-click run.bat to start Maquette.
echo  To build an installer instead, double-click build_installer.bat
echo.
if /i not "%~1"=="--nopause" pause
exit /b 0
