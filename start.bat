@echo off
cd /d "%~dp0"

if not exist .env (
    echo [ERROR] .env not found. Please copy .env.example to .env and fill in your API keys.
    echo     copy .env.example .env
    exit /b 1
)

if not exist .venv (
    echo [INFO] Creating Python virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create venv. Is Python installed?
        exit /b 1
    )
)

"%~dp0.venv\Scripts\python.exe" scripts\dev_manager.py start
exit /b %errorlevel%
