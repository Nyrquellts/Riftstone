@echo off
rem Build Riftstone Studio (32-bit, static CRT, no other DLL) and the selftest's stand-in game.
rem Output: native\studio\out\RiftstoneStudio.exe, fake_ddda.exe
rem catalog.inc and version.h come from tools\gen_studio_catalog.py (the gate checks them with --check).
setlocal
set "HERE=%~dp0"
set "OUT=%HERE%out"
set "FONTS=%HERE%..\..\src\riftstone\studio\fonts"
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
set "CFLAGS=/nologo /O2 /MT /W4 /WX /EHsc /GS /guard:cf /DUNICODE /D_UNICODE /std:c++17"
set "LFLAGS=/DYNAMICBASE /NXCOMPAT /guard:cf"
rc /nologo /I "%HERE%src" /I "%FONTS%" /fo studio.res "%HERE%src\studio.rc" || goto :fail
cl %CFLAGS% "%HERE%src\main.cpp" "%HERE%src\core.cpp" "%HERE%src\ui.cpp" ^
  "%HERE%src\live.cpp" "%HERE%src\graphics.cpp" "%HERE%src\pack.cpp" studio.res /Fe:RiftstoneStudio.exe ^
  /link %LFLAGS% /SUBSYSTEM:WINDOWS kernel32.lib user32.lib gdi32.lib advapi32.lib shell32.lib ole32.lib ^
  comctl32.lib uuid.lib d2d1.lib dwrite.lib windowscodecs.lib dwmapi.lib bcrypt.lib || goto :fail
cl %CFLAGS% "%HERE%test\fake_ddda.cpp" /Fe:fake_ddda.exe /link %LFLAGS% /SUBSYSTEM:WINDOWS kernel32.lib || goto :fail
popd
echo built: %OUT%\RiftstoneStudio.exe %OUT%\fake_ddda.exe
exit /b 0
:fail
popd
echo BUILD FAILED
exit /b 1
