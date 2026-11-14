@echo off
chcp 65001 >nul
cd /d "%~dp0"
start "" "C:\Python315\pythonw.exe" "%~dp0app.py"