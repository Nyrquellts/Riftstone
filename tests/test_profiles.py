"""Overhaul profiles (profiles.py, `riftstone profile`): parse and write back, export from a stand-in game folder,
import planned first (a dry run changes no byte; a taken move refuses the whole profile)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import SRC  # noqa: F401 -- puts src on sys.path

from riftstone import __version__, install, plugins, profiles
from riftstone.errors import RiftError
from riftstone.game import Game

CAP_INI = """; enemy_cap -- how many enemies the world can have active at once.
[enemy_cap]
slots = 30
record = 1
"""
SPRINT_INI = """; free_sprint -- sprinting costs no stamina.
[sprint]
Mode = out_of_battle
Who = party
"""
BACKUP_INI = """[backup]
Folder = auto
Keep = 20
"""
LOADER_INI = """[loader]
overlay = 1
[fps]
max_fps = 165
[overlay]
key = Insert
"""


def _profile(**over) -> dict:
    doc = {"schema": profiles.SCHEMA, "name": "Hordes", "made_with": "Riftstone Studio 1.0.5",
           "features": {"enemy_cap": True}, "settings": {"enemy_cap": {"enemy_cap": {"slots": "64"}}}}
    doc.update(over)
    return doc


def _raw(doc) -> bytes:
    return json.dumps(doc).encode("utf-8")


class ParseTest(unittest.TestCase):
    def test_a_profile_reads_back_from_its_own_text(self):
        p = profiles.parse(_raw(_profile()))
        self.assertEqual(p, {"name": "Hordes", "made_with": "Riftstone Studio 1.0.5", "features": {"enemy_cap": True},
                             "settings": {"enemy_cap": {"enemy_cap": {"slots": "64"}}}})
        text = profiles.to_json(p)
        self.assertTrue(text.endswith("}\n"))
        self.assertEqual(profiles.parse(text.encode("utf-8")), p)
        self.assertEqual(profiles.to_json(profiles.parse(text.encode("utf-8"))), text)

    def test_the_file_order_and_a_byte_order_mark_are_kept(self):
        doc = _profile(features={"lod_tuner": False, "enemy_cap": True, "free_sprint": True})
        p = profiles.parse(b"\xef\xbb\xbf" + _raw(doc))
        self.assertEqual(list(p["features"]), ["lod_tuner", "enemy_cap", "free_sprint"])
        self.assertIn('"name": "Hordes"', profiles.to_json(p))

    def test_made_with_features_and_settings_may_be_left_out(self):
        p = profiles.parse(_raw({"schema": profiles.SCHEMA, "name": "Empty"}))
        self.assertEqual(p, {"name": "Empty", "made_with": "", "features": {}, "settings": {}})

    def test_non_ascii_text_is_written_as_itself(self):
        p = profiles.parse(_raw(_profile(name="Hordes \u65e5\u672c")))
        self.assertIn("Hordes \u65e5\u672c", profiles.to_json(p))

    def test_a_file_that_is_not_a_profile_is_refused(self):
        cases = {
            "UTF-8": b"\xff\xfe{",
            "not a profile": b"{not json",
            "not a riftstone-profile/1": _raw(_profile(schema="riftstone-graphics/1")),
            "no field 'extra'": _raw(_profile(extra=1)),
            "is an object": b"[1, 2]",
            "names 'Name' twice": b'{"schema": "riftstone-profile/1", "name": "a", "Name": "b"}',
            "names 'ENEMY_CAP' twice": _raw(_profile(features={"enemy_cap": True, "ENEMY_CAP": False})),
            "true or false": _raw(_profile(features={"enemy_cap": 1})),
            "not a feature's name": _raw(_profile(features={"../dinput8": True})),
            "not a section or key name": _raw(_profile(settings={"enemy_cap": {"a=b": {"slots": "64"}}})),
            "the value is a short text": _raw(_profile(settings={"enemy_cap": {"enemy_cap": {"slots": 64}}})),
            "name is empty": _raw(_profile(name="")),
            "control character": _raw(_profile(name="a\tb")),
            "longer than 80": _raw(_profile(name="x" * 81)),
            "nested too deep": b'{"schema": "riftstone-profile/1", "name": "a", "features": ' + b"[" * 100000,
        }
        for message, raw in cases.items():
            with self.subTest(message), self.assertRaisesRegex(RiftError, message):
                profiles.parse(raw)

    def test_a_section_key_with_spaces_around_it_is_refused(self):
        with self.assertRaisesRegex(RiftError, "not a section or key name"):
            profiles.parse(_raw(_profile(settings={"enemy_cap": {" enemy_cap": {"slots": "64"}}})))

    def test_limits(self):
        with self.assertRaisesRegex(RiftError, "at most"):
            profiles.parse(b" " * (profiles.MAX_BYTES + 1))
        many = {f"f{i}": True for i in range(profiles.MAX_FEATURES + 1)}
        with self.assertRaisesRegex(RiftError, "at most 128 features"):
            profiles.parse(_raw(_profile(features=many)))
        values = {f"k{i}": "1" for i in range(profiles.MAX_VALUES + 1)}
        with self.assertRaisesRegex(RiftError, "at most 4096 values"):
            profiles.parse(_raw(_profile(settings={"x": {"s": values}})))


class GameTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.game = Game(Path(self.tmp.name) / "game")
        self.game.root.mkdir()
        (self.game.root / "DDDA.exe").write_bytes(b"stub")
        (self.game.root / "nativePC" / "rom").mkdir(parents=True)
        (self.game.root / "riftstone_loader.ini").write_text(LOADER_INI, encoding="utf-8")
        on, off = plugins.plugins_dir(self.game), plugins.off_dir(self.game)
        off.mkdir(parents=True)
        (on / "enemy_cap.dll").write_bytes(b"MZ cap")
        (on / "enemy_cap.ini").write_text(CAP_INI, encoding="utf-8")
        (on / "save_backup.dll").write_bytes(b"MZ backup")
        (on / "save_backup.ini").write_text(BACKUP_INI, encoding="utf-8")
        (off / "free_sprint.dll").write_bytes(b"MZ sprint")
        (off / "free_sprint.ini").write_text(SPRINT_INI, encoding="utf-8")
        self.closed = mock.patch.object(install, "game_running", lambda g: False)
        self.closed.start()

    def tearDown(self):
        self.closed.stop()
        self.tmp.cleanup()

    def files(self) -> dict[str, bytes]:
        return {str(p.relative_to(self.game.root)): p.read_bytes() for p in self.game.root.rglob("*") if p.is_file()}

    def test_export_carries_the_features_and_only_checked_values(self):
        p = profiles.export(self.game, "Mine")
        self.assertEqual(p["made_with"], f"Riftstone {__version__}")
        self.assertEqual(p["features"], {"enemy_cap": True, "free_sprint": False, "save_backup": True})
        self.assertEqual(p["settings"], {
            "enemy_cap": {"enemy_cap": {"slots": "30", "record": "1"}},
            "free_sprint": {"sprint": {"Mode": "out_of_battle", "Who": "party"}},
            "save_backup": {"backup": {"Keep": "20"}},                       # Folder is a path: not carried
            "loader": {"loader": {"overlay": "1"}, "fps": {"max_fps": "165"}, "overlay": {"key": "Insert"}},
        })
        self.assertEqual(profiles.parse(profiles.to_json(p).encode("utf-8")), p)

    def test_export_leaves_out_a_value_its_check_refuses_and_a_repeated_key(self):
        ini = plugins.plugins_dir(self.game) / "enemy_cap.ini"
        ini.write_text("[enemy_cap]\nslots = 999\nrecord = 1\nrecord = 0\n", encoding="utf-8")
        self.assertEqual(profiles.export(self.game, "x")["settings"]["enemy_cap"], {"enemy_cap": {"record": "1"}})

    def test_a_plugin_in_both_folders_counts_where_it_runs(self):
        off = plugins.off_dir(self.game)
        (off / "enemy_cap.dll").write_bytes(b"MZ old cap")
        rows = profiles.installed(self.game)
        self.assertEqual([(s, on) for s, _, on in rows], [("enemy_cap", True), ("free_sprint", False),
                                                           ("save_backup", True)])

    def test_a_dry_run_plans_and_changes_nothing(self):
        before = self.files()
        p = profiles.parse(_raw(_profile(features={"free_sprint": True, "enemy_cap": True},
                                         settings={"free_sprint": {"sprint": {"Mode": "always"}},
                                                   "enemy_cap": {"enemy_cap": {"slots": "64"}}})))
        res = profiles.apply(self.game, p, dry_run=True)
        self.assertEqual(self.files(), before)
        self.assertEqual((res["switched"], res["set"], res["unchanged"], res["dry_run"]), (1, 2, 1, True))
        self.assertEqual(res["writes"], [("free_sprint.ini", "sprint", "Mode", "always"),
                                         ("enemy_cap.ini", "enemy_cap", "slots", "64")])

    def test_import_switches_then_writes_into_the_moved_file(self):
        p = profiles.parse(_raw(_profile(features={"free_sprint": True, "save_backup": False},
                                         settings={"free_sprint": {"SPRINT": {"mode": "always"}},
                                                   "loader": {"overlay": {"key": "f7"}}})))
        res = profiles.apply(self.game, p)
        on, off = plugins.plugins_dir(self.game), plugins.off_dir(self.game)
        self.assertEqual((res["switched"], res["set"], res["skipped"]), (2, 2, []))
        self.assertTrue((on / "free_sprint.dll").is_file() and not (off / "free_sprint.dll").exists())
        self.assertTrue((off / "save_backup.dll").is_file() and (off / "save_backup.ini").is_file())
        self.assertIn("Mode = always", (on / "free_sprint.ini").read_text(encoding="utf-8"))
        self.assertIn("key = F7", (self.game.root / "riftstone_loader.ini").read_text(encoding="utf-8"))
        again = profiles.apply(self.game, p)
        self.assertEqual((again["switched"], again["set"], again["unchanged"]), (0, 0, 4))

    def test_what_cannot_be_imported_is_left_out_and_named(self):
        before = self.files()
        p = profiles.parse(_raw(_profile(
            features={"portcrystals": True},
            settings={"enemy_cap": {"enemy_cap": {"slots": "99", "missing": "1"}},
                      "save_backup": {"backup": {"Folder": "C:\\Windows"}},
                      "lod_tuner": {"lod": {"Enabled": "1"}}})))
        res = profiles.apply(self.game, p)
        self.assertEqual(self.files(), before)
        self.assertEqual(res["set"], 0)
        joined = "\n".join(res["skipped"])
        for line in ("portcrystals: not installed here", "enemy_cap [enemy_cap] slots: slots",
                     "enemy_cap [enemy_cap] missing: not in enemy_cap.ini",
                     "save_backup [backup] Folder: not a setting a profile carries", "lod_tuner: not installed here"):
            self.assertIn(line, joined)

    def test_a_taken_move_refuses_the_whole_profile(self):
        off = plugins.off_dir(self.game)
        (off / "enemy_cap.ini").write_text("[enemy_cap]\nslots = 12\n", encoding="utf-8")
        before = self.files()
        p = profiles.parse(_raw(_profile(features={"free_sprint": True, "enemy_cap": False})))
        with self.assertRaisesRegex(RiftError, "already exists"):
            profiles.apply(self.game, p)
        self.assertEqual(self.files(), before)

    def test_a_running_game_refuses_an_import(self):
        before = self.files()
        with mock.patch.object(install, "game_running", lambda g: True), \
                self.assertRaisesRegex(RiftError, "running"):
            profiles.apply(self.game, profiles.parse(_raw(_profile())))
        self.assertEqual(self.files(), before)

    def test_a_utf16_file_is_written_as_utf16_and_a_broken_one_is_left_out(self):
        ini = plugins.plugins_dir(self.game) / "enemy_cap.ini"
        ini.write_bytes(b"\xff\xfe" + "[enemy_cap]\r\nslots = 30\r\n".encode("utf-16-le"))
        profiles.apply(self.game, profiles.parse(_raw(_profile())))
        self.assertEqual(ini.read_bytes(), b"\xff\xfe" + "[enemy_cap]\r\nslots = 64\r\n".encode("utf-16-le"))
        ini.write_bytes(b"\xff\xfe" + "[enemy_cap]\r\nslots = 30\r\n".encode("utf-16-le") + b"\x00\xd8")
        res = profiles.apply(self.game, profiles.parse(_raw(_profile())))
        self.assertIn("does not read as UTF-16", "\n".join(res["skipped"]))

    def test_the_command_line_exports_and_imports(self):
        from riftstone import cli

        out = Path(self.tmp.name) / "Hordes.json"
        game = ["--game", str(self.game.root)]
        self.assertEqual(cli.main(["profile", "export", str(out)] + game), 0)
        self.assertEqual(profiles.parse(out.read_bytes())["name"], "Hordes")
        doc = json.loads(out.read_text(encoding="utf-8"))
        doc["settings"]["enemy_cap"]["enemy_cap"]["slots"] = "48"
        out.write_text(json.dumps(doc), encoding="utf-8")
        before = self.files()
        self.assertEqual(cli.main(["profile", "import", str(out), "--dry-run"] + game), 0)
        self.assertEqual(self.files(), before)
        self.assertEqual(cli.main(["profile", "import", str(out)] + game), 0)
        self.assertIn("slots = 48", (plugins.plugins_dir(self.game) / "enemy_cap.ini").read_text(encoding="utf-8"))
        out.write_bytes(b"{}")
        self.assertEqual(cli.main(["profile", "import", str(out)] + game), 2)


if __name__ == "__main__":
    unittest.main()
