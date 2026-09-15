@echo off
REM Sole launcher for EnvPilot. -STA is required for WinForms; -WindowStyle
REM Hidden suppresses the PowerShell console, not the GUI. This console window
REM flashes briefly, which is the cost of having no separate VBScript helper.
setlocal
cd /d "%~dp0"

if not exist "logs" mkdir "logs"

if not exist "vfd2-env.ps1" (
    echo EnvPilot: vfd2-env.ps1 was not found in "%~dp0"
    echo %DATE% %TIME% ^| vfd2-env.ps1 missing in "%~dp0" >> "logs\launcher_error.log"
    pause
    exit /b 1
)

start "" powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -STA -File "%~dp0vfd2-env.ps1"

endlocal
exit /b 0
