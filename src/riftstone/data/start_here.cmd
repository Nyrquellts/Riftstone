@echo off
rem ===========================================================================================
rem  Riftstone - Start Here          Riftstone @VERSION@ for Dragon's Dogma: Dark Arisen
rem
rem  Double-click this file, in the game folder (the one with DDDA.exe). It needs no Python.
rem    1  checks that Riftstone loaded in the game, says what is wrong when it did not, and
rem       fixes what it can (F): another mod's dinput8.dll, a missing or older loader
rem    2  turns Riftstone's features (plugins) on and off, one at a time
rem    3  opens the logs folder, to send to whoever is helping you
rem  From a command line (for support):
rem    "Riftstone - Start Here.cmd" check | fix | list | on NAME | off NAME | logs
rem  Generated when the Riftstone zip was made; it changes nothing in the game's own files.
rem  Every Windows program it runs is called by its full path in System32, so a PATH another
rem  program changed (or emptied) does not stop it.
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
set "SPARE=%RS%\loader\dinput8.dll"
set "KNOWN=@PLUGIN_NAMES@"
set "KNOWNSP= %KNOWN% "
set "CLI=0"
if not "%~1"=="" set "CLI=1"
rem The paths above were read while "!" is still an ordinary character, so a game folder with one in its name
rem works. From here on a path is used as !X!, never %X%: a "!" would be eaten by %X%, a ")" (Program Files
rem (x86)) would end a parenthesised block early, and a "&" would start another command. Nor is a path ever
rem passed to "call": call reads its line a second time, and a "%" or "^" in the path would change.
setlocal EnableDelayedExpansion

