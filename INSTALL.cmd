@echo off
REM English alias for the same wizard
setlocal EnableExtensions
cd /d "%~dp0"

REM #region agent log
set "DBGLOG=%~dp0debug-ee1a6d.log"
>>"%DBGLOG%" echo {"sessionId":"ee1a6d","runId":"pre-fix","hypothesisId":"H1","location":"INSTALL.cmd:entry","message":"INSTALL launcher started","data":{},"timestamp":%RANDOM%}
REM #endregion

where py >nul 2>&1
if "%ERRORLEVEL%"=="0" goto try_py
where python >nul 2>&1
if "%ERRORLEVEL%"=="0" goto try_python
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
echo.
pause
exit /b 1
