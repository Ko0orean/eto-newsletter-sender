@echo off
setlocal
REM ============================================================
REM  ETO Newsletter Sender - double-click updater (Windows)
REM  Pulls the latest version from GitHub, then starts the app.
REM  Your config.json and CSV files are NOT touched by updates.
REM ============================================================

cd /d "%~dp0"

REM 1) Git must be installed to receive updates
where git >nul 2>nul
if errorlevel 1 (
    echo Git was not found on this computer.
    echo Please install it from https://git-scm.com/download/win
    echo ^(default options are fine^), then run this file again.
    echo.
    pause
    exit /b 1
)

REM 2) Pull the latest version
echo Checking for updates...
git pull --ff-only
if errorlevel 1 (
    echo.
    echo Update failed. Check the internet connection and try again.
    echo If the message above mentions local changes, contact the developer.
    echo.
    pause
    exit /b 1
)

echo.
echo Up to date. Starting the app...
echo.

REM 3) Hand over to the normal launcher (installs packages if needed)
call "Run ETO Newsletter.bat"
endlocal
