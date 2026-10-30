@echo off
title V99 Wheel Hub - feche esta janela (ou Ctrl+C) para desconectar
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
python v99_app.py
if errorlevel 1 pause
