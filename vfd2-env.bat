@echo off
REM Legacy launcher — use EnvPilot.bat instead
cd /d "%~dp0"
call "%~dp0EnvPilot.bat"
exit /b %ERRORLEVEL%
