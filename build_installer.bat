@echo off
setlocal
cd /d "%~dp0"
title Maquette - build installer

call build_exe.bat --nopause
if errorlevel 1 exit /b 1

call :find_iscc
if not defined ISCC (
    echo.
    echo  Inno Setup 6 is needed to make the installer. Installing it with winget...
    winget install --id JRSoftware.InnoSetup -e --accept-package-agreements --accept-source-agreements
    call :find_iscc
)
if not defined ISCC (
    echo.
    echo  Could not find Inno Setup. Install it from https://jrsoftware.org/isdl.php
    echo  then run build_installer.bat again.
    echo  Your app is already built in dist\Maquette - it works without the installer.
    pause
    exit /b 1
)

echo.
echo  Creating installer...
"%ISCC%" /Q installer.iss
if errorlevel 1 (
    echo  Installer build failed.
    pause
    exit /b 1
)
echo.
echo  Done!  installer_output\Maquette-Setup-1.0.0.exe
echo.
start "" "installer_output"
pause
exit /b 0

:find_iscc
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
exit /b 0
