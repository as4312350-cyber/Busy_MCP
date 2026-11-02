@echo off
rem BUSY MCP installer: double-click this file.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1" %*
echo.
pause
