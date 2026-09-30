"""playtest.py: a play session checked item by item from the logs it left (a stand-in game folder with the
loader's log, enemy_cap's slot record, free_sprint's log and runtime-state.ini, as loader 0.3.3 writes
them), and the texture-guard test mod on a stand-in goblin archive.  No game, no loader process."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401 (sys.path)
from riftstone import arc, mrl, playtest, typemap
from riftstone.errors import RiftError
from riftstone.game import Game

LOADER_LOG = """22:22:12.395  Riftstone loader 0.3.3 in C:\\Games\\DDDA
22:22:12.395  game     Dragon's Dogma: Dark Arisen (DDDA.exe, PE timestamp 0x5a314c31), the build Riftstone's engine fixes were measured on
22:22:12.467  hook     KERNEL32.dll!CreateFileW installed
22:22:12.467  hook     KERNEL32.dll!CreateFileA installed
22:22:12.467  guard    missing textures get a neutral stand-in (riftstone\\standin)
22:22:12.475  plugins  2 found in C:\\Games\\DDDA\\riftstone\\plugins
22:22:14.880  plugin   enemy_cap.asi loaded at 0x613F0000
22:22:14.938  plugin   free_sprint.asi loaded at 0x613C0000
22:22:15.100  hook     CreateFileW was reset during start-up; installed again
22:22:23.276  overlay  F10 shows the diagnostics panel (top-right, scale auto)
22:25:44.100  overlay  panel shown on the 2560x1440 back buffer at scale 1.33 (top-right)
22:25:45.200  overlay  panel opened: enemy pool 40 / 40 slots (peak 40), address space 1.62 / 4.00 GB (59.5% headroom), 138.2 fps, stage 100
22:26:10.000  guard    C:\\Games\\DDDA\\nativePC\\riftstone\\playtest\\no_such_texture_BM.tex does not exist; the game gets a neutral stand-in texture instead of stopping
22:29:27.426  exit     Alt+F4, after 7 min 15 s: WM_SYSKEYDOWN F4 with Alt (from the keyboard), then SC_CLOSE (posted), then WM_CLOSE; in stage 100
22:29:29.483  summary  ran 7 min 17 s; 52758 frames (average 7.00 ms, 142.8 fps); 117 stutters; address space peak 1665 MB, peak commit 1400 MB, smallest free block 2046 MB (memory headroom); 4 overlay redirects, 1 missing files, 1 stand-ins, 0 fatal errors; ended by: Alt+F4
"""
CAP_LOG = """enemy_cap: 40 enemies at once (the game's limit is 10); 164 sites and 4 runs patched (game); slot record on
22:24:42  peak: 7 of 40 slots in use (7 with a unit), stage 100
22:25:41  peak: 20 of 40 slots in use (20 with a unit), stage 100
22:25:43  peak: 40 of 40 slots in use (40 with a unit), stage 100
22:25:43  all 40 slots in use, stage 100: more enemies wait for a free slot
22:26:40  40 of 40 slots in use (40 with a unit), stage 100
22:29:29  the game is exiting normally (a quit, a closed window or a fatal-error box; not a crash). Last sample 22:29:27: 40 of 40 slots in use (40 with a unit), stage 100. Peak 40 at 22:25:43, stage 100. All slots in use for 159 s in total
"""
SPRINT_LOG = """free_sprint: Mode = out_of_battle, Who = party (game): sprinting costs no stamina while the game is not in battle
  0x00B8147E  updateStamina's call to calcStaminaConsume (0x00B81920) goes through the plugin
sprinting freely (logged once)
in battle: sprinting costs stamina as usual (logged once)
"""
STATE = """[session]
early_crashes=0
clean=1
started=20260925-222212
loader=0.3.3
end=alt-f4
end_detail=WM_SYSKEYDOWN F4 with Alt (from the keyboard), then SC_CLOSE (posted), then WM_CLOSE; in stage 100
end_uptime_ms=435031
"""


def game(tmp: str, loader: str | None = LOADER_LOG, cap: str | None = CAP_LOG, sprint: str | None = SPRINT_LOG,
         state: str = STATE) -> Path:
    root = Path(tmp) / "game"
    logs = root / "riftstone" / "logs"
    logs.mkdir(parents=True)
    for name, text in (("loader.log", loader), ("enemy_cap.log", cap), ("free_sprint.log", sprint)):
        if text is not None:
            (logs / name).write_text(text, encoding="utf-8")
    (root / "riftstone" / "runtime-state.ini").write_text(state, encoding="utf-8")
    return root


def by_key(items) -> dict[str, playtest.Item]:
    return {i.key: i for i in items}


class CheckSessionTest(unittest.TestCase):
    def test_a_good_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            items = by_key(playtest.check_session(game(tmp)))
        self.assertEqual({k: i.status for k, i in items.items()},
                         {"loader": "ok", "plugins": "ok", "panel": "ok", "enemy_cap": "ok", "textures": "ok",
                          "safe_mode": "untested", "reports": "ok", "memory": "ok", "free_sprint": "ok", "end": "ok"})
        self.assertIn("loader 0.3.3 started at 22:22:12", items["loader"].lines[0])
        self.assertIn("2 hook(s) installed, 1 put back after the DRM wrapper reset them", items["loader"].lines[1])
        self.assertIn("free_sprint: free_sprint: Mode = out_of_battle, Who = party (game)", "\n".join(items["plugins"].lines))
        panel = "\n".join(items["panel"].lines)
        self.assertIn("agrees with enemy_cap: between enemy_cap's 40 at 22:25:43 and 40 at 22:26:40", panel)
        self.assertIsNone(items["panel"].todo)                       # 40 at once: it was seen under load
        self.assertIn("40 slots; the most with a unit at once: 40; all slots in use for 159 s", items["enemy_cap"].lines[0])
        self.assertIn("1 stand-in(s) served", items["textures"].lines[0])
        self.assertIn("no_such_texture_BM.tex does not exist", items["textures"].lines[1])
        self.assertIn("memory headroom", items["memory"].lines[1])
        self.assertIn("one in battle was charged as usual", items["free_sprint"].lines[1])
        self.assertIn("Alt+F4 was pressed", items["end"].lines[0])

    def test_a_graphics_profile_and_whether_dxvk_ran_under_enb(self):
        import json
        import os

        def session(tmp, proxy="true", dxvk_age=None, ended=True):
            root = game(tmp, loader=LOADER_LOG if ended else LOADER_LOG.rsplit("22:29:29.483", 1)[0])
            (root / "riftstone" / "graphics.json").write_text(json.dumps(
                {"schema": "riftstone-graphics-state/1", "profile": "modern_remaster", "title": "ENB over DXVK",
                 "applied": "2026-09-27T18:40:00", "files": {}}), encoding="utf-8")
            (root / "enblocal.ini").write_text(f"[PROXY]\nEnableProxyLibrary={proxy}\nProxyLibrary=riftstone\\dxvk\\"
                                               "d3d9.dll\n", encoding="latin-1")
            if dxvk_age is not None:
                end = (root / "riftstone" / "logs" / "loader.log").stat().st_mtime
                log = root / "DDDA_d3d9.log"
                log.write_text("info:  DXVK: v3.1.1\n", encoding="utf-8")
                os.utime(log, (end - dxvk_age, end - dxvk_age))
            return by_key(playtest.check_session(root))["graphics"]

        with tempfile.TemporaryDirectory() as tmp:
            it = session(tmp, dxvk_age=1)
        self.assertEqual(it.status, "ok")
        self.assertIn("modern_remaster: ENB over DXVK, in the game folder since 2026-09-27T18:40:00", it.lines[0])
        self.assertIn("DXVK ran this session: it wrote DDDA_d3d9.log in the game folder (ENB loaded it)", it.lines[1])
        with tempfile.TemporaryDirectory() as tmp:          # the log is from a session a day before
            it = session(tmp, dxvk_age=86400 + 3600)
        self.assertEqual(it.status, "fail")
        self.assertIn("ENB did not load it", "\n".join(it.lines))
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(session(tmp, ended=False).status, "untested")
        with tempfile.TemporaryDirectory() as tmp:          # ENB on Windows' own Direct3D 9: nothing to grade
            self.assertEqual(session(tmp, proxy="false").status, "info")
        with tempfile.TemporaryDirectory() as tmp:          # no profile: no item
            self.assertNotIn("graphics", by_key(playtest.check_session(game(tmp))))

    def test_more_portcrystals(self):
        patched = ("portcrystals: 15 Portcrystals placed at once (the game allows 10); 46 sites, 4 runs and 3 hooks "
                   "patched (game); the sidecar keeps 0 save(s)' crystals past ten\n")
        saved = ("20:01:02  saved: 1 crystal(s) placed past the save's ten (slots 11-15), kept in the sidecar for this "
                 "save (6ec3e69c2e0662e5)\n")
        loaded = "20:05:00  loaded: 1 crystal(s) past the save's ten came back from the sidecar (6ec3e69c2e0662e5)\n"
        for log, status in ((patched, "untested"), (patched + saved, "untested"), (patched + saved + loaded, "ok"),
                            ("refused: the game's sGameSys already exists (the plugin was loaded too late); nothing "
                             "patched\n", "fail")):
            with tempfile.TemporaryDirectory() as tmp:
                root = game(tmp)
                (root / "riftstone" / "logs" / "portcrystals.log").write_text(log, encoding="utf-8")
                it = by_key(playtest.check_session(root))["portcrystals"]
            with self.subTest(log=log[:40]):
                self.assertEqual(it.status, status)
                if status == "untested":
                    self.assertIn("place an eleventh Portcrystal", it.todo)
                if status == "ok":
                    self.assertIn("a load brought back 1 crystal(s) past the ten", "\n".join(it.lines))

    def test_a_panel_that_disagrees_with_enemy_cap_fails(self):
        log = LOADER_LOG.replace("enemy pool 40 / 40 slots (peak 40)", "enemy pool 12 / 40 slots (peak 40)")
        with tempfile.TemporaryDirectory() as tmp:
            panel = by_key(playtest.check_session(game(tmp, loader=log)))["panel"]
        self.assertEqual(panel.status, "fail")
        self.assertIn("DIFFERS from enemy_cap", "\n".join(panel.lines))
        log = LOADER_LOG.replace("enemy pool 40 / 40 slots", "enemy pool 40 / 30 slots")
        with tempfile.TemporaryDirectory() as tmp:
            panel = by_key(playtest.check_session(game(tmp, loader=log)))["panel"]
        self.assertEqual(panel.status, "fail")
        self.assertIn("the panel says 30 slots, enemy_cap's record 40", "\n".join(panel.lines))

    def test_between_two_records_agrees(self):
        samples = playtest.cap_samples(CAP_LOG, 22 * 3600)
        ok, why = playtest.panel_agrees(22 * 3600 + 25 * 60 + 42, 30, 40, samples)
        self.assertTrue(ok, why)
        self.assertIn("between enemy_cap's 20 at 22:25:41 and 40 at 22:25:43", why)
        ok, why = playtest.panel_agrees(22 * 3600 + 25 * 60 + 5, 30, 40, samples)
        self.assertFalse(ok)

    def test_what_was_not_exercised_says_what_to_do(self):
        log = "\n".join(s for s in LOADER_LOG.splitlines()
                        if "panel shown" not in s and "panel opened" not in s and "does not exist" not in s)
        log = log.replace("1 missing files, 1 stand-ins", "0 missing files, 0 stand-ins")
        sprint = SPRINT_LOG.split("sprinting freely")[0]
        with tempfile.TemporaryDirectory() as tmp:
            items = by_key(playtest.check_session(game(tmp, loader=log, sprint=sprint)))
        self.assertEqual((items["panel"].status, items["textures"].status, items["free_sprint"].status),
                         ("untested", "untested", "untested"))
        self.assertIn("press the key while the game is in front", items["panel"].todo)
        self.assertIn("no key press reached the loader", items["panel"].todo)
        self.assertIn("playtest guard-mod", items["textures"].todo)
        self.assertIn("sprint outside a fight", items["free_sprint"].todo)

    def test_the_startup_notice_and_the_key_presses_are_evidence(self):
        """1.0.3: the banner drawn proves the loader draws in the game; a key press that asked for the panel with
        nothing drawn after it is a failure (the report a Nexus player gave: the key did something else)."""
        base = "\n".join(s for s in LOADER_LOG.splitlines() if "panel shown" not in s and "panel opened" not in s)
        banner = "22:22:24.100  overlay  startup banner shown on the 2560x1440 back buffer at scale 1.33 (top-right)"
        with tempfile.TemporaryDirectory() as tmp:
            panel = by_key(playtest.check_session(game(tmp, loader=base + "\n" + banner)))["panel"]
        self.assertEqual(panel.status, "untested")
        self.assertIn("the startup notice was drawn", "\n".join(panel.lines))
        self.assertIn("no key press reached the loader", panel.todo)
        pressed = "22:22:30.000  overlay  Insert pressed with the game in front: the panel is now shown"
        with tempfile.TemporaryDirectory() as tmp:
            panel = by_key(playtest.check_session(game(tmp, loader=base + "\n" + banner + "\n" + pressed)))["panel"]
        self.assertEqual(panel.status, "fail")
        self.assertIn("the key arrived but nothing was drawn", "\n".join(panel.lines))
        hidden = "22:22:31.000  overlay  Insert pressed with the game in front: the panel is now hidden"
        with tempfile.TemporaryDirectory() as tmp:            # pressed twice, no panel: opened and closed before a frame
            panel = by_key(playtest.check_session(game(tmp, loader=base + "\n" + pressed + "\n" + hidden)))["panel"]
        self.assertEqual(panel.status, "fail")

    def test_a_1_0_1_session(self):
        # 1.0.x's summary carries the Direct3D part (and memory pressure) before "ended by"; 1.0.1 arms the archive
        # guard, whose serves count among the summary's stand-ins
        log = LOADER_LOG.replace("0.3.3", "1.0.1").replace(
            "22:22:12.467  guard    missing textures get a neutral stand-in (riftstone\\standin)\n",
            "22:22:12.467  guard    missing textures get a neutral stand-in (riftstone\\standin)\n"
            "22:22:12.467  guard    a resource the game asks for loose before its archive is read gets its own bytes "
            "from that archive (riftstone\\standin\\nativePC)\n"
            "22:27:01.000  guard    read the directories of 4113 archives (71910 resources) in 850 ms, to find "
            "resources the game asks for loose\n"
            "22:27:01.900  guard    C:\\Games\\DDDA\\nativePC\\ui\\credit\\credit2_01_99.gmd was not read in yet; the game "
            "gets its own bytes from C:\\Games\\DDDA\\nativePC\\rom\\stage\\stage802\\stage802.arc (5120 bytes) instead "
            "of stopping\n").replace(
            "1 stand-ins, 0 fatal errors; ended by: Alt+F4",
            "2 stand-ins, 0 fatal errors; Direct3D 9 Windows' own, managed textures and buffers peak 295 MB; "
            "memory pressure 2 times; ended by: Alt+F4")
        with tempfile.TemporaryDirectory() as tmp:
            items = by_key(playtest.check_session(game(tmp, loader=log)))
        self.assertEqual((items["memory"].status, items["archives"].status, items["textures"].status),
                         ("ok", "ok", "ok"))
        memory = "\n".join(items["memory"].lines)
        self.assertIn("142.8 fps on average", memory)
        self.assertIn("Direct3D 9 Windows' own, managed textures and buffers peak 295 MB", memory)
        self.assertIn("memory pressure 2 time(s)", memory)
        archives = "\n".join(items["archives"].lines)
        self.assertIn("1 resource(s) the game asked for before their archive was read", archives)
        self.assertIn("credit2_01_99.gmd was not read in yet", archives)
        self.assertIn("read the directories of 4113 archives", archives)
        self.assertIn("1 stand-in(s) served", items["textures"].lines[0])     # the texture guard's own, not 2
        self.assertIn("Alt+F4 was pressed", items["end"].lines[0])

        # armed, never needed: not exercised, with what to do; a copy it could not write: failed; off: said so
        quiet = "\n".join(s for s in log.splitlines() if "was not read in yet" not in s and "read the directories" not in s)
        broke = log.replace("was not read in yet; the game gets its own bytes from", "is in").replace(
            " (5120 bytes) instead of stopping", ", but its copy could not be written under riftstone\\standin (error 5)")
        off = log.replace("gets its own bytes from that archive (riftstone\\standin\\nativePC)", "stops the game as usual")
        off = "\n".join(s for s in off.splitlines() if "was not read in yet" not in s)
        for text, status, needle in ((quiet, "untested", "armed; the game never asked"),
                                     (broke, "fail", "could not be written"),
                                     (off, "info", "off this session")):
            with tempfile.TemporaryDirectory() as tmp:
                it = by_key(playtest.check_session(game(tmp, loader=text)))["archives"]
            self.assertEqual(it.status, status, text)
            self.assertIn(needle, "\n".join(it.lines))
            if status == "untested":
                self.assertIn("skip cutscenes", it.todo)

        # an older loader says nothing of it: no item
        with tempfile.TemporaryDirectory() as tmp:
            self.assertNotIn("archives", by_key(playtest.check_session(game(tmp))))

    def test_the_games_own_settings(self):
        # config.ini, read only when given: FARTHEST with draw_distance loaded says what FAR gives; VSync its steps
        cfg = ("[GRAPHICS]\nMaxFPS=150.000000\nViewRange=FARTHEST\n[DISPLAY]\nResolution=2560x1440\n"
               "RefreshRate=165.00Hz\nVSYNC=ON\n")
        log = LOADER_LOG.replace("22:22:14.938  plugin   free_sprint.asi loaded at 0x613C0000\n",
                                 "22:22:14.938  plugin   free_sprint.asi loaded at 0x613C0000\n"
                                 "22:22:14.940  plugin   draw_distance.asi loaded at 0x613A0000\n"
                                 "22:22:14.941  plugin   lod_tuner.asi loaded at 0x61390000\n")
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.ini"
            config.write_text(cfg, encoding="utf-8")
            root = game(tmp, loader=log)
            it = by_key(playtest.check_session(root, config=config))["settings"]
            self.assertEqual(it.status, "info")
            self.assertEqual(it.lines[0], "2560x1440, 165 Hz, VSync on, frame-rate ceiling 150, ViewRange FARTHEST")
            self.assertIn("full detail at any distance", it.lines[1])
            self.assertIn("lod_tuner's Farthest = lod gives it levels of detail (x3)", it.lines[1])
            self.assertIn("with draw_distance loaded, FAR keeps FARTHEST's distances", it.lines[1])
            self.assertIn("(165, 82, 55 fps): a frame that takes a little over 6.1 ms shows at 82", it.lines[2])

            # lod_tuner gave FARTHEST levels of detail this session
            (root / "riftstone" / "logs" / "lod_tuner.log").write_text(
                "lod_tuner: FARTHEST picks levels of detail at x3: 10 ViewRange tests changed\n", encoding="utf-8")
            it = by_key(playtest.check_session(root, config=config))["settings"]
            self.assertEqual(it.lines[1], "ViewRange FARTHEST, with levels of detail from lod_tuner's Farthest = lod (x3)")

            # FAR, no VSync, no draw_distance: just the settings
            config.write_text(cfg.replace("FARTHEST", "FAR").replace("VSYNC=ON", "VSYNC=OFF"), encoding="utf-8")
            it = by_key(playtest.check_session(game(Path(tmp) / "b"), config=config))["settings"]
            self.assertEqual(it.lines, ["2560x1440, 165 Hz, VSync off, frame-rate ceiling 150, ViewRange FAR"])

            # not given, missing, or the session before: no item
            self.assertNotIn("settings", by_key(playtest.check_session(game(Path(tmp) / "c"))))
            self.assertNotIn("settings", by_key(playtest.check_session(game(Path(tmp) / "d"), config=Path(tmp) / "none.ini")))

    def test_a_panel_seen_only_with_few_enemies_asks_for_load(self):
        log = LOADER_LOG.replace("enemy pool 40 / 40 slots (peak 40)", "enemy pool 7 / 40 slots (peak 7)") \
                        .replace("22:25:45.200", "22:24:42.300")
        with tempfile.TemporaryDirectory() as tmp:
            panel = by_key(playtest.check_session(game(tmp, loader=log)))["panel"]
        self.assertEqual(panel.status, "ok")
        self.assertIn("20 or more enemies", panel.todo)

    def test_failures(self):
        refused = "refused: calcStaminaConsume: the switch on the player's action (+0x2DD4) at 0x00B819FC is not the " \
                  "expected code (another build, or already patched)\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = game(tmp, sprint=refused)
            (root / "riftstone" / "logs" / "crash-20260925-222800.txt").write_text(
                "Riftstone crash report\nexception 0xC0000005 at DDDA.exe+0x1234\n", encoding="utf-8")
            (root / "riftstone" / "logs" / "crash-20260924-100000.txt").write_text("Riftstone crash report\n")
            items = by_key(playtest.check_session(root))
        self.assertEqual((items["plugins"].status, items["free_sprint"].status, items["reports"].status),
                         ("fail", "fail", "fail"))
        self.assertIn("refused:", "\n".join(items["plugins"].lines))
        self.assertEqual(len(items["reports"].lines), 1)             # only the report from this session
        self.assertIn("crash-20260925-222800.txt", items["reports"].lines[0])
        failed = LOADER_LOG.replace("plugin   free_sprint.asi loaded at 0x613C0000",
                                    "plugin   free_sprint.asi FAILED to load: %1 is not a valid Win32 application.")
        with tempfile.TemporaryDirectory() as tmp:
            items = by_key(playtest.check_session(game(tmp, loader=failed)))
        self.assertEqual(items["plugins"].status, "fail")
        bound = LOADER_LOG.replace("peak commit 1400 MB", "peak commit 3600 MB").replace("(memory headroom)", "(memory bound)")
        with tempfile.TemporaryDirectory() as tmp:
            items = by_key(playtest.check_session(game(tmp, loader=bound)))
        self.assertEqual(items["memory"].status, "fail")
        self.assertIn("Memory-bound", "\n".join(items["memory"].lines))

    def test_no_loader_yet_and_the_session_before(self):
        with tempfile.TemporaryDirectory() as tmp:
            items = playtest.check_session(game(tmp, loader=None))
        self.assertEqual([(i.key, i.status) for i in items], [("loader", "untested")])
        self.assertIn("riftstone loader install", items[0].todo)
        with tempfile.TemporaryDirectory() as tmp:
            root = game(tmp)
            (root / "riftstone" / "logs" / "loader.prev.log").write_text(LOADER_LOG, encoding="utf-8")
            items = by_key(playtest.check_session(root, previous=True))
        self.assertNotIn("enemy_cap", items)                          # the plugins' logs are the latest session's
        self.assertNotIn("free_sprint", items)
        self.assertEqual(items["panel"].status, "ok")
        self.assertNotIn("agrees", "\n".join(items["panel"].lines))

    def test_an_older_loader(self):
        """Loader 0.3.1 (as the owner's game logged it on 2026-09-25): no panel readings, no memory word."""
        old = LOADER_LOG.replace("Riftstone loader 0.3.3", "Riftstone loader 0.3.1") \
                        .replace(", peak commit 1400 MB", "").replace(" (memory headroom)", "")
        old = "\n".join(s for s in old.splitlines() if "panel opened" not in s)
        with tempfile.TemporaryDirectory() as tmp:
            items = by_key(playtest.check_session(game(tmp, loader=old)))
        self.assertEqual(items["panel"].status, "ok")
        self.assertIn("its readings were not noted (a loader before 0.3.3)", items["panel"].lines)
        self.assertEqual(items["memory"].status, "ok")
        self.assertEqual(len(items["memory"].lines), 2)

    def test_plugin_verdicts(self):
        self.assertEqual(playtest.plugin_verdict("lod_tuner", "lod_tuner: rModel::load patched at 0x00FA9479 (game)")[0],
                         "ok")
        self.assertEqual(playtest.plugin_verdict("inclination_lock", "inclination_lock: Mode = off, nothing patched")[0],
                         "info")
        self.assertEqual(playtest.plugin_verdict("mystery", "hello")[0], "info")
        self.assertEqual(playtest.plugin_verdict("enemy_cap", "")[0], "info")
        self.assertEqual(playtest.plugin_verdict("enemy_skins", "refused: not the expected code")[0], "fail")


class GuardModTest(unittest.TestCase):
    """The texture-guard test mod on a stand-in game holding the goblins' two body materials."""

    class FakeIndex:
        def archives_with(self, name: bytes, type_id: int) -> list[str]:
            return ["rom/enemy/em0100"] if name.startswith(b"model\\em\\e01\\e0100\\e0100") else []

    def material(self, texture: str) -> bytes:
        names = ["model\\em\\e01\\e0100\\e0100_all_NM", texture, "model\\em\\e01\\e0100\\e0100_all_TM"]
        texs = [mrl.Texture(mrl.TEX_TYPE_ID, 0, 0, (n.encode() + b"\0").ljust(mrl.NAME_LEN, b"\xcd")) for n in names]
        mats = [mrl.Material(0x1CAB245E, 0x6FC86CDB, list(range(13)))]
        return mrl.build(mrl.Mrl(mrl.VERSION, 0xB46006D5, texs, mats, b"\xcd\xcd\xcd\xcd"))

    def test_the_goblins_skin_points_at_a_texture_that_is_not_there(self):
        tid = typemap.type_for_extension("mrl")
        with tempfile.TemporaryDirectory() as tmp:
            g = Game(Path(tmp) / "game")
            rom = g.root / "nativePC" / "rom" / "enemy"
            rom.mkdir(parents=True)
            (g.root / "DDDA.exe").write_bytes(b"stub")
            entries = [arc.Entry.from_data(b"model\\em\\e01\\e0100\\e0100", tid,
                                           self.material("model\\em\\e01\\e0100\\d_e0100_all_BM")),
                       arc.Entry.from_data(b"model\\em\\e01\\e0100\\e0100_a", tid,
                                           self.material("model\\em\\e01\\e0100\\e0100_all_BM"))]
            (rom / "em0100.arc").write_bytes(arc.Archive(entries).build())
            before = (rom / "em0100.arc").read_bytes()
            written = playtest.guard_test_mod(g, self.FakeIndex(), Path(tmp) / "mods" / "Guard")
            self.assertEqual(sorted(written), ["files\\model\\em\\e01\\e0100\\e0100.mrl",
                                               "files\\model\\em\\e01\\e0100\\e0100_a.mrl"])
            for rel in written:
                doc = mrl.parse((Path(tmp) / "mods" / "Guard" / rel).read_bytes())
                names = [t.name for t in doc.textures]
                self.assertEqual(names[1], playtest.GUARD_MISSING)
                self.assertEqual((names[0], names[2]), ("model\\em\\e01\\e0100\\e0100_all_NM",
                                                        "model\\em\\e01\\e0100\\e0100_all_TM"))
            self.assertEqual((rom / "em0100.arc").read_bytes(), before)        # the game is untouched
            self.assertTrue((Path(tmp) / "mods" / "Guard" / "riftstone-mod.json").is_file())
            self.assertEqual(len(playtest.guard_test_mod(g, self.FakeIndex(), Path(tmp) / "mods" / "Guard")), 2)

    def test_refusals(self):
        with tempfile.TemporaryDirectory() as tmp:
            ddo = Game(Path(tmp) / "ddo", "ddo")
            with self.assertRaisesRegex(RiftError, "for Dark Arisen"):
                playtest.guard_test_mod(ddo, self.FakeIndex(), Path(tmp) / "m")
            g = Game(Path(tmp) / "game")

            class Empty:
                def archives_with(self, name, type_id):
                    return []

            with self.assertRaisesRegex(RiftError, "is not in this game's archives"):
                playtest.guard_test_mod(g, Empty(), Path(tmp) / "m2")


if __name__ == "__main__":
    unittest.main()
