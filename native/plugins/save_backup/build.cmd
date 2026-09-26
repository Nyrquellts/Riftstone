@echo off
rem Build the save_backup plugin (32-bit, static CRT) and its stand-in game process for the tests.
rem Output: native\plugins\save_backup\out\save_backup.asi (+ save_backup.ini), backup_host.exe
setlocal
set "HERE=%~dp0"
set "OUT=%HERE%out"
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
set "VS="
if exist "%VSWHERE%" for /f "usebackq delims=" %%i in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VS=%%i"
if not defined VS (
  echo Visual Studio with C++ tools was not found. Install "Desktop development with C++".
  exit /b 1
)
set "PATH=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer;%PATH%"
call "%VS%\VC\Auxiliary\Build\vcvarsall.bat" x86 >nul || exit /b 1
if not exist "%OUT%" mkdir "%OUT%"
pushd "%OUT%"
set "CFLAGS=/nologo /O2 /MT /W4 /EHsc /GS /DUNICODE /D_UNICODE /std:c++17"
cl %CFLAGS% /LD "%HERE%src\save_backup.cpp" /Fe:save_backup.asi /link /DYNAMICBASE /NXCOMPAT /SAFESEH:NO kernel32.lib advapi32.lib || goto :fail
copy /y "%HERE%save_backup.ini" save_backup.ini >nul || goto :fail
cl %CFLAGS% "%HERE%test\backup_host.cpp" /Fe:backup_host.exe /link kernel32.lib || goto :fail
popd
echo built: %OUT%\save_backup.asi %OUT%\backup_host.exe
exit /b 0
:fail
popd
echo BUILD FAILED
exit /b 1
