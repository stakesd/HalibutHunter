@echo off
REM ============================================================
REM  Halibut Predictor - San Diego & Mission Bay
REM  Updates dependencies, clears cached data so fresh NOAA/OSM/
REM  NDBC data is pulled, then launches the dashboard.
REM ============================================================

REM Run from this script's own folder regardless of where it's launched.
cd /d "%~dp0"

echo.
echo ================================================================
echo   HALIBUT PREDICTOR  -  startup
echo ================================================================

REM --- locate Python --------------------------------------------------
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found on your PATH.
    echo         Install Python 3.10+ from https://www.python.org/downloads/
    echo         and tick "Add Python to PATH" during setup.
    pause
    exit /b 1
)

REM --- update / install dependencies ----------------------------------
echo.
echo [1/3] Updating Python dependencies (requirements.txt)...
python -m pip install --upgrade pip >nul 2>nul
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Dependency install failed. See messages above.
    pause
    exit /b 1
)

REM --- clear cached data so the app refetches live data ---------------
echo.
echo [2/3] Clearing cached data (forces fresh tide / bathy / shore fetch)...
python -m streamlit cache clear >nul 2>nul

REM --- launch the dashboard -------------------------------------------
echo.
echo [3/3] Launching dashboard... a browser tab will open shortly.
echo       (Press Ctrl+C in this window to stop the server.)
echo.
python -m streamlit run app.py

REM Keep the window open if Streamlit exits with an error.
if errorlevel 1 pause
