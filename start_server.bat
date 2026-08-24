@echo off
setlocal
cd /d "%~dp0backend"
if not exist logs mkdir logs

rem Everything below is also written to backend\logs\start_server.log, so if
rem this window closes too fast to read (double-click, Task Scheduler with
rem no visible console, etc.) the reason is still on disk.
set LOGFILE=%~dp0backend\logs\start_server.log
echo ============================================== >> "%LOGFILE%"
echo %date% %time% - start_server.bat launched >> "%LOGFILE%"

where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: "python" was not found on PATH. Install Python and make sure it's added to PATH, then try again.
    echo ERROR: "python" was not found on PATH. >> "%LOGFILE%"
    pause
    exit /b 1
)

rem A venv is NOT relocatable - it bakes in the absolute path of the Python
rem install that created it. If this folder was zipped up on one machine and
rem pasted onto another (a common way to deploy this), the venv folder
rem exists but silently points at a Python install that isn't on THIS
rem machine, and everything below would fail. Self-check and rebuild rather
rem than trust that an existing venv folder is actually usable here.
if exist venv (
    echo Checking existing virtual environment...
    venv\Scripts\python.exe -c "import fastapi" >nul 2>nul
    if errorlevel 1 (
        echo Existing venv looks broken or was copied from a different machine - rebuilding it...
        echo Existing venv failed self-check - rebuilding >> "%LOGFILE%"
        rmdir /s /q venv
    )
)

if not exist venv (
    echo First-time setup: creating virtual environment and installing dependencies...
    echo First-time setup: creating venv and installing dependencies >> "%LOGFILE%"
    python -m venv venv
    if errorlevel 1 (
        echo ERROR: "python -m venv venv" failed.
        echo ERROR: "python -m venv venv" failed. >> "%LOGFILE%"
        pause
        exit /b 1
    )
    call venv\Scripts\activate.bat
    pip install -r requirements.txt
    if errorlevel 1 (
        echo ERROR: "pip install -r requirements.txt" failed.
        echo ERROR: "pip install -r requirements.txt" failed. >> "%LOGFILE%"
        pause
        exit /b 1
    )
) else (
    call venv\Scripts\activate.bat
)

echo Starting server on http://0.0.0.0:2805 ...
echo Starting uvicorn >> "%LOGFILE%"
python -m uvicorn app.main:app --host 0.0.0.0 --port 2805
if errorlevel 1 (
    echo.
    echo ERROR: the server exited unexpectedly - see backend\logs\start_server.log and backend\logs\app.log for details.
    echo Server exited with code %errorlevel% >> "%LOGFILE%"
    pause
    exit /b 1
)
