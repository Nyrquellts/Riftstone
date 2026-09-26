"""The plugin manager (plugins.py) and Studio's plugin, launch and static routes, in a stand-in game folder."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import SRC  # noqa: F401 -- puts src on sys.path

from riftstone import install, plugins, studio
from riftstone.errors import RiftError
from riftstone.game import Game

CAP_INI = """; enemy_cap -- how many enemies the world can have active at once.
; 10..64.  More enemies cost CPU for their AI and animation; 30 is a comfortable default.
[enemy_cap]
slots = 30
"""
LOADER_INI = """; Riftstone loader settings.
[loader]
; Serve archives from riftstone\\overlay.
overlay = 1
[fps]
; The options menu's "Variable" frame rate means this many fps.
max_fps = 165
[render]
shadow_map_size = 4096
[overlay]
key = F10
"""


class PluginsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.game = Game(base / "game")
        self.game.root.mkdir()
        (self.game.root / "DDDA.exe").write_bytes(b"stub")
        (self.game.root / "riftstone_loader.ini").write_text(LOADER_INI, encoding="utf-8")
        d = plugins.plugins_dir(self.game)
        d.mkdir(parents=True)
        (d / "enemy_cap.asi").write_bytes(b"MZ cap")
        (d / "enemy_cap.ini").write_text(CAP_INI, encoding="utf-8")
        (d / "mystery.dll").write_bytes(b"MZ other")
        self.build = base / "native"
        self.build.mkdir()
        (self.build / "lod_tuner.asi").write_bytes(b"MZ lod v2")
        (self.build / "lod_tuner.ini").write_text("[lod]\nEnabled = 1\nPopPixels = 24\n", encoding="utf-8")
        (self.build / "enemy_cap.asi").write_bytes(b"MZ cap v2")
        self.built = mock.patch.object(plugins, "built", lambda: {"lod_tuner": self.build / "lod_tuner.asi",
                                                                  "enemy_cap": self.build / "enemy_cap.asi"})
        self.built.start()
        self.closed = mock.patch.object(install, "game_running", lambda g: False)
        self.closed.start()

    def tearDown(self):
        self.built.stop()
        self.closed.stop()
        self.tmp.cleanup()

    def rows(self):
        return {r["name"]: r for r in plugins.describe(self.game)}

    def test_describe(self):
        rows = self.rows()
        self.assertEqual(set(rows), {"enemy_cap", "mystery", "lod_tuner"})
        cap = rows["enemy_cap"]
        self.assertEqual((cap["installed"], cap["enabled"], cap["title"], cap["own"], cap["update"]),
                         (True, True, "Enemy cap", True, True))
        self.assertEqual(cap["settings"], [{"section": "enemy_cap", "key": "slots", "value": "30",
                                            "help": "enemy_cap -- how many enemies the world can have active at once. "
                                                    "10..64.  More enemies cost CPU for their AI and animation; 30 is a "
                                                    "comfortable default."}])
        self.assertEqual((rows["mystery"]["title"], rows["mystery"]["own"]), ("mystery", False))
        self.assertEqual((rows["lod_tuner"]["installed"], rows["lod_tuner"]["settings"][1]["value"]), (False, "24"))

    def test_set_value_keeps_the_file_readable(self):
        out = plugins.set_value(self.game, "enemy_cap", "enemy_cap", "slots", 48)
        self.assertEqual(out["value"], "48")
        text = (plugins.plugins_dir(self.game) / "enemy_cap.ini").read_text(encoding="utf-8")
        self.assertIn("slots = 48", text)
        self.assertIn("; 10..64.", text)                       # comments kept
        raw = (plugins.plugins_dir(self.game) / "enemy_cap.ini").read_bytes()
        self.assertNotIn(b"\r\r", raw)                       # fuzz finding: text mode doubled each CR on Windows
        self.assertTrue(raw.endswith(b"slots = 48\r\n"))
        for bad in (9, 65, "x", "", "3;4", "1\n2", None, "12=3"):
            with self.assertRaises(RiftError, msg=bad):
                plugins.set_value(self.game, "enemy_cap", "enemy_cap", "slots", bad)
        with self.assertRaises(RiftError):
            plugins.set_value(self.game, "enemy_cap", "enemy_cap", "nope", 1)          # only existing keys
        with self.assertRaises(RiftError):
            plugins.set_value(self.game, "..\\x", "enemy_cap", "slots", 30)
        with self.assertRaises(RiftError):
            plugins.set_value(self.game, "absent", "a", "b", 1)

    def test_the_pawn_and_save_plugins_settings(self):
        """Their shipped ini files, edited through the same checks Studio uses."""
        native = Path(SRC).parent / "native" / "plugins"
        d = plugins.plugins_dir(self.game)
        for name in ("inclination_lock", "save_backup"):
            (d / f"{name}.asi").write_bytes(b"MZ")
            (d / f"{name}.ini").write_bytes((native / name / f"{name}.ini").read_bytes())
        ok = (("inclination_lock", "lock", "Mode", "Commands", "commands"),
              ("inclination_lock", "lock", "Mode", "off", "off"),
              ("save_backup", "backup", "Keep", "40", "40"),
              ("save_backup", "backup", "KeepSessions", "0", "0"),
              ("save_backup", "backup", "CheckSeconds", "0.5", "0.5"),
              ("save_backup", "backup", "Enabled", "0", "0"),
              ("save_backup", "backup", "Folder", "D:\\Saves\\DDDA", "D:\\Saves\\DDDA"))
        for name, sec, key, value, stored in ok:
            self.assertEqual(plugins.set_value(self.game, name, sec, key, value)["value"], stored, (key, value))
        bad = (("inclination_lock", "lock", "Mode", "freze"), ("inclination_lock", "lock", "Mode", ""),
               ("save_backup", "backup", "Keep", "0"), ("save_backup", "backup", "Keep", "1001"),
               ("save_backup", "backup", "KeepSessions", "-1"), ("save_backup", "backup", "CheckSeconds", "0"),
               ("save_backup", "backup", "Enabled", "2"))
        for name, sec, key, value in bad:
            with self.assertRaises(RiftError, msg=(key, value)):
                plugins.set_value(self.game, name, sec, key, value)
        text = (d / "inclination_lock.ini").read_text(encoding="utf-8")
        self.assertIn("Mode = off", text)
        self.assertIn("; freeze    Your main pawn's inclinations never drift.", text)  # comments kept

    def test_loader_settings(self):
        self.assertEqual(plugins.set_value(self.game, "loader", "fps", "max_fps", "144")["value"], "144")
        self.assertEqual(plugins.set_value(self.game, "loader", "fps", "max_fps", "0")["value"], "0")
        self.assertEqual(plugins.set_value(self.game, "loader", "render", "shadow_map_size", "8192")["value"], "8192")
        self.assertEqual(plugins.set_value(self.game, "loader", "overlay", "key", "f9")["value"], "F9")
        self.assertEqual(plugins.set_value(self.game, "loader", "loader", "overlay", "0")["value"], "0")
        for sec, key, bad in (("fps", "max_fps", "20"), ("fps", "max_fps", "400"), ("render", "shadow_map_size", "3000"),
                              ("overlay", "key", "F13"), ("overlay", "key", "Esc"), ("loader", "overlay", "2")):
            with self.assertRaises(RiftError, msg=(key, bad)):
                plugins.set_value(self.game, "loader", sec, key, bad)
        s = {(x["section"], x["key"]): x["value"] for x in plugins.loader_settings(self.game)}
        self.assertEqual(s[("fps", "max_fps")], "0")

    def test_free_sprint_settings_are_the_plugins_own_choices(self):
        """The shipped free_sprint.ini: Studio offers only the values the plugin reads (anything else patches nothing)."""
        shipped = Path(SRC).parent / "native" / "plugins" / "free_sprint" / "free_sprint.ini"
        d = plugins.plugins_dir(self.game)
        (d / "free_sprint.asi").write_bytes(b"MZ sprint")
        (d / "free_sprint.ini").write_bytes(shipped.read_bytes())
        row = self.rows()["free_sprint"]
        self.assertEqual((row["title"], row["own"]), ("Free sprint", True))
        self.assertEqual({(s["key"], s["value"]) for s in row["settings"]}, {("Mode", "out_of_battle"), ("Who", "party")})
        self.assertEqual(plugins.set_value(self.game, "free_sprint", "sprint", "Mode", "Always")["value"], "always")
        self.assertEqual(plugins.set_value(self.game, "free_sprint", "sprint", "Who", "arisen")["value"], "arisen")
        for key, bad in (("Mode", "sometimes"), ("Mode", "on"), ("Who", "everyone"), ("Who", "pawns")):
            with self.assertRaises(RiftError, msg=(key, bad)):
                plugins.set_value(self.game, "free_sprint", "sprint", key, bad)
        text = (d / "free_sprint.ini").read_text(encoding="utf-8")
        self.assertIn("Mode = always", text)
        self.assertIn("Who = arisen", text)
        self.assertIn("; out_of_battle  Sprinting costs no stamina", text)      # comments kept

    def test_draw_distance_settings_are_the_plugins_own_ranges(self):
        """The shipped draw_distance.ini: Studio takes 0 (the game's own) or the range the plugin clamps to."""
        shipped = Path(SRC).parent / "native" / "plugins" / "draw_distance" / "draw_distance.ini"
        d = plugins.plugins_dir(self.game)
        (d / "draw_distance.asi").write_bytes(b"MZ draw")
        (d / "draw_distance.ini").write_bytes(shipped.read_bytes())
        row = self.rows()["draw_distance"]
        self.assertEqual((row["title"], row["own"]), ("Draw distance", True))
        self.assertEqual({(s["key"], s["value"]) for s in row["settings"]},
                         {("Enabled", "1"), ("Objects", "3"), ("Grass", "3"), ("Enemies", "0"), ("HumanEnemies", "0"),
                          ("ObjectsNeverHide", "0"), ("EnemiesAlwaysActive", "0")})
        self.assertTrue(all(s["help"] for s in row["settings"]), "every key has its plain-English comment")
        ok = (("Objects", "8", "8"), ("Objects", "2.5", "2.5"), ("Objects", "20.0", "20"), ("Objects", "0", "0"),
              ("Objects", "0.0", "0"), ("Grass", "1", "1"), ("Grass", "3", "3"), ("Enemies", "6", "6"),
              ("HumanEnemies", "250", "250"), ("HumanEnemies", "0", "0"), ("ObjectsNeverHide", "1", "1"),
              ("EnemiesAlwaysActive", "1", "1"), ("Enabled", "0", "0"))
        for key, value, stored in ok:
            self.assertEqual(plugins.set_value(self.game, "draw_distance", "draw", key, value)["value"], stored, (key, value))
        for key, bad in (("Objects", "0.5"), ("Objects", "21"), ("Objects", "many"), ("Objects", "nan"), ("Objects", "-3"),
                         ("Grass", "4"), ("Grass", "0.9"), ("Enemies", "25"), ("HumanEnemies", "99"),
                         ("HumanEnemies", "2001"), ("HumanEnemies", "150.5"), ("ObjectsNeverHide", "2"),
                         ("EnemiesAlwaysActive", "yes")):
            with self.assertRaises(RiftError, msg=(key, bad)):
                plugins.set_value(self.game, "draw_distance", "draw", key, bad)
        text = (d / "draw_distance.ini").read_text(encoding="utf-8")
        self.assertIn("Objects = 0", text)
        self.assertIn("HumanEnemies = 0", text)
        self.assertIn("; 1 = on. 0 = the plugin loads and changes nothing.", text)      # comments kept

    def test_six_skill_warrior_settings_are_the_plugins_own_choices(self):
        """The shipped six_skill_warrior.ini: Studio offers only six and off (anything else patches nothing)."""
        shipped = Path(SRC).parent / "native" / "plugins" / "six_skill_warrior" / "six_skill_warrior.ini"
        d = plugins.plugins_dir(self.game)
        (d / "six_skill_warrior.asi").write_bytes(b"MZ warrior")
        (d / "six_skill_warrior.ini").write_bytes(shipped.read_bytes())
        row = self.rows()["six_skill_warrior"]
        self.assertEqual((row["title"], row["own"]), ("Six-skill Warrior", True))
        self.assertEqual({(s["key"], s["value"]) for s in row["settings"]}, {("Mode", "six")})
        self.assertEqual(plugins.set_value(self.game, "six_skill_warrior", "warrior", "Mode", "OFF")["value"], "off")
        for bad in ("sometimes", "on", "6", "three"):
            with self.assertRaises(RiftError, msg=bad):
                plugins.set_value(self.game, "six_skill_warrior", "warrior", "Mode", bad)
        text = (d / "six_skill_warrior.ini").read_text(encoding="utf-8")
        self.assertIn("Mode = off", text)
        self.assertIn("; six  A Warrior in your party", text)      # comments kept

    def test_toggle_moves_the_plugin_and_its_settings(self):
        out = plugins.toggle(self.game, "enemy_cap", False)
        self.assertEqual((out["enabled"], out["changed"]), (False, True))
        off = plugins.off_dir(self.game)
        self.assertTrue((off / "enemy_cap.asi").is_file() and (off / "enemy_cap.ini").is_file())
        self.assertFalse((plugins.plugins_dir(self.game) / "enemy_cap.asi").exists())
        self.assertFalse(self.rows()["enemy_cap"]["enabled"])
        self.assertFalse(plugins.toggle(self.game, "enemy_cap", False)["changed"])
        plugins.toggle(self.game, "enemy_cap", True)
        self.assertTrue((plugins.plugins_dir(self.game) / "enemy_cap.ini").is_file())
        with mock.patch.object(install, "game_running", lambda g: True):
            with self.assertRaises(RiftError):
                plugins.toggle(self.game, "enemy_cap", False)
        with self.assertRaises(RiftError):
            plugins.toggle(self.game, "absent", False)

    def test_the_command_line_adds_a_built_plugin_by_name(self):
        """`riftstone loader plugin add free_sprint` works as Studio's Add does; a file path still works too."""
        from riftstone import cli

        (self.game.root / "nativePC" / "rom").mkdir(parents=True, exist_ok=True)   # a game folder to the CLI
        game = ["--game", str(self.game.root)]
        self.assertEqual(cli.main(["loader", "plugin", "add", "lod_tuner"] + game), 0)
        d = plugins.plugins_dir(self.game)
        self.assertEqual((d / "lod_tuner.asi").read_bytes(), b"MZ lod v2")
        self.assertIn("PopPixels = 24", (d / "lod_tuner.ini").read_text(encoding="utf-8"))
        other = self.build / "extra.asi"
        other.write_bytes(b"MZ extra")
        self.assertEqual(cli.main(["loader", "plugin", "add", str(other)] + game), 0)
        self.assertEqual((d / "extra.asi").read_bytes(), b"MZ extra")
        self.assertEqual(cli.main(["loader", "plugin", "add", "not_built_here"] + game), 2)        # refused
        self.assertEqual(cli.main(["loader", "plugin", "add", str(self.build / "missing.asi")] + game), 2)

    def test_add_installs_and_keeps_the_owners_values(self):
        plugins.add(self.game, "lod_tuner")
        d = plugins.plugins_dir(self.game)
        self.assertEqual((d / "lod_tuner.asi").read_bytes(), b"MZ lod v2")
        plugins.set_value(self.game, "lod_tuner", "lod", "PopPixels", "8")
        plugins.add(self.game, "lod_tuner")                              # an update
        self.assertIn("PopPixels = 8", (d / "lod_tuner.ini").read_text(encoding="utf-8"))
        # a plugin that is off is updated where it is
        plugins.toggle(self.game, "enemy_cap", False)
        self.assertTrue(plugins.add(self.game, "enemy_cap")["file"] == "enemy_cap.asi")
        self.assertEqual((plugins.off_dir(self.game) / "enemy_cap.asi").read_bytes(), b"MZ cap v2")
        with self.assertRaises(RiftError):
            plugins.add(self.game, "not_built")


class StudioRoutesTest(PluginsTest):
    def studio(self):
        return studio.Studio(self.game, Path(self.tmp.name) / "mods")

    def test_plugin_routes(self):
        s = self.studio()
        r = s.api("GET", "plugins", {}, {})
        self.assertTrue(r["supported"])
        self.assertEqual({p["name"] for p in r["plugins"]}, {"enemy_cap", "mystery", "lod_tuner"})
        self.assertEqual(s.api("POST", "plugins/set", {}, {"name": "enemy_cap", "section": "enemy_cap", "key": "slots",
                                                           "value": "40"})["value"], "40")
        self.assertFalse(s.api("POST", "plugins/toggle", {}, {"name": "mystery", "on": False})["enabled"])
        self.assertEqual(s.api("POST", "plugins/add", {}, {"name": "lod_tuner"})["file"], "lod_tuner.asi")
        self.assertEqual([p["name"] for p in s.plugin_summary()], ["enemy_cap", "lod_tuner", "mystery"])
        for route, body in (("plugins/set", {"name": 3}), ("plugins/toggle", {}), ("plugins/add", {"name": "x/y"})):
            with self.assertRaises(RiftError, msg=route):
                s.api("POST", route, {}, body)

    def test_launch(self):
        s = self.studio()
        s._running, s._running_at = False, 1e18
        with mock.patch.object(os, "startfile", create=True) as start:
            self.assertEqual(s.api("POST", "launch", {}, {})["how"], "steam")
            start.assert_called_once_with("steam://rungameid/367500")
        s._running = True
        with self.assertRaises(RiftError):
            s.api("POST", "launch", {}, {})

    def test_the_workspace_follows_the_installed_mods(self):
        mods = Path(self.tmp.name) / "elsewhere" / "mods"
        (mods / "A").mkdir(parents=True)
        with mock.patch.object(install, "load_state", lambda g: {"mods": [{"path": str(mods / "A")}]}), \
                mock.patch.dict(os.environ, {"RIFTSTONE_WORKSPACE": ""}):
            self.assertEqual(studio.default_workspace(self.game), mods)
        with mock.patch.dict(os.environ, {"RIFTSTONE_WORKSPACE": str(mods)}):
            self.assertEqual(studio.default_workspace(None), mods)

    def test_a_game_switch_takes_that_games_mods_folder(self):
        """Studio started without a mods folder shows each game's own (Online's Solo Balance lives elsewhere than
        Dark Arisen's mods); a folder chosen with --workspace stays."""
        other = Path(self.tmp.name) / "ddo-mods"
        for follow, want in ((True, other), (False, Path(self.tmp.name) / "mods")):
            s = studio.Studio(self.game, Path(self.tmp.name) / "mods", follow=follow)
            with mock.patch.object(studio, "find_game", lambda kind: self.game), \
                    mock.patch.object(studio, "default_workspace", lambda g: other), \
                    mock.patch.object(studio.Studio, "start_index", lambda self: None):
                s.switch("ddo")
            self.assertEqual(s.workspace, want, f"follow={follow}")
            self.assertTrue(want.is_dir())

    def test_static_files(self):
        from importlib import resources
        for name, ctype in studio.STATIC.items():
            data = resources.files("riftstone").joinpath("studio/" + name).read_bytes()
            self.assertTrue(data, name)
        self.assertEqual(resources.files("riftstone").joinpath("studio/fonts/riftstone-blade.woff2").read_bytes()[:4], b"wOF2")


if __name__ == "__main__":
    unittest.main()
