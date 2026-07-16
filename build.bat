@echo off
setlocal enabledelayedexpansion

echo ===================================================
echo   Building Background Worker Executable
echo ===================================================

:: Check if VIRTUAL_ENV is already set
if not "%VIRTUAL_ENV%"=="" (
    echo [INFO] Virtual environment is already active: %VIRTUAL_ENV%
    goto :build
)

:: Try to activate .venv locally
if exist ".venv\Scripts\activate.bat" (
    echo [INFO] Activating virtual environment from .venv...
    call .venv\Scripts\activate.bat
    if not "!VIRTUAL_ENV!"=="" (
        echo [SUCCESS] Virtual environment activated.
        goto :build
    )
)

:: If we reach here, it failed to activate
echo [WARNING] Virtual environment is NOT active, and .venv\Scripts\activate.bat was not found!
echo Please activate your virtual environment before running this script.
echo.
choice /M "Do you want to attempt building anyway using global system Python?"
if errorlevel 2 (
    echo [INFO] Build canceled by user.
    exit /b 1
)

:build
echo [INFO] Cleaning up previous build artifacts...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"

echo [INFO] Running PyInstaller build...
pyinstaller --clean background_worker.spec

if %ERRORLEVEL% equ 0 (
    echo [SUCCESS] Executable built successfully!
    echo [SUCCESS] You can find the executable at: dist\background_worker.exe
) else (
    echo [ERROR] Build failed with exit code %ERRORLEVEL%.
    exit /b %ERRORLEVEL%
)

endlocal
