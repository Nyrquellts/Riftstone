@echo off
rem Build the compat plugin (32-bit, static CRT) and its test harness.
rem Output: native\plugins\compat\out\compat.asi + compat.ini, compat_stub.exe + compat_harness_core.dll
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
set "CFLAGS=/nologo /O2 /MT /W4 /EHsc /GS /DUNICODE /D_UNICODE /std:c++17 /D_CRT_SECURE_NO_WARNINGS"
cl %CFLAGS% /LD "%HERE%src\compat.cpp" /Fe:compat.asi /link /DYNAMICBASE /NXCOMPAT /SAFESEH:NO kernel32.lib user32.lib || goto :fail
copy /y "%HERE%compat.ini" compat.ini >nul || goto :fail
rem Test harness: a CRT-free stub exe that owns DDDA.exe's fixed range (0x00400000 + 0x160C000) as
rem its own image, and the DLL that maps DDDA.exe over it and runs the checks.
cl /nologo /O1 /GS- /c "%HERE%test\compat_stub.cpp" /Fo:compat_stub.obj || goto :fail
link /nologo compat_stub.obj /OUT:compat_stub.exe /ENTRY:Start /NODEFAULTLIB /SUBSYSTEM:CONSOLE /BASE:0x400000 /FIXED /DYNAMICBASE:NO /NXCOMPAT:NO /SAFESEH:NO kernel32.lib || goto :fail
cl %CFLAGS% /LD "%HERE%test\compat_harness.cpp" /Fe:compat_harness_core.dll /link /SAFESEH:NO kernel32.lib shell32.lib || goto :fail
popd
echo built: %OUT%\compat.asi %OUT%\compat_stub.exe %OUT%\compat_harness_core.dll
exit /b 0
:fail
popd
echo BUILD FAILED
exit /b 1
