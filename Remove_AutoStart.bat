@echo off
echo ============================================
echo   Removing AUT Proxy Bridge Auto-Start
echo ============================================
echo.

REM Check for Administrator privileges
net session >nul 2>&1
if %errorLevel% == 0 (
    echo Administrator privileges confirmed.
) else (
    echo ERROR: You must right-click this file and select "Run as Administrator"!
    pause
    exit /b 1
)

set TASKNAME=AUT_Proxy_Bridge

echo Removing Scheduled Task "%TASKNAME%"...
schtasks /delete /tn "%TASKNAME%" /f

if %errorLevel% == 0 (
    echo.
    echo SUCCESS! The bridge will no longer start automatically on boot.
) else (
    echo.
    echo Task not found or already removed.
)
echo.
pause
