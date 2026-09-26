"""The runtime from outside the game: live stats, reports and their explanations, safe mode, save
backups, and the loader settings merge.  No game, no loader process: the live page is built here
with the same offsets native/loader/live.cpp writes (native/loader/test/run_tests.py checks the real
one end to end)."""
from __future__ import annotations

import json
import struct
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401 (sys.path)
from riftstone import loader, runtime
from riftstone.errors import FormatError, RiftError


def live_block(**over) -> bytes:
    """A live-stats page as the loader writes it."""
    v = {"version": 1, "size": 0x1000, "pid": 4242, "game": 1, "exe_timestamp": 0x5A314C31, "flags": 1 | 4,
         "seq": 8, "uptime_ms": 125_000, "update_filetime": 133_000_000_000_000_000, "va_total": 4 << 30,
         "va_used": 3 << 30, "va_largest_free": 512 << 20, "va_used_peak": (3 << 30) + (100 << 20),
         "va_largest_free_min": 300 << 20, "private_bytes": 2 << 30, "working_set": 1 << 30, "handles": 900,
         "frames": 300, "frame_us_last": 6060, "frame_us_avg": 6060, "frame_us_p99": 9000, "frame_us_max": 20000,
         "stutters": 2, "redirects": 7, "missing": 1, "fallbacks": 1, "fatals": 0, "plugins_loaded": 2,
         "plugins_not_loaded": 0, "enemies_active": 12, "enemies_usable": 30, "enemy_slots": 30,
         "backbuffer_w": 2560, "backbuffer_h": 1440, "windowed": 0, "refresh_hz": 165, "ring_pos": 44,
         "vram_avail_mb": 3500, "stage": 100, "resources_used": 15000, "resource_slots": 16384,
         "private_bytes_peak": (3 << 30) + (200 << 20), "mem_verdict": 2,
         "d3d_managed": 0, "d3d_managed_peak": 0, "d3d_default": 0, "d3d_system": 0, "d3d_objects": 0,
         "d3d_provider": 0, "pressure": 0, "pressure_episodes": 0}
    v.update({k: over[k] for k in over if k in v})
    page = bytearray(0x1000)
    page[0:8] = b"RSLIVE1\0"
    for off, fmt, name in runtime._FIELDS:
        struct.pack_into("<" + fmt, page, off, v[name])
    ring = over.get("ring", [6000 + i for i in range(256)])
    struct.pack_into("<256I", page, 0x0C0, *ring)
    strings = {"loader_version": "0.2.0", "last_file": "C:\\g\\nativePC\\rom\\stage\\stage100\\stage100.arc",
               "last_fallback": "", "plugins": "enemy_cap.asi=loaded;lod_tuner.asi=loaded", "notes": "", "d3d_path": ""}
    strings.update({k: over[k] for k in over if k in strings})
    for off, size, name in runtime._STRINGS:
        raw = strings[name].encode("utf-8")[:size - 1]
        page[off:off + len(raw)] = raw
    return bytes(page)


CRASH = """Riftstone crash report (Riftstone loader 0.2.0)
time        2026-09-25 21:14:02
program     C:\\Steam\\steamapps\\common\\DDDA\\DDDA.exe
game        Dragon's Dogma: Dark Arisen, PE timestamp 0x5a314c31 (the build Riftstone's engine facts are for)
uptime      41.250 s (start-up)
safe mode   off
stage       100

exception   0xc0000005 ACCESS_VIOLATION at 0x10001234 (enemy_cap.asi+0x1234)
            reading address 0x00000008
fault in    plugin enemy_cap.asi (riftstone\\plugins)

registers   EAX=00000000 EBX=00000001 ECX=0a1b2c3d EDX=00000000
            ESI=0b000000 EDI=00000000 EBP=0019f000 ESP=0019ef00
            EIP=10001234 EFLAGS=00010246

objects in registers (engine classes)
  ECX   0x0A1B2C3D  -> sSetManager object
  ESI   0x0B000000  -> uEm5200 object

stack
  #00 0x10001234  enemy_cap.asi+0x1234  <- plugin enemy_cap.asi
  #01 0x004a21b0  DDDA.exe+0xa21b0

stack words that point into modules or at engine objects (from ESP)
  [esp+0x004] 0x004A21B0  DDDA.exe+0xa21b0
  [esp+0x010] 0x0C000000  rLayout object

memory
  address space used   3950 MB of 4096 MB
  largest free block   42 MB (free in all: 146 MB)
  private bytes        3100 MB (peak 3300 MB)
  working set          2500 MB (peak 2600 MB)
  system RAM free      9000 MB of 32000 MB
  handles              1200
  VERDICT              the game had nearly run out of address space: a 32-bit game has 4 GB,
                       and HD textures and more enemies use it up.

plugins
  enemy_cap.asi                loaded at 0x10000000..0x10020000
  lod_tuner.asi                loaded at 0x10030000..0x10040000

last files opened (oldest first)
  C:\\Steam\\steamapps\\common\\DDDA\\nativePC\\rom\\stage\\stage100\\stage100.arc
  C:\\Steam\\steamapps\\common\\DDDA\\riftstone\\overlay\\rom\\enemy\\em5200.arc

files the game looked for under nativePC and did not find: 0

overlay redirects this session: 3; missing textures given a stand-in: 0

modules
  0x00400000  0x0160c000  C:\\Steam\\steamapps\\common\\DDDA\\DDDA.exe
"""

