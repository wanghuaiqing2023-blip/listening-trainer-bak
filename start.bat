@echo off
cd /d "%~dp0"

if not exist .env (
    echo [ERROR] .env not found. Please copy .env.example to .env and fill in your API keys.
    echo     copy .env.example .env
    pause
    exit /b 1
)

rem Check Node.js
where npm >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Node.js not found. Please install from https://nodejs.org
    echo         After install, close and reopen this terminal, then run start.bat again.
    pause
    exit /b 1
)

if not exist .venv (
    echo [INFO] Creating Python virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create venv. Is Python installed?
        pause
        exit /b 1
    )
)

call .venv\Scripts\activate.bat

set PYTHON=%~dp0.venv\Scripts\python.exe
set PIP=%~dp0.venv\Scripts\pip.exe

echo [INFO] Upgrading pip...
%PYTHON% -m pip install --upgrade pip -q

echo [INFO] Installing Python dependencies...
%PIP% install -r requirements.txt --prefer-binary
if errorlevel 1 (
    echo [ERROR] pip install failed. See errors above.
    pause
    exit /b 1
)

%PYTHON% -c "import spacy; spacy.load('en_core_web_sm')" 2>nul
if errorlevel 1 (
    echo [INFO] Downloading spaCy model...
    %PYTHON% -m spacy download en_core_web_sm
)

echo [INFO] Starting backend on http://localhost:8000 ...
start "ListeningTrainer-Backend" cmd /k "cd /d %~dp0 && %~dp0.venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload"

cd /d "%~dp0frontend"

if not exist node_modules (
    echo [INFO] Installing frontend dependencies...
    npm install
    if errorlevel 1 (
        echo [ERROR] npm install failed.
        pause
        exit /b 1
    )
)

echo [INFO] Starting frontend on http://localhost:5173 ...
start "ListeningTrainer-Frontend" cmd /k "cd /d %~dp0frontend && npm run dev"

echo.
echo =============================================
echo  System started:
echo    Frontend : http://localhost:5173
echo    Backend  : http://localhost:8000
echo    API docs : http://localhost:8000/docs
echo =============================================
echo.
pause >nul