if not exist "!GAME!DDDA.exe" goto :wrongplace
call :upgrade
if /i "%~1"=="check" goto :cli_check
if /i "%~1"=="fix" goto :cli_fix
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
echo   Riftstone - Start Here.cmd fix        fix what the check marks F ^(another mod's dinput8.dll, the loader^)
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
echo     1   Check that Riftstone is working ^(and fix it^)
echo     2   Choose features      ^(turn plugins on or off^)
echo     3   Open the logs folder ^(to send if something goes wrong^)
echo     4   Help
echo     Q   Quit
echo.
call :ask 1234Q "   Press a key: "
if "!ANSWER!"=="1" goto :menu_check
if "!ANSWER!"=="2" goto :features
if "!ANSWER!"=="3" goto :menu_logs
if "!ANSWER!"=="4" goto :help
goto :done

:menu_check
cls
call :check
echo.
if not "!FIXABLE!"=="1" goto :menu_check_end
call :ask FB "   Press F to fix what is marked F above, or B to go back: "
if /i not "!ANSWER!"=="F" goto :menu
echo.
call :fix
echo.
echo   Start the game once, close it, then check again ^(option 1^).
:menu_check_end
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
echo   * No notice? Run option 1. It says what is missing, and F fixes what it can.
echo   * Another mod uses dinput8.dll ^(DDDA Tweak, an ASI loader^)? Keep it: option 1, then F,
echo     renames it dinput8_chain.dll and Riftstone starts it, so both work.
echo   * To remove Riftstone: delete dinput8.dll, riftstone_loader.ini and the riftstone
echo     folder from the game folder ^(and rename a dinput8_chain.dll back to dinput8.dll^).
echo     The game is as it was.
echo.
echo   Help: the Riftstone page on Nexus Mods ^(comments^), or github.com/Nyrquellts/Riftstone
echo   When you ask, send the two files option 3 shows: loader.log and the newest crash-*.txt,
echo   and what option 1 prints.
echo.
pause
goto :menu

rem -----------------------------------------------------------------------------------------
rem :ask KEYS "PROMPT"  sets ANSWER to the key pressed, one of KEYS; with choice.exe, or typed and Enter when
rem Windows has no choice.exe. Empty when nothing was answered (the input ended).
:ask
set "ANSWER="
if not exist "%SYS%\choice.exe" goto :ask_typed
"%SYS%\choice.exe" /c %~1 /n /m "%~2"
if errorlevel 255 goto :ask_typed
set /a "I=!errorlevel!-1"
set "K=%~1"
if !I! GEQ 0 if !I! LSS 9 for %%I in (!I!) do set "ANSWER=!K:~%%I,1!"
exit /b 0
:ask_typed
set /p "ANSWER=%~2"
if defined ANSWER set "ANSWER=!ANSWER:~0,1!"
exit /b 0

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

rem :isloader FILE  errorlevel 0 when FILE (a name in the game folder) is a Riftstone loader: it holds the
rem loader's version tag. Takes a name, not a path (see the top of this file).
:isloader
"%SYS%\findstr.exe" /m /c:"RiftstoneLoaderVersion" "!GAME!%~1" >nul 2>&1
exit /b

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
start "" "%SystemRoot%\explorer.exe" "!LOGDIR!"
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
if "!FIXABLE!"=="1" echo   "Riftstone - Start Here.cmd" fix  fixes what is marked F.
exit /b %BAD%

:cli_fix
call :fix
exit /b %errorlevel%

rem -----------------------------------------------------------------------------------------
rem :fix  what the check marks F. Another mod's dinput8.dll becomes dinput8_chain.dll, which the loader starts
rem (riftstone_loader.ini need not name it); a missing loader, or one of another build, is replaced by this zip's
rem spare copy (riftstone\loader\dinput8.dll); a Riftstone loader kept as dinput8_chain.dll, which the loader never
rem starts, is renamed aside. Nothing is deleted. Never while the game runs.
:fix
call :isrunning
if "%RUNNING%"=="1" (
  echo   The game is running. Close it first.
  exit /b 2
)
set "FIXED=0"
if not exist "!GAME!dinput8_chain.dll" goto :fix_main
call :isloader dinput8_chain.dll
if errorlevel 1 goto :fix_main
move /y "!GAME!dinput8_chain.dll" "!GAME!dinput8_chain.dll.riftstone-old" >nul
if errorlevel 1 (
  echo   Could not rename dinput8_chain.dll. Is the game or another program using it?
  exit /b 4
)
echo   dinput8_chain.dll was a Riftstone loader, not another mod's: it is now dinput8_chain.dll.riftstone-old.
set "FIXED=1"
:fix_main
if exist "!SPARE!" goto :fix_have
echo   riftstone\loader\dinput8.dll, this zip's spare copy of the loader, is not here: unzip the whole
echo   Riftstone zip into the game folder again ^(keep your own dinput8.dll if Windows asks^), then fix again.
exit /b 5
:fix_have
if not exist "!GAME!dinput8.dll" goto :fix_put
call :isloader dinput8.dll
if not errorlevel 1 goto :fix_ours
if not exist "!GAME!dinput8_chain.dll" goto :fix_rename
echo   The dinput8.dll here is another mod's, and a dinput8_chain.dll is here too, so neither can be
echo   renamed safely. Move one of them out of the game folder, then fix again.
exit /b 6
:fix_rename
move /y "!GAME!dinput8.dll" "!GAME!dinput8_chain.dll" >nul
if errorlevel 1 (
  echo   Could not rename dinput8.dll. Is the game or another program using it?
  exit /b 4
)
echo   The other mod's dinput8.dll is now dinput8_chain.dll. Riftstone starts it, so it keeps working.
goto :fix_put
:fix_ours
"%SYS%\fc.exe" /b "!SPARE!" "!GAME!dinput8.dll" >nul 2>&1
if errorlevel 1 goto :fix_put
if "!FIXED!"=="0" echo   Nothing to fix: dinput8.dll is already this zip's Riftstone loader.
exit /b 0
:fix_put
copy /y "!SPARE!" "!GAME!dinput8.dll" >nul
if errorlevel 1 (
  echo   Could not copy the loader in. Is the game running, or did Windows Security take the file away?
  exit /b 4
)
if not exist "!GAME!dinput8.dll" (
  echo   The loader was copied in and is gone again: Windows Security probably took it. Open Windows
  echo   Security, Virus and threat protection, Protection history.
  exit /b 4
)
echo   dinput8.dll is now this zip's Riftstone loader.
exit /b 0

rem -----------------------------------------------------------------------------------------
rem :check  what is where, and what the last game session's log says; FIXABLE=1 when :fix has something to do
:check
set "BAD=0"
set "FIXABLE=0"
echo.
echo   Riftstone check                       this file: Riftstone @VERSION@
echo   Game folder: !GAME!
echo.
if not exist "!GAME!dinput8.dll" goto :c_nodll
call :isloader dinput8.dll
if errorlevel 1 goto :c_otherdll
if not exist "!SPARE!" goto :c_ours
"%SYS%\fc.exe" /b "!SPARE!" "!GAME!dinput8.dll" >nul 2>&1
if errorlevel 1 goto :c_olddll
:c_ours
echo   [OK]  dinput8.dll here is the Riftstone loader.
goto :c_chain
:c_olddll
echo   [X]   dinput8.dll here is a Riftstone loader, but not this zip's: an older one was kept when the
echo         zip was unzipped, so this zip's features may not work with it.                       F fixes it.
set /a BAD+=1
set "FIXABLE=1"
goto :c_chain
:c_nodll
echo   [X]   dinput8.dll is not in the game folder, so Riftstone cannot load.
if exist "!SPARE!" (
  echo         This zip's copy of it is in riftstone\loader.                                          F fixes it.
  set "FIXABLE=1"
) else (
  echo         Unzip everything from the Riftstone zip into the folder that has DDDA.exe.
)
echo         Copied it and it is gone again? Windows Security may have taken it: open Windows Security,
echo         Virus and threat protection, Protection history.
set /a BAD+=1
goto :c_chain
:c_otherdll
echo   [X]   The dinput8.dll here is another mod's ^(DDDA Tweak, an ASI loader...^), so Riftstone cannot load.
if exist "!GAME!dinput8_chain.dll" (
  echo         A dinput8_chain.dll is here too: move one of the two out of the game folder, then check again.
) else if exist "!SPARE!" (
  echo         F renames it dinput8_chain.dll and puts Riftstone's loader in; Riftstone then starts the
  echo         other mod too, so both keep working.                                                  F fixes it.
  set "FIXABLE=1"
) else (
  echo         Rename it dinput8_chain.dll, then copy Riftstone's dinput8.dll from the zip in beside it:
  echo         Riftstone starts the other mod too, so both keep working.
)
set /a BAD+=1
:c_chain
if not exist "!GAME!dinput8_chain.dll" goto :c_ini
call :isloader dinput8_chain.dll
if errorlevel 1 (
  echo   [i]   dinput8_chain.dll is here: Riftstone's loader starts it, so that mod keeps working.
) else (
  echo   [X]   dinput8_chain.dll is a Riftstone loader, not another mod's: it is never started.     F fixes it.
  set /a BAD+=1
  set "FIXABLE=1"
)
:c_ini
if exist "!GAME!riftstone_loader.ini" (echo   [OK]  riftstone_loader.ini is here.) else (
  echo   [X]   riftstone_loader.ini is missing: copy it from the Riftstone zip, next to DDDA.exe.
  set /a BAD+=1
)
if not exist "!RS!" (
  echo   [X]   the riftstone folder is missing: copy it from the Riftstone zip, next to DDDA.exe.
  set /a BAD+=1
  goto :c_others
)
echo   [OK]  the riftstone folder is here.
set "WT=!RS!\riftstone-write-test.tmp"
(echo test>"!WT!") >nul 2>&1
if exist "!WT!" (
  del /q "!WT!" >nul 2>&1
) else (
  echo   [X]   Windows does not let you write in the riftstone folder, so Riftstone cannot write its log
  echo         and option 2 cannot switch features. Run the game and this file as the Windows user that
  echo         installed the game, or move the game to a library outside protected folders.
  set /a BAD+=1
)
set "NON=0"
set "NOFF=0"
set "ONLIST="
for %%P in (%KNOWN%) do (
  if exist "!PLUGDIR!\%%P.asi" (
    set /a NON+=1
    set "ONLIST=!ONLIST! %%P"
  ) else if exist "!OFFDIR!\%%P.asi" set /a NOFF+=1
)
if "!NON!"=="0" (
  echo   [i]   Every Riftstone feature is OFF, so the game plays as it always did ^(Riftstone itself still
  echo         runs: crash guards, crash reports^). Option 2 turns on the ones you want.
) else (
  echo   [i]   Features ON:!ONLIST!
  if not "!NOFF!"=="0" echo         !NOFF! more are OFF: they change nothing in the game until option 2 turns them on.
)
:c_others
for %%P in (%KNOWN%) do for %%D in ("" "scripts\" "plugins\") do if exist "!GAME!%%~D%%P.asi" (
  echo   [X]   %%~D%%P.asi is outside riftstone\plugins: Riftstone never loads it there, but another ASI
  echo         loader would, a second time. Delete that copy; Riftstone's own is in riftstone\plugins.
  set /a BAD+=1
)
for %%D in (d3d9 xinput1_3 winmm version dsound) do if exist "!GAME!%%D.dll" echo   [i]   %%D.dll is also here: another tool's; that is usually fine.
if exist "!GAME!riftstone_loader.dll" echo   [i]   riftstone_loader.dll is also here: the loader for other loaders. Only one Riftstone loader runs.
echo.
if not exist "!LOGDIR!\loader.log" goto :c_nolog
rem loader.log holds one session (the last one moves to loader.prev.log), so it has one start line. findstr prints it
rem as it is: a game folder named with "!" or "&" is in that line, and echoing it here would mangle or run it.
"%SYS%\findstr.exe" /r /c:"Riftstone loader [0-9]" "!LOGDIR!\loader.log" >nul 2>&1
if errorlevel 1 goto :c_nostart
<nul set /p "=  [OK]  The game started with Riftstone: "
"%SYS%\findstr.exe" /r /c:"Riftstone loader [0-9]" "!LOGDIR!\loader.log"
goto :c_started
:c_nostart
echo   [i]   loader.log exists but has no start line yet.
:c_started
set "NPLUG=0"
rem for /f runs its command through "cmd /c", which drops the first and last quote of a line that starts with one;
rem "type" first keeps the full paths' quotes whole.
for /f %%N in ('type "!LOGDIR!\loader.log" ^| "%SYS%\findstr.exe" /r /c:"plugin   .* loaded at" ^| "%SYS%\find.exe" /c /v ""') do set "NPLUG=%%N"
echo   [i]   Plugins that loaded last time: %NPLUG%   ^(option 2 turns them on and off^)
"%SYS%\findstr.exe" /r /c:"plugin   .* FAILED to load" "!LOGDIR!\loader.log" >nul 2>&1
if not errorlevel 1 (
  echo   [X]   A plugin failed to load:
  "%SYS%\findstr.exe" /r /c:"plugin   .* FAILED to load" "!LOGDIR!\loader.log"
  set /a BAD+=1
)
"%SYS%\findstr.exe" /c:"chain    " "!LOGDIR!\loader.log" >nul 2>&1
if errorlevel 1 goto :c_safe
rem Every way the chain is not started ends its line with "using the system dinput8".
"%SYS%\findstr.exe" /c:"using the system dinput8" "!LOGDIR!\loader.log" >nul 2>&1
if errorlevel 1 (
  echo   [OK]  Another mod's dinput8 started with Riftstone:
) else (
  echo   [X]   Another mod's dinput8 did not start:
  set /a BAD+=1
)
"%SYS%\findstr.exe" /c:"chain    " "!LOGDIR!\loader.log"
:c_safe
"%SYS%\findstr.exe" /c:"another Riftstone loader is in the game" "!LOGDIR!\loader.log" >nul 2>&1
if not errorlevel 1 (
  echo   [X]   Two Riftstone loaders were in the game; keep one:
  "%SYS%\findstr.exe" /c:"another Riftstone loader is in the game" "!LOGDIR!\loader.log"
  set /a BAD+=1
)
rem "SAFE MODE:" (in capitals) is written only while safe mode is on; the words in small letters are also in the
rem line saying it ended ("... since safe mode began; they are back on").
"%SYS%\findstr.exe" /c:"SAFE MODE:" "!LOGDIR!\loader.log" >nul 2>&1
if not errorlevel 1 (
  echo   [X]   Safe mode was on: after two start-up crashes in a row the game started without plugins.
  echo         Switching a feature with option 2 ends it. If it comes back, send us loader.log and the
  echo         newest crash-*.txt ^(option 3^).
  set /a BAD+=1
)
"%SYS%\findstr.exe" /c:"last run ended in a crash" "!LOGDIR!\loader.log" >nul 2>&1
if not errorlevel 1 echo   [i]   The run before that ended in a crash; the report is in the logs folder.
"%SYS%\findstr.exe" /c:"shows the diagnostics panel" "!LOGDIR!\loader.log" >nul 2>&1
if errorlevel 1 (
  echo   [i]   The panel was not announced in the log: it starts once the game's graphics are up.
) else (
  for /f "usebackq delims=" %%L in (`type "!LOGDIR!\loader.log" ^| "%SYS%\findstr.exe" /c:"shows the diagnostics panel"`) do echo   [OK]  %%L
)
"%SYS%\findstr.exe" /c:"startup banner shown" "!LOGDIR!\loader.log" >nul 2>&1
if not errorlevel 1 echo   [OK]  The RUNNING notice was drawn in the game.
"%SYS%\findstr.exe" /c:"pressed with the game in front" "!LOGDIR!\loader.log" >nul 2>&1
if not errorlevel 1 echo   [OK]  The panel key reached the game.
goto :c_reports
:c_nolog
echo   [i]   No log yet: the game has not been started with Riftstone in this folder.
echo         Start the game, wait for the title screen, close it, then run this check again.
echo         Started it and still no log? Then Riftstone did not load: check that Steam starts the game
echo         from this folder ^(Manage, Browse local files^) and Windows Security's Protection history.
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