FATAL = """Riftstone fatal-error report (Riftstone loader 0.2.0)
time        2026-09-24 22:31:10
program     C:\\Steam\\steamapps\\common\\DDDA\\DDDA.exe
game        Dragon's Dogma: Dark Arisen, PE timestamp 0x5a314c31 (the build Riftstone's engine facts are for)
uptime      70.000 s (start-up)
safe mode   off

the game stopped with this message
  caption: Fatal error.
  message: Failed open file. nativePC\\model\\em\\e52\\e5200\\s01\\e5200_skin_BM.tex 2

what it means
  The game needed the file ...

memory
  address space used   2100 MB of 4096 MB
  largest free block   900 MB (free in all: 1996 MB)

plugins
  enemy_skins.asi              loaded at 0x10000000..0x10020000

last files opened (oldest first)
  C:\\Steam\\steamapps\\common\\DDDA\\riftstone\\overlay\\rom\\enemy\\em5200.arc

files the game looked for under nativePC and did not find: 1
  C:\\Steam\\steamapps\\common\\DDDA\\nativePC\\model\\em\\e52\\e5200\\s01\\e5200_skin_BM.tex
"""

HANG = """Riftstone hang report (Riftstone loader 0.2.0)
time        2026-09-25 10:00:00
game        Dragon's Dogma: Dark Arisen, PE timestamp 0x5a314c31
uptime      900.000 s
safe mode   off

the game drew no frame for 25 s while it was the window in front; this report does not
stop or change the game.

main thread at 0x00cf1234 (DDDA.exe+0x8f1234)
registers   EAX=00000000 EBX=00000001 ECX=0a1b2c3d EDX=00000000

objects in registers (engine classes)
  ECX   0x0A1B2C3D  -> sResource object
"""


