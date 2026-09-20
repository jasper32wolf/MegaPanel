@echo off
REM Site Panel setup launcher (Windows)
setlocal EnableExtensions
cd /d "%~dp0"

where py >nul 2>&1
set "HAS_PY=%ERRORLEVEL%"
where python >nul 2>&1
set "HAS_PYTHON=%ERRORLEVEL%"

if "%HAS_PY%"=="0" goto try_py
if "%HAS_PYTHON%"=="0" goto try_python
goto no_python

:try_py
py -3 "%~dp0scripts\setup_wizard.py" %*
exit /b %ERRORLEVEL%

:try_python
python "%~dp0scripts\setup_wizard.py" %*
exit /b %ERRORLEVEL%

:no_python
echo.
echo [ERROR] Python 3.12+ not found.
echo Install: https://www.python.org/downloads/
echo Enable "Add python.exe to PATH", then run this file again.
echo.
pause
exit /b 1
