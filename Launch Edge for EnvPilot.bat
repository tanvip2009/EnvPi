@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "PROFILE=%SCRIPT_DIR%.edge-automation-profile"
if not exist "%PROFILE%" mkdir "%PROFILE%"

where msedge >nul 2>&1
if %ERRORLEVEL%==0 (
    start "" msedge --remote-debugging-port=9222 --user-data-dir="%PROFILE%"
    goto :done
)

if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" (
    start "" "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" --remote-debugging-port=9222 --user-data-dir="%PROFILE%"
    goto :done
)

if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" (
    start "" "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" --remote-debugging-port=9222 --user-data-dir="%PROFILE%"
    goto :done
)

echo Could not find Microsoft Edge.
exit /b 1

:done
echo Edge started with EnvPilot profile on debug port 9222.
echo Deploy will reuse this browser when it is still open.
endlocal
