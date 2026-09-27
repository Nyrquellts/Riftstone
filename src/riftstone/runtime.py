"""The Riftstone runtime from outside the game: live stats, reports, safe mode, save backups.

The loader (``native/loader``, docs/runtime.md) runs inside DDDA.exe or DDO.exe.  It publishes live
stats in a page of shared memory named ``Local\\RiftstoneLive``, writes crash, fatal-error and
hang reports to ``<game>\\riftstone\\logs``, and records in ``riftstone\\runtime-state.ini`` what ended
each session (:func:`session_end`: Alt+F4, the close button, another program, a crash...).  This module
reads all of that for ``Riftstone.cmd live``, ``Riftstone.cmd crash``, ``doctor`` and Studio.

It only reads, except the functions that say they write: :func:`safe_mode_off` and
:func:`release_plugin` edit the loader's own ``runtime-state.ini``; :func:`backup_saves` copies saves
into ``%LOCALAPPDATA%\\Riftstone\\saves``; :func:`restore_save` puts a backup back (the game closed,
the current save copied aside first).  Nothing here opens a port or talks to the game.
"""
from __future__ import annotations

import configparser
import hashlib
import os
import re
import shutil
import struct
import sys
import time
from pathlib import Path

from .errors import FormatError, RiftError

LIVE_NAME = "Local\\RiftstoneLive"
LIVE_SIZE = 0x1000
LIVE_MAGIC = b"RSLIVE1\0"
GAMES = {0: "another program", 1: "Dragon's Dogma: Dark Arisen", 2: "Dragon's Dogma Online"}
REPORT_KINDS = ("crash", "fatal", "hang")
STATE_FILE = "runtime-state.ini"
DDDA_APP = "367500"

# LiveBlock (native/loader/live.cpp): offset, struct format, name
_FIELDS = [
    (0x008, "I", "version"), (0x00C, "I", "size"), (0x010, "I", "pid"), (0x014, "I", "game"),
    (0x018, "I", "exe_timestamp"), (0x01C, "I", "flags"), (0x020, "I", "seq"), (0x024, "I", "uptime_ms"),
    (0x028, "Q", "update_filetime"),
    (0x030, "Q", "va_total"), (0x038, "Q", "va_used"), (0x040, "Q", "va_largest_free"),
    (0x048, "Q", "va_used_peak"), (0x050, "Q", "va_largest_free_min"),
    (0x058, "Q", "private_bytes"), (0x060, "Q", "working_set"),
    (0x068, "I", "handles"), (0x06C, "I", "frames"),
    (0x070, "I", "frame_us_last"), (0x074, "I", "frame_us_avg"), (0x078, "I", "frame_us_p99"),
    (0x07C, "I", "frame_us_max"), (0x080, "I", "stutters"),
    (0x084, "I", "redirects"), (0x088, "I", "missing"), (0x08C, "I", "fallbacks"), (0x090, "I", "fatals"),
    (0x094, "I", "plugins_loaded"), (0x098, "I", "plugins_not_loaded"),
    (0x09C, "i", "enemies_active"), (0x0A0, "i", "enemies_usable"), (0x0A4, "i", "enemy_slots"),
    (0x0A8, "I", "backbuffer_w"), (0x0AC, "I", "backbuffer_h"), (0x0B0, "I", "windowed"),
    (0x0B4, "I", "refresh_hz"), (0x0B8, "I", "ring_pos"), (0x0BC, "I", "vram_avail_mb"),
    (0x9D8, "i", "stage"), (0x9DC, "I", "resources_used"), (0x9E0, "I", "resource_slots"),
    (0x9E4, "Q", "private_bytes_peak"), (0x9EC, "I", "mem_verdict"),
    (0x9F0, "Q", "d3d_managed"), (0x9F8, "Q", "d3d_managed_peak"), (0xA00, "Q", "d3d_default"),
    (0xA08, "Q", "d3d_system"), (0xA10, "I", "d3d_objects"), (0xA14, "I", "d3d_provider"),
    (0xA18, "I", "pressure"), (0xA1C, "I", "pressure_episodes"),
]
_STRINGS = [(0x4C0, 16, "loader_version"), (0x4D0, 260, "last_file"), (0x5D4, 260, "last_fallback"),
            (0x6D8, 512, "plugins"), (0x8D8, 256, "notes"), (0xA20, 160, "d3d_path")]
_FLAGS = {"known_build": 1, "safe_mode": 2, "frame_timing": 4, "address_space_low": 8, "hang": 16, "exited": 32,
          "large_address_aware": 64, "memory_pressure": 128, "d3d_counted": 256}
# Where the game's Direct3D 9 came from (live.cpp d3dProvider, graphics.cpp D3DProvider).
D3D_PROVIDERS = {1: "Windows' own", 2: "chained ([d3d9] chain)", 3: "a d3d9.dll in the game folder",
                 4: "another module"}
_FILETIME_EPOCH = 116444736000000000


