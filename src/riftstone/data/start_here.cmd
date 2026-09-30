@echo off
rem ===========================================================================================
rem  Riftstone - Start Here          Riftstone @VERSION@ for Dragon's Dogma: Dark Arisen
rem
rem  Double-click this file, in the game folder (the one with DDDA.exe). It needs no Python.
rem    1  checks that Riftstone loaded in the game, and says what is wrong when it did not
rem    2  turns Riftstone's features (plugins) on and off, one at a time
rem    3  opens the logs folder, to send to whoever is helping you
rem  From a command line (for support):  "Riftstone - Start Here.cmd" check | list | on NAME | off NAME | logs
rem  Generated when the Riftstone zip was made; it changes nothing in the game's own files.
rem ===========================================================================================
setlocal EnableExtensions DisableDelayedExpansion
title Riftstone - Start Here
cd /d "%~dp0"
set "GAME=%~dp0"
set "SYS=%SystemRoot%\System32"
set "RS=%GAME%riftstone"
set "PLUGDIR=%RS%\plugins"
set "OFFDIR=%RS%\plugins\off"
set "LOGDIR=%RS%\logs"
set "KNOWN=@PLUGIN_NAMES@"
set "KNOWNSP= %KNOWN% "
set "CLI=0"
if not "%~1"=="" set "CLI=1"
rem The paths above were read while "!" is still an ordinary character, so a game folder with one in its name
rem works. From here on a path is used as !X!, never %X%: a "!" would be eaten by %X%, a ")" (Program Files
rem (x86)) would end a parenthesised block early, and a "&" would start another command.
setlocal EnableDelayedExpansion

if not exist "!GAME!DDDA.exe" goto :wrongplace
call :upgrade
if /i "%~1"=="check" goto :cli_check
if /i "%~1"=="list" goto :cli_list
if /i "%~1"=="on" goto :cli_toggle
if /i "%~1"=="off" goto :cli_toggle
if /i "%~1"=="logs" goto :logs
if not "%~1"=="" goto :usage
goto :menu

:wrongplace
echo.
echo   This file is not in the game folder.
echo.
echo   Put it next to DDDA.exe: in Steam, right-click Dragon's Dogma: Dark Arisen, then
echo   Manage, then Browse local files. Copy everything from the Riftstone zip there,
echo   and run this file again from that folder.
echo.
rem A double-click (no arguments) keeps the window open; a command line run just exits.
if "%CLI%"=="0" pause
exit /b 1

:usage
echo.
echo   Riftstone - Start Here.cmd            the menu
echo   Riftstone - Start Here.cmd check      check the setup and print what it finds
echo   Riftstone - Start Here.cmd list       the features and whether each is on
echo   Riftstone - Start Here.cmd on NAME    turn a feature on  ^(NAME is in the list; "all" for every one^)
echo   Riftstone - Start Here.cmd off NAME   turn a feature off
echo   Riftstone - Start Here.cmd logs       open the logs folder
echo.
exit /b 2

rem -----------------------------------------------------------------------------------------
:menu
cls
echo.
echo   ==============================================================
echo    Riftstone - Start Here            Dragon's Dogma: Dark Arisen
echo   ==============================================================
echo.
echo     1   Check that Riftstone is working
echo     2   Choose features      ^(turn plugins on or off^)
echo     3   Open the logs folder ^(to send if something goes wrong^)
echo     4   Help
echo     Q   Quit
echo.
choice /c 1234Q /n /m "   Press a key: "
if errorlevel 5 goto :done
if errorlevel 4 goto :help
if errorlevel 3 goto :menu_logs
if errorlevel 2 goto :features
if errorlevel 1 goto :menu_check
goto :menu

:menu_check
cls
call :check
echo.
pause
goto :menu

:menu_logs
call :openlogs
goto :menu

