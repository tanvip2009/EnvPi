@echo off
setlocal

cd /d "%~dp0"

echo ============================================
echo Jenkins Deployment Utility Launcher
echo ============================================
echo.

set /p JENKINS_USERNAME=Enter Jenkins username (NTNET user, e.g., gauracha): 
set /p PROJECT_NAME=Enter ProjectName (OGW/O2A/SKY): 
set /p RELEASE_NAME=Enter Release_name (e.g., 4000_WAVE11 or 4000_WAVE10_PCK02): 
set /p BUILD_NUMBER=Enter BuildNumber (must exist for selected ProjectName+Release_name): 
set /p FOLDER_NAME=Enter Folder_Name (e.g., 26.06.OMA / 26.02.OMA / 26.06.OMI): 

echo.
echo Detecting Python...
echo.

set "PY_CMD="
where py >nul 2>nul
if %errorlevel%==0 set "PY_CMD=py -3"

if not defined PY_CMD (
    where python >nul 2>nul
    if %errorlevel%==0 set "PY_CMD=python"
)

if not defined PY_CMD (
    echo [ERROR] Python not found in PATH.
    echo Install Python 3 and ensure 'py' or 'python' works in terminal.
    pause
    exit /b 1
)

echo Using: %PY_CMD%
%PY_CMD% --version
echo.
echo Checking Selenium dependency...
%PY_CMD% -m pip show selenium >nul 2>nul
if not %errorlevel%==0 (
    echo Selenium not found. Installing now...
    %PY_CMD% -m pip install --user -U selenium
    if not %errorlevel%==0 (
        echo [ERROR] Failed to install selenium.
        echo Try manually: %PY_CMD% -m pip install --user -U selenium
        pause
        exit /b 1
    )
    echo Selenium installed successfully.
)
echo Checking pywin32 dependency (for Outlook fallback)...
%PY_CMD% -m pip show pywin32 >nul 2>nul
if not %errorlevel%==0 (
    echo pywin32 not found. Installing now...
    %PY_CMD% -m pip install --user -U pywin32
    if not %errorlevel%==0 (
        echo [WARN] Failed to install pywin32. Outlook fallback may not work.
    ) else (
        echo pywin32 installed successfully.
    )
)
echo.
echo Running script...
echo.

%PY_CMD% "jenkins_deploy_utility.py" --username "%JENKINS_USERNAME%" --project-name "%PROJECT_NAME%" --release-name "%RELEASE_NAME%" --build-number "%BUILD_NUMBER%" --folder-name "%FOLDER_NAME%" --pause-on-exit
set "SCRIPT_EXIT=%errorlevel%"

echo.
echo Log file: "%~dp0jenkins_debug\run.log"
echo Script exit code: %SCRIPT_EXIT%
pause