def _cstr(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("utf-8", "replace")


def parse_live(blob: bytes) -> dict:
    """One LiveBlock page -> a dict of plain values (sizes in bytes, times in microseconds)."""
    if len(blob) < LIVE_SIZE:
        raise FormatError("live", "shorter than one live-stats page")
    if blob[:8] != LIVE_MAGIC:
        raise FormatError("live", "not a Riftstone live-stats page")
    out: dict = {name: struct.unpack_from("<" + fmt, blob, off)[0] for off, fmt, name in _FIELDS}
    if out["version"] != 1 or out["size"] != LIVE_SIZE:
        raise FormatError("live", f"live-stats version {out['version']} (size {out['size']}) is not the one this Riftstone reads")
    for off, size, name in _STRINGS:
        out[name] = _cstr(blob[off:off + size])
    ring = struct.unpack_from("<256I", blob, 0x0C0)
    pos = out["ring_pos"] & 255
    frames = min(out["frames"], 256)
    ordered = [ring[(pos + i) & 255] for i in range(256)]
    out["frame_times_us"] = ordered[256 - frames:] if frames else []
    out["game_name"] = GAMES.get(out["game"], f"game {out['game']}")
    for name, bit in _FLAGS.items():
        out[name] = bool(out["flags"] & bit)
    out["windowed"] = None if out["windowed"] == 0xFFFFFFFF else bool(out["windowed"])
    out["fps"] = round(1_000_000 / out["frame_us_avg"], 1) if out["frame_us_avg"] else None
    ft = out["update_filetime"]
    out["updated_epoch"] = (ft - _FILETIME_EPOCH) / 1e7 if ft > _FILETIME_EPOCH else None
    plugins = []
    for part in out["plugins"].split(";"):
        name, _, state = part.partition("=")
        if name:
            plugins.append({"name": name, "state": state or "?"})
    out["plugin_list"] = plugins
    for k in ("enemies_active", "enemies_usable", "enemy_slots", "stage"):
        if out[k] < 0:
            out[k] = None
    if not out["resource_slots"]:
        out["resources_used"] = out["resource_slots"] = None
    out["d3d_provider_name"] = D3D_PROVIDERS.get(out["d3d_provider"])
    return out


def _open_mapping(name: str) -> bytes | None:
    """The bytes of one named page of shared memory, or None when nobody publishes it."""
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenFileMappingW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
    k32.OpenFileMappingW.restype = wintypes.HANDLE
    k32.MapViewOfFile.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t)
    k32.MapViewOfFile.restype = ctypes.c_void_p
    k32.UnmapViewOfFile.argtypes = (ctypes.c_void_p,)
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    file_map_read = 0x0004
    h = k32.OpenFileMappingW(file_map_read, False, name)
    if not h:
        return None
    try:
        view = k32.MapViewOfFile(h, file_map_read, 0, 0, LIVE_SIZE)
        if not view:
            return None
        try:
            # A seqlock: the writer makes seq odd while it writes; take a copy between two equal, even reads.
            for _ in range(50):
                seq1 = ctypes.c_uint32.from_address(view + 0x20).value
                if seq1 & 1:
                    time.sleep(0.002)
                    continue
                data = ctypes.string_at(view, LIVE_SIZE)
                if ctypes.c_uint32.from_address(view + 0x20).value == seq1:
                    return data
            return ctypes.string_at(view, LIVE_SIZE)
        finally:
            k32.UnmapViewOfFile(view)
    finally:
        k32.CloseHandle(h)


def read_live(pid: int | None = None) -> dict | None:
    """What the running game publishes right now, or None when no game with the runtime is running."""
    if sys.platform != "win32":
        return None
    names = [f"{LIVE_NAME}-{pid}", LIVE_NAME] if pid else [LIVE_NAME]
    for name in names:
        data = _open_mapping(name)
        if data is None:
            continue
        try:
            live = parse_live(data)
        except FormatError:
            continue
        if pid and live["pid"] != pid:
            continue
        return live
    return None


# Memory verdict: does this session's peak commit leave room for any memory work, or is it at the ~4 GB
# ceiling a 32-bit process cannot exceed?  The thresholds match native/loader/live.cpp MemVerdict();
# keep the two in step.  peak_private_mb is the peak commit (PeakPagefileUsage, the 4 GB budget);
# free_min_mb is the smallest free address-space block seen and va_left_min_mb the least address space left
# (0 = not measured).  Address space counts on its own because under DXVK Direct3D's copies of the textures
# are mapped views, not commit.
MEM_LEVELS = {0: "unknown", 1: "headroom", 2: "tight", 3: "bound"}


def memory_verdict(peak_private_mb: int, free_min_mb: int = 0, va_left_min_mb: int = 0,
                   offloaded: bool = False) -> tuple[int, str, str]:
    """(level, word, advice) for a session's peak commit.  Level 0 unknown, 1 headroom, 2 tight, 3 bound.

    This is the answer docs/native-dynamic-recipes.md used to ask the owner to work out by hand in Task
    Manager: whether a memory-heavy setup is bumping the 32-bit ceiling, and so whether raising internal
    pools could help at all (only with headroom) or only fewer in-process texture bytes can (at the
    ceiling).  ``offloaded``: the game's Direct3D 9 already keeps its texture copies out of the address space
    (DXVK), so that is not suggested again.  A 32-bit process cannot address beyond ~4 GB; nothing here
    changes that."""
    if not peak_private_mb:
        return 0, "unknown", "Memory: not measured yet — play a memory-heavy setup for a while, then check again."
    va_short = bool(va_left_min_mb) and va_left_min_mb < 400
    if peak_private_mb >= 3400 or (free_min_mb and free_min_mb <= 128) or va_short:
        return 3, "bound", (
            f"Memory-bound: peak commit {peak_private_mb:,} MB"
            + (f" (smallest free block {free_min_mb:,} MB)" if free_min_mb else "")
            + (f", {va_left_min_mb:,} MB of address space left at the least" if va_short else "")
            + " — at the ~4 GB a 32-bit process cannot exceed. Only fewer in-process texture bytes help: a "
              "lower TextureDetail, fewer or smaller HD texture mods"
            + ("" if offloaded else ", or DXVK through [d3d9] chain ('riftstone loader d3d9'), which keeps "
               "Direct3D's copy of the game's textures out of the address space")
            + ". Raising internal pools would make it worse.")
    if peak_private_mb >= 2800:
        return 2, "tight", (
            f"Memory tight: peak commit {peak_private_mb:,} MB — close to the ~4 GB ceiling. A heavier area could "
            "still crash; lowering TextureDetail or trimming texture mods buys room. Raising pools would not help.")
    return 1, "headroom", (
        f"Memory headroom: peak commit {peak_private_mb:,} MB — well under the ~4 GB a 32-bit process can address. "
        "Higher TextureDetail, more texture mods, or raised internal pools are safe here.")


