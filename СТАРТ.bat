@echo off
chcp 65001 >nul
title Desktop Atlas
cd /d "%~dp0"

set PORT=8777
set PYTHONIOENCODING=utf-8

rem Если сервер уже поднят - просто открываем вкладку и выходим.
netstat -ano | findstr /r /c:"LISTENING" | findstr /c:"127.0.0.1:%PORT%" >nul
if %errorlevel%==0 (
    echo Сервер уже запущен.
    start "" "http://127.0.0.1:%PORT%/"
    timeout /t 2 >nul
    exit /b 0
)

rem Ищем интерпретатор: сначала своё окружение, потом соседнее от соц аналитика,
rem в последнюю очередь - системный python.
set PY=
if exist ".venv\Scripts\python.exe" set PY=.venv\Scripts\python.exe
if not defined PY if exist "..\соц аналитик\socanalytic (запустить и показать)\venv\Scripts\python.exe" (
    set PY=..\соц аналитик\socanalytic (запустить и показать)\venv\Scripts\python.exe
)
if not defined PY (
    where python >nul 2>nul
    if %errorlevel%==0 set PY=python
)
if not defined PY (
    echo.
    echo Не нашёл Python. Установи его или положи окружение в .venv рядом с этим файлом.
    pause
    exit /b 1
)

if not exist "data\map.json" (
    echo.
    echo Карты ещё нет. Считаю с нуля - это займёт около десяти минут.
    echo.
    "%PY%" scan_desktop.py || goto :fail
    "%PY%" build_map.py    || goto :fail
)

echo Запускаю сервер на http://127.0.0.1:%PORT%/
"%PY%" serve.py
goto :eof

:fail
echo.
echo Что-то пошло не так - смотри сообщение выше.
pause