class LiveTest(unittest.TestCase):
    def test_round_trip_of_the_page(self):
        live = runtime.parse_live(live_block())
        self.assertEqual(live["pid"], 4242)
        self.assertEqual(live["game_name"], "Dragon's Dogma: Dark Arisen")
        self.assertTrue(live["known_build"] and live["frame_timing"] and not live["safe_mode"])
        self.assertEqual(live["fps"], round(1_000_000 / 6060, 1))
        self.assertEqual(live["windowed"], False)
        self.assertEqual((live["enemies_active"], live["enemies_usable"]), (12, 30))
        self.assertEqual((live["stage"], live["resources_used"], live["resource_slots"]), (100, 15000, 16384))
        self.assertEqual([p["name"] for p in live["plugin_list"]], ["enemy_cap.asi", "lod_tuner.asi"])
        self.assertEqual(live["loader_version"], "0.2.0")
        # The ring comes back oldest first, starting at the next write position.
        self.assertEqual(live["frame_times_us"][0], 6000 + 44)
        self.assertEqual(len(live["frame_times_us"]), 256)
        self.assertEqual(live["private_bytes_peak"], (3 << 30) + (200 << 20))
        self.assertEqual(live["mem_verdict"], 2)

    def test_an_old_block_without_the_memory_verdict_reads_zero(self):
        # A 0.3.1 loader zeroes the reserved tail these fields live in: they read 0, and the verdict is
        # "unknown" rather than a crash.
        page = bytearray(live_block())
        struct.pack_into("<QI", page, 0x9E4, 0, 0)
        live = runtime.parse_live(bytes(page))
        self.assertEqual((live["private_bytes_peak"], live["mem_verdict"]), (0, 0))
        self.assertEqual(runtime.memory_verdict(live["private_bytes_peak"] >> 20)[1], "unknown")

    def test_direct3d_and_the_pressure_watch(self):
        mb = 1 << 20
        page = live_block(flags=1 | 4 | 64 | 128 | 256, d3d_managed=1200 * mb, d3d_managed_peak=1400 * mb,
                          d3d_default=300 * mb, d3d_system=2 * mb, d3d_objects=5123, d3d_provider=1, pressure=1,
                          pressure_episodes=2, d3d_path="C:\\WINDOWS\\SYSTEM32\\d3d9.dll")
        live = runtime.parse_live(page)
        self.assertTrue(live["large_address_aware"] and live["memory_pressure"] and live["d3d_counted"])
        self.assertEqual((live["d3d_managed"], live["d3d_objects"], live["pressure_episodes"]), (1200 * mb, 5123, 2))
        self.assertEqual(live["d3d_provider_name"], "Windows' own")
        text = "\n".join(runtime.describe_live(live))
        self.assertIn("Direct3D 9: Windows' own (C:\\WINDOWS\\SYSTEM32\\d3d9.dll); managed textures and buffers 1,200 MB "
                      "(peak 1,400 MB)", text)
        self.assertIn("DXVK through [d3d9] chain keeps it out", text)
        self.assertIn("MEMORY PRESSURE now (began 2 time(s)", text)
        # Under DXVK (chained) the copy is out already: no DXVK hint, in the verdict either.
        chained = runtime.parse_live(live_block(flags=1 | 4 | 256, d3d_managed=1200 * mb, d3d_provider=2,
                                                private_bytes_peak=3500 * mb, d3d_path="C:\\g\\riftstone\\dxvk\\d3d9.dll"))
        text = "\n".join(runtime.describe_live(chained))
        self.assertIn("Direct3D 9: chained ([d3d9] chain)", text)
        self.assertNotIn("DXVK", text)
        self.assertIn("Memory-bound", text)

    def test_a_block_before_0_4_reads_direct3d_as_unknown(self):
        # A 0.3.2 loader zeroes the tail: no Direct3D line, no pressure, flags it never set read false.
        live = runtime.parse_live(live_block())
        self.assertIsNone(live["d3d_provider_name"])
        self.assertFalse(live["d3d_counted"] or live["memory_pressure"])
        self.assertNotIn("Direct3D 9:", "\n".join(runtime.describe_live(live)))

    def test_two_gigabytes_says_the_flag_is_missing(self):
        live = runtime.parse_live(live_block(va_total=2 << 30, va_used=1 << 30, va_used_peak=1 << 30))
        self.assertIn("not large-address aware", "\n".join(runtime.describe_live(live)))

    def test_unknowns_stay_unknown(self):
        live = runtime.parse_live(live_block(enemies_active=-1, enemies_usable=-1, enemy_slots=-1, windowed=0xFFFFFFFF,
                                             frames=3, frame_us_avg=0, stage=-1, resources_used=0, resource_slots=0))
        self.assertIsNone(live["enemies_active"])
        self.assertIsNone(live["stage"])
        self.assertIsNone(live["resource_slots"])
        self.assertIsNone(live["windowed"])
        self.assertIsNone(live["fps"])
        self.assertEqual(len(live["frame_times_us"]), 3)

    def test_describe_says_what_matters(self):
        low = runtime.parse_live(live_block(flags=1 | 4 | 8 | 2))
        text = "\n".join(runtime.describe_live(low))
        self.assertIn("LOW", text)
        self.assertIn("SAFE MODE", text)
        self.assertIn("enemies active 12 of 30", text)
        self.assertIn("stage 100", text)
        self.assertIn("resource table 15,000 of 16,384 slots (91%); FULL", text)

    def test_refuses_other_pages(self):
        for bad in (b"", b"RSLIVE1\0" + bytes(10), bytes(0x1000), live_block(version=2), live_block(size=0x800)):
            with self.assertRaises(FormatError):
                runtime.parse_live(bad)

    def test_no_game_running_reads_nothing(self):
        self.assertIsNone(runtime.read_live(pid=1))


class MemoryVerdictTest(unittest.TestCase):
    def test_the_four_levels(self):
        self.assertEqual(runtime.memory_verdict(0)[:2], (0, "unknown"))
        self.assertEqual(runtime.memory_verdict(2100)[:2], (1, "headroom"))
        self.assertEqual(runtime.memory_verdict(3000)[:2], (2, "tight"))
        self.assertEqual(runtime.memory_verdict(3600)[:2], (3, "bound"))

    def test_boundaries_match_the_loader(self):
        # native/loader/live.cpp MemVerdict() uses the same numbers: >=3400 or free<=128 -> bound;
        # >=2800 -> tight; else headroom.
        self.assertEqual(runtime.memory_verdict(2799)[0], 1)
        self.assertEqual(runtime.memory_verdict(2800)[0], 2)
        self.assertEqual(runtime.memory_verdict(3399)[0], 2)
        self.assertEqual(runtime.memory_verdict(3400)[0], 3)

    def test_a_starved_free_block_is_bound_even_with_low_commit(self):
        # Fragmentation kills a 32-bit game below the commit ceiling: a tiny largest-free block is bound.
        self.assertEqual(runtime.memory_verdict(2000, free_min_mb=64)[0], 3)
        self.assertEqual(runtime.memory_verdict(2000, free_min_mb=300)[0], 1)

    def test_advice_names_the_permanent_ceiling_and_no_false_hope(self):
        _, _, bound = runtime.memory_verdict(3800, free_min_mb=40)
        self.assertIn("4 GB", bound)
        self.assertIn("cannot exceed", bound)
        self.assertIn("DXVK through [d3d9] chain", bound)
        self.assertNotIn("DXVK", runtime.memory_verdict(3800, offloaded=True)[2])   # it is on already
        _, _, headroom = runtime.memory_verdict(1800)
        self.assertIn("safe", headroom)

    def test_address_space_left_counts_on_its_own(self):
        # Under DXVK the texture copies are mapped views, not commit: a low commit with the address space all but
        # used up is still bound (the loader's MemVerdict: less than 400 MB left).
        self.assertEqual(runtime.memory_verdict(1500, 300, va_left_min_mb=350)[:2], (3, "bound"))
        self.assertIn("350 MB of address space left", runtime.memory_verdict(1500, 300, va_left_min_mb=350)[2])
        self.assertEqual(runtime.memory_verdict(1500, 300, va_left_min_mb=400)[0], 1)
        self.assertEqual(runtime.memory_verdict(1500, 300, va_left_min_mb=0)[0], 1)   # not measured

    def test_describe_live_shows_the_budget_line(self):
        text = "\n".join(runtime.describe_live(runtime.parse_live(live_block())))
        self.assertIn("peak commit", text)
        self.assertIn("Memory tight", text)