def describe_live(live: dict) -> list[str]:
    """A few lines a person reads at a glance."""
    mb = 1 << 20
    lines = [f"{live['game_name']}, process {live['pid']}, up {live['uptime_ms'] // 60000} min "
             f"{(live['uptime_ms'] // 1000) % 60} s (loader {live['loader_version']})"]
    if live["exited"]:
        lines.append("the game has exited normally")
    if live["safe_mode"]:
        lines.append("SAFE MODE: running without plugins and mods (two start-up crashes in a row)")
    if live["va_total"]:
        lines.append(f"address space {live['va_used'] // mb} MB of {live['va_total'] // mb} MB used (peak "
                     f"{live['va_used_peak'] // mb} MB); largest free block {live['va_largest_free'] // mb} MB")
        if live["address_space_low"]:
            lines.append("  LOW: the game is close to its 4 GB limit; a crash is likely soon")
        if live["va_total"] < (3 << 30):
            lines.append(f"  only {live['va_total'] // mb} MB of address space: the exe is not large-address aware "
                         "('riftstone laa' checks it)")
        peak_mb = live.get("private_bytes_peak", 0) >> 20
        if peak_mb:
            left = (live["va_total"] - live["va_used_peak"]) >> 20 if live["va_used_peak"] else 0
            _, _, advice = memory_verdict(peak_mb, live.get("va_largest_free_min", 0) >> 20, max(left, 0),
                                          offloaded=live.get("d3d_provider") in (2, 3))
            lines.append("  " + advice)
        if live.get("memory_pressure"):
            lines.append(f"  MEMORY PRESSURE now (began {live['pressure_episodes']} time(s) this session): loader.log "
                         "says what holds the memory")
        elif live.get("pressure_episodes"):
            lines.append(f"  memory pressure {live['pressure_episodes']} time(s) this session, eased now")
    if live.get("d3d_provider_name") or live.get("d3d_counted"):
        text = f"Direct3D 9: {live.get('d3d_provider_name') or 'not created yet'}"
        if live.get("d3d_path"):
            text += f" ({live['d3d_path']})"
        if live.get("d3d_counted"):
            text += (f"; managed textures and buffers {live['d3d_managed'] // mb:,} MB (peak "
                     f"{live['d3d_managed_peak'] // mb:,} MB), video memory {live['d3d_default'] // mb:,} MB, system "
                     f"memory {live['d3d_system'] // mb:,} MB; {live['d3d_objects']:,} objects")
        lines.append(text)
        if live.get("d3d_counted") and live.get("d3d_provider") == 1 and live["d3d_managed"] >= 256 * mb:
            lines.append("  Windows keeps a copy of the managed ones inside the game's address space; DXVK through "
                         "[d3d9] chain keeps it out ('riftstone loader d3d9')")
    if live["frame_timing"] and live["frames"]:
        lines.append(f"frames {live['frames']}: {live['fps']} fps average, {live['frame_us_avg'] / 1000:.1f} ms "
                     f"(99% under {live['frame_us_p99'] / 1000:.1f} ms, worst {live['frame_us_max'] / 1000:.1f} ms), "
                     f"{live['stutters']} stutters")
    elif live["frame_timing"]:
        lines.append("frames: none drawn yet")
    if live["backbuffer_w"]:
        mode = {True: "windowed", False: "fullscreen", None: "?"}[live["windowed"]]
        lines.append(f"display {live['backbuffer_w']}x{live['backbuffer_h']} {mode}"
                     + (f", {live['refresh_hz']} Hz" if live["refresh_hz"] else "")
                     + (f"; Direct3D reports {live['vram_avail_mb']} MB of texture memory free" if live["vram_avail_mb"] else ""))
    if live["stage"] is not None:
        lines.append(f"stage {live['stage']}")
    if live["resource_slots"]:
        full = live["resources_used"] * 100 // live["resource_slots"]
        lines.append(f"resource table {live['resources_used']:,} of {live['resource_slots']:,} slots ({full}%)"
                     + ("; FULL buckets make the game load resources again" if full >= 90 else ""))
    if live["enemies_active"] is not None:
        lines.append(f"enemies active {live['enemies_active']} of {live['enemies_usable']} usable "
                     f"({live['enemy_slots']} slots)")
    lines.append(f"overlay redirects {live['redirects']}, missing files {live['missing']}, texture stand-ins "
                 f"{live['fallbacks']}, fatal errors {live['fatals']}")
    if live["plugin_list"]:
        lines.append("plugins: " + ", ".join(f"{p['name']} ({p['state']})" for p in live["plugin_list"]))
    if live["hang"]:
        lines.append("HANG: no frame for a while; a hang report is in riftstone\\logs")
    if live["notes"]:
        lines.append(f"note: {live['notes']}")
    if live["last_fallback"]:
        lines.append(f"last stand-in: {live['last_fallback']}")
    return lines


# ---------------------------------------------------------------------------------------------
# reports

# "<kind>-<YYYYMMDD-HHMMSS>.txt", or "-2", "-3" ... before .txt for more reports in the same second
_REPORT_NAME = re.compile(r"^(crash|fatal|hang)-(\d{8}-\d{6})(?:-(\d{1,2}))?\.txt$")


def list_reports(logs: Path) -> list[dict]:
    """Crash, fatal-error and hang reports in a logs folder, newest first."""
    out = []
    try:
        entries = list(Path(logs).iterdir())
    except OSError:
        return []
    for p in entries:
        m = _REPORT_NAME.match(p.name)
        if m and p.is_file():
            out.append({"name": p.name, "kind": m.group(1), "stamp": m.group(2), "path": p,
                        "dump": p.with_suffix(".dmp").is_file(), "_n": int(m.group(3) or 1)})
    out.sort(key=lambda r: (r["stamp"], r["_n"], r["kind"]), reverse=True)
    for r in out:
        del r["_n"]
    return out


# A number the loader writes has at most 9 digits; a longer one (damaged text) is not read: int() refuses
# more than 4,300 digits.
_NUM = r"(?<!\d)(\d{1,9})(?!\d)"


def _mb(text: str) -> int | None:
    """The first number of a memory line ("42 MB (free in all: 146 MB)" -> 42)."""
    m = re.match(r"\s*" + _NUM + r"\s*MB", text)
    return int(m.group(1)) if m else None


