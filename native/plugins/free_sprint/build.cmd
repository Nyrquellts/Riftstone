@echo off
rem Build the free_sprint plugin (32-bit, static CRT) and its test harness.
rem Output: native\plugins\free_sprint\out\free_sprint.asi (+ free_sprint.ini),
rem         sprint_stub.exe + sprint_harness_core.dll
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
cl %CFLAGS% /LD "%HERE%src\free_sprint.cpp" /Fe:free_sprint.asi /link /DYNAMICBASE /NXCOMPAT /SAFESEH:NO kernel32.lib || goto :fail
copy /y "%HERE%free_sprint.ini" free_sprint.ini >nul || goto :fail
rem Test harness: a CRT-free stub exe that owns DDDA.exe's fixed range (0x00400000 + 0x160C000) as
rem its own image, and the DLL that maps DDDA.exe over it and runs the checks.
cl /nologo /O1 /GS- /c "%HERE%test\sprint_stub.cpp" /Fo:sprint_stub.obj || goto :fail
link /nologo sprint_stub.obj /OUT:sprint_stub.exe /ENTRY:Start /NODEFAULTLIB /SUBSYSTEM:CONSOLE /BASE:0x400000 /FIXED /DYNAMICBASE:NO /NXCOMPAT:NO /SAFESEH:NO kernel32.lib || goto :fail
cl %CFLAGS% /LD "%HERE%test\sprint_harness.cpp" /Fe:sprint_harness_core.dll /link /SAFESEH:NO kernel32.lib shell32.lib || goto :fail
popd
echo built: %OUT%\free_sprint.asi %OUT%\sprint_stub.exe %OUT%\sprint_harness_core.dll
exit /b 0
:fail
popd
echo BUILD FAILED
exit /b 1
