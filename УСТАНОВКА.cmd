@echo off
REM Site Panel setup launcher (Windows)
setlocal EnableExtensions
cd /d "%~dp0"

REM #region agent log
set "DBGLOG=%~dp0debug-ee1a6d.log"
>>"%DBGLOG%" echo {"sessionId":"ee1a6d","runId":"pre-fix","hypothesisId":"H1","location":"USTANOVKA.cmd:entry","message":"launcher started","data":{"dp0":"%~dp0"},"timestamp":%RANDOM%}
REM #endregion

where py >nul 2>&1
set "HAS_PY=%ERRORLEVEL%"
where python >nul 2>&1
set "HAS_PYTHON=%ERRORLEVEL%"

REM #region agent log
>>"%DBGLOG%" echo {"sessionId":"ee1a6d","runId":"pre-fix","hypothesisId":"H3","location":"USTANOVKA.cmd:where","message":"python discovery","data":{"has_py":%HAS_PY%,"has_python":%HAS_PYTHON%},"timestamp":%RANDOM%}
REM #endregion

if "%HAS_PY%"=="0" goto try_py
if "%HAS_PYTHON%"=="0" goto try_python
goto no_python

:try_py
REM #region agent log
>>"%DBGLOG%" echo {"sessionId":"ee1a6d","runId":"pre-fix","hypothesisId":"H3","location":"USTANOVKA.cmd:try_py","message":"invoking py launcher","data":{"arg":"-3"},"timestamp":%RANDOM%}
REM #endregion
py -3 "%~dp0scripts\setup_wizard.py" %*
set "RC=%ERRORLEVEL%"
REM #region agent log
>>"%DBGLOG%" echo {"sessionId":"ee1a6d","runId":"pre-fix","hypothesisId":"H3","location":"USTANOVKA.cmd:try_py_done","message":"py exit","data":{"rc":%RC%},"timestamp":%RANDOM%}
REM #endregion
exit /b %RC%

:try_python
REM #region agent log
>>"%DBGLOG%" echo {"sessionId":"ee1a6d","runId":"pre-fix","hypothesisId":"H3","location":"USTANOVKA.cmd:try_python","message":"invoking python.exe","data":{},"timestamp":%RANDOM%}
REM #endregion
python "%~dp0scripts\setup_wizard.py" %*
set "RC=%ERRORLEVEL%"
REM #region agent log
>>"%DBGLOG%" echo {"sessionId":"ee1a6d","runId":"pre-fix","hypothesisId":"H3","location":"USTANOVKA.cmd:try_python_done","message":"python exit","data":{"rc":%RC%},"timestamp":%RANDOM%}
REM #endregion
exit /b %RC%

:no_python
REM #region agent log
>>"%DBGLOG%" echo {"sessionId":"ee1a6d","runId":"pre-fix","hypothesisId":"H2","location":"USTANOVKA.cmd:no_python","message":"python missing","data":{},"timestamp":%RANDOM%}
REM #endregion
echo.
echo [ERROR] Python 3.12+ not found.
echo Install: https://www.python.org/downloads/
echo Enable "Add python.exe to PATH", then run this file again.
echo.
pause
exit /b 1