def parse_report(text: str) -> dict:
    """A report's text (any of the three kinds) -> its facts.  Tolerates damaged or foreign text."""
    lines = text.replace("\r\n", "\n").split("\n")
    first = lines[0] if lines else ""
    kind = "crash" if "crash report" in first else "fatal" if "fatal-error report" in first else \
        "hang" if "hang report" in first else "unknown"
    r: dict = {"kind": kind, "loader": None, "time": None, "game": None, "uptime_s": None, "startup": False,
               "safe_mode": False, "exception": None, "access": None, "fault_in": None, "fault_plugin": None,
               "objects": [], "stack": [], "memory": {}, "out_of_memory": False, "plugins": [], "last_files": [],
               "missing_count": 0, "missing": [], "message": None, "caption": None, "missing_file": None,
               "main_thread": None, "stage": None}
    m = re.search(r"\(Riftstone loader ([^)]+)\)", first)
    if m:
        r["loader"] = m.group(1)
    section = None
    for raw in lines[1:]:
        line = raw.rstrip()
        s = line.strip()
        if not s:
            continue
        head = line[:12].rstrip()
        if not line.startswith(" "):
            section = None
            if head == "time":
                r["time"] = line[12:].strip()
            elif head == "game":
                r["game"] = line[12:].strip()
            elif head == "uptime":
                m = re.match(r"(\d{1,12}(?:\.\d{1,6})?) s", line[12:].strip())
                if m:
                    r["uptime_s"] = float(m.group(1))
                r["startup"] = "(start-up)" in line
            elif head == "safe mode":
                r["safe_mode"] = line[12:].strip().startswith("ON")
            elif head == "stage":
                m = re.match(r"-?\d{1,9}(?!\d)", line[12:].strip())
                r["stage"] = int(m.group(0)) if m else None
            elif head == "exception":
                m = re.match(r"0x([0-9a-fA-F]+) (.+?) at (0x[0-9a-fA-F]+) \((.*)\)$", line[12:].strip())
                if m:
                    r["exception"] = {"code": int(m.group(1), 16), "name": m.group(2), "address": m.group(3),
                                      "where": m.group(4)}
            elif head == "fault in":
                v = line[12:].strip()
                r["fault_in"] = v
                m = re.match(r"plugin (.+?) \(riftstone\\plugins\)$", v)
                if m:
                    r["fault_plugin"] = m.group(1)
            elif s.startswith("objects in registers"):
                section = "objects"
            elif s.startswith("stack words"):
                section = "words"
            elif s == "stack":
                section = "stack"
            elif s == "memory":
                section = "memory"
            elif s == "plugins":
                section = "plugins"
            elif s.startswith("last files opened"):
                section = "files"
            elif s.startswith("files the game looked for"):
                m = re.search(r":\s*" + _NUM + r"\s*$", s)
                r["missing_count"] = int(m.group(1)) if m else 0
                section = "missing"
            elif s.startswith("the game stopped with this message"):
                section = "message"
            elif s.startswith("main thread at"):
                r["main_thread"] = s[len("main thread at"):].strip()
            elif s == "modules" or s.startswith("overlay redirects") or s.startswith("what it means"):
                section = "skip"
            elif head == "registers" or s.startswith("the game drew no frame"):
                section = "skip"
            continue
        if line.startswith("            ") and r["exception"] and not r["access"] and \
                (s.startswith("reading") or s.startswith("writing") or s.startswith("executing")):
            op, _, addr = s.partition(" address ")
            r["access"] = {"op": op, "address": addr}
            continue
        if section == "objects":
            m = re.match(r"(\w+)\s+(0x[0-9a-fA-F]+)\s+->\s+(\S+) object", s)
            if m:
                r["objects"].append({"register": m.group(1), "address": m.group(2), "class": m.group(3)})
        elif section == "stack":
            m = re.match(r"#(\d{1,9}) (0x[0-9a-fA-F]+)\s+(\S+)(?:\s+<- plugin (.+))?$", s)
            if m:
                r["stack"].append({"frame": int(m.group(1)), "address": m.group(2), "where": m.group(3),
                                   "plugin": m.group(4)})
        elif section == "words":
            m = re.match(r"\[esp\+0x[0-9a-fA-F]+\] (0x[0-9a-fA-F]+)\s+(\S+) object$", s)
            if m:
                r["objects"].append({"register": "stack", "address": m.group(1), "class": m.group(2)})
        elif section == "memory":
            key, _, rest = s.partition("  ")
            key = key.strip()
            if key == "address space used":
                nums = re.findall(_NUM + r" MB", rest)
                if len(nums) >= 2:
                    r["memory"]["used_mb"], r["memory"]["total_mb"] = int(nums[0]), int(nums[1])
            elif key == "largest free block":
                r["memory"]["largest_free_mb"] = _mb(rest)
            elif key == "private bytes":
                r["memory"]["private_mb"] = _mb(rest)
            elif key == "system RAM free":
                r["memory"]["ram_free_mb"] = _mb(rest)
            elif key == "large-address aware":
                r["memory"]["large_address_aware"] = rest.strip().startswith("yes")
            elif key == "Direct3D 9":
                r["memory"]["d3d9"] = rest.strip()
            elif key == "Direct3D pools":
                m = re.search(r"managed (\d+) MB", rest)
                r["memory"]["d3d_managed_mb"] = int(m.group(1)) if m else None
            elif key == "VERDICT":
                r["out_of_memory"] = True
        elif section == "plugins":
            m = re.match(r"(\S+)\s+(loaded|failed to load|quarantined|skipped \(safe mode\))", s)
            if m:
                r["plugins"].append({"name": m.group(1), "state": m.group(2)})
        elif section == "files":
            r["last_files"].append(s)
        elif section == "missing":
            r["missing"].append(s)
        elif section == "message":
            if s.startswith("caption:"):
                r["caption"] = s[len("caption:"):].strip()
            elif s.startswith("message:"):
                r["message"] = s[len("message:"):].strip()
            elif r["message"] is not None:
                r["message"] += "\n" + s
    if r["message"]:
        # The game writes "Failed open file. <path> <error>".
        m = re.search(r"open file[\s:.\"\[]*([^\r\n\"\]]+)", r["message"], re.I)
        if m:
            r["missing_file"] = re.sub(r"\s+\d+$", "", m.group(1).strip()) or None
    return r


_GRAPHICS_MODULES = ("d3d9.dll", "d3dx9_43.dll", "nvd3dum.dll", "nvldumd.dll", "atiumdag.dll", "atidxx32.dll",
                     "aticfx32.dll", "amdxc32.dll", "igdumd32.dll", "igdumdim32.dll", "igd9dxva32.dll", "dxgi.dll")


def _resource_key(path: str) -> str | None:
    """'...\\nativePC\\rom\\enemy\\em5200.arc' or '...\\riftstone\\overlay\\rom\\...' -> 'rom/enemy/em5200'."""
    p = path.replace("/", "\\")
    low = p.lower()
    for marker in ("\\riftstone\\overlay\\", "\\nativepc\\"):
        i = low.find(marker)
        if i != -1:
            rel = p[i + len(marker):].replace("\\", "/")
            return rel[:-4] if rel.lower().endswith(".arc") else rel
    return None


def _mods_by_archive(game_root: Path | None) -> dict[str, list[str]]:
    if not game_root:
        return {}
    import json

    try:
        state = json.loads((Path(game_root) / "riftstone" / "state.json").read_text(encoding="utf-8"))
        return {k.lower(): list(v.get("mods", [])) for k, v in state.get("archives", {}).items()}
    except (OSError, ValueError, AttributeError, TypeError, RecursionError):     # a damaged record: nothing to add
        return {}


def _installed_mod_roots(game_root: Path | None) -> list[tuple[str, Path]]:
    if not game_root:
        return []
    import json

    try:
        state = json.loads((Path(game_root) / "riftstone" / "state.json").read_text(encoding="utf-8"))
        return [(m.get("name") or Path(m["path"]).name, Path(m["path"])) for m in state.get("mods", []) if m.get("path")]
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
        return []


