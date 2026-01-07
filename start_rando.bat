@echo off
setlocal enabledelayedexpansion

echo ========================================
echo   Rando Cal Bot - Startup Script
echo ========================================
echo.

:: Check Python version
echo Checking Python installation...
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH.
    echo.
    echo Please install Python 3.10 or newer from https://www.python.org/downloads/
    echo IMPORTANT: Check "Add Python to PATH" during installation!
    echo.
    pause
    exit /b 1
)

:: Get Python version and check it's 3.10+
for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYVER=%%i
for /f "tokens=1,2 delims=." %%a in ("%PYVER%") do (
    set PYMAJOR=%%a
    set PYMINOR=%%b
)
if %PYMAJOR% LSS 3 (
    echo ERROR: Python 3.10 or newer required. You have Python %PYVER%
    pause
    exit /b 1
)
if %PYMAJOR%==3 if %PYMINOR% LSS 10 (
    echo ERROR: Python 3.10 or newer required. You have Python %PYVER%
    pause
    exit /b 1
)
echo Found Python %PYVER% - OK!
echo.

:: Check for card data
echo Checking for card data...
if not exist "..\swccg-card-json" (
    echo Card data not found. Downloading...
    echo This may take a minute...
    git --version >nul 2>&1
    if errorlevel 1 (
        echo Git not found. Downloading card data via Python...
        python -c "import urllib.request, zipfile, io; z=zipfile.ZipFile(io.BytesIO(urllib.request.urlopen('https://github.com/swccgpc/swccg-card-json/archive/refs/heads/main.zip').read())); z.extractall('..')" 2>nul
        if exist "..\swccg-card-json-main" (
            rename "..\swccg-card-json-main" "swccg-card-json"
        )
    ) else (
        cd ..
        git clone https://github.com/swccgpc/swccg-card-json.git
        cd new_rando
    )
)
if exist "..\swccg-card-json" (
    echo Card data found - OK!
) else (
    echo WARNING: Could not download card data. Bot may not work correctly.
)
echo.

:: Create virtual environment if needed
echo Checking virtual environment...
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment.
        pause
        exit /b 1
    )
)
echo Virtual environment ready - OK!
echo.

:: Activate virtual environment
call venv\Scripts\activate.bat

:: Install/update requirements
echo Installing dependencies...
pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo ERROR: Failed to install dependencies.
    pause
    exit /b 1
)
echo Dependencies installed - OK!
echo.

:: Check for credentials
if not exist "credentials.py" (
    echo.
    echo ========================================
    echo   First-time Setup: GEMP Credentials
    echo ========================================
    echo.
    echo You need a GEMP account on the TEST server:
    echo https://www.200monkeys.com/gemp-swccg/
    echo.
    echo ^(This is separate from the main GEMP server^)
    echo.
    set /p USERNAME="Enter your GEMP username: "
    set /p PASSWORD="Enter your GEMP password: "
    echo.
    echo # GEMP credentials - keep this file private! > credentials.py
    echo GEMP_USERNAME = '!USERNAME!' >> credentials.py
    echo GEMP_PASSWORD = '!PASSWORD!' >> credentials.py
    echo.
    echo Credentials saved to credentials.py
    echo.
)

:: Start the bot
echo ========================================
echo   Starting Rando Cal Bot
echo ========================================
echo.
echo Server: https://www.200monkeys.com/gemp-swccg-server/
echo Admin panel will open at: http://127.0.0.1:5001
echo.
echo Press Ctrl+C to stop the bot.
echo.

:: Open browser after short delay
start "" cmd /c "timeout /t 3 /nobreak >nul && start http://127.0.0.1:5001"

:: Run the bot
python app.py

:: Deactivate on exit
call venv\Scripts\deactivate.bat
pause
