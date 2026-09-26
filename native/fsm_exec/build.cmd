@echo off
rem Build fsm_exec: the stub that owns DDDA.exe's fixed range and the DLL that maps DDDA.exe over it
rem and runs the game's own state-machine code on made-up machines (32-bit, static CRT).
rem Output: native\fsm_exec\out\fsm_stub.exe + fsm_exec_core.dll
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
cl /nologo /O1 /GS- /c "%HERE%src\fsm_stub.cpp" /Fo:fsm_stub.obj || goto :fail
link /nologo fsm_stub.obj /OUT:fsm_stub.exe /ENTRY:Start /NODEFAULTLIB /SUBSYSTEM:CONSOLE /BASE:0x400000 /FIXED /DYNAMICBASE:NO /NXCOMPAT:NO /SAFESEH:NO kernel32.lib || goto :fail
cl /nologo /O2 /MT /W4 /EHsc /GS /DUNICODE /D_UNICODE /std:c++17 /LD "%HERE%src\fsm_exec.cpp" /Fe:fsm_exec_core.dll /link /SAFESEH:NO kernel32.lib shell32.lib || goto :fail
popd
echo built: %OUT%\fsm_stub.exe %OUT%\fsm_exec_core.dll
exit /b 0
:fail
popd
echo BUILD FAILED
exit /b 1