class ReportTest(unittest.TestCase):
    def test_crash(self):
        r = runtime.parse_report(CRASH)
        self.assertEqual(r["kind"], "crash")
        self.assertEqual(r["loader"], "0.2.0")
        self.assertEqual(r["exception"]["name"], "ACCESS_VIOLATION")
        self.assertEqual(r["access"], {"op": "reading", "address": "0x00000008"})
        self.assertEqual(r["fault_plugin"], "enemy_cap.asi")
        self.assertTrue(r["startup"] and r["out_of_memory"])
        self.assertEqual(r["stage"], 100)
        self.assertEqual(r["memory"]["largest_free_mb"], 42)
        self.assertEqual([o["class"] for o in r["objects"]], ["sSetManager", "uEm5200", "rLayout"])
        self.assertEqual(r["stack"][0]["plugin"], "enemy_cap.asi")
        self.assertEqual(len(r["last_files"]), 2)
        text = "\n".join(runtime.explain(r))
        self.assertIn("inside the plugin enemy_cap.asi", text)
        self.assertIn("run out of address space", text)
        self.assertIn("sSetManager", text)
        self.assertIn("safe mode", text)
        self.assertIn("in stage 100", text)

    def test_fatal_names_the_mods_that_mention_the_missing_file(self):
        r = runtime.parse_report(FATAL)
        self.assertEqual(r["kind"], "fatal")
        self.assertEqual(r["missing_file"], "nativePC\\model\\em\\e52\\e5200\\s01\\e5200_skin_BM.tex")
        self.assertEqual(r["missing_count"], 1)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "game"
            (root / "riftstone").mkdir(parents=True)
            good = Path(tmp) / "mods" / "Tidy"
            bad = Path(tmp) / "mods" / "DDO Chimeras"
            (good / "files" / "model").mkdir(parents=True)
            (good / "files" / "model" / "other.mrl.yaml").write_text("textures: [model/em/e01/e0100_BM]\n")
            (bad / "files" / "model" / "em" / "e52" / "e5200" / "s01").mkdir(parents=True)
            (bad / "files" / "model" / "em" / "e52" / "e5200" / "s01" / "e5200_a.mrl.yaml").write_text(
                "textures:\n  - model\\em\\e52\\e5200\\s01\\e5200_skin_BM\n")
            (root / "riftstone" / "state.json").write_text(json.dumps({
                "schema": "riftstone.state/1",
                "mods": [{"path": str(good), "name": "Tidy"}, {"path": str(bad), "name": "DDO Chimeras"}],
                "archives": {"rom/enemy/em5200": {"mods": ["DDO Chimeras"]}}}))
            text = "\n".join(runtime.explain(r, root))
        self.assertIn("Installed mods that mention these files: DDO Chimeras", text)
        self.assertNotIn("Tidy", text)
        self.assertIn("archives changed by: DDO Chimeras", text)
        self.assertIn("missing_textures = 1", text)

    def test_out_of_memory_under_windows_direct3d_names_dxvk(self):
        text = CRASH.replace("  handles              1200\n", "  handles              1200\n"
                             "  large-address aware  NO (2048 MB of address space)\n"
                             "  Direct3D 9           Windows' own, C:\\WINDOWS\\SYSTEM32\\d3d9.dll\n"
                             "  Direct3D pools       managed 1380 MB (peak 1400 MB), default 300 MB, system 2 MB; 5123 "
                             "textures and buffers\n")
        r = runtime.parse_report(text)
        self.assertEqual((r["memory"]["large_address_aware"], r["memory"]["d3d_managed_mb"]), (False, 1380))
        self.assertTrue(r["memory"]["d3d9"].startswith("Windows' own"))
        said = "\n".join(runtime.explain(r))
        self.assertIn("held 1,380 MB of managed textures", said)
        self.assertIn("DXVK through [d3d9] chain", said)
        self.assertIn("not large-address aware", said)

    def test_a_fault_in_the_chained_direct3d_says_how_to_go_back(self):
        text = CRASH.replace("fault in    plugin enemy_cap.asi (riftstone\\plugins)",
                             "fault in    C:\\Steam\\steamapps\\common\\DDDA\\riftstone\\dxvk\\d3d9.dll")
        said = "\n".join(runtime.explain(runtime.parse_report(text)))
        self.assertIn("the Direct3D 9 that [d3d9] chain names", said)
        self.assertIn("riftstone loader d3d9 off", said)

    def test_hang(self):
        r = runtime.parse_report(HANG)
        self.assertEqual(r["kind"], "hang")
        self.assertEqual(r["main_thread"], "0x00cf1234 (DDDA.exe+0x8f1234)")
        self.assertIn("sResource", "\n".join(runtime.explain(r)))

    def test_anything_else_is_harmless(self):
        # fuzz (report target): an uptime of dots crashed the float conversion
        self.assertIsNone(runtime.parse_report("Riftstone crash report\nuptime      70....000 s\n")["uptime_s"])
        for text in ("", "hello", "Riftstone crash report\n\x00\x01", "exception   garbage\nmemory\n  VERDICT"):
            r = runtime.parse_report(text)
            self.assertIsInstance(runtime.explain(r), list)

    def test_list_reports_newest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp)
            for n in ("crash-20260101-000001.txt", "fatal-20260102-000000.txt", "hang-20250101-000000.txt",
                      "crash-20260101-000001-2.txt", "crash-x.txt", "notes.txt"):
                (logs / n).write_text("x")
            (logs / "crash-20260101-000001.dmp").write_text("x")
            found = runtime.list_reports(logs)
        self.assertEqual([r["name"] for r in found],
                         ["fatal-20260102-000000.txt", "crash-20260101-000001-2.txt", "crash-20260101-000001.txt",
                          "hang-20250101-000000.txt"])
        self.assertTrue(found[2]["dump"])


