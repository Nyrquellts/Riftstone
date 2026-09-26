"""The mods folder the command line shares with Studio: a plain name means a mod there, a path means that
folder (as it always did); riftstone new makes a mod there, riftstone mods lists it."""
import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401 (sys.path)
from riftstone import cli, install, mod, studio
from riftstone.errors import RiftError
from riftstone.game import Game


class ModsFolderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.folder = self.base / "mods"
        self.saved = {k: os.environ.get(k) for k in (*mod.MODS_ENV, "RIFTSTONE_HOME")}
        os.environ.pop("RIFTSTONE_WORKSPACE", None)
        os.environ["RIFTSTONE_MODS"] = str(self.folder)
        os.environ["RIFTSTONE_HOME"] = str(self.base / "home")
        mod.Mod.create(self.folder / "HydraStormMK", "HydraStorm MK")
        mod.Mod.create(self.folder / "Harder Goblins")
        # a stand-in Dark Arisen: DDDA.exe and nativePC\rom, nothing else
        self.game = self.base / "game"
        (self.game / "nativePC" / "rom").mkdir(parents=True)
        (self.game / "DDDA.exe").write_bytes(b"stub")

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def run_cli(self, *argv) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(list(argv))
        return code, out.getvalue()

    def test_the_folder_follows_the_environment_and_studio_uses_it(self):
        self.assertEqual(mod.mods_folder(), self.folder)
        self.assertEqual(mod.mods_folders(), [self.folder])          # the environment's folder alone
        self.assertEqual(studio.default_workspace(None), self.folder)
        os.environ.pop("RIFTSTONE_MODS")
        os.environ["RIFTSTONE_WORKSPACE"] = str(self.base / "older")    # Studio's first name for it
        self.assertEqual(mod.mods_folder(), self.base / "older")
        os.environ.pop("RIFTSTONE_WORKSPACE")
        own = Path(mod.__file__).resolve().parents[2] / "mods"
        has = own.is_dir() and any((d / mod.MOD_FILE).is_file() for d in own.iterdir() if d.is_dir())
        self.assertEqual(mod.mods_folder(None), own if has else Path.home() / "Documents" / "Riftstone" / "mods")

    def test_bare_names_and_paths(self):
        for name in ("Harder Goblins", "x", "a.b (c)", " padded "):
            self.assertTrue(mod.is_bare_name(name), name)
        for path in ("mods/x", "mods\\x", "C:x", ".", "..", "", "  ", "/x"):
            self.assertFalse(mod.is_bare_name(path), path)

    def test_a_mod_is_found_by_folder_name_display_name_or_path(self):
        folders = [self.folder]
        self.assertEqual(mod.locate("Harder Goblins", folders), self.folder / "Harder Goblins")
        self.assertEqual(mod.locate("harder  GOBLINS", folders), self.folder / "Harder Goblins")   # case, spaces
        self.assertEqual(mod.locate("HydraStorm MK", folders), self.folder / "HydraStormMK")       # its own name
        self.assertEqual(mod.locate(str(self.folder / "HydraStormMK"), folders), self.folder / "HydraStormMK")
        self.assertEqual(mod.locate(str(self.base / "elsewhere"), folders), self.base / "elsewhere")  # a path: as given
        self.assertEqual([p.name for p in mod.list_mods(self.folder)], ["Harder Goblins", "HydraStormMK"])
        self.assertEqual(mod.list_mods(self.base / "missing"), [])

    def test_a_mod_folder_right_here_still_wins(self):
        cwd = os.getcwd()
        os.chdir(self.base)
        try:
            mod.Mod.create(self.base / "Harder Goblins")                 # ./Harder Goblins, as before the mods folder
            self.assertEqual(mod.locate("Harder Goblins", [self.folder]), Path("Harder Goblins"))
        finally:
            os.chdir(cwd)

    def test_a_missing_mod_names_the_ones_that_exist(self):
        with self.assertRaises(RiftError) as e:
            mod.locate("Harder Goblin", [self.folder])
        self.assertIn('"Harder Goblins"', str(e.exception))
        self.assertIn('riftstone new "Harder Goblin"', str(e.exception))

    def test_a_command_that_makes_its_mod_makes_it_in_the_mods_folder(self):
        self.assertEqual(mod.locate("Solo Access", [self.folder], self.folder), self.folder / "Solo Access")
        self.assertEqual(mod.locate("harder goblins", [self.folder], self.folder), self.folder / "Harder Goblins")
        for bad in ("...", "CON", "a*b", "x" * 65):
            with self.assertRaises(RiftError, msg=bad):
                mod.locate(bad, [self.folder], self.folder)

    def test_command_arguments_resolve_before_the_command_runs(self):
        ns = argparse.Namespace(cmd="extract", mod="harder goblins", game=None)
        cli._mod_names(ns)
        self.assertEqual(ns.mod, str(self.folder / "Harder Goblins"))
        ns = argparse.Namespace(cmd="install", mods=["HydraStorm MK", str(self.base / "x")], game=None)
        cli._mod_names(ns)
        self.assertEqual(ns.mods, [str(self.folder / "HydraStormMK"), str(self.base / "x")])
        ns = argparse.Namespace(cmd="uninstall", mods=["Gone Mod"], game=None)
        cli._mod_names(ns)                                   # a mod whose folder is gone uninstalls by its path
        self.assertEqual(ns.mods, ["Gone Mod"])
        for cmd in ("import", "ddo", "playtest"):            # these make the mod they are given
            ns = argparse.Namespace(cmd=cmd, mod="Brand New", game=str(self.game))
            cli._mod_names(ns)
            self.assertEqual(ns.mod, str(self.folder / "Brand New"), cmd)
        with self.assertRaises(RiftError):
            cli._mod_names(argparse.Namespace(cmd="encounter", mod="Brand New", game=None))

    def test_new_makes_a_plain_name_in_the_mods_folder_and_here_where_asked(self):
        code, _ = self.run_cli("new", "Rift Tonic")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads((self.folder / "Rift Tonic" / mod.MOD_FILE).read_text())["name"], "Rift Tonic")
        self.assertEqual(self.run_cli("new", "rift tonic")[0], 2)                 # that name is taken
        self.assertEqual(self.run_cli("new", "HydraStorm MK")[0], 2)              # so is a mod's own name
        self.assertEqual(self.run_cli("new", "...", "--name", "Fine")[0], 2)     # not a folder name Windows keeps
        cwd = os.getcwd()
        os.chdir(self.base)
        try:
            self.assertEqual(self.run_cli("new", "Local", "--here")[0], 0)
            self.assertEqual(self.run_cli("new", "sub/Nested")[0], 0)            # a path stays a path
        finally:
            os.chdir(cwd)
        self.assertTrue((self.base / "Local" / mod.MOD_FILE).is_file())
        self.assertTrue((self.base / "sub" / "Nested" / mod.MOD_FILE).is_file())
        self.assertFalse((self.folder / "Local").exists())

    def test_mods_lists_the_folder_and_what_is_installed(self):
        state = {"schema": install.STATE_SCHEMA, "archives": {},
                 "mods": [{"path": str(self.folder / "Harder Goblins")}, {"path": str(self.base / "Old Mod")}]}
        g = Game(self.game)
        g.state_dir.mkdir(parents=True)
        (g.state_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
        code, text = self.run_cli("mods", "--game", str(self.game))
        self.assertEqual(code, 0)
        lines = text.splitlines()
        self.assertIn(str(self.folder), lines[0])
        goblins = next(line for line in lines if "Harder Goblins" in line)
        self.assertIn("[installed]", goblins)
        self.assertIn("[not installed]", next(line for line in lines if "HydraStorm MK" in line))
        self.assertIn("kept elsewhere", text)
        self.assertIn("Old Mod", text)                                          # installed, folder gone

    def test_a_command_takes_a_mod_by_name(self):
        code, _ = self.run_cli("build", "harder goblins", "--game", str(self.game), "-o", str(self.base / "out"))
        self.assertEqual(code, 0)
        self.assertEqual(self.run_cli("build", str(self.folder / "HydraStormMK"), "--game", str(self.game),
                                      "-o", str(self.base / "out"))[0], 0)          # a path, as always
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code, text = self.run_cli("build", "Nope", "--game", str(self.game))
        self.assertEqual(code, 2)
        self.assertIn('no mod called "Nope"', text + err.getvalue())


if __name__ == "__main__":
    unittest.main()