:help
cls
echo.
echo   How Riftstone works
echo   -------------------
echo   * Riftstone loads next to the game and changes none of the game's own files.
echo     It keeps its protections on: crash guards, save backups, crash reports.
echo   * Its FEATURES are plugins: more enemies at once, less pop-in, free sprint and so on.
echo     They start OFF. Turn on the ones you want with option 2, then start the game.
echo   * When the game starts you see a small "RUNNING" notice for a few seconds. That is the
echo     proof Riftstone is in the game. It also says which key opens the diagnostics panel
echo     ^(Insert unless you changed it in riftstone_loader.ini^).
echo   * No notice? Run option 1. It says what is missing.
echo   * To remove Riftstone: delete dinput8.dll, riftstone_loader.ini and the riftstone
echo     folder from the game folder. The game is as it was.
echo.
echo   Help: the Riftstone page on Nexus Mods ^(comments^), or github.com/Nyrquellts/Riftstone
echo   When you ask, send the two files option 3 shows: loader.log and the newest crash-*.txt.
echo.
pause
goto :menu

rem -----------------------------------------------------------------------------------------
:features
cls
echo.
echo   Features ^(plugins^). ON is read the next time the game starts; close the game first.
echo   ------------------------------------------------------------------------------------
call :list
echo.
echo   Type a number to switch that feature on or off, A to switch every one ON, N for every
echo   one OFF, or just press Enter to go back.
echo.
set "PICK="
set /p "PICK=   > "
if not defined PICK goto :menu
if /i "%PICK%"=="A" goto :all_on
if /i "%PICK%"=="N" goto :all_off
for /f "delims=0123456789" %%X in ("%PICK%") do goto :features
set "TARGET=!N%PICK%!"
if not defined TARGET goto :features
set "NOW=!S%PICK%!"
if "%NOW%"=="ON" (call :toggle "%TARGET%" off) else (call :toggle "%TARGET%" on)
echo.
pause
goto :features

:all_on
call :toggle all on
echo.
pause
goto :features

:all_off
call :toggle all off
echo.
pause
goto :features

rem -----------------------------------------------------------------------------------------
rem :list  every known plugin, and any other .asi in the plugin folders; sets N1.. (names) and S1.. (ON/off)
:list
set "COUNT=0"
for %%P in (%KNOWN%) do call :listone "%%P" 1
for %%F in ("!PLUGDIR!\*.asi" "!OFFDIR!\*.asi") do (
  if "!KNOWNSP: %%~nF =!"=="!KNOWNSP!" call :listone "%%~nF" 0
)
rem Paths are always !X!, never %X% (see the top of this file).
if "%COUNT%"=="0" echo   There are no plugins in !PLUGDIR!.
exit /b 0

:listone
set "NAME=%~1"
set "STATE=absent"
rem The loader reads only plugins\ and never plugins\off\, so when a copy is in both (an old install
rem unzipped over) the one in plugins\ is what runs: it is checked last and wins.
if exist "!OFFDIR!\%NAME%.asi" set "STATE=off"
if exist "!PLUGDIR!\%NAME%.asi" set "STATE=ON"
if "%STATE%"=="absent" if "%~2"=="1" exit /b 0
set /a COUNT+=1
set "TITLE=%NAME%"
set "WHAT=Not part of Riftstone; its author's terms apply."
if "%~2"=="1" call :desc_%NAME%
if not defined WHAT set "WHAT=No description."
set "N%COUNT%=%NAME%"
set "S%COUNT%=%STATE%"
set "MARK= ON "
if "%STATE%"=="off" set "MARK=off "
set "NUM=  %COUNT%"
echo    !NUM:~-2!   [!MARK!]  !TITLE!
echo                 !WHAT!
exit /b 0

rem -----------------------------------------------------------------------------------------
rem :toggle NAME on|off      NAME may be "all"; moves the plugin and its .ini between plugins and plugins\off
:toggle
call :isrunning
if "%RUNNING%"=="1" (
  echo   The game is running. Close it first: plugins are read when the game starts.
  exit /b 2
)
if /i "%~1"=="all" (
  for %%P in (%KNOWN%) do call :move1 "%%P" "%~2" quiet
  exit /b 0
)
call :move1 "%~1" "%~2"
exit /b %errorlevel%

