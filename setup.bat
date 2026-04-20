@echo off
:: Wrapper to run setup.ps1 from CMD or double-click
powershell -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
pause
