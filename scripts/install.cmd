@echo off
REM Windows launcher — double-click or: install.cmd
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
