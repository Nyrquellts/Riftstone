@echo off
rem The release gate: unit tests, loader harness, then the corpus proofs against the installed game.
rem   tools\test_all.cmd           unit + loader + XFS/YAML/collision/text corpus (about 3 minutes)
rem   tools\test_all.cmd full      also every archive rebuilt byte for byte (about 30 minutes)
setlocal
set "ROOT=%~dp0.."
set "PY=py -3"
where py >nul 2>nul || set "PY=python"
set "PYTHONUTF8=1"
pushd "%ROOT%\tests"
rem discover, so a new test file can never be left out of the gate
%PY% -B -m unittest discover -q -s . -p "test_*.py" || goto :fail
popd
rem game rules written in NYR-Lang: every module nyr.json lists must match what its .nyr source compiles to
if not defined NYRC set "NYRC=<path>"
if not exist "%NYRC%" (
  echo NYR-Lang's compiler is not at %NYRC% ^(set NYRC^): the rules in src\riftstone\rules were not checked
  goto :fail
)
call "%NYRC%" --check "%ROOT%\nyr.json" || goto :fail
if exist "%ROOT%\native\loader\out\dinput8.dll" (
  %PY% -B "%ROOT%\native\loader\test\run_tests.py" || goto :fail
) else (
  echo loader not built; run native\loader\build.cmd to include it
)
rem the enemy_skins plugin, run inside the real DDDA.exe code (mapped read-only; skips without a build or game)
%PY% -B "%ROOT%\native\plugins\enemy_skins\test\run_tests.py" || goto :fail
rem the enemy_cap plugin (more enemies at once), the same way
%PY% -B "%ROOT%\native\plugins\enemy_cap\test\run_tests.py" || goto :fail
rem the lod_tuner plugin, the same way
%PY% -B "%ROOT%\native\plugins\lod_tuner\test\run_tests.py" || goto :fail
rem the inclination_lock plugin, the same way
%PY% -B "%ROOT%\native\plugins\inclination_lock\test\run_tests.py" || goto :fail
rem the compat plugin (Online skills as Dark Arisen actions), inside the real DDDA.exe code (skips without a build)
%PY% -B "%ROOT%\native\plugins\compat\test\run_tests.py" || goto :fail
rem the free_sprint plugin, the same way
%PY% -B "%ROOT%\native\plugins\free_sprint\test\run_tests.py" || goto :fail
rem the draw_distance plugin, the same way
%PY% -B "%ROOT%\native\plugins\draw_distance\test\run_tests.py" || goto :fail
rem the six_skill_warrior plugin, the same way (every patched compare, and the game's skill code on fake players)
%PY% -B "%ROOT%\native\plugins\six_skill_warrior\test\run_tests.py" || goto :fail
rem the portcrystals plugin, the same way (the moved list, the replaced runs, the save sidecar through the real
rem save and load copies, a reload in a new process)
%PY% -B "%ROOT%\native\plugins\portcrystals\test\run_tests.py" || goto :fail
rem the save_backup plugin, in a stand-in game process against fake Steam saves (skips without a build)
%PY% -B "%ROOT%\native\plugins\save_backup\test\run_tests.py" || goto :fail
rem the stage_enemies plugin (load an enemy's archive in a stage it is not native to), inside the real
rem DDDA.exe code: the byte sites, tag resolution against the archive table, and the thunk on a fake frame
%PY% -B "%ROOT%\native\plugins\stage_enemies\test\run_tests.py" || goto :fail
rem Ninput, the native plugin host: its offline harnesses (hook arbiter, XInput proxy, display arbiter; no game)
if exist "%ROOT%\native\ninput\out-msvc" (
  %PY% -B "%ROOT%\native\ninput\test\run_tests.py" || goto :fail
) else (
  echo ninput not built; run native\ninput\build_msvc.cmd to include it
)
rem fsmcheck against the game's own state-machine code, run on made-up machines (skips without a build or game)
%PY% -B "%ROOT%\native\fsm_exec\test\run_tests.py" || goto :fail
rem Explicit native runtime: only its synthetic host, never the game.
if exist "%ROOT%\native\runtime\out\runtime_host.exe" (
  %PY% -B "%ROOT%\native\runtime\test\run_tests.py" || goto :fail
)
rem Explicit clone appearance ABI: the opt-in synthetic factory host, never the game.
if exist "%ROOT%\native\clone_sync\out\clone_host.exe" (
  %PY% -B "%ROOT%\native\clone_sync\test\run_tests.py" || goto :fail
) else (
  echo clone_sync not built; synthetic factory interception gate skipped
)
rem Standalone performance hosts: synthetic snapshots/offscreen rendering only.
rem The combined runner reports missing components explicitly; build.py --test requires all.
%PY% -B "%ROOT%\native\performance\test\run_tests.py" || goto :fail
rem every byte and string the docs quote as evidence (`8B 81 ...` at `0x...`), against the exe itself
%PY% -B "%ROOT%\tools\doc_claims.py" check || goto :fail
if /i "%~1"=="full" (
  %PY% -B "%ROOT%\tools\check_corpus.py" || goto :fail
) else (
  %PY% -B "%ROOT%\tools\check_corpus.py" --only xfs,yaml,ocl,gmd,itl,lot,arcs,tables,flat,gpl,sbc,fsm,nav || goto :fail
)
echo ALL GATES PASSED
exit /b 0
:fail
echo GATE FAILED
exit /b 1