:move1
set "NAME=%~1"
set "WANT=%~2"
set "HERE="
if exist "!OFFDIR!\%NAME%.asi" set "HERE=!OFFDIR!"
if exist "!PLUGDIR!\%NAME%.asi" set "HERE=!PLUGDIR!"
if not defined HERE (
  if not "%~3"=="quiet" echo   %NAME% is not in !PLUGDIR!.
  exit /b 3
)
if /i "%WANT%"=="on" (set "DEST=!PLUGDIR!") else (set "DEST=!OFFDIR!")
if /i "!HERE!"=="!DEST!" (
  echo   !NAME! is already %WANT%.
  exit /b 0
)
if not exist "!DEST!" mkdir "!DEST!"
move /y "!HERE!\%NAME%.asi" "!DEST!\" >nul
if errorlevel 1 (
  echo   Could not move !NAME!.asi. Is the game or another program using it?
  exit /b 4
)
if exist "!HERE!\%NAME%.ini" move /y "!HERE!\%NAME%.ini" "!DEST!\" >nul
echo   !NAME! is now %WANT%.
exit /b 0

:isrunning
set "RUNNING=0"
"%SYS%\tasklist.exe" /fi "imagename eq DDDA.exe" 2>nul | "%SYS%\find.exe" /i "DDDA.exe" >nul && set "RUNNING=1"
exit /b 0

rem :upgrade  a zip unzipped over an older Riftstone leaves the old copy of a feature in plugins\ (the one that
rem runs) and the zip's fresh copy in plugins\off\. Put the fresh build where the old one is: it stays ON, and
rem the player's own .ini stays. Nothing happens on a first install, or while the game is running.
:upgrade
call :isrunning
if "%RUNNING%"=="1" exit /b 0
for %%P in (%KNOWN%) do if exist "!PLUGDIR!\%%P.asi" if exist "!OFFDIR!\%%P.asi" call :upgrade1 "%%P"
exit /b 0

:upgrade1
move /y "!OFFDIR!\%~1.asi" "!PLUGDIR!\" >nul
if errorlevel 1 (
  echo   Could not update %~1.asi. Is the game or another program using it?
  exit /b 1
)
if exist "!OFFDIR!\%~1.ini" if exist "!PLUGDIR!\%~1.ini" del /q "!OFFDIR!\%~1.ini"
if exist "!OFFDIR!\%~1.ini" move /y "!OFFDIR!\%~1.ini" "!PLUGDIR!\" >nul
echo   Updated %~1 to this zip's copy: it stays on and keeps your settings.
exit /b 0

rem -----------------------------------------------------------------------------------------
:openlogs
if not exist "!LOGDIR!" (
  echo.
  echo   There is no logs folder yet: it appears the first time the game starts with Riftstone.
  echo.
  pause
  exit /b 1
)
start "" explorer "!LOGDIR!"
exit /b 0

:logs
call :openlogs
goto :done

rem -----------------------------------------------------------------------------------------
:cli_list
call :list
goto :done

:cli_toggle
if "%~2"=="" goto :usage
call :toggle "%~2" "%~1"
exit /b %errorlevel%

:cli_check
call :check
exit /b %BAD%

