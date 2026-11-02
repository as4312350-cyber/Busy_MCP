@echo off
rem BUSY MCP installer: double-click this file.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1" %*
set "RESULT=%ERRORLEVEL%"
echo.
rem Keep the window open for people; CI (where CI=true) must not wait for a key press.
if not defined CI pause
exit /b %RESULT%
