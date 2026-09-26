"""Exercise the built loader against a stand-in game folder (no game is launched).

    python native/loader/test/run_tests.py [--work DIR]

Builds throwaway "games": harness.exe copied in as DDDA.exe (or DDO.exe, or a launcher), the loader,
nativePC\\ and riftstone\\overlay\\ with marker files, and checks every rule the loader promises:
overlay redirection and write pass-through, missing files and the texture stand-in, the import table
put back after start-up, plugins, crash / fatal-error / hang reports, report rotation, plugin
quarantine and safe mode, live stats in shared memory (with real Direct3D 9 frames when the machine
can draw), the window fixes, save backups, why the game closed (each close path, and the next start's
account of it), the in-game diagnostics panel (drawn over real frames and read back: where it is, what it
shows, that the game's drawing state survives it and a Reset, and when it must draw nothing), the Direct3D 9
side (the textures and buffers the game holds, by pool; [d3d9] chain with a stand-in DLL and every way it is
refused; with DXVK when a copy is at hand, what managed textures cost the address space under each), the
large-address flag and the memory pressure watch, and pass-through in programs that are not the game. The
panel's frames are saved as native\\loader\\out\\overlay-proof*.png.
Nothing here shows a window or a message box, types, moves the mouse, or touches the real game or saves.
"""
from __future__ import annotations

import argparse
import ctypes
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "out"
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
from riftstone import pe, runtime, tex  # noqa: E402

# The version the loader sources carry (runtime.h), which the built DLL must report.
VERSION = re.search(r'#define RIFTSTONE_VERSION_A "([^"]+)"',
                    (OUT.parent / "runtime.h").read_text(encoding="utf-8")).group(1)

FAILS: list[str] = []
# Every stand-in game gets these: no notice box, no real fatal box, no backup of the real saves.
SAFE_TEST_INI = "\n[loader]\nsafe_mode_notice = 0\nfatal_dialog = 0\n[saves]\nbackup = 0\n"


def merge_ini(*texts: str) -> str:
    """One section block per section, later texts overriding earlier keys (Windows' ini reader
    looks only in the first block of a section, so the settings must not be split)."""
    sections: dict[str, dict[str, str]] = {}
    for text in texts:
        current = None
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith(";"):
                continue
            if line.startswith("[") and line.endswith("]"):
                current = sections.setdefault(line[1:-1].strip().lower(), {})
            elif "=" in line and current is not None:
                k, _, v = line.partition("=")
                current[k.strip().lower()] = v.strip()
    return "".join(f"[{s}]\n" + "".join(f"{k} = {v}\n" for k, v in keys.items()) + "\n" for s, keys in sections.items())


