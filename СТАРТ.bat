@echo off
rem ---------------------------------------------------------------------
rem  Desktop Atlas launcher.
rem
rem  ASCII only, on purpose. cmd.exe reads a .bat byte by byte in the
rem  console codepage, so UTF-8 Cyrillic desynchronises the parser:
rem  "set PORT=8777" silently fails and every echo turns into garbage.
rem  All human-facing text lives in startup.py, which speaks UTF-8 fine.
rem ---------------------------------------------------------------------

cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"

rem Find the interpreter. The neighbouring project has a Cyrillic name, which
rem must not appear in this file, so we scan sibling folders one level deep.
rem A recursive search is not an option here: the desktop next door holds
rem tens of gigabytes.
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=%CD%\.venv\Scripts\python.exe"
if not defined PY for /d %%D in ("..\*") do (
    if not defined PY if exist "%%~fD\socanalytic\venv\Scripts\python.exe" set "PY=%%~fD\socanalytic\venv\Scripts\python.exe"
)
if not defined PY for /f "delims=" %%P in ('where python 2^>nul') do if not defined PY set "PY=%%P"

if not defined PY (
    echo Python not found. Install it, or put a virtualenv in .venv next to this file.
    pause
    exit /b 1
)

"%PY%" startup.py --open
if errorlevel 1 pause
