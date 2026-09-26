@echo off
setlocal
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
set "PYTHONUTF8=1"
set "RS_AUDIO_PY="
where py >nul 2>nul && set "RS_AUDIO_PY=py -3"
if not defined RS_AUDIO_PY (where python >nul 2>nul && set "RS_AUDIO_PY=python")
if not defined RS_AUDIO_PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "RS_AUDIO_PY="%LOCALAPPDATA%\Programs\Python\Python313\python.exe""
if not defined RS_AUDIO_PY (
  echo Riftstone Audio needs Python 3.11 or newer; Python 3.12 is supported.
  exit /b 9009
)
%RS_AUDIO_PY% -B -m riftstone.audio_cli %*
exit /b %ERRORLEVEL%