def mods_mentioning(resource: str, mods: list[tuple[str, Path]], limit_bytes: int = 8 << 20) -> list[str]:
    """Names of the mods whose files name ``resource`` (a path under nativePC, any slashes, with or
    without its extension): the mods that could have asked the game for it."""
    stem = resource.replace("/", "\\").strip("\\")
    if stem.lower().startswith("nativepc\\"):
        stem = stem[len("nativepc\\"):]
    if stem.lower().startswith("rom\\"):
        stem = stem[4:]
    stem = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", stem)
    if len(stem) < 4:
        return []
    needles = {stem.lower().encode("utf-8"), stem.lower().replace("\\", "/").encode("utf-8")}
    hits = []
    for name, root in mods:
        files = root / "files"
        if not files.is_dir():
            continue
        found = False
        for dirpath, _, names in os.walk(files):
            for n in names:
                f = Path(dirpath) / n
                rel = f.relative_to(files).as_posix().lower().encode("utf-8")
                if any(nd.replace(b"\\", b"/") in rel for nd in needles):
                    found = True
                    break
                try:
                    if f.stat().st_size > limit_bytes:
                        continue
                    data = f.read_bytes().lower()
                except OSError:
                    continue
                if any(nd in data for nd in needles):
                    found = True
                    break
            if found:
                break
        if found:
            hits.append(name)
    return hits


def explain(report: dict, game_root: Path | None = None) -> list[str]:
    """What a report means, in plain words, with the mods and plugins involved."""
    out: list[str] = []
    mods = _installed_mod_roots(game_root)
    by_arc = _mods_by_archive(game_root)
    kind = report.get("kind")
    when = f" {report['uptime_s']:.0f} s after the game started" if report.get("uptime_s") is not None else ""
    if report.get("stage") is not None:
        when += f", in stage {report['stage']}"
    if kind == "crash":
        exc = report.get("exception") or {}
        out.append(f"The game crashed{when} ({exc.get('name', 'an exception')} in {exc.get('where', '?')}).")
    elif kind == "fatal":
        out.append(f"The game stopped itself with a fatal error{when}: {report.get('message') or '?'}")
    elif kind == "hang":
        out.append(f"The game stopped drawing frames{when}"
                   + (f"; its main thread was at {report['main_thread']}." if report.get("main_thread") else "."))
    else:
        out.append("This does not look like a Riftstone report.")
        return out
    mem = report.get("memory") or {}
    if report.get("out_of_memory"):
        out.append(f"It had nearly run out of address space ({mem.get('used_mb', '?')} MB of {mem.get('total_mb', '?')} MB "
                   f"used, largest free block {mem.get('largest_free_mb', '?')} MB). A 32-bit game has 4 GB; HD textures "
                   "and more enemies use it up. Fewer or smaller texture mods, or a lower TextureDetail, give it room.")
        if mem.get("large_address_aware") is False:
            out.append("The exe is not large-address aware, so it had 2 GB instead of 4 GB ('riftstone laa' checks it and "
                       "writes a copy with the flag).")
        managed = mem.get("d3d_managed_mb") or 0
        if managed >= 256 and str(mem.get("d3d9", "")).startswith("Windows' own"):
            out.append(f"Windows' Direct3D 9 held {managed:,} MB of managed textures and buffers, and keeps a copy of those "
                       "inside the game's address space. DXVK through [d3d9] chain keeps that copy out "
                       "('riftstone loader d3d9 add <DXVK release>', docs/runtime.md).")
    fault_in = str(report.get("fault_in") or "")
    chained_d3d9 = fault_in.lower().split(" ")[0].endswith("d3d9.dll") and "\\riftstone\\" in fault_in.lower()
    if report.get("fault_plugin"):
        name = report["fault_plugin"]
        out.append(f"The fault is inside the plugin {name}. If it keeps crashing the game while it starts, the loader "
                   f"skips it by itself after the second time. To take it out now: Riftstone.cmd loader plugin remove {name}")
    elif chained_d3d9:
        out.append(f"The fault is in the Direct3D 9 that [d3d9] chain names ({fault_in.split(' ')[0]}, DXVK or another "
                   "runtime), not Windows' own. 'riftstone loader d3d9 off' goes back to Windows' Direct3D 9; its log in "
                   "riftstone\\logs may say more.")
    elif report.get("fault_in"):
        base = report["fault_in"].split("\\")[-1].split(" ")[0].lower()
        if base in _GRAPHICS_MODULES or base.startswith(("nv", "ati", "amd", "igd")):
            out.append("The fault is in Direct3D or the graphics driver: often video or address space running out, "
                       "an overlay or recorder hooking the game, or a driver bug. Update the driver and try without overlays.")
    for f in report.get("stack", []):
        if f.get("plugin") and f["plugin"] != report.get("fault_plugin"):
            out.append(f"The plugin {f['plugin']} was on the stack when it happened.")
            break
    missing = report.get("missing_file")
    wanted = [missing] if missing else []
    wanted += [m for m in report.get("missing", []) if m not in wanted]
    if wanted:
        shown = wanted[:6]
        out.append("The game looked for files that do not exist: " + "; ".join(shown)
                   + (f" (and {len(wanted) - 6} more)" if len(wanted) > 6 else ""))
        if kind == "fatal" and missing:
            out.append("A material or model a mod changed or added names this file, but no archive the game had "
                       "loaded holds it, so the game tried a loose file. The mod must carry the file, or list it "
                       "before the material that uses it (Riftstone's build does both).")
        named = set()
        for w in wanted[:20]:
            for mod in mods_mentioning(w, mods):
                named.add(mod)
        if named:
            out.append("Installed mods that mention these files: " + ", ".join(sorted(named)))
        if kind == "fatal":
            out.append("[guard] missing_textures = 1 in riftstone_loader.ini lets the game use a neutral stand-in for a "
                       "missing texture instead of stopping.")
    arc_mods: list[str] = []
    for f in report.get("last_files", [])[-12:]:
        key = _resource_key(f)
        if key:
            for m in by_arc.get(key.lower(), []):
                if m not in arc_mods:
                    arc_mods.append(m)
    if arc_mods:
        out.append("The last files the game read include archives changed by: " + ", ".join(arc_mods))
    classes = []
    for o in report.get("objects", []):
        if o["class"] not in classes:
            classes.append(o["class"])
    if classes:
        out.append("Engine objects involved: " + ", ".join(classes[:8]))
    if report.get("startup") and kind in ("crash", "fatal"):
        out.append("It happened while the game was starting. Two start-up crashes in a row start the next run in "
                   "safe mode (no plugins, no mods) until the mods or plugins change.")
    return out


