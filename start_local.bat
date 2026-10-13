@echo off
REM ============================================================
REM  HazardGuard-RAG  Quick Start (wrapper)
REM
REM  This script only locates a usable Python and delegates to
REM  scripts\start.py, which does the real work.
REM
REM  Why not implement everything here:
REM  1. cmd.exe parses .bat as GBK; this file MUST stay ASCII-only
REM  2. Quotes in `python -c "..."` get mangled across shells
REM  3. pause / del can block in non-interactive environments
REM
REM  See scripts\start.py for the actual startup logic.
REM ============================================================

cd /d "%~dp0"

REM Prefer the project venv, fall back to system python.
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" scripts\start.py %*
    goto :eof
)

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found.
    echo         Install Python 3.11+ and create the venv:
    echo         python -m venv .venv
    pause
    exit /b 1
)

python scripts\start.py %*
goto :eof