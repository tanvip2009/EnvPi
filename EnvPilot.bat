@echo off
cd /d "%~dp0"
wscript.exe //B //Nologo "%~dp0vfd2-env-launch.vbs"
if errorlevel 1 (
    echo Failed to start EnvPilot. Check logs\launcher_error.log
    pause
)
exit /b 0
