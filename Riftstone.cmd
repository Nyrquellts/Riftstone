@echo off
rem Riftstone launcher.  Double-click it for the menu, drag archives, folders or parameter files onto it,
rem or run it from a terminal:  Riftstone.cmd <command> ...   (Riftstone.cmd --help lists the commands)
setlocal
set "RIFTSTONE_ROOT=%~dp0"
set "PYTHONPATH=%RIFTSTONE_ROOT%src;%PYTHONPATH%"
set "PYTHONUTF8=1"
set "PYTHONUNBUFFERED=1"
set "RS_PY="
where py >nul 2>nul && set "RS_PY=py -3"
if not defined RS_PY (where python >nul 2>nul && set "RS_PY=python")
if not defined RS_PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "RS_PY="%LOCALAPPDATA%\Programs\Python\Python313\python.exe""
if not defined RS_PY (
  echo Riftstone needs Python 3.11 or newer: https://www.python.org/downloads/
  echo Tick "Add python.exe to PATH" during setup, then try again.
  pause
  exit /b 9009
)
set "RIFTSTONE_LAUNCHER=1"
if "%~1"=="" goto :menu
%RS_PY% -B -m riftstone %*
set "RC=%errorlevel%"
rem Files or a folder dragged onto this file: keep the answer on screen (a command such as doctor is not a path).
if exist "%~1" (
  echo.
  pause
)
exit /b %RC%

:menu
cls
echo.
echo   ==================================================================
echo    Riftstone           modding tools for Dragon's Dogma
echo   ==================================================================
echo.
echo     1   Open Riftstone Studio      ^(the mod maker, in your browser^)
echo     2   Check my setup             ^(is everything right?^)
echo     3   Choose features            ^(turn plugins on or off^)
echo     4   What happened last time I played?
echo     5   Show every command
echo     Q   Quit
echo.
choice /c 12345Q /n /m "   Press a key: "
if errorlevel 6 exit /b 0
if errorlevel 5 goto :m_help
if errorlevel 4 goto :m_last
if errorlevel 3 goto :m_features
if errorlevel 2 goto :m_doctor
if errorlevel 1 goto :m_studio
goto :menu

:m_studio
echo.
echo   Studio opens in your browser. Close this window, or press Ctrl+C here, to stop it.
echo.
%RS_PY% -B -m riftstone studio
goto :again

:m_doctor
cls
%RS_PY% -B -m riftstone doctor
goto :again

:m_features
cls
echo.
echo   Features ^(plugins^). Close the game first.  ON is read the next time the game starts.
echo.
%RS_PY% -B -m riftstone plugins menu
goto :again

:m_last
cls
%RS_PY% -B -m riftstone playtest
goto :again

:m_help
cls
%RS_PY% -B -m riftstone --help
goto :again

:again
echo.
pause
goto :menu
