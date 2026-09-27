"""Dark Arisen mods go only through the loader's overlay: the game's own files stay as Steam installed them.
Removing the loader takes the mods out (they wait for it); installing it brings them back, and moves mods an
older Riftstone installed directly into the overlay."""
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401 (sys.path)
import world_fixture
from riftstone import encounter, install, loader, mod, world
from riftstone.errors import RiftError
from riftstone.index import Index

setUpModule, tearDownModule = helpers.module_env("RIFTSTONE_HOME")


def tree(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


class OverlayOnlyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        os.environ["RIFTSTONE_HOME"] = str(base / "home")
        self.game = world_fixture.make(base / "game")
        self.idx = Index(self.game)
        self.idx.refresh()
        w = world.load(self.game, self.idx)
        self.mod = mod.Mod.create(base / "mods" / "Goblins", "Goblins").root
        encounter.write(encounter.plan(self.game, self.idx, w, self.mod, 424, "em0100", 2, "0,0,0"), self.mod)
        self.native = tree(self.game.native)
        self.built = base / "built"
        self.built.mkdir()
        (self.built / "dinput8.dll").write_bytes(b"MZ " + loader.MARKER + b" RiftstoneLoaderVersion=0.3.1\0")
        # the stand-in game is closed, whatever else runs a DDDA.exe on this PC (other tests' stand-ins)
        closed = mock.patch.object(install, "game_running", return_value=False)
        closed.start()
        self.addCleanup(closed.stop)

    def tearDown(self):
        self.idx.close()
        self.tmp.cleanup()

    def put_loader(self):
        with mock.patch.object(loader, "built_loader", return_value=self.built), \
                mock.patch.object(install, "game_running", return_value=False):
            return loader.install_loader(self.game, self.idx)

    def test_no_loader_no_install(self):
        with self.assertRaises(RiftError) as e:
            install.apply(self.game, self.idx, [self.mod])
        self.assertIn("loader", str(e.exception))
        with self.assertRaises(RiftError):
            install.apply(self.game, self.idx, [self.mod], dry_run=True)
        self.assertEqual(tree(self.game.native), self.native)
        self.assertFalse(self.game.vanilla_dir.exists())
        self.assertEqual(install.apply(self.game, self.idx, []).written, [])      # nothing to write is fine

    def test_through_the_overlay_and_back(self):
        self.put_loader()
        rep = install.apply(self.game, self.idx, [self.mod])
        self.assertEqual(rep.mode, "overlay")
        self.assertTrue(rep.written)
        served = [self.game.overlay_dir / (w["archive"] + ".arc") for w in rep.written]
        self.assertTrue(all(p.is_file() for p in served))
        self.assertEqual(tree(self.game.native), self.native)                      # the game's files as they were
        with mock.patch.object(install, "game_running", return_value=False):
            r = loader.remove_loader(self.game, self.idx)
        self.assertEqual(r["mods_waiting"], ["Goblins"])
        self.assertFalse(any(p.is_file() for p in served))
        self.assertEqual(tree(self.game.native), self.native)                      # not written in there either
        state = install.load_state(self.game)
        self.assertEqual((state["archives"], state[loader.WAITING]), ({}, [str(self.mod)]))
        r = self.put_loader()                                                      # the mods come back
        self.assertEqual(r["mods_moved_to_overlay"], ["Goblins"])
        self.assertTrue(all(p.is_file() for p in served))
        self.assertNotIn(loader.WAITING, install.load_state(self.game))
        self.assertEqual(tree(self.game.native), self.native)

    def test_an_older_direct_install_moves_into_the_overlay(self):
        self.put_loader()
        rep = install.apply(self.game, self.idx, [self.mod])
        written = [w["archive"] for w in rep.written]
        modded = {a: (self.game.overlay_dir / (a + ".arc")).read_bytes() for a in written}
        state = install.load_state(self.game)

        def older_direct_install():
            """What an older Riftstone without the loader left: each archive replaced in nativePC, its original
            in riftstone\\vanilla, the state in direct mode."""
            st = json.loads(json.dumps(state))
            for a in written:
                live = self.game.arc_path(a)
                backup = self.game.vanilla_dir / live.relative_to(self.game.native)
                backup.parent.mkdir(parents=True, exist_ok=True)
                if not backup.is_file():
                    shutil.copyfile(live, backup)
                live.write_bytes(modded[a])
                (self.game.overlay_dir / (a + ".arc")).unlink(missing_ok=True)
                st["archives"][a]["vanilla_sha256"] = hashlib.sha256(backup.read_bytes()).hexdigest()
            st["mode"] = "direct"
            (self.game.state_dir / "state.json").write_text(json.dumps(st), encoding="utf-8")
            (self.game.root / "dinput8.dll").unlink(missing_ok=True)

        older_direct_install()
        self.assertNotEqual(tree(self.game.native), self.native)
        with mock.patch.object(install, "game_running", return_value=False):   # (a stand-in DDDA.exe may be up)
            install.restore_all(self.game)                                        # still restores, loader or not
        self.assertEqual(tree(self.game.native), self.native)
        older_direct_install()
        r = self.put_loader()                                                      # moves them into the overlay
        self.assertEqual(r["mods_moved_to_overlay"], ["Goblins"])
        self.assertEqual({a: (self.game.overlay_dir / (a + ".arc")).read_bytes() for a in written}, modded)
        self.assertEqual(tree(self.game.native), self.native)                      # the originals are back
        self.assertEqual(install.load_state(self.game)["mode"], "overlay")


if __name__ == "__main__":
    unittest.main()
