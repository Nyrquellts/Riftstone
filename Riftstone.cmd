@echo off
rem Riftstone launcher. Drag archives, folders or parameter files onto this file,
rem or run it from a terminal:  Riftstone.cmd <command> ...
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
%RS_PY% -B -m riftstone %*
exit /b %ERRORLEVEL%
