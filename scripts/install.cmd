@echo off
REM Windows launcher. Default: wizard. Direct engine: install.cmd --direct ...
setlocal EnableExtensions
cd /d "%~dp0.."

REM #region agent log
set "DBGLOG=%~dp0..\debug-ee1a6d.log"
>>"%DBGLOG%" echo {"sessionId":"ee1a6d","runId":"pre-fix","hypothesisId":"H1","location":"scripts/install.cmd:entry","message":"scripts install.cmd started","data":{"arg1":"%~1"},"timestamp":%RANDOM%}
REM #endregion

if /I "%~1"=="--direct" goto direct

where py >nul 2>&1
if "%ERRORLEVEL%"=="0" goto try_py
where python >nul 2>&1
if "%ERRORLEVEL%"=="0" goto try_python
goto no_python

:try_py
py -3 "%~dp0setup_wizard.py" %*
exit /b %ERRORLEVEL%

:try_python
python "%~dp0setup_wizard.py" %*
exit /b %ERRORLEVEL%

:direct
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %2 %3 %4 %5 %6 %7 %8 %9
exit /b %ERRORLEVEL%

:no_python
echo.
echo [ERROR] Python 3.12+ not found.
echo Install: https://www.python.org/downloads/
echo.
pause
exit /b 1
