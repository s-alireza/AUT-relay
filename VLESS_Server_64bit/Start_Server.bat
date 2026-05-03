@echo off
echo ============================================
echo   AUT Proxy Bridge - Starting All Services
echo ============================================
echo.

REM Find Python
where python >nul 2>&1
if %errorlevel%==0 (
    set PYEXE=python
) else if exist "C:\Python34\python.exe" (
    set PYEXE=C:\Python34\python.exe
) else (
    echo ERROR: Python not found!
    echo Download Python from: https://www.python.org/downloads/
    pause
    exit /b 1
)

REM Check if setup has been completed
if not exist "%~dp0config\settings.json" (
    echo.
    echo  First time setup required! Opening Web UI...
    echo.
    start http://127.0.0.1:3080
    %PYEXE% "%~dp0setup_server.py"
    if not exist "%~dp0config\settings.json" (
        echo Setup was cancelled. Cannot start.
        pause
        exit /b 1
    )
    echo.
)

echo [1/1] Starting Master Controller...
echo.
%PYEXE% "%~dp0account_manager.py"

echo.
echo ============================================
echo   SHUTDOWN COMPLETE
echo ============================================
echo.
exit /b 0
