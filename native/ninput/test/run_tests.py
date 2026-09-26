"""Run the Ninput offline harnesses (built by build_msvc.cmd). No game is launched.

    python native/ninput/test/run_tests.py

  1. the hook-arbiter unit test (pillar 6)
  2. the xinput1_3 proxy + SDK-handshake test (pillars 1 and 3): a stand-in exe loads the proxy,
     fetches XInputGetState by ordinal 2 as the game does, and the marker plugin gets initialised.
  3. the display arbiter following the Riftstone loader's [d3d9] chain (a stand-in d3d9.dll named in a
     riftstone_loader.ini): the chained Direct3DCreate9 goes through Ninput's hook.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
FAILS: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  pass  " if cond else "  FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def built(name: str) -> Path | None:
    hits = list((ROOT / "out-msvc").rglob(name))
    return max(hits, key=lambda p: p.stat().st_mtime) if hits else None


def registry_test() -> None:
    print("hook arbiter (pillar 6)")
    exe = built("ninput_registry_test.exe")
    if not exe:
        check(False, "ninput_registry_test.exe built")
        return
    p = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120, creationflags=NO_WINDOW)
    for line in p.stdout.splitlines():
        if line.strip().startswith(("pass", "FAIL")):
            print("  " + line.strip())
    check(p.returncode == 0, "all hook-arbiter checks passed")


def display_test() -> None:
    print("D3D9 reset arbiter (pillar 2)")
    exe = built("ninput_display_test.exe")
    if not exe:
        check(False, "ninput_display_test.exe built")
        return
    p = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120, creationflags=NO_WINDOW)
    for line in p.stdout.splitlines():
        s = line.strip()
        if s.startswith(("pass", "FAIL", "SKIP")):
            print("  " + s)
    check(p.returncode == 0, "all D3D9-arbiter checks passed")


def chain_test() -> None:
    print("D3D9 arbiter follows the Riftstone loader's [d3d9] chain (pillar 2 with DXVK)")
    exe = built("ninput_chain_test.exe")
    dll = built("chain_d3d9.dll")
    if not (exe and dll):
        check(bool(exe), "ninput_chain_test.exe built")
        check(bool(dll), "chain_d3d9.dll built")
        return
    work = Path(tempfile.mkdtemp(prefix="ninput-chain-"))
    try:
        (work / "riftstone" / "dxvk").mkdir(parents=True)
        shutil.copy(dll, work / "riftstone" / "dxvk" / "d3d9.dll")
        p = subprocess.run([str(exe), str(work)], capture_output=True, text=True, timeout=120, creationflags=NO_WINDOW)
        for line in p.stdout.splitlines():
            s = line.strip()
            if s.startswith(("pass", "FAIL")):
                print("  " + s)
        check(p.returncode == 0, "all chain checks passed")
        check((work / "riftstone" / "dxvk" / "chain-called.txt").is_file(), "the chained DLL itself was called")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def engine_test() -> None:
    print("engine capability registry (pillar 5)")
    exe = built("ninput_engine_test.exe")
    if not exe:
        check(False, "ninput_engine_test.exe built")
        return
    p = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120, creationflags=NO_WINDOW)
    for line in p.stdout.splitlines():
        s = line.strip()
        if s.startswith(("pass", "FAIL")):
            print("  " + s)
    check(p.returncode == 0, "all engine-registry checks passed")


def input_test() -> None:
    print("input layer: hotkeys + transforms (pillar 4)")
    exe = built("ninput_input_test.exe")
    if not exe:
        check(False, "ninput_input_test.exe built")
        return
    p = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120, creationflags=NO_WINDOW)
    for line in p.stdout.splitlines():
        s = line.strip()
        if s.startswith(("pass", "FAIL")):
            print("  " + s)
    check(p.returncode == 0, "all input-layer checks passed")


def provider_test() -> None:
    print("engine providers: enemy_cap + lod_tuner register and route (pillar 5 wiring)")
    exe = built("ninput_provider_test.exe")
    ec = built("enemy_cap.asi")
    lt = built("lod_tuner.asi")
    if not (exe and ec and lt):
        check(bool(exe), "ninput_provider_test.exe built")
        check(bool(ec), "enemy_cap.asi built")
        check(bool(lt), "lod_tuner.asi built")
        return
    p = subprocess.run([str(exe)], cwd=str(exe.parent), capture_output=True, text=True, timeout=120,
                       creationflags=NO_WINDOW)
    for line in p.stdout.splitlines():
        s = line.strip()
        if s.startswith(("pass", "FAIL")):
            print("  " + s)
    check(p.returncode == 0, "all engine-provider wiring checks passed")


def proxy_test() -> None:
    print("xinput1_3 proxy + SDK handshake (pillars 1, 3)")
    proxy = built("xinput1_3.dll")
    harness = built("ninput_proxy_harness.exe")
    plugin = built("marker_plugin.dll")
    if not (proxy and harness and plugin):
        check(bool(proxy), "xinput1_3.dll built")
        check(bool(harness), "ninput_proxy_harness.exe built")
        check(bool(plugin), "marker_plugin.dll built")
        return
    work = Path(tempfile.mkdtemp(prefix="ninput-proxy-"))
    try:
        shutil.copy(proxy, work / "xinput1_3.dll")
        shutil.copy(harness, work / "harness.exe")
        (work / "ninput" / "plugins").mkdir(parents=True)
        shutil.copy(plugin, work / "ninput" / "plugins" / "marker_plugin.dll")

        p = subprocess.run([str(work / "harness.exe")], cwd=work, capture_output=True, text=True,
                           timeout=120, creationflags=NO_WINDOW)
        out = {k: v for k, _, v in (ln.partition(" ") for ln in p.stdout.splitlines())}
        check(out.get("proxy-load") == "ok", "the game-side exe loaded the proxy named xinput1_3.dll")
        check(out.get("ordinal2") == "resolved", "XInputGetState is exported at ordinal 2 (as the game imports it)")
        check(out.get("getstate") in ("1167", "0"), f"the call forwarded to the real system XInput (got {out.get('getstate')})")
        check((work / "ninput" / "plugins" / "marker.ok").is_file(),
              "the marker plugin's Ninput_Initialize ran (SDK handshake)")
        log = (work / "ninput" / "ninput.log")
        text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
        check("Ninput 0.1.0 attached" in text, "ninput.log records the attach")
        check("marker_plugin: handshake ok" in text and "plugin   marker_plugin.dll initialised" in text,
              "ninput.log records the plugin initialising through the interface")
    finally:
        if not FAILS:
            shutil.rmtree(work, ignore_errors=True)
        else:
            print(f"  (kept work dir: {work})")


def main() -> int:
    if not (ROOT / "out-msvc").is_dir():
        print("out-msvc not found; run native/ninput/build_msvc.cmd first")
        return 2
    registry_test()
    display_test()
    chain_test()
    engine_test()
    input_test()
    provider_test()
    proxy_test()
    print(f"\n{'ALL PASSED' if not FAILS else f'{len(FAILS)} FAILED'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
