@echo off
REM DebugStudio double-click launcher: opens browser and starts server (module=treasure).
REM ASCII-only (PowerShell 5.1 reads UTF-8-without-BOM as ANSI/GBK).
powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0..\..\scripts\start_debug_studio.ps1" -Module treasure
if errorlevel 1 pause