@echo off
chcp 65001 > NUL
title Загрузчик прайса и обновление БД

echo ==================================================
echo   Запуск загрузчика прайса и обновления БД
echo ==================================================

if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" "%~dp0loader.py"
) else (
    python "%~dp0loader.py"
)

pause