# runtime-state.ini as the loader leaves it (session.cpp, stability.cpp)
STATE_CLOSED = """[session]
early_crashes=0
clean=1
alive_ms=2580000
started=20260925-210201
loader=0.2.1
end=alt-f4
end_detail=WM_SYSKEYDOWN F4 with Alt (from the keyboard), then SC_CLOSE (posted), then WM_CLOSE
end_uptime_ms=2581234
"""
STATE_RUNNING = """[session]
clean=0
alive_ms=30000
started=20260926-100000
loader=0.2.1
[last_session]
started=20260925-210201
end=close-message
detail=WM_CLOSE posted to the game's window (the game never posts it; another program or a plugin did)
uptime_ms=61000
clean=1
loader=0.2.1
"""
STATE_KILLED = "[session]\nclean=0\nalive_ms=90000\nstarted=20260925-210201\nloader=0.2.1\n"
STATE_CLOSING = STATE_CLOSED.replace("clean=1", "clean=0")


class SessionEndTest(unittest.TestCase):
    def _root(self, tmp: str, state: str | None, note: str | None = None) -> Path:
        root = Path(tmp)
        (root / "riftstone" / "logs").mkdir(parents=True)
        if state is not None:
            (root / "riftstone" / "runtime-state.ini").write_text(state)
        if note is not None:
            (root / "riftstone" / "logs" / "last-crash.txt").write_text(note)
        return root

    def test_alt_f4_is_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            end = runtime.session_end(self._root(tmp, STATE_CLOSED))
        self.assertEqual((end["reason"], end["clean"], end["started"]), ("alt-f4", True, "20260925-210201"))
        self.assertAlmostEqual(end["uptime_s"], 2581.234)
        self.assertIn("from the keyboard", end["detail"])
        line = runtime.describe_end(end)
        self.assertIn("started 2026-09-25 21:02, ran 43 min 1 s", line)
        self.assertIn("Alt+F4 was pressed", line)
        self.assertTrue(line.endswith("It was not a crash."))

    def test_while_the_game_runs_the_session_before_answers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp, STATE_RUNNING)
            end = runtime.session_end(root, running=True)
            self.assertEqual((end["reason"], end["clean"], end["uptime_s"]), ("close-message", True, 61.0))
            self.assertIn("another program", runtime.describe_end(end))
            # Not running: the session that started 2026-09-26 never exited and left no report.
            end = runtime.session_end(root, running=False)
            self.assertEqual(end["reason"], "not-clean")
            self.assertIn("ran at least 0 min 30 s", runtime.describe_end(end))

    def test_a_close_from_outside_says_who_was_in_front(self):
        """Loader 0.3.3+ notes who was in front when a close came from outside; the sentence says what most likely
        sent it.  An older loader's detail (0.3.1, as the owner's game recorded it on 2026-09-25) adds nothing."""
        sc = ("SC_CLOSE sent from another thread to the game's window with no Alt+F4 before it (another program, e.g. "
              "the taskbar's Close window, or a plugin), then WM_CLOSE")

        def ended(front, game="not in front", idle="1.2", under="explorer.exe [Shell_TrayWnd]", stage="; in stage 100"):
            detail = (f"{sc}; at that moment: in front {front}; the game's window {game}; the last keyboard or mouse "
                      f"input {idle} s before; the pointer over {under}{stage}")
            return runtime.describe_end({"reason": "close-message", "text": runtime.END_REASONS["close-message"],
                                         "detail": detail, "clean": True})

        self.assertIn("most likely the taskbar's Close window", ended("explorer.exe [Shell_TrayWnd]", "minimized"))
        self.assertIn("the game's window was minimized", ended("explorer.exe [Shell_TrayWnd]", "minimized"))
        self.assertIn("Task Manager was in front: most likely its End task", ended("Taskmgr.exe [TaskManagerWindow]"))
        self.assertIn("Steam was in front", ended("steamwebhelper.exe [SDL_app]"))
        line = ended("the game [DDDA]", "in front", "0.4", "the game [DDDA]", "")
        self.assertIn("The game's own window was in front, and the last keyboard or mouse input was 0.4 s before", line)
        self.assertIn("a program running in the background, a plugin, or an overlay", line)
        self.assertIn("process 4242 was in front", ended("process 4242 [Chrome_WidgetWin_1]"))
        self.assertIn("msedge.exe was in front (the game's window was not in front; the last keyboard or mouse input "
                      "12 s before)", ended("msedge.exe [Chrome_WidgetWin_1]", idle="12.0"))
        older = {"reason": "close-message", "text": runtime.END_REASONS["close-message"], "clean": True,
                 "detail": sc + "; in stage 100"}
        self.assertTrue(runtime.describe_end(older).endswith("It was not a crash."))
        self.assertIsNone(runtime.who_closed(None))
        self.assertIsNone(runtime.who_closed("at that moment: in front ; the game's window sideways"))

    def test_the_crash_note_wins_and_names_what_was_closing(self):
        note = "kind=crash\r\nuptime_ms=2590000\r\nmodule=C:\\g\\DDDA.exe\r\nreport=C:\\g\\riftstone\\logs\\crash-20260925-214511.txt\r\n"
        with tempfile.TemporaryDirectory() as tmp:
            end = runtime.session_end(self._root(tmp, STATE_CLOSING, note))
        self.assertEqual((end["reason"], end["closing"], end["report"]), ("crash", "alt-f4", "crash-20260925-214511.txt"))
        self.assertEqual(end["uptime_s"], 2590.0)
        self.assertIn("It was closing when it crashed (Alt+F4).", runtime.describe_end(end))
        with tempfile.TemporaryDirectory() as tmp:
            end = runtime.session_end(self._root(tmp, STATE_KILLED, "kind=fatal\nuptime_ms=70000\n"))
        self.assertEqual(end["reason"], "fatal-error")
        self.assertNotIn("not a crash", runtime.describe_end(end))

    def test_closing_that_did_not_finish(self):
        with tempfile.TemporaryDirectory() as tmp:
            end = runtime.session_end(self._root(tmp, STATE_CLOSING))
        self.assertEqual((end["reason"], end["clean"]), ("alt-f4", False))
        self.assertIn("did not finish closing by itself", runtime.describe_end(end))
        with tempfile.TemporaryDirectory() as tmp:
            end = runtime.session_end(self._root(tmp, STATE_CLOSING.replace("end=alt-f4", "end=session-end")))
        self.assertIn("Windows then ended the game", runtime.describe_end(end))

    def test_nothing_yet_and_damaged_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(runtime.session_end(self._root(tmp, None)))
        for text in ("", "[session]\nclean=1\n", "garbage\n[[x", "[last_session]\nend=\n"):
            with tempfile.TemporaryDirectory() as tmp:
                self.assertIsNone(runtime.session_end(self._root(tmp, text)))
                self.assertIsNone(runtime.session_end(Path(tmp), running=True))
        with tempfile.TemporaryDirectory() as tmp:
            odd = STATE_CLOSED.replace("end=alt-f4", "end=from-a-newer-loader").replace("2581234", "-5")
            end = runtime.session_end(self._root(tmp, odd))
        self.assertEqual((end["text"], end["uptime_s"]), ("it ended (from-a-newer-loader)", None))
        self.assertIsInstance(runtime.describe_end(end), str)

    def test_every_reason_has_words_and_a_label(self):
        self.assertEqual(set(runtime.END_REASONS), set(runtime.END_LABELS))
        codes = {"alt-f4", "close-button", "window-menu", "close-message", "session-end", "exit-menu", "exit-request",
                 "window-destroyed", "quit-message", "fatal-error", "self-exit", "unknown"}   # session.cpp's ENDS
        self.assertTrue(codes <= set(runtime.END_REASONS))
        self.assertEqual(set(runtime.END_REASONS) - runtime.NORMAL_ENDS, {"fatal-error", "crash", "not-clean"})

    def test_crash_command_says_how_the_last_session_ended(self):
        import contextlib
        import io
        from unittest import mock

        from riftstone import cli, install

        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp, STATE_CLOSED)
            (root / "DDDA.exe").write_bytes(b"stub")
            (root / "nativePC" / "rom").mkdir(parents=True)
            (root / "riftstone" / "logs" / "hang-20260101-000000.txt").write_text(HANG)
            out = io.StringIO()
            with mock.patch.object(install, "game_running", return_value=False), contextlib.redirect_stdout(out):
                code = cli.main(["crash", "--game", str(root)])
        text = out.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("Alt+F4 was pressed", text)
        self.assertIn("what the loader saw: WM_SYSKEYDOWN F4", text)
        self.assertIn("hang-20260101-000000.txt", text)
        self.assertIn("from an earlier session than the last one", text)