def check(cond: bool, label: str) -> None:
    print(("  pass  " if cond else "  FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def game(root: Path, exe: str = "DDDA.exe", proxy: bool = True, ini: str = "", plugins: tuple = (),
         harness: str = "harness.exe") -> Path:
    root.mkdir(parents=True)
    shutil.copy(OUT / harness, root / exe)
    if proxy:
        shutil.copy(OUT / "dinput8.dll", root / "dinput8.dll")
    else:
        shutil.copy(OUT / "riftstone_loader.dll", root / "riftstone_loader.dll")
    files = {
        "nativePC/rom/enemy/em0100.arc": b"VANILLA-EM0100",
        "nativePC/rom/game_main.arc": b"VANILLA-MAIN",
        "nativePC/rom/model/present_BM.tex": b"TEX\0\x44\x33\x22\x11",
        "riftstone/overlay/rom/enemy/em0100.arc": b"OVERLAY-EM0100",
        "riftstone/overlay/rom/newthing.arc": b"OVERLAY-NEW",
        "other/em0100.arc": b"OTHER",
    }
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    template = (OUT.parent / "riftstone_loader.ini").read_text()
    (root / "riftstone_loader.ini").write_text(merge_ini(template, SAFE_TEST_INI, ini))
    if plugins:
        (root / "riftstone" / "plugins").mkdir(parents=True, exist_ok=True)
        for name in plugins:
            shutil.copy(OUT / name, root / "riftstone" / "plugins" / name)
    return root


def run(root: Path, *args: str, exe: str = "DDDA.exe") -> tuple[int, dict[str, str], str]:
    p = subprocess.run([str(root / exe), *args], cwd=root, capture_output=True, text=True, timeout=90,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    lines = {}
    for line in p.stdout.splitlines():
        key, _, value = line.partition(" ")
        lines[key] = value
    return p.returncode, lines, p.stdout


def log_of(root: Path) -> str:
    f = root / "riftstone/logs/loader.log"
    return f.read_text(encoding="utf-8", errors="replace") if f.is_file() else ""


def reports(root: Path, kind: str) -> list[Path]:
    return sorted((root / "riftstone/logs").glob(f"{kind}-*.txt"))


def is_crash_code(code: int) -> bool:
    return code in (0xC0000005, -1073741819)


def test_files(work: Path) -> None:
    print("overlay redirection, missing files and the texture stand-in (proxy build, as DDDA.exe)")
    root = game(work / "proxy")
    code, r, raw = run(root, "files")
    check(code == 0, f"harness exits 0 (got {code})")
    check(r.get("dinput") == "ok", "DirectInput8Create forwarded to the system dinput8")
    check(r.get("relative-A") == "OVERLAY-EM0100", "relative CreateFileA served from the overlay")
    check(r.get("relative-W") == "OVERLAY-EM0100", "relative CreateFileW served from the overlay")
    check(r.get("absolute-upper") == "OVERLAY-EM0100", "absolute, different-case path served from the overlay")
    check(r.get("dotted") == "OVERLAY-EM0100", "path with .. normalised, then served from the overlay")
    check(r.get("not-in-overlay") == "VANILLA-MAIN", "archive without an overlay copy opens the original")
    check(r.get("overlay-only") == "OVERLAY-NEW", "archive that exists only in the overlay opens")
    check(r.get("outside-nativepc") == "OTHER", "files outside nativePC are never redirected")
    check(r.get("attributes-overlay-only") == "exists", "GetFileAttributes sees overlay-only archives")
    check(r.get("write-open") == "VANILLA-EM0100", "opens for writing are never redirected")
    check(r.get("missing-arc", "").startswith("<cannot open> error=2"), "a missing archive still fails, with file-not-found")
    check(r.get("missing-tex") == "size=52 magic=TEX word1=20000099",
          "a missing texture under nativePC opens as the 52-byte DDDA stand-in (revision 0x99)")
    check(r.get("missing-tex-outside", "").startswith("<cannot open>"), "a missing texture outside nativePC still fails")
    check(r.get("present-tex") == "size=8 magic=TEX word1=11223344", "a texture that exists is served as it is")
    log = log_of(root)
    check("hook     KERNEL32.dll!CreateFileA installed" in log and "hook     KERNEL32.dll!CreateFileW installed" in log,
          "loader.log records the hooks")
    check(any("->" in line and "riftstone\\overlay\\rom\\enemy\\em0100.arc" in line for line in log.splitlines()),
          "loader.log records each redirect")
    check("missing  " in log and "em9999.arc" in log, "loader.log names the file the game looked for and did not find")
    check("guard    " in log and "missing_BM.tex does not exist" in log, "loader.log names the texture given a stand-in")
    check("Dragon's Dogma: Dark Arisen" in log, "loader.log says which game this is")
    standin = root / "riftstone/standin/missing-texture-ddda.tex"
    ok = False
    if standin.is_file():
        t = tex.parse(standin.read_bytes())
        ok = (t.width, t.height, t.mip_count, t.fmt, t.depth) == (4, 4, 3, 20, 1)
        dds = tex.to_dds(t)
        ok = ok and dds[:4] == b"DDS "
    check(ok, "the stand-in parses as a 4x4 BC1 texture with 3 mips (tex.py) and converts to .dds")

    print("texture guard switched off")
    root = game(work / "noguard", ini="[guard]\nmissing_textures = 0\n")
    code, r, _ = run(root, "files")
    check(r.get("missing-tex", "").startswith("<cannot open> error=2"), "[guard] missing_textures = 0: the open fails as before")


def test_reset_plugins_off_addon(work: Path) -> None:
    print("import table restored after start-up (what a DRM wrapper could do)")
    root = game(work / "reset")
    code, r, _ = run(root, "reset")
    check(r.get("restored") == "yes", "harness put the original CreateFileA back into its import table")
    check(r.get("after-watchdog") == "OVERLAY-EM0100", "the watchdog re-installed the hook within 400 ms")
    check("CreateFileA was reset during start-up; installed again" in log_of(root), "loader.log says the hook was re-installed")

    print("native plugin loading")
    root = game(work / "plugins", plugins=("marker_plugin.asi",))
    code, r, _ = run(root, "files")
    check((root / "riftstone/plugins/marker_plugin.loaded").is_file(),
          "a plugin in riftstone\\plugins was brought into the process (its DllMain ran)")
    check("plugin   marker_plugin.asi loaded at 0x" in log_of(root), "loader.log records the plugin load and address")

    print("overlay switched off")
    root = game(work / "off", ini="[loader]\noverlay = 0\n")
    code, r, _ = run(root, "files")
    check(r.get("relative-A") == "VANILLA-EM0100", "overlay = 0 serves the originals")

    print("add-on build (loaded by another dinput8, e.g. DDDA Tweak)")
    root = game(work / "addon", proxy=False)
    code, r, _ = run(root, "addon")
    check(r.get("addon") == "loaded", "riftstone_loader.dll loads with LoadLibrary")
    check(r.get("dinput") == "ok", "the system dinput8 still works")


def test_crash(work: Path) -> None:
    print("crash report")
    root = game(work / "crash", plugins=("marker_plugin.asi",))
    code, r, raw = run(root, "crash")
    check(is_crash_code(code), f"process ends with the access violation (got {code:#x})")
    check("game-filter-called" in raw, "the game's own crash filter still runs after the report")
    found = reports(root, "crash")
    check(len(found) == 1, "one crash-*.txt written")
    if found:
        text = found[0].read_text(encoding="utf-8", errors="replace")
        check("ACCESS_VIOLATION" in text and "writing address 0x00000010" in text, "report names the fault and address")
        check("DDDA.exe+0x" in text, "report gives module+offset for the faulting code")
        check("EIP=" in text and "ESP=" in text, "report has registers")
        check("address space used" in text and "largest free block" in text, "report has the memory headroom")
        check("marker_plugin.asi" in text.split("\nplugins")[-1], "report lists the plugins")
        check("em0100.arc" in text.split("last files opened")[-1], "report lists the last files opened")
        check("(start-up)" in text, "report says the crash was during start-up")
        rep = runtime.parse_report(text)
        check(rep["kind"] == "crash" and rep["exception"] and rep["exception"]["name"] == "ACCESS_VIOLATION",
              "runtime.parse_report reads the report back")
        dumps = sorted((root / "riftstone/logs").glob("crash-*.dmp"))
        check(len(dumps) == 1 and dumps[0].stat().st_size > 4096, "minidump written")
    check((root / "riftstone/logs/last-crash.txt").is_file(), "the crash left a note for the next start")
    end = runtime.session_end(root, running=False)
    check(bool(end) and end["reason"] == "crash" and (end["report"] or "").startswith("crash-"),
          "session_end (the game closed): a crash, with its report")
    code, r, _ = run(root, "files")
    check("last run ended in a crash" in log_of(root), "the next start reads the note and logs how the last run ended")
    check(not (root / "riftstone/logs/last-crash.txt").exists(), "the note is used up")
    end = runtime.session_end(root, running=True)
    check(bool(end) and end["reason"] == "crash" and (end["report"] or "").startswith("crash-"),
          "[last_session] keeps the crash and its report")

    print("exceptions that are not crashes, and crashes the game recovers from")
    root = game(work / "recover")
    code, r, raw = run(root, "recover")
    found = reports(root, "crash")
    texts = [p.read_text(encoding="utf-8", errors="replace") for p in found]
    check("recovering-filter 0x80000003" in raw and not any("0x80000003" in t for t in texts),
          "a debugger/anti-tamper probe goes to the game's filter without a report")
    check("still-running" in raw, "a filter that recovers keeps the game running")
    check(len(found) == 2 and any("0xe0001234" in t for t in texts) and any("ACCESS_VIOLATION" in t for t in texts),
          "the recovered exception and the later real crash each get a report")

    print("fatal-error box")
    root = game(work / "fatal")
    code, r, raw = run(root, "fatal")
    check(r.get("fatal-returned") == "1", "the game's MessageBoxA was answered (IDOK) without a real box ([loader] fatal_dialog = 0)")
    found = reports(root, "fatal")
    check(len(found) == 1, "one fatal-*.txt written")
    if found:
        text = found[0].read_text(encoding="utf-8", errors="replace")
        rep = runtime.parse_report(text)
        check(rep["kind"] == "fatal" and rep["missing_file"] == "nativePC\\rom\\model\\missing_BM.tex",
              "the report holds the message and the missing file")
        check("what it means" in text, "the report explains what the message means")
    check("fatal    the game showed" in log_of(root), "loader.log records the fatal error")
    check(_session(root).get("end") == "fatal-error", "runtime-state.ini: the session ended with the fatal error")


def test_rotation(work: Path) -> None:
    print("report rotation")
    root = game(work / "rotate")
    logs = root / "riftstone/logs"
    logs.mkdir(parents=True)
    for i in range(14):
        (logs / f"crash-20260101-0000{i:02d}.txt").write_text("old")
        (logs / f"crash-20260101-0000{i:02d}.dmp").write_text("old")
    run(root, "files")
    left = sorted(logs.glob("crash-*.txt"))
    check(len(left) == 10 and left[0].name == "crash-20260101-000004.txt", "the newest 10 crash reports are kept")
    check(len(list(logs.glob("crash-*.dmp"))) == 10, "their minidumps go with them")
    run(root, "files")
    check((logs / "loader.prev.log").is_file(), "the previous session's log is kept as loader.prev.log")


def test_quarantine(work: Path) -> None:
    print("plugin quarantine (a plugin that crashes the game's start-up twice)")
    root = game(work / "quarantine", plugins=("marker_plugin.asi", "crash_plugin.asi"))
    code, r, raw = run(root, "plugincrash")
    check(r.get("crash-plugin") == "loaded" and is_crash_code(code), "the plugin crashed the stand-in game")
    found = reports(root, "crash")
    text = found[0].read_text(encoding="utf-8", errors="replace") if found else ""
    check("fault in    plugin crash_plugin.asi" in text, "the crash report says the fault is inside crash_plugin.asi")
    check(runtime.parse_report(text)["fault_plugin"] == "crash_plugin.asi", "parse_report finds the plugin")
    code, r, _ = run(root, "plugincrash")
    check(is_crash_code(code), "second start-up crash in the same plugin")
    (root / "riftstone/plugins/marker_plugin.loaded").unlink(missing_ok=True)
    code, r, _ = run(root, "plugincrash")
    log = log_of(root)
    check("crash_plugin.asi QUARANTINED" in log, "loader.log says the plugin is quarantined")
    check(r.get("crash-plugin") == "absent" and code == 4, "the quarantined plugin is not loaded; the game runs")
    check((root / "riftstone/plugins/marker_plugin.loaded").is_file(), "the other plugin still loads")
    st = runtime.runtime_state(root)
    check("crash_plugin.asi" in st["quarantine"], "runtime_state shows the quarantine")
    # A new version of the plugin (another write time) gets another chance.
    p = root / "riftstone/plugins/crash_plugin.asi"
    os.utime(p, (time.time() + 5, time.time() + 5))
    code, r, _ = run(root, "files")
    check("crash_plugin.asi changed since its quarantine; loading it again" in log_of(root),
          "a changed plugin file is loaded again")
    check(runtime.release_plugin(root, "crash_plugin.asi") is False, "release_plugin says there was nothing to release")


def test_safe_mode(work: Path) -> None:
    print("safe mode (two start-up crashes in a row that no plugin explains)")
    root = game(work / "safe", plugins=("marker_plugin.asi",))
    run(root, "crash")
    run(root, "crash")
    (root / "riftstone/plugins/marker_plugin.loaded").unlink(missing_ok=True)
    code, r, _ = run(root, "files")
    log = log_of(root)
    check("SAFE MODE" in log, "loader.log announces safe mode")
    check(r.get("relative-A") == "VANILLA-EM0100", "safe mode serves the game's own files (no overlay)")
    check(not (root / "riftstone/plugins/marker_plugin.loaded").exists(), "safe mode loads no plugins")
    check(runtime.runtime_state(root)["safe_mode"], "runtime_state shows safe mode")
    code, r, _ = run(root, "files")
    check(r.get("relative-A") == "VANILLA-EM0100", "safe mode stays while nothing changes")
    (root / "riftstone/overlay/rom/changed.arc").write_bytes(b"NEW")
    code, r, _ = run(root, "files")
    check("they are back on" in log_of(root) and r.get("relative-A") == "OVERLAY-EM0100",
          "changing the mods ends safe mode by itself")
    print("safe mode ended from Riftstone")
    root = game(work / "safe2")
    run(root, "crash")
    run(root, "crash")
    run(root, "files")
    check(runtime.runtime_state(root)["safe_mode"], "safe mode is on")
    check(runtime.safe_mode_off(root) is True, "runtime.safe_mode_off ends it")
    code, r, _ = run(root, "files")
    check(r.get("relative-A") == "OVERLAY-EM0100" and "SAFE MODE" not in log_of(root), "the next start is normal")


def _wait_line(p: subprocess.Popen, want: str, timeout: float = 60) -> list[str]:
    seen = []
    end = time.time() + timeout
    while time.time() < end:
        line = p.stdout.readline()
        if not line:
            break
        seen.append(line.strip())
        if line.strip() == want:
            break
    return seen


def test_live(work: Path) -> None:
    print("live stats in shared memory (and Direct3D 9 frame timing)")
    root = game(work / "live", plugins=("marker_plugin.asi",))
    p = subprocess.Popen([str(root / "DDDA.exe"), "live"], cwd=root, stdout=subprocess.PIPE, text=True,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        seen = _wait_line(p, "ready")
        drew = any(s.startswith("presented") for s in seen)
        time.sleep(1.5)                      # a publish and a memory sample
        live = runtime.read_live(p.pid)
        check(live is not None, "the stand-in game publishes live stats")
        if live:
            check(live["pid"] == p.pid and live["game"] == 1, "the block names this process and Dark Arisen")
            check(live["loader_version"] == VERSION, "the block carries the loader version")
            check(live["va_total"] > 0 and 0 < live["va_used"] < live["va_total"], "address space used/total are sampled")
            check(live["va_largest_free"] > 0, "the largest free block is measured")
            # Peak commit (the 4 GB budget) and the memory verdict the loader computes from it.  A tiny
            # stand-in process sits far under the ceiling, so the verdict is "headroom" (level 1).
            check(live["private_bytes_peak"] > 0, f"peak commit is published ({live['private_bytes_peak'] >> 20} MB)")
            check(live["mem_verdict"] == 1, f"the loader's memory verdict is headroom (got {live['mem_verdict']})")
            peak_mb = live["private_bytes_peak"] >> 20
            check(runtime.memory_verdict(peak_mb, live["va_largest_free_min"] >> 20)[0] == live["mem_verdict"],
                  "runtime.memory_verdict agrees with the loader's own verdict")
            check(live["redirects"] >= 1, "overlay redirects are counted")
            check(any(pl["name"] == "marker_plugin.asi" and pl["state"] == "loaded" for pl in live["plugin_list"]),
                  "the plugins and their state are listed")
            check(live["enemies_active"] is None, "enemy slots stay unknown outside the real build")
            if drew:
                check(live["frame_timing"] and live["frames"] >= 60, f"Present was timed ({live['frames']} frames)")
                check(live["backbuffer_w"] == 320 and live["backbuffer_h"] == 240 and live["windowed"] is True,
                      "the device's back buffer and mode are recorded")
                check(live["fps"] is not None and 5 <= live["fps"] <= 1000, f"a plausible frame rate ({live['fps']} fps)")
                check(len(live["frame_times_us"]) >= 59, "the frame-time ring holds the recent frames")
            else:
                print("  skip  Direct3D 9 could not draw on this machine; frame timing not checked")
            check(bool(runtime.describe_live(live)), "describe_live summarises it")
    finally:
        (root / "done").write_text("")
        p.wait(timeout=60)
    check("summary  ran" in log_of(root), "a normal exit writes the summary line to loader.log")
    check(runtime.read_live(p.pid) is None, "the block is gone once the game has exited")
    st = runtime.runtime_state(root)
    check(st["last_clean"], "runtime-state.ini records the clean exit")

    print("hang report")
    root = game(work / "hang", ini="[live]\nhang_seconds = 2\nhang_needs_front = 0\n")
    p = subprocess.Popen([str(root / "DDDA.exe"), "hang"], cwd=root, stdout=subprocess.PIPE, text=True,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        seen = _wait_line(p, "ready")
        drew = any(s.startswith("presented") for s in seen)
        time.sleep(4.0)
        found = reports(root, "hang")
        if drew:
            check(len(found) == 1, "no frame for 2 s writes one hang report")
            if found:
                rep = runtime.parse_report(found[0].read_text(encoding="utf-8", errors="replace"))
                check(rep["kind"] == "hang" and rep["main_thread"], "the hang report holds where the main thread was")
        else:
            print("  skip  Direct3D 9 could not draw on this machine; hang detection not checked")
    finally:
        (root / "done").write_text("")
        p.wait(timeout=60)


def test_window(work: Path) -> None:
    print("window fixes")
    root = game(work / "window-plain")
    code, r, _ = run(root, "window")
    check(r.get("window-caption") == "yes" and r.get("activateapp-seen") == "yes", "without the fixes: framed, pauses on focus loss")
    root = game(work / "window", ini="[window]\nborderless = 1\nbackground_run = 1\n")
    code, r, _ = run(root, "window")
    check(r.get("window-caption") == "no", "borderless: the game window has no frame")
    check(r.get("window-covers-monitor") == "yes", f"borderless: the window covers the monitor ({r.get('window-size')})")
    check(r.get("activateapp-seen") == "no", "background_run: losing focus does not reach the game (it keeps running)")


def test_saves(work: Path) -> None:
    print("save backups")
    remote = work / "steam-remote"
    remote.mkdir(parents=True)
    (remote / "DDDA.sav").write_bytes(b"SAVE-1" * 100)
    (remote / "0").write_bytes(b"x" * 16)
    target = work / "save-backups"
    ini = f"[saves]\nbackup = 1\nkeep = 2\nsource = {remote}\ntarget = {target}\n"
    root = game(work / "saves", ini=ini)
    run(root, "files")
    made = sorted(d for d in target.iterdir() if d.is_dir()) if target.is_dir() else []
    check(len(made) == 1 and (made[0] / "DDDA.sav").read_bytes() == b"SAVE-1" * 100 and (made[0] / "0").is_file(),
          "the save folder is copied before the game reads it")
    time.sleep(1.1)
    run(root, "files")
    check(len([d for d in target.iterdir() if d.is_dir()]) == 1, "an unchanged save is not copied again")
    for n in (2, 3):
        time.sleep(1.1)
        (remote / "DDDA.sav").write_bytes(f"SAVE-{n}".encode() * 100)
        run(root, "files")
    made = sorted(d for d in target.iterdir() if d.is_dir())
    check(len(made) == 2 and (made[-1] / "DDDA.sav").read_bytes() == b"SAVE-3" * 100,
          "a changed save is copied, and only the newest [saves] keep backups stay")


_USER32 = None
WM_DESTROY, WM_CLOSE, WM_QUERYENDSESSION, WM_QUIT, WM_ENDSESSION, WM_SYSCOMMAND = 0x2, 0x10, 0x11, 0x12, 0x16, 0x112
SC_CLOSE, ENDSESSION_LOGOFF = 0xF060, 0x80000000


def _user32():
    global _USER32
    if _USER32 is None:
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        u.PostMessageW.restype = wintypes.BOOL
        u.SendMessageTimeoutW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                          wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t))
        u.SendMessageTimeoutW.restype = ctypes.c_size_t
        _USER32 = u
    return _USER32


def _post(hwnd: int, msg: int, wp: int = 0, lp: int = 0) -> None:
    _user32().PostMessageW(hwnd, msg, wp, lp)


def _send(hwnd: int, msg: int, wp: int = 0, lp: int = 0) -> None:
    result = ctypes.c_size_t()
    _user32().SendMessageTimeoutW(hwnd, msg, wp, lp, 0x0002, 5000, ctypes.byref(result))   # SMTO_ABORTIFHUNG


def _front(game_pid: int) -> str:
    """The window in front, worded as the loader words it: '<program> [<class>]' ('the game' for the game's)."""
    u = ctypes.WinDLL("user32", use_last_error=True)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    u.GetForegroundWindow.restype = wintypes.HWND
    u.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    u.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    k.OpenProcess.restype = wintypes.HANDLE
    k.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                             ctypes.POINTER(wintypes.DWORD))
    w = u.GetForegroundWindow()
    if not w:
        return "no window"
    cls = ctypes.create_unicode_buffer(80)
    u.GetClassNameW(w, cls, 80)
    pid = wintypes.DWORD()
    u.GetWindowThreadProcessId(w, ctypes.byref(pid))
    if pid.value == game_pid:
        name = "the game"
    else:
        name = f"process {pid.value}"
        h = k.OpenProcess(0x1000, False, pid.value)                                 # PROCESS_QUERY_LIMITED_INFORMATION
        if h:
            buf, n = ctypes.create_unicode_buffer(260), wintypes.DWORD(260)
            if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                name = buf.value.rsplit("\\", 1)[-1]
            k.CloseHandle(h)
    return f"{name} [{cls.value}]"


def _as_build_2364871(root: Path) -> None:
    """The copied stand-in gets build 2364871's PE time stamp, so the loader takes it for that build and
    checks exit_sites.h's bytes (the DDDA-layout stand-in has them where DDDA.exe does)."""
    exe = root / "DDDA.exe"
    data = bytearray(exe.read_bytes())
    struct.pack_into("<I", data, struct.unpack_from("<I", data, 0x3C)[0] + 8, 0x5A314C31)
    exe.write_bytes(bytes(data))


def _closed(root: Path, how: str, act=None, timeout: float = 60) -> dict[str, str]:
    """Run `close <how>`; act(process, hwnd) runs once the loader watches the window (another program's part)."""
    p = subprocess.Popen([str(root / "DDDA.exe"), "close", how], cwd=root, stdout=subprocess.PIPE, text=True,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    lines: dict[str, str] = {}
    out = ""
    try:
        for line in _wait_line(p, "ready", timeout):
            key, _, value = line.partition(" ")
            lines[key] = value
        if act and lines.get("hwnd"):
            act(p, int(lines["hwnd"], 16))
        out, _ = p.communicate(timeout=timeout)
    finally:
        if p.poll() is None:
            p.kill()
            p.wait(timeout=30)
    for line in (out or "").splitlines():
        key, _, value = line.partition(" ")
        lines[key] = value
    return lines


def _session(root: Path) -> dict:
    return runtime.runtime_state(root)["session"]


def _exit_logged(root: Path) -> bool:
    """loader.log has the `exit` line naming the reason, and the exit summary ends with it."""
    lines = log_of(root).splitlines()
    return any("  exit     " in s and ", after " in s for s in lines) and \
        any("  summary  " in s and "; ended by: " in s for s in lines)


def test_exit(work: Path) -> None:
    print("why the game closed: its window's close paths (DDDA's window procedure and loop, stood in; nothing is "
          "typed or clicked)")
    cases = [
        ("altf4", "alt-f4", "WM_SYSKEYDOWN F4 with Alt (from a message posted to the window, not a key press), then "
         "SC_CLOSE (posted), then WM_CLOSE", "Alt+F4: the key, SC_CLOSE, WM_CLOSE"),
        ("button", "close-button", "SC_CLOSE clicked at 620,18", "the close button: SC_CLOSE at the click, WM_CLOSE"),
        ("menu", "window-menu", "SC_CLOSE chosen by keyboard (a menu key)", "the window menu's Close by keyboard"),
        ("destroy", "window-destroyed", "WM_DESTROY of a game window on its own thread",
         "a game window destroyed with no close before it"),
        ("none", "self-exit", "no window message asked the game to close", "the game ending with no message"),
    ]
    for how, want, detail, label in cases:
        root = game(work / f"exit-{how}")
        lines = _closed(root, how)
        st = _session(root)
        check(lines.get("watched") == "yes", f"{how}: the loader found the game window and watches its thread")
        check(st.get("end") == want and detail in st.get("end_detail", ""),
              f"{label}: runtime-state.ini end = {want} (got {st.get('end')}: {st.get('end_detail')})")
        check(_exit_logged(root) and st.get("clean") == "1", f"{how}: loader.log names it, in the exit summary too")
        if how in ("altf4", "button", "menu"):
            log = log_of(root)
            check("exit     WM_DESTROY of the game window" in log and "exit     WM_QUIT reached the game's message loop "
                  "(from PostQuitMessage" in log, f"{how}: loader.log follows the close to WM_DESTROY and WM_QUIT")
    root = work / "exit-altf4"
    run(root, "files")
    log = log_of(root)
    check("last run closed after" in log and "ended by: Alt+F4" in log, "the next start logs how the last session ended")
    end = runtime.session_end(root, running=True)
    check(bool(end) and end["reason"] == "alt-f4" and end["clean"] and "It was not a crash." in runtime.describe_end(end),
          "[last_session] keeps it: session_end / describe_end say Alt+F4, not a crash")

    print("close messages from another program")
    outside = [
        ("a posted WM_CLOSE", lambda p, h: _post(h, WM_CLOSE), "close-message", "WM_CLOSE posted to the game's window"),
        ("a sent WM_CLOSE", lambda p, h: _send(h, WM_CLOSE), "close-message",
         "WM_CLOSE sent to the game's window from another thread"),
        ("a sent SC_CLOSE (e.g. the taskbar)", lambda p, h: _send(h, WM_SYSCOMMAND, SC_CLOSE, 0), "close-message",
         "SC_CLOSE sent from another thread"),
        ("a posted WM_QUIT", lambda p, h: _post(h, WM_QUIT, 5), "quit-message", "WM_QUIT posted to a window of the game"),
        ("a sent WM_DESTROY", lambda p, h: _send(h, WM_DESTROY), "close-message",
         "WM_DESTROY sent to the game's window from another thread"),
    ]
    for i, (label, act, want, detail) in enumerate(outside):
        root = game(work / f"exit-outside-{i}")
        seen: dict[str, str] = {}

        def act_and_look(p, h, act=act):
            seen["before"] = _front(p.pid)
            act(p, h)
            seen["after"] = _front(p.pid)

        lines = _closed(root, "outside", act_and_look)
        st = _session(root)
        got = st.get("end_detail", "")
        check(lines.get("close-ended") == "quit" and st.get("end") == want and detail in got,
              f"{label}: end = {want} (got {st.get('end')}: {got})")
        # Who was in front when it came: the loader names the same program and window class this test sees.
        m = re.search(r"at that moment: in front (.+?); the game's window (in front|minimized|not in front); the last "
                      r"keyboard or mouse input (\d+)\.(\d) s before; the pointer over (.+?)(?:; in stage \d+)?$", got)
        steady = seen.get("before") == seen.get("after")
        check(bool(m) and (m.group(1) == seen.get("before") or not steady),
              f"{label}: it notes the window in front ({m.group(1) if m else None!r}; this test saw "
              f"{seen.get('before')!r}), whether the game's was, the idle time and the window under the pointer")

    print("Windows ending the session, and a game ended from outside")
    root = game(work / "exit-session")

    def end_session(p, h):
        _send(h, WM_QUERYENDSESSION, 0, ENDSESSION_LOGOFF)
        _send(h, WM_ENDSESSION, 1, ENDSESSION_LOGOFF)
        time.sleep(0.3)
        p.kill()                                           # what Windows does after WM_ENDSESSION

    _closed(root, "keep", end_session)
    st = _session(root)
    check(st.get("end") == "session-end" and st.get("clean") == "0" and "signing out" in st.get("end_detail", ""),
          "WM_ENDSESSION is written down at once, before Windows ends the process")
    check("Windows asks whether the session may end" in log_of(root), "loader.log has Windows' question too")
    run(root, "files")
    end = runtime.session_end(root, running=True)
    check("last run was ended by Windows" in log_of(root) and bool(end) and end["reason"] == "session-end"
          and not end["clean"], "the next start: ended by Windows, without the game's own shutdown")
    root = game(work / "exit-killed")
    _closed(root, "keep", lambda p, h: p.kill())
    run(root, "files")
    end = runtime.session_end(root, running=True)
    check("did not exit normally and left no report" in log_of(root) and bool(end) and end["reason"] == "not-clean",
          "a game ended from outside: the next start says it left no report (not-clean)")
    # With nothing running, session_end reads the last session itself: here the `files` run, which had no window.
    end = runtime.session_end(root, running=False)
    check(bool(end) and end["reason"] == "unknown" and end["clean"], "a run without a window: exited, reason unknown")

    print("the game's own exit and exit request, in DDDA.exe's layout (build 2364871's bytes)")
    ddda = [("quitflag", "exit-menu", "the game set its quit flag (sApp+0x266C) with no close message",
             "Exit Game: the quit flag, no message"),
            ("altf4", "alt-f4", "WM_SYSKEYDOWN F4 with Alt", "Alt+F4 (the loop's own quit flag afterwards changes nothing)"),
            ("exitrequest", "exit-request", "sMain's exit request (0x00DBD0F0) with no WM_CLOSE",
             "sMain's exit request with no WM_CLOSE (the debug Exit command)"),
            ("startset-button", "close-button", "SC_CLOSE clicked at 620,18",
             "the quit flag reading set during start-up is not Exit Game (seen in the real game); the close button "
             "is what ended it")]
    for how, want, detail, label in ddda:
        root = game(work / f"exit-ddda-{how}", harness="harness_ddda.exe")
        _as_build_2364871(root)
        lines = _closed(root, how)
        st = _session(root)
        verified = "the game's own exit (its quit flag) is verified for this build" in log_of(root)
        check(lines.get("layout") == "ddda" and verified, f"{how}: the loader verified the exit code where DDDA has it")
        check(st.get("end") == want and detail in st.get("end_detail", ""),
              f"{label}: end = {want} (got {st.get('end')}: {st.get('end_detail')})")
    root = game(work / "exit-other-code")
    _as_build_2364871(root)
    _closed(root, "quitflag")
    check("is not what build 2364871 has" in log_of(root) and _session(root).get("end") == "self-exit",
          "code that is not build 2364871's: the quit flag is never read, and the log says why")
    root = game(work / "exit-off", ini="[loader]\nexit_reason = 0\n")
    lines = _closed(root, "altf4")
    log = log_of(root)
    check(lines.get("watched") == "no" and "why the game closes is not recorded" in log and not _exit_logged(root)
          and not _session(root).get("end") and _session(root).get("clean") == "1",
          "[loader] exit_reason = 0: no hooks, nothing recorded, the exit is still clean")


# ---- the in-game diagnostics panel (overlay.cpp) ----------------------------------------------------------------

PANEL_CLEAR = (58, 74, 92)                  # harness.cpp's OVERLAY_CLEAR: the frame under the panel
PANEL_BASE = (8, 8, 12)                     # the panel's surface, drawn at 92 %
PANEL_TRACK = (0x28, 0x2D, 0x39)            # a meter's empty track


def _panel_rect(w: int, h: int, scale: str = "auto", position: str = "top-right") -> tuple[int, int, int, int]:
    """Where overlay.cpp puts the panel, worked out here on its own: 416 x 256 at 1080p, 16 px from its corner,
    times the scale (auto: the height / 1080; 0.75..3; smaller when the back buffer needs it)."""
    s = h / 1080 if scale == "auto" else float(scale)
    s = min(max(s, 0.75), 3.0, w / 448, h / 288)

    def px(v: float) -> int:
        return math.floor(v * s + 0.5)

    pw, ph, inset = px(416), px(256), px(16)
    x0 = inset if position.endswith("left") else w - inset - pw
    y0 = inset if position.startswith("top") else h - inset - ph
    return x0, y0, x0 + pw, y0 + ph


def _is_cyan(p) -> bool:
    return p[0] < 110 and p[1] > 150 and p[2] > 150


def _is_ruby(p) -> bool:
    return p[0] > 150 and p[1] < 90 and p[2] < 90


def _is_track(p) -> bool:
    return all(abs(a - b) <= 6 for a, b in zip(p, PANEL_TRACK))


def _is_notch(p) -> bool:                   # the quiet white mark at the memory warning level
    return min(p) > 90 and max(p) - min(p) < 40


def _all_clear(img) -> bool:
    return img.getcolors(1) == [(img.width * img.height, PANEL_CLEAR)]


def _ruby_count(img) -> int:
    data = img.get_flattened_data() if hasattr(img, "get_flattened_data") else img.getdata()   # Pillow 12+ / older
    return sum(_is_ruby(p) for p in data)


def _meters(img, rect) -> list[dict]:
    """The panel's meters, top to bottom: runs of rows, 2 px tall or more, where at least half the panel's width
    is a meter's colours (track, cyan, ruby); the 1 px dividers are not meters. Each with its rows, its columns
    and the share of its middle row filled in cyan or ruby."""
    pix = img.load()
    x0, y0, x1, y1 = rect

    def meter_px(p) -> bool:
        return _is_track(p) or _is_cyan(p) or _is_ruby(p)

    rows = [y for y in range(y0, y1) if sum(meter_px(pix[x, y]) for x in range(x0, x1)) * 2 >= x1 - x0]
    groups: list[list[int]] = []
    for y in rows:
        if groups and y == groups[-1][1] + 1:
            groups[-1][1] = y
        else:
            groups.append([y, y])
    out = []
    for top, bottom in groups:
        if bottom == top:
            continue
        mid = (top + bottom) // 2
        # The meter is the longest run of its colours on its middle row (a 1 px mark may cross it; the caption
        # beside it is not part of it).
        runs: list[list[int]] = []
        for x in range(x0, x1):
            if meter_px(pix[x, mid]):
                if runs and x - runs[-1][1] <= 3:
                    runs[-1][1] = x
                else:
                    runs.append([x, x])
        left, right = max(runs, key=lambda run: run[1] - run[0])
        right += 1
        out.append({"top": top, "bottom": bottom, "left": left, "right": right,
                    "cyan": sum(_is_cyan(pix[x, mid]) for x in range(left, right)) / (right - left),
                    "ruby": sum(_is_ruby(pix[x, mid]) for x in range(left, right)) / (right - left)})
    return out


def _mark_above(img, meter: dict, pred, frac: float | None = None) -> bool:
    """A mark (a tick) just above the meter matching pred: at frac of its length (within 2 px), or anywhere."""
    pix = img.load()
    if frac is None:
        xs = range(meter["left"], meter["right"])
    else:
        x = meter["left"] + round((meter["right"] - meter["left"]) * frac)
        xs = range(x - 2, x + 3)
    return any(pred(pix[x, meter["top"] - 1]) for x in xs)


def _panel_shape(img, rect, label: str) -> None:
    """Nothing is drawn outside the panel, its cut corners are cut and the others square, and most of it is
    the panel's surface: 92 % over the frame below."""
    w, h = img.size
    x0, y0, x1, y1 = rect
    outside = [(0, 0, w, y0), (0, y1, w, h), (0, y0, x0, y1), (x1, y0, w, y1)]
    clean = all(img.crop(b).getcolors(1) == [((b[2] - b[0]) * (b[3] - b[1]), PANEL_CLEAR)]
                for b in outside if b[2] > b[0] and b[3] > b[1])
    check(clean, f"{label}: every pixel outside the panel {rect} is exactly the frame's own (the clear colour)")
    pix = img.load()
    check(pix[x0, y0] == PANEL_CLEAR and pix[x1 - 1, y1 - 1] == PANEL_CLEAR and pix[x1 - 1, y0] != PANEL_CLEAR
          and pix[x0, y1 - 1] != PANEL_CLEAR, f"{label}: the upper-left and lower-right corners are cut, the others square")
    surface = tuple(round(c * 235 / 255 + k * 20 / 255) for c, k in zip(PANEL_BASE, PANEL_CLEAR))
    inner = [pix[x, y] for y in range(y0 + 4, y1 - 4, 2) for x in range(x0 + 4, x1 - 4, 2)]
    share = sum(all(abs(a - b) <= 3 for a, b in zip(p, surface)) for p in inner) / len(inner)
    check(share > 0.6, f"{label}: {share:.0%} of the panel is its surface colour {surface}")


def _proof_sheet(crops: list, path: Path) -> None:
    """The panels side by side at twice their size, for a close look."""
    from PIL import Image

    big = [c.resize((c.width * 2, c.height * 2), Image.NEAREST) for c in crops]
    sheet = Image.new("RGB", (sum(b.width for b in big) + 24 * (len(big) + 1), max(b.height for b in big) + 48),
                      PANEL_CLEAR)
    x = 24
    for b in big:
        sheet.paste(b, (x, 24))
        x += b.width + 24
    sheet.save(path)


def test_overlay(work: Path) -> None:
    print("the in-game diagnostics panel over real Direct3D 9 frames (each read back after its Present; nothing is "
          "shown or pressed)")
    try:
        from PIL import Image
    except ImportError:
        print("  skip  Pillow is not installed; the panel is not checked")
        return
    # DDDA.exe's layout with a stand-in engine (7, then 3 of 10 enemy slots in use; stage 100), a plugin that
    # loads and one that does not; 1080p, then address space nearly used up, then a Reset to 1440p.
    root = game(work / "panel", ini="[overlay]\nshow_at_start = 1\n", plugins=("marker_plugin.asi",),
                harness="harness_ddda.exe")
    (root / "riftstone/plugins/broken_plugin.asi").write_bytes(b"MZ, but not a plugin")
    _as_build_2364871(root)
    code, r, raw = run(root, "overlay", "1920x1080", "then=2560x1440", "critical", "engine")
    if not r.get("overlay-device"):
        print("  skip  Direct3D 9 could not draw on this machine; the panel is not checked")
        return
    check(code == 0 and r.get("engine") == "stand-in" and r.get("overlay-device", "")[-4:] in ("0x46", "0x26"),
          f"the stand-in game drew with DDDA's own device flags and a stand-in engine ({r.get('overlay-device')})")
    kept = re.match(r"(\d+)/(\d+)", r.get("states-kept", ""))
    check(bool(kept) and kept.group(1) == kept.group(2) and int(kept.group(2)) >= 250,
          "after every Present the game's render target, depth-stencil surface, textures, buffers, viewport, scissor, "
          f"shader constants, transform and states are as it bound them ({r.get('states-kept')})")
    log = log_of(root)
    check("overlay  F10 shows the diagnostics panel (top-right, scale auto); shown from the first frame" in log,
          "loader.log says the panel is available and which key shows it")
    check("overlay  panel shown on the 1920x1080 back buffer at scale 1.00 (top-right)" in log and
          "overlay  panel shown on the 2560x1440 back buffer at scale 1.33 (top-right)" in log,
          "loader.log names the back buffer and scale it draws at, before and after the Reset")
    opened = re.findall(r"overlay  panel opened: enemy pool (\d+) / (\d+) slots \(peak (\d+)\), address space "
                        r"([\d.]+) / ([\d.]+) GB \(([\d.]+)% headroom\)(?:, nearly used up)?, (?:[\d.]+ fps|fps UNKNOWN), "
                        r"stage (\d+)", log)
    check(len(opened) == 1 and opened[0][1] == "10" and opened[0][0] in ("7", "3") and opened[0][2] == "7"
          and opened[0][6] == "100",
          "loader.log notes what the panel showed as it opened, once (the stand-in's enemy pool of 10 with its peak "
          f"of 7, the address space, the frame rate, stage 100): {opened}")
    crops = []
    shots = {}
    for name in ("normal", "critical", "reset"):
        f = root / f"overlay-{name}.bmp"
        shots[name] = Image.open(f).convert("RGB") if f.is_file() else None
    img = shots["normal"]
    if img:
        rect = _panel_rect(1920, 1080)
        _panel_shape(img, rect, "1080p")
        meters = _meters(img, rect)
        check(len(meters) == 2, f"1080p: two meters, the enemy pool and the address space (found {len(meters)})")
        if len(meters) == 2:
            en, mem = meters
            check(abs(en["cyan"] - 0.3) <= 0.02 and en["ruby"] == 0 and _mark_above(img, en, _is_cyan, 0.7),
                  f"enemy pool: 3 of 10 slots filled in cyan ({en['cyan']:.3f}), the session's peak (7) ticked at 70 %")
            warn = 1 - (400 << 20) / int(r.get("va-total", "0") or 1)
            check(0 < mem["cyan"] < 0.6 and mem["ruby"] == 0 and _mark_above(img, mem, _is_notch, warn),
                  f"address space: the share in use in cyan ({mem['cyan']:.3f}), a notch at the loader's warning level "
                  "(400 MB before the end), no ruby")
            above = img.crop((rect[0], rect[1], rect[2], mem["bottom"] + 2))
            below = img.crop((rect[0], mem["bottom"] + 2, rect[2], rect[3]))
            check(_ruby_count(below) > 0 and _ruby_count(above) == 0,
                  "the plugin that failed to load gets a ruby-accented chip; nothing above the chips is ruby")
        crops.append(img.crop((rect[0] - 12, rect[1] - 12, rect[2] + 12, rect[3] + 12)))
        img.save(OUT / "overlay-proof.png")
    else:
        check(False, "the 1080p frame was read back")
    img = shots["critical"]
    left = re.search(r"(\d+) MB left", r.get("reserved", ""))
    if img and left:
        rect = _panel_rect(1920, 1080)
        meters = _meters(img, rect)
        mem = meters[1] if len(meters) == 2 else None
        check(int(left.group(1)) < 400 and mem is not None and mem["ruby"] >= 0.75 and mem["cyan"] == 0
              and abs(meters[0]["cyan"] - 0.3) <= 0.02,
              f"address space nearly used up ({left.group(1)} MB left): the memory meter turns ruby, the enemy meter "
              "stays as it was")
        if mem:
            # The label row sits 24..6 px above the meter at 1080p; the droplet at the content's left edge.
            drop = img.crop((rect[0] + 14, mem["top"] - 24, rect[0] + 23, mem["top"] - 6))
            edge = any(_is_ruby(img.getpixel((rect[0], y))) for y in range(mem["top"] - 24, mem["bottom"] + 1))
            check(_ruby_count(drop) >= 25 and edge,
                  "critical: a ruby droplet before the reason, and a short ruby mark on the panel's edge")
        crops.append(img.crop((rect[0] - 12, rect[1] - 12, rect[2] + 12, rect[3] + 12)))
        img.save(OUT / "overlay-proof-critical.png")
    else:
        check(False, "the address-space frame was read back")
    img = shots["reset"]
    check(r.get("reset", "").startswith("ok 2560x1440") and img is not None,
          "a device Reset (to 2560x1440) with the panel showing succeeds, and the panel draws again after it")
    if img:
        rect = _panel_rect(2560, 1440)
        _panel_shape(img, rect, "1440p after the Reset")
        meters = _meters(img, rect)
        check(len(meters) == 2 and abs(meters[0]["cyan"] - 0.3) <= 0.02,
              f"1440p: the panel at scale 1.33 ({rect[2] - rect[0]} x {rect[3] - rect[1]}), its meters as before")
        crops.append(img.crop((rect[0] - 12, rect[1] - 12, rect[2] + 12, rect[3] + 12)))
        img.save(OUT / "overlay-proof-1440.png")
    if crops:
        _proof_sheet(crops, OUT / "overlay-proof-panels.png")
        print(f"        proof: {OUT / 'overlay-proof.png'} (and -critical, -1440, -panels)")

    print("readings that are not there (not build 2364871: no enemy slots, no stage) and another corner and size")
    root = game(work / "panel-unknown", ini="[overlay]\nshow_at_start = 1\n")
    code, r, _ = run(root, "overlay", "1920x1080")
    f = root / "overlay-normal.bmp"
    if f.is_file():
        img = Image.open(f).convert("RGB")
        rect = _panel_rect(1920, 1080)
        meters = _meters(img, rect)
        check(len(meters) == 2 and meters[0]["cyan"] == 0 and meters[0]["ruby"] == 0
              and not _mark_above(img, meters[0], _is_cyan) and meters[1]["cyan"] > 0,
              "an unknown enemy count leaves its meter an empty track, no fill and no peak (memory still shows)")
    else:
        check(False, "the frame with unknown readings was read back")
    root = game(work / "panel-corner", ini="[overlay]\nshow_at_start = 1\nposition = bottom-left\nscale = 1.5\n")
    code, r, _ = run(root, "overlay", "1920x1080")
    f = root / "overlay-normal.bmp"
    if f.is_file():
        _panel_shape(Image.open(f).convert("RGB"), _panel_rect(1920, 1080, "1.5", "bottom-left"),
                     "position = bottom-left, scale = 1.5")
    else:
        check(False, "the bottom-left frame was read back")
    root = game(work / "panel-bad", ini="[overlay]\nshow_at_start = 1\nkey = F13\nposition = middle\nscale = huge\n")
    code, r, _ = run(root, "overlay", "1920x1080")
    f = root / "overlay-normal.bmp"
    log = log_of(root)
    ok = f.is_file() and "key = F13 is not one of F1..F12; F10 it is" in log and "position = middle is not" in log \
        and "scale = huge is neither auto nor a number; auto it is" in log
    if ok:
        img = Image.open(f).convert("RGB")
        x0, y0, x1, y1 = _panel_rect(1920, 1080)
        ok = img.crop((0, 0, x0, 1080)).getcolors(1) == [(x0 * 1080, PANEL_CLEAR)] and \
            img.getpixel(((x0 + x1) // 2, y0 + 3)) != PANEL_CLEAR
    check(ok, "settings it cannot read (key = F13, position = middle, scale = huge) are named in loader.log, and "
          "F10, top-right and auto are used")

    print("hidden until its key, switched off, Direct3D left alone, and an exception while drawing")
    cases = [
        ("hidden", "[overlay]\nkey = F7\n", "overlay  F7 shows the diagnostics panel (top-right, scale auto)\n",
         "show_at_start = 0: nothing is drawn until the key (F7 here) is pressed with the game in front"),
        ("off", "[overlay]\nenabled = 0\nshow_at_start = 1\n", "overlay  the diagnostics panel is off ([overlay] enabled = 0)",
         "[overlay] enabled = 0: nothing is drawn"),
        ("no-d3d", "[overlay]\nshow_at_start = 1\n[live]\nframe_stats = 0\n",
         "overlay  the diagnostics panel is off: [live] frame_stats = 0 leaves Direct3D alone",
         "[live] frame_stats = 0: Direct3D is left alone, so no panel either"),
        ("fault", "[overlay]\nshow_at_start = 1\ntest_fault = 1\n",
         "overlay  the diagnostics panel stopped: exception 0xc0000005",
         "an exception while drawing: the game's state is put back, the panel is off for the session, one log line, "
         "no crash report"),
    ]
    for name, ini, want, label in cases:
        root = game(work / f"panel-{name}", ini=ini)
        code, r, _ = run(root, "overlay", "1920x1080")
        f = root / "overlay-normal.bmp"
        kept = re.match(r"(\d+)/(\d+)", r.get("states-kept", ""))
        log = log_of(root)
        ok = code == 0 and f.is_file() and _all_clear(Image.open(f).convert("RGB")) and bool(kept) \
            and kept.group(1) == kept.group(2) and want in log
        if name == "fault":
            ok = ok and not reports(root, "crash") and log.count("overlay  the diagnostics panel stopped") == 1
        check(ok, label)


# ---- Direct3D 9: the pools, the chain, DXVK (graphics.cpp) ------------------------------------------------------

def _texture_bytes(w: int, h: int, levels: int, block: int = 0, bits: int = 32, depth: int = 1, faces: int = 1) -> int:
    """All levels of a texture, worked out here on its own (block: bytes per 4x4 block of a compressed format)."""
    total = 0
    for level in range(levels):
        lw, lh, ld = max(1, w >> level), max(1, h >> level), max(1, depth >> level)
        one = ((lw + 3) // 4) * ((lh + 3) // 4) * block if block else lw * lh * bits // 8
        total += one * ld
    return total * faces


# What harness.cpp's d3d9 mode holds, by pool.
POOLS_MANAGED = {"plain": _texture_bytes(256, 256, 1), "dxt1": _texture_bytes(512, 512, 10, block=8),
                 "dxt5": _texture_bytes(128, 64, 3, block=16), "cube": _texture_bytes(64, 64, 1, faces=6),
                 "volume": _texture_bytes(32, 32, 1, depth=8), "vb": 65536, "ib": 32768}
POOLS_DEFAULT = {"target": _texture_bytes(128, 128, 1), "dynamic_vb": 16384}
POOLS_SYSTEM = {"sys": _texture_bytes(64, 64, 1), "scratch": _texture_bytes(32, 32, 1)}
DXT5_2048 = _texture_bytes(2048, 2048, 12, block=16)          # d3d9mem's texture: 5,592,432 bytes


def _lines(seen: list[str]) -> dict[str, str]:
    out = {}
    for line in seen:
        key, _, value = line.partition(" ")
        out[key] = value
    return out


def _popen(root: Path, *args: str) -> subprocess.Popen:
    return subprocess.Popen([str(root / "DDDA.exe"), *args], cwd=root, stdout=subprocess.PIPE, text=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _finish(p: subprocess.Popen, root: Path) -> None:
    (root / "done").write_text("")
    try:
        p.wait(timeout=60)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait(timeout=30)


def _live_soon(pid: int, want=lambda live: True, timeout: float = 4.0) -> dict | None:
    """The live page once it says what `want` asks (the loader publishes four times a second)."""
    end = time.time() + timeout
    live = None
    while time.time() < end:
        live = runtime.read_live(pid)
        if live and want(live):
            return live
        time.sleep(0.25)
    return live


def test_d3d9(work: Path) -> None:
    print("Direct3D 9: the textures and buffers the game holds, by pool (graphics.cpp)")
    root = game(work / "pools")
    p = _popen(root, "d3d9")
    try:
        first = _lines(_wait_line(p, "ready1"))
        if not first.get("made", "").startswith("11"):
            print(f"  skip  Direct3D 9 could not make the resources here ({first.get('made') or first.get('d3d')})")
            return
        managed, default, system = sum(POOLS_MANAGED.values()), sum(POOLS_DEFAULT.values()), sum(POOLS_SYSTEM.values())
        check(first.get("levels") == "dxt1 10", "the full-chain DXT1 texture has 10 levels, as the sums here assume")
        live = _live_soon(p.pid, lambda lv: lv["d3d_counted"] and lv["d3d_objects"] >= 11)
        check(bool(live) and live["d3d_counted"], "the live page says the textures and buffers are counted")
        if live:
            check(live["d3d_managed"] == managed and live["d3d_managed_peak"] == managed,
                  f"managed: {live['d3d_managed']:,} bytes held, what a texture, a DXT1 chain, part of a DXT5 chain, a "
                  f"cube, a volume and two buffers take ({managed:,}, worked out here)")
            check(live["d3d_default"] == default, f"default pool: a render target and a dynamic buffer "
                  f"({live['d3d_default']:,} of {default:,})")
            check(live["d3d_system"] == system, f"system memory and scratch ({live['d3d_system']:,} of {system:,})")
            check(live["d3d_objects"] == 11, f"11 objects; an AddRef/Release pair and a surface change nothing "
                  f"({live['d3d_objects']})")
            path = live["d3d_path"].lower()
            check(live["d3d_provider"] == 1 and path.endswith("\\d3d9.dll") and ("system32" in path or "syswow64" in path),
                  f"Direct3D 9 is Windows' own, named with its path ({live['d3d_path']})")
        (root / "step").write_text("")
        _wait_line(p, "ready2")
        released = POOLS_MANAGED["dxt1"] + POOLS_MANAGED["cube"] + POOLS_MANAGED["vb"]
        live = _live_soon(p.pid, lambda lv: lv["d3d_objects"] <= 7)
        if live:
            check(live["d3d_managed"] == managed - released and live["d3d_managed_peak"] == managed,
                  f"after four releases: managed {live['d3d_managed']:,} ({managed - released:,}), the peak kept")
            check(live["d3d_default"] == POOLS_DEFAULT["dynamic_vb"] and live["d3d_objects"] == 7,
                  f"the render target is gone from the default pool; 7 objects left ({live['d3d_objects']})")
            check(any("managed textures and buffers" in line for line in runtime.describe_live(live)),
                  "describe_live says what Direct3D holds")
    finally:
        _finish(p, root)
    log = log_of(root)
    check("d3d9     Direct3D 9 is Windows' own (" in log and "d3d9     counting the game's textures and buffers by pool" in log,
          "loader.log names the runtime and says the pools are counted")
    check(any("  summary  " in line and "Direct3D 9 Windows' own, managed textures and buffers peak" in line
              for line in log.splitlines()), "the exit summary carries the managed peak")

    print("pool counting switched off")
    root = game(work / "pools-off", ini="[d3d9]\npool_stats = 0\n")
    p = _popen(root, "d3d9")
    try:
        first = _lines(_wait_line(p, "ready1"))
        if first.get("made", "").startswith("11"):
            live = _live_soon(p.pid, lambda lv: lv["frames"] >= 10)
            check(bool(live) and not live["d3d_counted"] and live["d3d_objects"] == 0 and live["d3d_provider"] == 1,
                  "[d3d9] pool_stats = 0: nothing counted, the runtime still named")
        (root / "step").write_text("")
        _wait_line(p, "ready2")
    finally:
        _finish(p, root)


def _chain_game(work: Path, name: str, ini: str = "", chain: str = "riftstone\\dxvk\\d3d9.dll", dll: Path | None = None,
                where: str = "riftstone/dxvk", conf: bool = False, fail: bool = False, machine: int | None = None,
                harness: str = "harness.exe") -> Path:
    root = game(work / name, ini=f"[d3d9]\nchain = {chain}\n" + ini, harness=harness)
    folder = root / where
    folder.mkdir(parents=True, exist_ok=True)
    data = bytearray((dll or OUT / "chain_d3d9.dll").read_bytes())
    if machine is not None:
        struct.pack_into("<H", data, pe.header(bytes(data)).offset + 4, machine)
    (folder / "d3d9.dll").write_bytes(bytes(data))
    if conf:
        (folder / "dxvk.conf").write_text("# a test's settings\n", encoding="utf-8")
    if fail:
        (folder / "fail").write_text("")
    return root


def _chain_note(root: Path, where: str = "riftstone/dxvk") -> dict[str, str]:
    f = root / where / "chain-called.txt"
    if not f.is_file():
        return {}
    out = {}
    for line in f.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        if line.startswith("called "):
            out["called"] = line
            continue
        key, sep, value = line.partition("=")
        if sep:
            out[key] = value
    return out


def _run_live(root: Path, want=lambda live: live["frames"] >= 60) -> tuple[dict, dict | None]:
    """`live` mode (60 frames through Direct3DCreate9), the live page once it has them, then exit."""
    p = _popen(root, "live")
    try:
        seen = _lines(_wait_line(p, "ready"))
        live = _live_soon(p.pid, want) if "presented" in seen else runtime.read_live(p.pid)
    finally:
        _finish(p, root)
    return seen, live


def test_chain(work: Path) -> None:
    print("[d3d9] chain: the game's Direct3D 9 from a DLL in the game folder (a stand-in for DXVK)")
    root = _chain_game(work, "chain", conf=True)
    seen, live = _run_live(root)
    if "presented" not in seen:
        print("  skip  Direct3D 9 could not draw on this machine; the chain is not checked")
        return
    note = _chain_note(root)
    log = log_of(root)
    dll = root / "riftstone" / "dxvk" / "d3d9.dll"
    check(note.get("called") == "called sdk=32", "the game's Direct3DCreate9 reached the chained DLL (SDK 32)")
    check(note.get("DXVK_LOG_PATH", "").lower() == str(root / "riftstone" / "logs").lower() and
          note.get("DXVK_CONFIG_FILE", "").lower() == str(dll.parent / "dxvk.conf").lower(),
          "DXVK's log goes to riftstone\\logs and its settings come from the dxvk.conf beside the DLL")
    check(f"d3d9     chained {dll}" in log and "d3d9     for DXVK: DXVK_LOG_PATH = " in log,
          "loader.log says what was chained and what it set up for DXVK")
    check(bool(live) and live["d3d_provider"] == 2 and live["d3d_path"].lower() == str(dll).lower(),
          f"the live page names the chained DLL ({live and live['d3d_path']})")
    check(bool(live) and live["frame_timing"] and live["frames"] >= 60,
          "frame timing works on the chained runtime's device (Present hooked there)")
    check(not (root / "d3d9.dll").exists(), "nothing was put into the game folder itself")

    cases = [
        ("chain-fail", dict(fail=True), "did not start (for DXVK: no Vulkan device it can use",
         "a chained DLL that cannot start (DXVK without Vulkan): the game gets Windows' own and draws", True),
        ("chain-x64", dict(machine=0x8664), "is a 64-bit DLL, and the game is 32-bit",
         "a 64-bit DLL is refused before it is loaded, with the reason", False),
        ("chain-outside", dict(chain="..\\outside\\d3d9.dll", where="../chain-outside-dll"), "is not inside the game folder",
         "a path outside the game folder is refused", False),
        ("chain-absolute", dict(chain="C:\\Windows\\SysWOW64\\d3d9.dll"), "is not inside the game folder",
         "an absolute path is refused", False),
        ("chain-missing", dict(chain="riftstone\\dxvk\\none.dll"), "riftstone\\dxvk\\none.dll does not exist",
         "a DLL that is not there is named as missing", False),
        ("chain-notdll", dict(chain="riftstone\\dxvk\\d3d9.txt"), "is not a .dll", "a file that is not a .dll is refused",
         False),
    ]
    for name, kw, want, label, called in cases:
        root = _chain_game(work, name, **kw)
        seen, live = _run_live(root)
        note = _chain_note(root, kw.get("where", "riftstone/dxvk"))
        log = log_of(root)
        ok = want in log and "presented" in seen and bool(live) and live["d3d_provider"] == 1 and live["frames"] >= 60
        check(ok and bool(note) == called, f"{label} ({'called, ' if note else ''}provider "
              f"{live and live['d3d_provider']})")

    print("[d3d9] chain next to a d3d9.dll in the game folder, in safe mode, and with frame timing off")
    root = _chain_game(work, "chain-folder")
    shutil.copy(OUT / "chain_d3d9.dll", root / "d3d9.dll")      # what DXVK put there the classic way would be
    seen, live = _run_live(root)
    log = log_of(root)
    check("which stays in charge; [d3d9] chain = riftstone\\dxvk\\d3d9.dll is not loaded" in log and bool(live)
          and live["d3d_provider"] == 3 and bool(_chain_note(root, ".")) and not _chain_note(root),
          "the game folder's own d3d9.dll stays in charge; the chain is not loaded (provider: the game folder)")
    root = _chain_game(work, "chain-safe")
    run(root, "crash")
    run(root, "crash")
    seen, live = _run_live(root)
    log = log_of(root)
    check("SAFE MODE" in log and "d3d9     safe mode: [d3d9] chain = riftstone\\dxvk\\d3d9.dll is off" in log
          and not _chain_note(root) and bool(live) and live["d3d_provider"] == 1,
          "safe mode: the chain is off, the game gets Windows' own Direct3D 9")
    root = _chain_game(work, "chain-nostats", ini="[live]\nframe_stats = 0\n")
    seen, live = _run_live(root, want=lambda lv: lv["d3d_provider"] != 0)
    log = log_of(root)
    check(bool(_chain_note(root)) and bool(live) and live["d3d_provider"] == 2 and not live["frame_timing"]
          and "hook     d3d9.dll!Direct3DCreate9 installed" in log,
          "[live] frame_stats = 0 leaves the rest of Direct3D alone, but the chain still applies")


def _dxvk() -> Path | None:
    """DXVK's 32-bit d3d9.dll: $RIFTSTONE_DXVK (the DLL or a release folder), else vendor/dxvk-*/x32/d3d9.dll."""
    env = os.environ.get("RIFTSTONE_DXVK")
    cands = []
    if env:
        e = Path(env)
        cands += [e, e / "x32" / "d3d9.dll"]
    cands += sorted((OUT.parents[2] / "vendor").glob("dxvk-*/x32/d3d9.dll"), reverse=True)
    return next((c for c in cands if c.is_file()), None)


def _mem_run(root: Path, count: int) -> tuple[dict, dict | None]:
    p = _popen(root, "d3d9mem", str(count))
    try:
        seen = _lines(_wait_line(p, "ready", timeout=240))
        want = int(seen["made"].split()[1]) if "made" in seen else -1
        live = _live_soon(p.pid, lambda lv: lv["d3d_managed"] >= want) if "made" in seen else None
    finally:
        _finish(p, root)
    return seen, live


def test_dxvk(work: Path) -> None:
    print("DXVK through [d3d9] chain: what managed textures cost the address space, Windows' Direct3D 9 against DXVK")
    dll = _dxvk()
    if not dll:
        print("  skip  no DXVK here (set RIFTSTONE_DXVK to a release's x32\\d3d9.dll, or put the release in vendor\\)")
        return
    count = 128
    want = count * DXT5_2048
    growth = {}
    for name, chained in (("windows", False), ("dxvk", True)):
        root = _chain_game(work, f"mem-{name}", dll=dll) if chained else game(work / f"mem-{name}")
        seen, live = _mem_run(root, count)
        if "mem-before" not in seen or "mem-after" not in seen:
            print(f"  skip  {name}: Direct3D 9 could not draw here ({seen.get('d3d') or seen})")
            return
        made, total = (int(x) for x in seen["made"].split())
        before, after = (int(x) for x in seen["mem-before"].split()), (int(x) for x in seen["mem-after"].split())
        (va0, commit0), (va1, commit1) = before, after
        growth[name] = (va1 - va0, commit1 - commit0)
        module = seen.get("d3d-module", "").lower()
        check(made == count and total == want, f"{name}: {count} managed DXT5 2048x2048 textures, {total >> 20} MB "
              f"with all levels, made and filled through LockRect")
        check(bool(live) and live["d3d_managed"] == want,
              f"{name}: the loader counts exactly those bytes as managed ({live and live['d3d_managed']:,})")
        if chained:
            check(module == str(root / "riftstone" / "dxvk" / "d3d9.dll").lower() and bool(live)
                  and live["d3d_provider"] == 2, f"dxvk: the Direct3D object came from riftstone\\dxvk\\d3d9.dll ({module})")
            logs = list((root / "riftstone" / "logs").glob("*_d3d9.log"))
            check(bool(logs), "dxvk: DXVK wrote its log into riftstone\\logs (DXVK_LOG_PATH), not the game folder "
                  f"({', '.join(f.name for f in logs)})")
        else:
            check("system32" in module or "syswow64" in module, f"windows: the Direct3D object is Windows' own ({module})")
        print(f"        {name}: address space +{growth[name][0]} MB, commit +{growth[name][1]} MB for {want >> 20} MB of "
              "managed textures")
    (win_va, win_commit), (dx_va, dx_commit) = growth["windows"], growth["dxvk"]
    check(win_va >= 0.9 * (want >> 20) and win_commit >= 0.9 * (want >> 20),
          f"Windows' Direct3D 9 keeps a copy of every managed texture in the process: +{win_va} MB of address space, "
          f"+{win_commit} MB of commit for {want >> 20} MB")
    check(dx_va <= 0.4 * (want >> 20) and dx_va <= win_va / 2,
          f"DXVK keeps that copy out of the address space: +{dx_va} MB of address space, +{dx_commit} MB of commit for "
          f"the same {want >> 20} MB")

    print("the in-game panel over DXVK")
    try:
        from PIL import Image
    except ImportError:
        print("  skip  Pillow is not installed; the panel is not checked")
        return
    root = _chain_game(work, "panel-dxvk", dll=dll, ini="[overlay]\nshow_at_start = 1\n")
    code, r, _ = run(root, "overlay", "1920x1080")
    f = root / "overlay-normal.bmp"
    kept = re.match(r"(\d+)/(\d+)", r.get("states-kept", ""))
    check(code == 0 and bool(kept) and kept.group(1) == kept.group(2) and int(kept.group(2)) >= 90,
          f"over DXVK the game's drawing state is as it bound it after every Present ({r.get('states-kept')})")
    if f.is_file():
        img = Image.open(f).convert("RGB")
        rect = _panel_rect(1920, 1080)
        _panel_shape(img, rect, "over DXVK")
        check(len(_meters(img, rect)) == 2, "over DXVK: both meters are drawn")
        img.save(OUT / "overlay-proof-dxvk.png")
    else:
        check(False, "the frame over DXVK was read back")


def test_memory(work: Path) -> None:
    print("memory: the exe's large-address flag, and the pressure watch")
    root = game(work / "laa-no", ini="[memory]\npressure_mb = 0\n")
    run(root, "files")
    log = log_of(root)
    check("memory   WARNING: DDDA.exe is not large-address aware" in log and "pressure watch off" in log,
          "a stand-in without the flag: loader.log warns (2048 MB of address space); pressure_mb = 0 turns the watch off")
    root = game(work / "pressure")
    exe = root / "DDDA.exe"
    staged = root / "DDDA.laa.exe"
    r = pe.write_large_address_aware_copy(exe, staged)
    os.replace(staged, exe)
    check(r["changed"] and pe.header(exe.read_bytes()).large_address_aware and pe.checksum(exe.read_bytes()) == r["checksum"],
          "the staged stand-in gets the flag (pe.write_large_address_aware_copy), with its checksum redone")
    p = _popen(root, "pressure", "3450")
    try:
        first = _lines(_wait_line(p, "ready", timeout=120))
        m = re.match(r"(\d+) MB, private (\d+) MB", first.get("committed", ""))
        if not m or int(m.group(2)) < 3400:
            print(f"  skip  this machine could not commit 3.4 GB in one process ({first.get('committed')})")
            return
        live = _live_soon(p.pid, lambda lv: lv["memory_pressure"])
        check(first.get("pressure-logged") == "yes", f"past 3,400 MB of commit ({m.group(2)} MB) loader.log says PRESSURE")
        check(bool(live) and live["memory_pressure"] and live["pressure_episodes"] == 1 and live["large_address_aware"]
              and live["va_total"] > (3 << 30), "the live page: pressure now, once; large-address aware, 4 GB")
        check(bool(live) and live["mem_verdict"] == 3, "and the session's verdict is memory-bound")
        (root / "step").write_text("")
        second = _lines(_wait_line(p, "ready2", timeout=60))
        live = _live_soon(p.pid, lambda lv: not lv["memory_pressure"])
        check(second.get("eased-logged") == "yes" and bool(live) and not live["memory_pressure"]
              and live["pressure_episodes"] == 1, "given back: the watch says it eased; still one episode")
    finally:
        _finish(p, root)
    log = log_of(root)
    check("memory   DDDA.exe is large-address aware: 409" in log, "loader.log: large-address aware, 4 GB")
    check("The engine keeps no unused resources, so there is nothing to flush." in log,
          "the PRESSURE line says why nothing is flushed")
    check(any("  summary  " in line and "memory pressure 1 time" in line and "(memory memory-bound)" in line
              for line in log.splitlines()), "the exit summary: memory-bound, pressure once")


def test_other_programs(work: Path) -> None:
    print("programs that are not the game (DDO's launcher imports dinput8 too)")
    root = game(work / "launcher", exe="__ddo_launcher.exe")
    code, r, _ = run(root, "files", exe="__ddo_launcher.exe")
    check(r.get("dinput") == "ok", "DirectInput still works in the launcher")
    check(r.get("relative-A") == "VANILLA-EM0100", "the launcher gets no overlay")
    check(not (root / "riftstone/logs/loader.log").exists(), "the launcher writes no log")

    print("Dragon's Dogma Online (DDO.exe)")
    root = game(work / "ddo", exe="DDO.exe")
    code, r, _ = run(root, "files", exe="DDO.exe")
    check(r.get("relative-A") == "OVERLAY-EM0100", "the overlay serves DDO.exe as well")
    check(r.get("missing-tex") == "size=52 magic=TEX word1=2000209d", "DDO's stand-in texture carries DDO's revision 0x9D")
    check("Dragon's Dogma Online" in log_of(root), "loader.log says it is Dragon's Dogma Online")


# Engine classes of DDDA build 2364871 (vftable -> name); the MtDTI and the name string are found in
# the exe below, independently of the C++ side.
ENGINE_CLASSES = {
    0x014310A4: "rTexture", 0x01441DB8: "rMaterial", 0x01438618: "rModel", 0x015623D4: "sSetManager",
    0x015C4E28: "uEm5200", 0x0142F81C: "sShadow", 0x014298BC: "sRender", 0x0142A624: "sResource",
    0x015E90D0: "uPlayer", 0x01562414: "sSetManager::cUnitData", 0x0142E2CC: "rArchive", 0x0142A848: "sMain",
    0x0159E2D0: "uCharacterBase", 0x01617560: "rLayout", 0x01593E30: "cLayoutSetEnemy",
    0x0155BC60: "sEnemyManager", 0x0155DB00: "sGameSys", 0x0143FF48: "rAIFSM",
}


def _engine_cases(exe: Path) -> list[str]:
    """'<vftable> <MtDTI> <name VA> <name>' lines, read from DDDA.exe's bytes."""
    import struct

    data = exe.read_bytes()
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    nsec = struct.unpack_from("<H", data, pe + 6)[0]
    base = struct.unpack_from("<I", data, pe + 0x34)[0]
    first = pe + 24 + struct.unpack_from("<H", data, pe + 20)[0]
    secs = []
    for i in range(nsec):
        o = first + i * 40
        vsz, va, rsz, raw = struct.unpack_from("<IIII", data, o + 8)
        secs.append((data[o:o + 8].rstrip(b"\0"), va, max(vsz, rsz), raw, rsz))

    def off(va):
        for _, v, size, raw, rsz in secs:
            if v <= va - base < v + size and va - base - v < rsz:
                return raw + va - base - v
        return None

    def va_of(o):
        for _, v, size, raw, rsz in secs:
            if raw <= o < raw + rsz:
                return base + v + o - raw
        return None

    rdata = next(s for s in secs if s[0] == b".rdata")
    lines = []
    for vt, name in ENGINE_CLASSES.items():
        fn = struct.unpack_from("<I", data, off(vt + 16))[0]
        code = data[off(fn):off(fn) + 6]
        if code[0] != 0xB8 or code[5] != 0xC3:
            continue
        dti = struct.unpack_from("<I", code, 1)[0]
        i = data.find(name.encode() + b"\0", rdata[3], rdata[3] + rdata[4])
        while i > 0 and data[i - 1] != 0:          # a whole string, not the tail of a longer one
            i = data.find(name.encode() + b"\0", i + 1, rdata[3] + rdata[4])
        if i > 0:
            lines.append(f"{vt:08x} {dti:08x} {va_of(i):08x} {name}")
    return lines


def test_engine(work: Path) -> None:
    print("engine parts on the real DDDA.exe code (mapped read-only; the game is not launched)")
    if not (OUT / "engine_stub.exe").is_file() or not (OUT / "engine_harness_core.dll").is_file():
        print("  skip  the engine harness is not built")
        return
    try:
        from riftstone.game import find_game
        exe = find_game().root / "DDDA.exe"
    except Exception as e:  # noqa: BLE001 -- no game on this machine
        print(f"  skip  no game here ({e})")
        return
    root = work / "engine"
    root.mkdir(parents=True)
    shutil.copy(OUT / "engine_stub.exe", root / "DDDA.exe")
    shutil.copy(OUT / "engine_harness_core.dll", root / "engine_harness_core.dll")
    cases = _engine_cases(exe)
    check(len(cases) == len(ENGINE_CLASSES), f"every sample class has a getDTI and a name string ({len(cases)})")
    (root / "classes.txt").write_text("\n".join(cases) + "\n")
    p = subprocess.run([str(root / "DDDA.exe"), str(exe), str(root / "classes.txt")], cwd=root, capture_output=True,
                       text=True, timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for line in p.stdout.splitlines():
        s = line.strip()
        if s.startswith(("pass ", "FAIL ")):
            check(s.startswith("pass"), s[4:].strip())
        elif s.startswith(("miss", "SKIP")):
            print("        " + s)
    if p.returncode == 2:
        print("  skip  the image could not be mapped in this process")
    else:
        check(p.returncode == 0, f"engine harness exits 0 (got {p.returncode})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work")
    ap.add_argument("--only", help="comma-separated test names (files,reset,crash,rotation,quarantine,safe,live,window,"
                                   "saves,exit,overlay,d3d9,chain,dxvk,memory,other,engine)")
    a = ap.parse_args()
    need = ["harness.exe", "harness_ddda.exe", "dinput8.dll", "riftstone_loader.dll", "marker_plugin.asi",
            "crash_plugin.asi", "chain_d3d9.dll"]
    if not all((OUT / n).is_file() for n in need):
        print("the loader is not built; run native\\loader\\build.cmd")
        return 1
    base = Path(a.work) if a.work else Path(tempfile.mkdtemp(prefix="rs-loader-"))
    base.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="run-", dir=base))
    tests = {"files": test_files, "reset": test_reset_plugins_off_addon, "crash": test_crash,
             "rotation": test_rotation, "quarantine": test_quarantine, "safe": test_safe_mode, "live": test_live,
             "window": test_window, "saves": test_saves, "exit": test_exit, "overlay": test_overlay,
             "d3d9": test_d3d9, "chain": test_chain, "dxvk": test_dxvk, "memory": test_memory,
             "other": test_other_programs, "engine": test_engine}
    wanted = a.only.split(",") if a.only else list(tests)
    check(runtime.loader_version(OUT / "dinput8.dll") == VERSION, f"the built loader carries its version tag ({VERSION})")
    for name in wanted:
        tests[name](work)
    print(f"\n{'ALL PASSED' if not FAILS else f'{len(FAILS)} FAILED'}  (work: {work})")
    if not FAILS and not a.work:
        shutil.rmtree(base, ignore_errors=True)   # our own throwaway games; kept when something failed
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
