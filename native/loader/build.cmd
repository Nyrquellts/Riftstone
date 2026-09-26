@echo off
rem Build the Riftstone loader (32-bit, static CRT) and its test harness.
rem Output: native\loader\out\dinput8.dll, riftstone_loader.dll, harness.exe, harness_ddda.exe,
rem         marker_plugin.asi, crash_plugin.asi, chain_d3d9.dll, engine_stub.exe + engine_harness_core.dll
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
if not exist "%OUT%\proxy" mkdir "%OUT%\proxy"
if not exist "%OUT%\noproxy" mkdir "%OUT%\noproxy"
pushd "%OUT%"
set "CFLAGS=/nologo /O2 /MT /W4 /EHsc /GS /guard:cf /DUNICODE /D_UNICODE /std:c++17"
set "SRC="%HERE%loader.cpp" "%HERE%stability.cpp" "%HERE%live.cpp" "%HERE%fixes.cpp" "%HERE%session.cpp" "%HERE%overlay.cpp" "%HERE%graphics.cpp""
set "LIBS=kernel32.lib user32.lib advapi32.lib gdi32.lib"
cl %CFLAGS% /LD %SRC% /Fo:proxy\ /Fe:dinput8.dll /link /DEF:"%HERE%dinput8.def" /DYNAMICBASE /NXCOMPAT %LIBS% || goto :fail
cl %CFLAGS% /LD /DRIFTSTONE_NO_PROXY %SRC% /Fo:noproxy\ /Fe:riftstone_loader.dll /link /DYNAMICBASE /NXCOMPAT %LIBS% || goto :fail
cl %CFLAGS% "%HERE%test\harness.cpp" /Fe:harness.exe /link /DYNAMICBASE /NXCOMPAT dinput8.lib dxguid.lib d3d9.lib user32.lib kernel32.lib || goto :fail
rem The stand-in with DDDA.exe's layout: fixed at 0x400000 with DDDA's range as its own image, so the addresses
rem of exit_sites.h are its to fill (run_tests.py gives the copy build 2364871's PE time stamp).
cl /nologo /O2 /MT /W4 /EHsc /GS /DUNICODE /D_UNICODE /std:c++17 /DHARNESS_DDDA_LAYOUT "%HERE%test\harness.cpp" /Fo:harness_ddda.obj /Fe:harness_ddda.exe /link /BASE:0x400000 /FIXED /DYNAMICBASE:NO /NXCOMPAT dinput8.lib dxguid.lib d3d9.lib user32.lib kernel32.lib || goto :fail
cl %CFLAGS% /LD "%HERE%test\marker_plugin.cpp" /Fe:marker_plugin.asi /link /DYNAMICBASE /NXCOMPAT kernel32.lib || goto :fail
cl %CFLAGS% /LD "%HERE%test\crash_plugin.cpp" /Fe:crash_plugin.asi /link /DYNAMICBASE /NXCOMPAT kernel32.lib || goto :fail
rem A stand-in for DXVK's d3d9.dll ([d3d9] chain): notes the call, then hands out Windows' own Direct3D 9.
cl %CFLAGS% /LD "%HERE%test\chain_d3d9.cpp" /Fe:chain_d3d9.dll /link /DEF:"%HERE%test\chain_d3d9.def" /DYNAMICBASE /NXCOMPAT kernel32.lib || goto :fail
rem Engine harness: a CRT-free stub exe that owns DDDA.exe's fixed range (0x00400000 + 0x160C000) as
rem its own image, and the DLL that maps DDDA.exe over it and runs fixes.cpp against the real code.
cl /nologo /O1 /GS- /c "%HERE%test\engine_stub.cpp" /Fo:engine_stub.obj || goto :fail
link /nologo engine_stub.obj /OUT:engine_stub.exe /ENTRY:Start /NODEFAULTLIB /SUBSYSTEM:CONSOLE /BASE:0x400000 /FIXED /DYNAMICBASE:NO /NXCOMPAT:NO /SAFESEH:NO kernel32.lib || goto :fail
cl %CFLAGS% /LD "%HERE%test\engine_harness.cpp" /Fo:engine_harness.obj /Fe:engine_harness_core.dll /link /SAFESEH:NO kernel32.lib user32.lib advapi32.lib shell32.lib || goto :fail
popd
echo built: %OUT%\dinput8.dll %OUT%\riftstone_loader.dll %OUT%\harness.exe %OUT%\harness_ddda.exe %OUT%\marker_plugin.asi %OUT%\crash_plugin.asi %OUT%\chain_d3d9.dll
exit /b 0
:fail
popd
echo BUILD FAILED
exit /b 1
