@echo off
setlocal
REM ============================================================
REM  ETO Newsletter Sender - double-click launcher (Windows)
REM  Uses the same "python" command that works in your terminal.
REM  If anything fails, this window stays open with the message.
REM ============================================================

cd /d "%~dp0"

REM 1) Find a working Python (prefer "python", matching manual use)
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY where py >nul 2>nul && set "PY=py"
if not defined PY (
    echo Python was not found on this computer.
    echo Please install Python from python.org and tick "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

REM 2) Install packages only if they are actually missing
%PY% -c "import PySide6, requests, markdown" >nul 2>nul
if errorlevel 1 (
    echo Installing required packages, please wait...
    %PY% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Package installation failed. Check the internet connection,
        echo or contact IT if pip is blocked by the proxy.
        echo.
        pause
        exit /b 1
    )
)

REM 3) Launch the app as a module (no reliance on .pyw file association).
REM    Use pythonw for a windowless start when available.
where pythonw >nul 2>nul
if not errorlevel 1 (
    start "" pythonw -m eto_newsletter
    exit /b 0
)

REM Fallback: run with a console; keep it open if the app fails to start
%PY% -m eto_newsletter
if errorlevel 1 (
    echo.
    echo The app failed to start. The error message is shown above.
    echo.
    pause
    exit /b 1
)
endlocal
