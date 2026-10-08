@echo off
rem Build the pool_cap plugin (32-bit, static CRT) and its test harness.
rem Output: native\plugins\pool_cap\out\pool_cap.asi (+ pool_cap.ini),
rem         pool_cap_stub.exe + pool_cap_harness_core.dll
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
cl %CFLAGS% /LD "%HERE%src\pool_cap.cpp" /Fe:pool_cap.asi /link /DYNAMICBASE /NXCOMPAT /SAFESEH:NO kernel32.lib || goto :fail
copy /y "%HERE%pool_cap.ini" pool_cap.ini >nul || goto :fail
rem Test harness: a CRT-free stub exe that owns DDDA.exe's fixed range (0x00400000 + 0x160C000) as its
rem own image (large-address aware: the pools ask for up to 1.3 GB), and the DLL that maps DDDA.exe over it
rem and runs the game's own pool builder and allocator with the patches.
cl /nologo /O1 /GS- /c "%HERE%test\pool_cap_stub.cpp" /Fo:pool_cap_stub.obj || goto :fail
link /nologo pool_cap_stub.obj /OUT:pool_cap_stub.exe /ENTRY:Start /NODEFAULTLIB /SUBSYSTEM:CONSOLE /BASE:0x400000 /FIXED /DYNAMICBASE:NO /NXCOMPAT:NO /SAFESEH:NO /LARGEADDRESSAWARE kernel32.lib || goto :fail
cl %CFLAGS% /I "%HERE%src" /LD "%HERE%test\pool_cap_harness.cpp" /Fe:pool_cap_harness_core.dll /link /SAFESEH:NO kernel32.lib shell32.lib || goto :fail
popd
echo built: %OUT%\pool_cap.asi %OUT%\pool_cap_stub.exe %OUT%\pool_cap_harness_core.dll
exit /b 0
:fail
popd
echo BUILD FAILED
exit /b 1
