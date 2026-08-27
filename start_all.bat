@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0backend"
if not exist logs mkdir logs
if not exist data mkdir data

if exist data\supervisor.pid (
    set /p OLD_PID=<data\supervisor.pid
    if defined OLD_PID (
        rem tasklist prints one info line ("INFO: No tasks are running...")
        rem instead of an error when nothing matches both filters - that line
        rem never contains "python.exe", so searching for that exact text is
        rem what tells a genuine match apart from "nothing found", not just
        rem counting output lines (which the info line would also count).
        tasklist /FI "PID eq !OLD_PID!" /FI "IMAGENAME eq python.exe" /NH 2>nul | findstr /I "python.exe" >nul
        if !errorlevel! equ 0 (
            echo Found a previous run still registered ^(PID !OLD_PID!^) - stopping it first...
            taskkill /F /T /PID !OLD_PID! >nul 2>nul
        )
    )
    del /f /q data\supervisor.pid >nul 2>nul
)

set LOGFILE=%~dp0backend\logs\start_all.log
echo ============================================== >> "%LOGFILE%"
echo %date% %time% - start_all.bat launched >> "%LOGFILE%"

where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: "python" was not found on PATH.
    echo ERROR: "python" was not found on PATH. >> "%LOGFILE%"
    pause
    exit /b 1
)

if exist venv (
    echo Checking existing virtual environment...
    venv\Scripts\python.exe -c "import fastapi" >nul 2>nul
    if errorlevel 1 (
        echo Existing venv is unusable - rebuilding it...
        echo Existing venv failed self-check - rebuilding >> "%LOGFILE%"
        rmdir /s /q venv
    )
)

if not exist venv (
    echo First-time setup: creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo ERROR: "python -m venv venv" failed.
        echo ERROR: "python -m venv venv" failed. >> "%LOGFILE%"
        pause
        exit /b 1
    )
)

call venv\Scripts\activate.bat

echo Checking dependencies are up to date...
pip install -r requirements.txt --disable-pip-version-check
if errorlevel 1 (
    echo ERROR: "pip install -r requirements.txt" failed.
    echo ERROR: dependency installation failed. >> "%LOGFILE%"
    pause
    exit /b 1
)

echo Checking the IOCL browser runtime...
python -m playwright install chromium
if errorlevel 1 (
    echo ERROR: Playwright Chromium installation failed.
    echo ERROR: Playwright Chromium installation failed. >> "%LOGFILE%"
    pause
    exit /b 1
)

if "%API_WORKERS%"=="" set API_WORKERS=2
if "%JOB_WORKER_PROCESSES%"=="" set JOB_WORKER_PROCESSES=2

echo Starting API, processing workers, and scheduler on http://0.0.0.0:2805 ...
echo Starting supervised multi-process suite >> "%LOGFILE%"
python -m app.supervisor
set EXIT_CODE=%errorlevel%
if not "%EXIT_CODE%"=="0" (
    echo.
    echo ERROR: the suite exited unexpectedly - see backend\logs.
    echo Suite exited with code %EXIT_CODE% >> "%LOGFILE%"
    pause
)
exit /b %EXIT_CODE%
