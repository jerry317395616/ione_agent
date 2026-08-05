@echo off
setlocal
title I-ONE Agent Device Setup
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
if errorlevel 1 (
  echo.
  echo Installation failed. Review the message above and try again.
  pause
  exit /b 1
)
echo.
echo I-ONE Agent Device installation completed.
pause
