@echo off
rem Build Ninput (32-bit) with the VS-bundled CMake + MSVC.
rem Network is needed once: CMake FetchContent pulls safetyhook (authorized by the owner).
setlocal
set "HERE=%~dp0"
set "VS=<path>"
set "CMAKE=%VS%\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
if not exist "%CMAKE%" ( echo CMake not found under VS. & exit /b 1 )
"%CMAKE%" -S "%HERE%." -B "%HERE%out-msvc" -G "Visual Studio 17 2022" -A Win32 -T host=x64
if errorlevel 1 (
  echo Stale/mismatched CMake cache; reconfiguring clean...
  rmdir /s /q "%HERE%out-msvc" 2>nul
  "%CMAKE%" -S "%HERE%." -B "%HERE%out-msvc" -G "Visual Studio 17 2022" -A Win32 -T host=x64 || goto :fail
)
"%CMAKE%" --build "%HERE%out-msvc" --config RelWithDebInfo || goto :fail
echo built: %HERE%out-msvc
exit /b 0
:fail
echo BUILD FAILED
exit /b 1
