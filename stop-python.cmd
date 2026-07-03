@echo off
chcp 65001 >nul
pwsh -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-python.ps1" %*
