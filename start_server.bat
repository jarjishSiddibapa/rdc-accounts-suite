@echo off
rem Backward-compatible entry point. The suite now requires its API,
rem processing workers, and scheduler to be supervised together.
call "%~dp0start_all.bat"
exit /b %errorlevel%