# ---------------------------------------------------------------------------------------------
# ini files, as the loader and the plugins read them

# They read their inis with GetPrivateProfileStringW, which reads a file without a UTF-16 mark in the ANSI
# code page, one with a UTF-8 mark included, and the loader writes runtime-state.ini with
# WritePrivateProfileStringW, in the code page (measured on Windows 11, code page 1252: a UTF-8 "ä" reads
# as "Ã¤"; a UTF-8 mark hides the first section).  Riftstone reads and writes every such ini in it too.
INI_ENCODING = "mbcs"
# A file that starts with a UTF-16 (little-endian) mark Windows reads as UTF-16, every character, and
# WritePrivateProfileStringW keeps it UTF-16 (measured the same way: "Spielstände 日本" reads back whole).
INI_UTF16 = b"\xff\xfe"


def ini_text(raw: bytes) -> str:
    """An ini's bytes as the loader and the plugins read them: UTF-16 after its mark, else the code page."""
    if raw.startswith(INI_UTF16):
        return raw[2:].decode("utf-16-le", "replace")
    return raw.decode(INI_ENCODING, "replace")


def ini_bytes(text: str, like: bytes = b"") -> bytes:
    """A whole ini written as Windows writes one: CRLF line ends, in the code page ('?' for a character it lacks:
    only a byte that did not decode can bring one), or as UTF-16 behind its mark when `like`, the file it
    replaces, is one."""
    text = text.replace("\n", "\r\n")
    if like.startswith(INI_UTF16):
        return INI_UTF16 + text.encode("utf-16-le", "replace")
    return text.encode(INI_ENCODING, "replace")


def ini_value(value: str, key: str, utf16: bool = False) -> bytes:
    """A value a person typed, as an ini holds it: in UTF-16 for a UTF-16 file, else in the code page (refused
    when the code page lacks one of its characters)."""
    try:
        return value.encode("utf-16-le" if utf16 else INI_ENCODING)
    except UnicodeEncodeError:
        if utf16:
            raise RiftError(f"{key}: {value!r} is not text that can be written") from None
        raise RiftError(f"{key} holds only characters of this PC's Windows code page, which the loader and its "
                        f"plugins read their settings in; {value!r} has others") from None


# ---------------------------------------------------------------------------------------------
# safe mode and quarantine (the loader's runtime-state.ini)

def _state_ini(game_root: Path) -> Path:
    return Path(game_root) / "riftstone" / STATE_FILE


def _read_ini(path: Path) -> configparser.ConfigParser:
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.optionxform = str
    try:
        cp.read_string(ini_text(path.read_bytes()))     # the loader writes it: the code page (ini_text)
    except (OSError, configparser.Error):
        pass
    return cp


def plugin_key(name: str) -> str:
    """A plugin's key in runtime-state.ini as the loader (0.4.1, stability.cpp PluginKey) writes it: a plain ini
    key as it is; any other name ('=' in it, a leading ; # [ ~ or blank, a trailing blank, anything outside
    printable ASCII) as '~' and the hex of its lower-case UTF-8."""
    plain = bool(name) and name[0] not in ";#[~ \t" and name[-1] not in " \t" \
        and all(0x20 <= ord(c) < 0x7F and c != "=" for c in name)
    return name if plain else "~" + name.lower().encode("utf-8", "surrogatepass").hex()


def plugin_name(key: str) -> str:
    """The plugin file a runtime-state.ini key names ('~' and hex back to its name; any other key as it is)."""
    if re.fullmatch(r"~(?:[0-9a-f]{2})+", key):
        try:
            return bytes.fromhex(key[1:]).decode("utf-8")
        except UnicodeDecodeError:
            pass
    return key


def runtime_state(game_root: Path) -> dict:
    cp = _read_ini(_state_ini(game_root))

    def sec(name):
        return dict(cp.items(name)) if cp.has_section(name) else {}

    session, safe = sec("session"), sec("safe_mode")
    return {"session": session, "last_session": sec("last_session"), "safe_mode": safe.get("on", "0").strip() == "1",
            # plugin names as they are, not the loader's ~hex keys for unusual ones
            "quarantine": sorted(plugin_name(k) for k in sec("quarantine")),
            "strikes": {plugin_name(k): v for k, v in sec("strikes").items()},
            "last_clean": session.get("clean", "1").strip() == "1", "loader": session.get("loader")}