rem -----------------------------------------------------------------------------------------
rem :check  what is where, and what the last game session's log says
:check
set "BAD=0"
echo.
echo   Riftstone check
echo   Game folder: !GAME!
echo.
if not exist "!GAME!dinput8.dll" goto :c_nodll
findstr /m /c:"RiftstoneLoaderVersion" "!GAME!dinput8.dll" >nul 2>&1
if errorlevel 1 goto :c_otherdll
echo   [OK]  dinput8.dll here is the Riftstone loader.
goto :c_ini
:c_nodll
echo   [X]   dinput8.dll is not in the game folder, so Riftstone cannot load.
echo         Unzip everything from the Riftstone zip into the folder that has DDDA.exe.
set /a BAD+=1
goto :c_ini
:c_otherdll
echo   [X]   The dinput8.dll here is not Riftstone's; another mod uses that name.
echo         Rename it dinput8_chain.dll, then copy Riftstone's dinput8.dll from the zip in beside
echo         it. Open riftstone_loader.ini and, under [loader], change the line  chain =
echo         to  chain = dinput8_chain.dll  and save: both keep working ^(the README has the steps^).
set /a BAD+=1
:c_ini
if exist "!GAME!riftstone_loader.ini" (echo   [OK]  riftstone_loader.ini is here.) else (
  echo   [X]   riftstone_loader.ini is missing: copy it from the Riftstone zip, next to DDDA.exe.
  set /a BAD+=1
)
if exist "!RS!" (echo   [OK]  the riftstone folder is here.) else (
  echo   [X]   the riftstone folder is missing: copy it from the Riftstone zip, next to DDDA.exe.
  set /a BAD+=1
)
for %%D in (d3d9 xinput1_3 winmm version dsound) do if exist "!GAME!%%D.dll" echo   [i]   %%D.dll is also here: another tool's; that is usually fine.
if exist "!GAME!dinput8.dll.riftstone-off" echo   [i]   a dinput8.dll.riftstone-off file is here: Riftstone was switched off by hand.
echo.
if not exist "!LOGDIR!\loader.log" goto :c_nolog
rem loader.log holds one session (the last one moves to loader.prev.log), so it has one start line. findstr prints it
rem as it is: a game folder named with "!" or "&" is in that line, and echoing it here would mangle or run it.
findstr /r /c:"Riftstone loader [0-9]" "!LOGDIR!\loader.log" >nul 2>&1
if errorlevel 1 goto :c_nostart
<nul set /p "=  [OK]  The game started with Riftstone: "
findstr /r /c:"Riftstone loader [0-9]" "!LOGDIR!\loader.log"
goto :c_started
:c_nostart
echo   [i]   loader.log exists but has no start line yet.
:c_started
set "NPLUG=0"
for /f %%N in ('findstr /r /c:"plugin   .* loaded at" "!LOGDIR!\loader.log" ^| "%SYS%\find.exe" /c /v ""') do set "NPLUG=%%N"
echo   [i]   Plugins that loaded last time: %NPLUG%   ^(option 2 turns them on and off^)
findstr /r /c:"plugin   .* FAILED to load" "!LOGDIR!\loader.log" >nul 2>&1
if not errorlevel 1 (
  echo   [X]   A plugin failed to load:
  findstr /r /c:"plugin   .* FAILED to load" "!LOGDIR!\loader.log"
  set /a BAD+=1
)
findstr /i /c:"safe mode" "!LOGDIR!\loader.log" >nul 2>&1
if not errorlevel 1 (
  echo   [X]   Safe mode was on: after two start-up crashes in a row the game starts without plugins
  echo         until something changes. Send us loader.log and the newest crash-*.txt ^(option 3^).
  set /a BAD+=1
)
findstr /c:"last run ended in a crash" "!LOGDIR!\loader.log" >nul 2>&1
if not errorlevel 1 echo   [i]   The run before that ended in a crash; the report is in the logs folder.
findstr /c:"shows the diagnostics panel" "!LOGDIR!\loader.log" >nul 2>&1
if errorlevel 1 (
  echo   [i]   The panel was not announced in the log: it starts once the game's graphics are up.
) else (
  for /f "usebackq delims=" %%L in (`findstr /c:"shows the diagnostics panel" "!LOGDIR!\loader.log"`) do echo   [OK]  %%L
)
findstr /c:"startup banner shown" "!LOGDIR!\loader.log" >nul 2>&1
if not errorlevel 1 echo   [OK]  The RUNNING notice was drawn in the game.
findstr /c:"pressed with the game in front" "!LOGDIR!\loader.log" >nul 2>&1
if not errorlevel 1 echo   [OK]  The panel key reached the game.
goto :c_reports
:c_nolog
echo   [i]   No log yet: the game has not been started with Riftstone in this folder.
echo         Start the game, wait for the title screen, close it, then run this check again.
:c_reports
set "NREP=0"
for %%R in ("!LOGDIR!\crash-*.txt") do set /a NREP+=1
if not "%NREP%"=="0" echo   [i]   Crash reports in the logs folder: %NREP%   ^(option 3 opens it^)
echo.
if "%BAD%"=="0" (
  echo   Nothing wrong found.
) else (
  echo   %BAD% problem^(s^) above marked [X]. Fix those, then run this check again.
)
exit /b %BAD%

rem -----------------------------------------------------------------------------------------
:done
exit /b 0

@DESCRIPTIONS@
