"""Native plugin management in a stand-in game folder (no real game touched)."""
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from helpers import SRC  # noqa: F401 -- puts src on sys.path

from riftstone import loader
from riftstone.errors import RiftError
from riftstone.game import Game


class PluginTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.game = Game(base / "game")
        self.game.root.mkdir()
        (self.game.root / "DDDA.exe").write_bytes(b"stub")
        self.build = base / "build"
        self.build.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_plugin_and_its_settings_are_copied(self):
        (self.build / "lod_tuner.asi").write_bytes(b"MZ plugin")
        (self.build / "lod_tuner.ini").write_text("[lod]\nPopPixels = 24\n", encoding="utf-8")
        self.assertEqual(loader.add_plugin(self.game, self.build / "lod_tuner.asi"), "lod_tuner.asi")
        d = loader.plugins_dir(self.game)
        self.assertEqual((d / "lod_tuner.asi").read_bytes(), b"MZ plugin")
        self.assertEqual((d / "lod_tuner.ini").read_text(encoding="utf-8"), "[lod]\nPopPixels = 24\n")
        self.assertEqual(loader.list_plugins(self.game), ["lod_tuner.asi"])

    def test_the_owners_settings_are_kept_on_update(self):
        (self.build / "lod_tuner.asi").write_bytes(b"v1")
        (self.build / "lod_tuner.ini").write_text("[lod]\nPopPixels = 24\n", encoding="utf-8")
        loader.add_plugin(self.game, self.build / "lod_tuner.asi")
        d = loader.plugins_dir(self.game)
        (d / "lod_tuner.ini").write_text("[lod]\nPopPixels = 8\n", encoding="utf-8")
        (self.build / "lod_tuner.asi").write_bytes(b"v2")
        loader.add_plugin(self.game, self.build / "lod_tuner.asi")
        self.assertEqual((d / "lod_tuner.asi").read_bytes(), b"v2")
        self.assertEqual((d / "lod_tuner.ini").read_text(encoding="utf-8"), "[lod]\nPopPixels = 8\n")

    def test_an_update_brings_new_settings_and_keeps_the_owners(self):
        # 1.0.6's lod_tuner added FadeSeconds; an update through `loader plugin add` kept the owner's file as it was,
        # so the new setting never showed (Studio's update merged).  Now both merge.
        (self.build / "lod_tuner.asi").write_bytes(b"v1")
        (self.build / "lod_tuner.ini").write_bytes(b"[lod]\r\nPopPixels = 24\r\nFarthest = high\r\n")
        loader.add_plugin(self.game, self.build / "lod_tuner.asi")
        d = loader.plugins_dir(self.game)
        (d / "lod_tuner.ini").write_bytes(b"[lod]\r\nPopPixels = 8\r\nFarthest = lod\r\n")
        (self.build / "lod_tuner.ini").write_bytes(
            b"[lod]\r\nPopPixels = 24\r\nFarthest = high\r\n\r\n; seconds per swap\r\nFadeSeconds = 0.75\r\n")
        loader.add_plugin(self.game, self.build / "lod_tuner.asi")
        self.assertEqual((d / "lod_tuner.ini").read_bytes(),
                         b"[lod]\r\nPopPixels = 8\r\nFarthest = lod\r\n\r\n; seconds per swap\r\nFadeSeconds = 0.75\r\n")
        self.assertEqual([p.name for p in d.iterdir() if p.name.endswith(".riftstone-tmp")], [])

    def test_a_section_the_template_has_is_not_added_again(self):
        # fuzz finding plugin_update-invariant-075e94aa6562: a section the template declares without keys was added
        # again at the end of every update (the second update changed the file).
        template = "[a]\n[a]\n[b]\nn = 2\n"
        once = loader.merge_ini(template, "k = 1\nK = 5\nmine = yes\n[c]\nx = 1\n")
        self.assertEqual(once, "[a]\n[a]\n[b]\nn = 2\n\n[c]\nx = 1\n")
        self.assertEqual(loader.merge_ini(template, once), once)
        self.assertEqual(loader.merge_ini("[a]\n; none yet\n[b]\nn = 2\n", "[a]\nmine = 1\n"),
                         "[a]\n; none yet\nmine = 1\n[b]\nn = 2\n")

    def test_a_templates_line_stays_as_written_when_its_value_is_kept(self):
        # fuzz finding plugin_update-invariant-113ce00dfc75: a template line not spelled "key = value" was copied as
        # it is the first time and respelled the next, since the owner then "had" the key.
        template = "[a]\nk=1\nj =2\n"
        once = loader.merge_ini(template, "[a]\nj = 5\n")
        self.assertEqual(once, "[a]\nk=1\nj = 5\n")
        self.assertEqual(loader.merge_ini(template, once), once)
        self.assertEqual(loader.merge_ini(template, "[a]\nk = 1\n"), template)

    def test_only_the_first_of_a_repeated_key_takes_the_owners_value(self):
        # fuzz finding plugin_update-invariant-9967a95d674a: a key the template gives twice in a section (Windows
        # reads the first) took the owner's value at the second the next time.  The same for a repeated section.
        template = "[lod]\nPopPixels = 24\nFarthest = high\nPopPixels = 1.0\n"
        once = loader.merge_ini(template, "[lod]\nFarthest = lod\n")
        self.assertEqual(once, "[lod]\nPopPixels = 24\nFarthest = lod\nPopPixels = 1.0\n")
        self.assertEqual(loader.merge_ini(template, once), once)
        self.assertEqual(loader.merge_ini("[a]\nk = 1\n[a]\nk = 2\n", "[a]\nk = 9\n"), "[a]\nk = 9\n[a]\nk = 2\n")

    def test_an_empty_ini_of_the_builds_arrives(self):
        # fuzz crash in plugin_update (the merge's first version): an empty build ini and no owner's file wrote nothing.
        (self.build / "p.asi").write_bytes(b"MZ")
        (self.build / "p.ini").write_bytes(b"")
        loader.add_plugin(self.game, self.build / "p.asi")
        self.assertEqual((loader.plugins_dir(self.game) / "p.ini").read_bytes(), b"")

    def test_a_section_with_no_name_is_merged_like_the_others(self):
        # fuzz finding plugin_update-invariant-c73371ef76bc: keys under "[]" were copied as they were and then added
        # again from the owner's file at every update.
        once = loader.merge_ini("[]\nPopPixels = 24\n", "[]\nPopPixels = 8\n[lod]\nx = 1\n")
        self.assertEqual(once, "[]\nPopPixels = 8\n\n[lod]\nx = 1\n")
        self.assertEqual(loader.merge_ini("[]\nPopPixels = 24\n", once), once)

    def test_a_default_the_owners_code_page_lacks_arrives_as_windows_writes_it(self):
        # fuzz finding plugin_update-invariant-8d1bda0979fd (the target's check, not the merge): the owner's file is
        # in the code page, so a new default it cannot hold is written as '?', as WritePrivateProfileStringA would.
        (self.build / "p.asi").write_bytes(b"MZ")
        (self.build / "p.ini").write_bytes(b"\xff\xfe" + "[backup]\r\nFolder = D:\\\u65e5\u672c\r\n".encode("utf-16-le"))
        d = loader.plugins_dir(self.game)
        d.mkdir(parents=True)
        (d / "p.ini").write_bytes(b"[lod]\r\nPopPixels = 8\r\n")
        loader.add_plugin(self.game, self.build / "p.asi")
        self.assertEqual((d / "p.ini").read_bytes(), b"[backup]\r\nFolder = D:\\??\r\n\r\n[lod]\r\nPopPixels = 8\r\n")

    def test_built_loader_skips_an_unreadable_dll(self):
        # A dinput8.dll that is present but momentarily unreadable -- locked by a build in flight or an AV scan --
        # must not crash callers such as Studio's API (fuzz finding studio-crash-c1770614807c: PermissionError out
        # of built_loader).  It is treated as not-ours and the clean "not built" error is raised instead.
        cand = self.build / "out"
        cand.mkdir()
        (cand / "dinput8.dll").write_bytes(loader.MARKER + b" the rest of the dll")
        with mock.patch.object(loader, "_candidates", return_value=[cand]):
            with mock.patch.object(Path, "read_bytes", side_effect=PermissionError(13, "locked")):
                with self.assertRaises(RiftError):
                    loader.built_loader()
            self.assertEqual(loader.built_loader(), cand)   # readable again: the same candidate is found

    def test_a_plugin_without_settings(self):
        (self.build / "enemy_skins.asi").write_bytes(b"MZ")
        loader.add_plugin(self.game, self.build / "enemy_skins.asi")
        self.assertFalse((loader.plugins_dir(self.game) / "enemy_skins.ini").exists())

    def test_only_plugins_are_accepted(self):
        (self.build / "lod_tuner.ini").write_text("[lod]\n", encoding="utf-8")
        with self.assertRaises(RiftError):
            loader.add_plugin(self.game, self.build / "lod_tuner.ini")
        with self.assertRaises(RiftError):
            loader.add_plugin(self.game, self.build / "missing.asi")

    def test_remove_names_a_plugin_only(self):
        (self.build / "lod_tuner.asi").write_bytes(b"MZ")
        loader.add_plugin(self.game, self.build / "lod_tuner.asi")
        with self.assertRaises(RiftError):
            loader.remove_plugin(self.game, "..\\DDDA.exe")
        loader.remove_plugin(self.game, "lod_tuner.asi")
        self.assertEqual(loader.list_plugins(self.game), [])
        self.assertTrue((self.game.root / "DDDA.exe").is_file())

    def test_the_panel_key_default_is_insert_and_an_old_f10_default_moves(self):
        """1.0.3: the panel key was F10 (a system key in the game's window; a player's F10 only hid the HUD).  A
        settings file that still holds it takes Insert when the loader updates; any other key stays."""
        ini = self.game.root / "riftstone_loader.ini"
        self.assertEqual(loader.panel_key(self.game), "Insert")                       # no settings file yet
        ini.write_text("[fps]\nmax_fps = 165\n[overlay]\nkey = F10\nposition = bottom-left\n", encoding="utf-8")
        self.assertEqual(loader.panel_key(self.game), "F10")
        loader._write_ini(self.game, "")
        values = loader._ini_values(ini.read_text(encoding="utf-8"))
        self.assertEqual(values["overlay"]["key"], "Insert")
        self.assertEqual((values["overlay"]["position"], values["fps"]["max_fps"]), ("bottom-left", "165"))   # kept
        self.assertEqual(loader.panel_key(self.game), "Insert")
        ini.write_text("[overlay]\nkey = F7\n", encoding="utf-8")                     # the owner's own choice stays
        loader._write_ini(self.game, "")
        self.assertEqual(loader.panel_key(self.game), "F7")
        ini.write_text("[overlay]\nkey = f10\n", encoding="utf-8")                    # any spelling of the old default
        loader._write_ini(self.game, "")
        self.assertEqual(loader.panel_key(self.game), "Insert")

    def test_the_shipped_settings_name_the_new_key_and_the_banner(self):
        text = (Path(__file__).resolve().parents[1] / "native" / "loader" / "riftstone_loader.ini").read_text(
            encoding="utf-8")
        values = loader._ini_values(text)["overlay"]
        self.assertEqual((values["key"], values["banner"], values["banner_seconds"]), ("Insert", "1", "12"))


if __name__ == "__main__":
    unittest.main()
