@echo off
setlocal
title Fake Shop Checker
cd /d "%~dp0"

echo.
echo ============================================================
echo                 Fake Shop Checker for Windows
echo ============================================================
echo.

where py >nul 2>&1
if errorlevel 1 goto python_missing

if not exist ".venv\Scripts\python.exe" (
    echo [1/4] Creating the private Python environment...
    py -3 -m venv .venv
    if errorlevel 1 goto setup_failed
) else (
    echo [1/4] Private Python environment found.
)

if exist ".venv\.fakeshop-ready-v1" goto setup_ready

echo [2/4] Updating the installer...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto setup_failed

echo [3/4] Installing Fake Shop Checker packages...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto arm_install
goto install_browser

:install_browser
echo [4/4] Installing the private Chromium browser...
".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 goto setup_failed
echo ready>".venv\.fakeshop-ready-v1"
goto setup_ready

:setup_ready
echo [2/4] Packages are ready.
echo [3/4] Chromium is ready.
echo [4/4] Setup is complete.

echo.
echo Starting Fake Shop Checker...
echo Keep this window open while you use the application.
echo Press Ctrl+C here when you want to stop it.
echo.
start "" powershell.exe -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 4; Start-Process 'http://127.0.0.1:8000'"
".venv\Scripts\python.exe" -m fakeshop.web
set APP_EXIT=%ERRORLEVEL%
if not "%APP_EXIT%"=="0" goto app_failed
exit /b 0

:arm_install
echo.
echo The standard package installation failed.
echo Trying the Windows ARM-compatible installation method...
".venv\Scripts\python.exe" -m pip install ddgs --no-deps
if errorlevel 1 goto setup_failed
".venv\Scripts\python.exe" -m pip install "click>=8.1.8" "primp>=1.2.3" "lxml>=4.9.4" "httpx[http2,socks]>=0.28.1" "fake-useragent>=2.2.0" playwright openpyxl requests python-dotenv fastapi uvicorn jinja2 python-multipart yfinance
if errorlevel 1 goto setup_failed
goto install_browser

:python_missing
echo Python was not found on this computer.
echo.
echo 1. Install Python from https://www.python.org/downloads/windows/
echo 2. Select "Add python.exe to PATH" in the installer.
echo 3. Restart the computer, then double-click start_windows.bat again.
echo.
start "" "https://www.python.org/downloads/windows/"
pause
exit /b 1

:setup_failed
echo.
echo Setup could not be completed.
echo Check the internet connection, then close this window and try again.
echo If it fails again, copy the last error shown above when asking for help.
echo.
pause
exit /b 1

:app_failed
echo.
echo Fake Shop Checker stopped because of an error.
echo Copy the last error shown above when asking for help.
echo.
pause
exit /b %APP_EXIT%