class SafeModeTest(unittest.TestCase):
    def test_state_and_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "riftstone").mkdir()
            (root / "riftstone" / "runtime-state.ini").write_text(
                "[session]\nclean=0\nearly_crashes=1\n[safe_mode]\non=1\nsetup=1234abcd\n"
                "[quarantine]\ncrash_plugin.asi=52-01dc\n[strikes]\ncrash_plugin.asi=2\n")
            st = runtime.runtime_state(root)
            self.assertTrue(st["safe_mode"])
            self.assertEqual(st["quarantine"], ["crash_plugin.asi"])
            self.assertFalse(st["last_clean"])
            self.assertTrue(runtime.safe_mode_off(root))
            self.assertFalse(runtime.runtime_state(root)["safe_mode"])
            self.assertTrue(runtime.release_plugin(root, "crash_plugin.asi"))
            self.assertEqual(runtime.runtime_state(root)["quarantine"], [])
            self.assertFalse(runtime.release_plugin(root, "crash_plugin.asi"))
            # Keys keep their case: Windows reads them back.
            self.assertIn("setup=1234abcd", (root / "riftstone" / "runtime-state.ini").read_text())

    def test_no_state_yet(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = runtime.runtime_state(Path(tmp))
            self.assertFalse(st["safe_mode"])
            self.assertTrue(st["last_clean"])


class SavesTest(unittest.TestCase):
    def test_backup_dedupe_keep_and_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            remote = Path(tmp) / "userdata" / "920192618" / "367500" / "remote"
            remote.mkdir(parents=True)
            (remote / "DDDA.sav").write_bytes(b"one" * 50)
            (remote / "0").write_bytes(b"\x01" * 8)
            root = Path(tmp) / "backups"
            first = runtime.backup_saves([remote], root, keep=2, stamp="20260101-000001")
            self.assertEqual(len(first), 1)
            self.assertEqual(first[0].parent.name, "920192618")
            self.assertEqual(runtime.backup_saves([remote], root, keep=2, stamp="20260101-000002"), [])
            for i, stamp in ((2, "20260101-000003"), (3, "20260101-000004")):
                (remote / "DDDA.sav").write_bytes(f"v{i}".encode() * 50)
                self.assertEqual(len(runtime.backup_saves([remote], root, keep=2, stamp=stamp)), 1)
            listed = runtime.list_save_backups(root)
            self.assertEqual([b["stamp"] for b in listed], ["20260101-000004", "20260101-000003"])
            self.assertTrue(all(b["has_save"] and b["files"] == 2 for b in listed))
            with self.assertRaises(RiftError):
                runtime.restore_save(listed[1]["path"], remote, game_running=True)
            aside = runtime.restore_save(listed[1]["path"], remote, game_running=False)
            self.assertEqual((remote / "DDDA.sav").read_bytes(), b"v2" * 50)
            self.assertEqual((aside / "DDDA.sav").read_bytes(), b"v3" * 50)
            self.assertEqual(list(remote.glob("*.riftstone-tmp")), [])

    def test_restore_refuses_a_folder_without_a_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RiftError):
                runtime.restore_save(Path(tmp) / "nothing", Path(tmp), game_running=False)


