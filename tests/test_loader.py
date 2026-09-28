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


if __name__ == "__main__":
    unittest.main()
