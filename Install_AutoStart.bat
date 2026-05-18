@echo off
echo ============================================
echo   Installing AUT Proxy Bridge Auto-Start
echo ============================================
echo.
echo This will create a Windows Scheduled Task to run the bridge
echo automatically in the background when the computer boots.
echo.

REM Check for Administrator privileges
net session >nul 2>&1
if %errorLevel% == 0 (
    echo Administrator privileges confirmed.
) else (
    echo ERROR: You must right-click this file and select "Run as Administrator"!
    echo The script cannot install the service without admin rights.
    pause
    exit /b 1
)

set TASKNAME=AUT_Proxy_Bridge
set SCRIPT_PATH=%~dp0Start_Server.bat

echo Creating Scheduled Task "%TASKNAME%"...
REM Create task to run on system boot, as the SYSTEM user (hidden), with highest privileges
schtasks /create /tn "%TASKNAME%" /tr "\"%SCRIPT_PATH%\"" /sc onstart /ru SYSTEM /rl HIGHEST /f

if %errorLevel% == 0 (
    echo.
    echo ============================================
    echo SUCCESS! 
    echo The bridge will now start automatically in the 
    echo background every time this computer turns on.
    echo ============================================
) else (
    echo.
    echo FAILED to create the scheduled task.
)
echo.
pause