class LoaderSettingsTest(unittest.TestCase):
    TEMPLATE = ("; header\n[loader]\n; overlay\noverlay = 1\nplugins = 1\nchain =\n\n[guard]\nmissing_textures = 1\n\n"
                "[fps]\nmax_fps = 0\n")

    def test_owner_values_survive_an_update(self):
        old = "[loader]\noverlay = 0\nchain = old.dll\nmy_own = 7\n[fps]\nmax_fps = 165\n[custom]\nx = 1\n"
        text = loader.merge_ini(self.TEMPLATE, old, {"loader": {"chain": "dinput8_chain.dll"}})
        v = loader._ini_values(text)
        self.assertEqual(v["loader"]["overlay"], "0")                  # the owner's value
        self.assertEqual(v["loader"]["plugins"], "1")                  # template default
        self.assertEqual(v["loader"]["chain"], "dinput8_chain.dll")    # the installer's value
        self.assertEqual(v["loader"]["my_own"], "7")                   # kept in its section
        self.assertEqual(v["guard"]["missing_textures"], "1")          # new section from the template
        self.assertEqual(v["fps"]["max_fps"], "165")
        self.assertEqual(v["custom"]["x"], "1")
        self.assertIn("; overlay", text)                               # the template's notes stay
        self.assertEqual(text.count("[loader]"), 1)

    def test_fresh_install_is_the_template(self):
        text = loader.merge_ini(self.TEMPLATE, "", {"loader": {"chain": ""}})
        self.assertEqual(loader._ini_values(text), loader._ini_values(self.TEMPLATE))

    def _stand_in(self, tmp: Path, old_ini: str) -> "Game":
        from riftstone.game import Game

        root = tmp / "game"
        (root / "nativePC" / "rom" / "enemy").mkdir(parents=True)
        (root / "DDDA.exe").write_bytes(b"stub")
        (root / "dinput8.dll").write_bytes(b"MZ" + "Riftstone loader".encode("utf-16-le"))      # 0.1
        (root / "riftstone_loader.ini").write_text(old_ini)
        (root / "riftstone" / "overlay" / "rom" / "enemy").mkdir(parents=True)
        (root / "riftstone" / "overlay" / "rom" / "enemy" / "em5200.arc").write_bytes(b"MOD")
        (root / "riftstone" / "state.json").write_text(json.dumps(
            {"schema": "riftstone.state/1", "mods": [{"path": str(tmp / "mods" / "X"), "name": "X"}],
             "archives": {"rom/enemy/em5200": {"sha256": "0" * 64, "replaced": [], "added": [], "mods": ["X"]}}}))
        built = tmp / "built"
        built.mkdir()
        (built / "dinput8.dll").write_bytes(b"MZ" + "Riftstone loader".encode("utf-16-le") +
                                            b"RiftstoneLoaderVersion=0.2.0\0")
        return Game(root), built

    def test_update_keeps_settings_and_mods(self):
        from unittest import mock

        from riftstone import install

        with tempfile.TemporaryDirectory() as tmp:
            game, built = self._stand_in(Path(tmp), "[loader]\noverlay = 1\nlog_redirects = 0\nchain = my_tweak.dll\n")
            with mock.patch.object(loader, "built_loader", return_value=built), \
                    mock.patch.object(install, "game_running", return_value=False), \
                    mock.patch.object(loader, "_switch_mods", side_effect=AssertionError("mods rebuilt")):
                r = loader.install_loader(game, None)
            self.assertTrue(r["updated"])
            self.assertEqual(r["mods_moved_to_overlay"], [])
            self.assertEqual(runtime.loader_version(game.root / "dinput8.dll"), "0.2.0")
            v = loader._ini_values((game.root / "riftstone_loader.ini").read_text())
            self.assertEqual(v["loader"]["log_redirects"], "0")          # the owner's value
            self.assertEqual(v["loader"]["chain"], "my_tweak.dll")       # a hand-made chain stays
            self.assertIn("guard", v)                                     # new settings arrive
            self.assertEqual((game.overlay_dir / "rom" / "enemy" / "em5200.arc").read_bytes(), b"MOD")

    def test_first_install_moves_mods(self):
        from unittest import mock

        from riftstone import install

        with tempfile.TemporaryDirectory() as tmp:
            game, built = self._stand_in(Path(tmp), "")
            (game.root / "riftstone_loader.ini").unlink()                # no loader yet: direct mode
            (game.root / "dinput8.dll").unlink()
            with mock.patch.object(loader, "built_loader", return_value=built), \
                    mock.patch.object(install, "game_running", return_value=False), \
                    mock.patch.object(loader, "_switch_mods", return_value=["X"]) as switch:
                r = loader.install_loader(game, None)
            self.assertFalse(r["updated"])
            switch.assert_called_once()
            self.assertEqual(r["mods_moved_to_overlay"], ["X"])

    def test_version_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            dll = Path(tmp) / "dinput8.dll"
            dll.write_bytes(b"MZ" + bytes(100) + b"RiftstoneLoaderVersion=0.2.0\0" + bytes(10))
            self.assertEqual(runtime.loader_version(dll), "0.2.0")
            dll.write_bytes(b"MZ" + "Riftstone loader".encode("utf-16-le"))
            self.assertEqual(runtime.loader_version(dll), "0.1")
            dll.write_bytes(b"MZ other")
            self.assertIsNone(runtime.loader_version(dll))


if __name__ == "__main__":
    unittest.main()