def _write_ini(path: Path, cp: configparser.ConfigParser) -> None:
    from io import StringIO

    buf = StringIO()
    cp.write(buf, space_around_delimiters=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(ini_bytes(buf.getvalue()))
    os.replace(tmp, path)


def safe_mode_off(game_root: Path) -> bool:
    """End safe mode; the next start loads plugins and mods again.  Writes runtime-state.ini."""
    path = _state_ini(game_root)
    cp = _read_ini(path)
    was = cp.has_section("safe_mode") and cp.get("safe_mode", "on", fallback="0").strip() == "1"
    for sec in ("safe_mode", "session"):
        if not cp.has_section(sec):
            cp.add_section(sec)
    cp.set("safe_mode", "on", "0")
    cp.set("session", "early_crashes", "0")
    _write_ini(path, cp)
    return was


def release_plugin(game_root: Path, name: str) -> bool:
    """Take a plugin out of quarantine (by its file name; the loader's ~hex key works too).  Writes
    runtime-state.ini."""
    path = _state_ini(game_root)
    cp = _read_ini(path)
    found = False
    # Windows matches ini keys ignoring case; an older loader wrote every name as it is
    want = {name.lower(), plugin_name(name).lower()}
    for sec in ("quarantine", "strikes", "strikes_file"):
        for key in cp.options(sec) if cp.has_section(sec) else ():
            if key.lower() in want or plugin_name(key).lower() in want:
                cp.remove_option(sec, key)
                found = found or sec == "quarantine"
    if found:
        _write_ini(path, cp)
    return found


# ---------------------------------------------------------------------------------------------
# how a session ended (the loader's session.cpp: [session] end / end_detail / end_uptime_ms while it
# runs; at the next start, with the crash note, [last_session])

# What ended a session, by the loader's code, in words.  "crash", "fatal-error" and "not-clean" come from
# what the session left behind: the crash note, and no clean exit.
END_REASONS = {
    "alt-f4": "Alt+F4 was pressed (the keyboard shortcut that closes a window)",
    "close-button": "the window's close button was clicked (or Close in its title-bar menu)",
    "window-menu": "Close was chosen in the window's menu with the keyboard",
    "close-message": "another program (or a plugin) told the game's window to close",
    "session-end": "Windows was shutting down, restarting or signing out, or an installer closed programs",
    "exit-menu": "it was quit from its own menu (Exit Game on the title screen, or the quit prompt of its start-up "
                 "save check)",
    "exit-request": "the game's own exit request ran with no close message before it (its debug Exit command)",
    "window-destroyed": "a window of the game was destroyed, and the game quits whenever one is",
    "quit-message": "a quit message reached it from outside its own close path (another program, or a plugin)",
    "fatal-error": "it stopped itself with a fatal-error message",
    "self-exit": "it ended by itself; no message asked it to close (its own exit menu, or code calling exit)",
    "unknown": "it exited normally, but what closed it was not recorded",
    "crash": "it crashed",
    "not-clean": "it did not exit normally and left no report: it was ended from outside (Task Manager, Steam's Stop, "
                 "another program), the PC lost power, or it died without a crash report",
}
# A short name for each, for Studio's tile.
END_LABELS = {"alt-f4": "Alt+F4", "close-button": "Close button", "window-menu": "Window menu",
              "close-message": "Closed by a program", "session-end": "Windows ended it", "exit-menu": "Exit Game",
              "exit-request": "Exit request", "window-destroyed": "Window destroyed", "quit-message": "Quit message",
              "fatal-error": "Fatal error", "self-exit": "Ended itself", "unknown": "Exited", "crash": "Crashed",
              "not-clean": "Ended from outside"}
# The game closed normally: the reason is what asked it to.
NORMAL_ENDS = frozenset(END_REASONS) - {"fatal-error", "crash", "not-clean"}


def _crash_note(game_root: Path) -> dict | None:
    """The crash handler's note for the next start (logs\\last-crash.txt): kind, uptime_ms, module, report."""
    try:
        text = (Path(game_root) / "riftstone" / "logs" / "last-crash.txt").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    note = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            note[key.strip()] = value.strip()
    return note


def _seconds(ms) -> float | None:
    try:
        n = int(str(ms).strip())
    except (TypeError, ValueError):
        return None
    return n / 1000 if 0 <= n < 1 << 42 else None


def _file_name(path: str | None) -> str | None:
    name = (path or "").replace("/", "\\").rsplit("\\", 1)[-1].strip()
    return name or None


def _one_line(rec: dict, key: str) -> str | None:
    """An ini value as one line (the loader writes one; a damaged file can continue it on the next)."""
    return " ".join(str(rec.get(key) or "").split()) or None


def session_end(game_root: Path, running: bool = False) -> dict | None:
    """How the game's most recent finished session ended, or None before the loader's first one.

    ``running``: the game is running now, so its session has not ended; the answer is then the one before
    it ([last_session], which the loader wrote at this start).  Keys: reason (END_REASONS), text, detail
    (what the loader saw), closing (what was closing it when it crashed), started (YYYYMMDD-HHMMSS),
    uptime_s, clean (it finished its own exit), report (the crash or fatal-error report), loader."""
    st = runtime_state(game_root)
    if running:
        rec = st["last_session"]
        reason = _one_line(rec, "end")
        if not reason:
            return None
        closing = _one_line(rec, "closing")
        uptime = _seconds(rec.get("uptime_ms"))
        report = _file_name(_one_line(rec, "report"))
        clean = _one_line(rec, "clean") == "1"
        detail = _one_line(rec, "detail")
        started = _one_line(rec, "started")
        loader_version = _one_line(rec, "loader")
    else:
        rec = st["session"]
        started = _one_line(rec, "started")
        if not started:
            return None
        end = _one_line(rec, "end")
        clean = _one_line(rec, "clean") == "1"
        detail = _one_line(rec, "end_detail")
        uptime = _seconds(rec.get("end_uptime_ms")) if end else None
        loader_version = _one_line(rec, "loader")
        closing = report = None
        note = _crash_note(game_root)
        if note is not None:
            reason = "fatal-error" if note.get("kind") == "fatal" else "crash"
            closing = end if end and end != reason else None
            crash_uptime = _seconds(note.get("uptime_ms"))
            if crash_uptime is not None:
                uptime = crash_uptime
            report = _file_name(_one_line(note, "report"))
        elif end:
            reason = end
        else:
            reason = "unknown" if clean else "not-clean"
            uptime = _seconds(rec.get("alive_ms"))          # the live thread's last word, every 30 s
    return {"reason": reason, "text": END_REASONS.get(reason, f"it ended ({reason})"), "detail": detail,
            "closing": closing, "started": started, "uptime_s": uptime,
            "uptime_at_least": reason in ("unknown", "not-clean"), "clean": clean, "report": report,
            "loader": loader_version}


# What the loader (0.3.3 and later) notes when a close comes from outside the game: the window in front and
# its program, the game's window, the time since the last keyboard or mouse input, the window under the pointer.
_OUTSIDE = re.compile(r"at that moment: in front (?P<front>.+?) \[(?P<cls>[^\]]*)\]; the game's window "
                      r"(?P<game>in front|minimized|not in front); the last keyboard or mouse input "
                      r"(?P<idle>\d{1,9}(?:\.\d)?) s before; the pointer over (?P<under>.+?) \[(?P<ucls>[^\]]*)\]")
_FRONT_PROGRAMS = {
    "explorer.exe": "Windows Explorer was in front: most likely the taskbar's Close window (on the game's button or "
                    "its preview), or Alt+Tab's close",
    "taskmgr.exe": "Task Manager was in front: most likely its End task",
    "steam.exe": "Steam was in front: most likely its Stop button",
    "steamwebhelper.exe": "Steam was in front: most likely its Stop button",
}


def who_closed(detail: str | None) -> str | None:
    """What most likely sent a close from outside the game, in a sentence, from the loader's note on who was in
    front at that moment; None when the detail has no such note (an older loader, or another kind of end)."""
    m = _OUTSIDE.search(detail or "")
    if not m:
        return None
    front, idle = m["front"], float(m["idle"])
    if front == "the game":
        return (f"The game's own window was in front, and the last keyboard or mouse input was {idle:g} s before: "
                "no click on the taskbar or Task Manager closed it, but a program running in the background, a plugin, "
                "or an overlay drawn in the game.")
    known = _FRONT_PROGRAMS.get(front.lower())
    if known:
        return f"{known} (the game's window was {m['game']}; the last keyboard or mouse input {idle:g} s before)."
    return (f"{front} was in front (the game's window was {m['game']}; the last keyboard or mouse input {idle:g} s "
            "before): that program, or something it runs, most likely sent it.")


def describe_end(end: dict) -> str:
    """One sentence a person reads: when the session started, how long it ran, what ended it."""
    parts = []
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})", end.get("started") or "")
    if m:
        parts.append(f"started {m[1]}-{m[2]}-{m[3]} {m[4]}:{m[5]}")
    up = end.get("uptime_s")
    if up is not None:
        parts.append(f"ran {'at least ' if end.get('uptime_at_least') else ''}{int(up) // 60} min {int(up) % 60} s")
    s = "The last session" + (f" ({', '.join(parts)})" if parts else "") + f" ended: {end['text']}."
    reason = end["reason"]
    if reason == "crash" and end.get("closing") in END_REASONS:
        s += f" It was closing when it crashed ({END_LABELS.get(end['closing'], end['closing'])})."
    elif reason == "session-end" and not end.get("clean"):
        s += " Windows then ended the game without its own shutdown. It was not a crash."
    elif reason in NORMAL_ENDS and reason != "unknown" and not end.get("clean"):
        s += " It then did not finish closing by itself: it was ended from outside, or died without a report."
    elif reason in NORMAL_ENDS:
        s += " It was not a crash."
    if reason in ("close-message", "quit-message"):
        who = who_closed(end.get("detail"))
        if who:
            s += " " + who
    return s


