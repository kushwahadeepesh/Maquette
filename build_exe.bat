@echo off
setlocal
cd /d "%~dp0"
title Maquette - build exe
if not exist ".venv\Scripts\python.exe" (
    call setup.bat --nopause
    if errorlevel 1 exit /b 1
)
echo.
echo  Building Maquette.exe (2-4 minutes)...
".venv\Scripts\python.exe" -m pip install pyinstaller --disable-pip-version-check -q
if errorlevel 1 goto :fail

".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed ^
  --name Maquette --icon "assets\maquette.ico" --add-data "assets;assets" ^
  --collect-data trimesh --collect-all glfw --collect-all glcontext ^
  --exclude-module matplotlib --exclude-module scipy --exclude-module pandas ^
  --exclude-module sklearn --exclude-module skimage --exclude-module lxml ^
  --exclude-module IPython --exclude-module pytest --exclude-module PyQt5 --exclude-module PySide6 ^
  maquette.py
if errorlevel 1 goto :fail

echo.
echo  Testing the build...
"dist\Maquette\Maquette.exe" --selftest "%TEMP%\maquette_selftest.png"
if not exist "%TEMP%\maquette_selftest.png" goto :fail
echo.
echo  Done:  dist\Maquette\Maquette.exe
echo  (the whole dist\Maquette folder is the app - keep the files together)
echo.
if /i not "%~1"=="--nopause" pause
exit /b 0

:fail
echo.
echo  Build failed - see the messages above.
pause
exit /b 1