# ---------------------------------------------------------------------------------------------
# save backups (DDDA keeps its save in Steam's userdata\<account>\367500\remote)

def save_backup_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Riftstone" / "saves" / "DDDA"


def steam_save_folders() -> list[Path]:
    """Every <Steam>\\userdata\\<account>\\367500\\remote that exists (reads the registry)."""
    if sys.platform != "win32":
        return []
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            steam = Path(str(winreg.QueryValueEx(k, "SteamPath")[0]).replace("/", "\\"))
    except OSError:
        return []
    out = []
    try:
        for account in sorted((steam / "userdata").iterdir()):
            remote = account / DDDA_APP / "remote"
            if remote.is_dir():
                out.append(remote)
    except OSError:
        pass
    return out


def _digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


_BACKUP_NAME = re.compile(r"\d{8}-\d{6}(-[\w-]+)?")          # the stamp a backup folder is named by


def _backups(account: Path) -> list[Path]:
    """An account's backup folders, oldest first: only the stamp-named folders, never a link or junction.  A
    folder of the owner's own there is not a backup: it was counted as the newest, and pruned."""
    try:
        return sorted((d for d in account.iterdir() if _BACKUP_NAME.fullmatch(d.name) and d.is_dir()
                       and not d.is_symlink() and not getattr(d, "is_junction", lambda: False)()),
                      key=lambda d: d.name)
    except OSError:
        return []


def list_save_backups(root: Path | None = None) -> list[dict]:
    """Backups under %LOCALAPPDATA%\\Riftstone\\saves\\DDDA, newest first."""
    root = root or save_backup_root()
    out = []
    try:
        accounts = [a for a in root.iterdir() if a.is_dir()]
    except OSError:
        return []
    for account in accounts:
        for b in _backups(account):
            files = [f for f in b.iterdir() if f.is_file()]
            out.append({"account": account.name, "stamp": b.name, "path": b, "files": len(files),
                        "size": sum(f.stat().st_size for f in files),
                        "has_save": (b / "DDDA.sav").is_file()})
    out.sort(key=lambda r: r["stamp"], reverse=True)
    return out


def backup_saves(remotes: list[Path] | None = None, root: Path | None = None, keep: int = 20,
                 stamp: str | None = None) -> list[Path]:
    """Copy each save folder aside unless its newest backup already holds the same save.  Writes only
    under ``root``.  Returns the backup folders made."""
    root = root or save_backup_root()
    made = []
    for remote in remotes if remotes is not None else steam_save_folders():
        sav = remote / "DDDA.sav"
        digest = _digest(sav)
        if not digest:
            continue
        # a save outside Steam's userdata is filed as "other", as the save_backup plugin files it
        account = remote.parent.parent.name if remote.parent.name == DDDA_APP else "other"
        target = root / account
        existing = _backups(target)
        if existing and _digest(existing[-1] / "DDDA.sav") == digest:
            continue
        dest = target / (stamp or time.strftime("%Y%m%d-%H%M%S"))
        n = 0
        while dest.exists():
            n += 1
            dest = target / f"{stamp or time.strftime('%Y%m%d-%H%M%S')}-{n}"
        dest.mkdir(parents=True)
        for f in remote.iterdir():
            if f.is_file() and f.stat().st_size <= 64 << 20:
                shutil.copy2(f, dest / f.name)
        made.append(dest)
        existing.append(dest)
        for old in existing[:-keep] if keep > 0 else []:
            shutil.rmtree(old, ignore_errors=True)
    return made


def restore_save(backup: Path, remote: Path, game_running: bool) -> Path:
    """Put a backup's files back into the save folder.  The current files are copied aside first (a
    backup named ...-before-restore), so the restore can be undone.  Refuses while the game runs."""
    if game_running:
        raise RiftError("Dragon's Dogma is running. Close it first, then restore.")
    backup, remote = Path(backup), Path(remote)
    if not (backup / "DDDA.sav").is_file():
        raise RiftError(f"{backup} has no DDDA.sav")
    if not remote.is_dir():
        raise RiftError(f"{remote} is not a save folder")
    aside = backup.parent / (time.strftime("%Y%m%d-%H%M%S") + "-before-restore")
    aside.mkdir(parents=True, exist_ok=False)
    for f in remote.iterdir():
        if f.is_file():
            shutil.copy2(f, aside / f.name)
    for f in backup.iterdir():
        if f.is_file():
            tmp = remote / (f.name + ".riftstone-tmp")
            shutil.copy2(f, tmp)
            os.replace(tmp, remote / f.name)
    return aside


# ---------------------------------------------------------------------------------------------
# the installed loader

_VERSION_TAG = re.compile(rb"RiftstoneLoaderVersion=([0-9A-Za-z.\-+]{1,32})\0")


def loader_version(dll: Path) -> str | None:
    """The version a built or installed loader DLL carries ("0.1.0" builds carry none -> "0.1")."""
    try:
        data = Path(dll).read_bytes()
    except OSError:
        return None
    m = _VERSION_TAG.search(data)
    if m:
        return m.group(1).decode("ascii")
    return "0.1" if "Riftstone loader".encode("utf-16-le") in data else None
