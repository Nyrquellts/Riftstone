"""A mod's loose/ files: resources no archive holds, installed into the loader's overlay and taken out again,
on the stand-in game of world_fixture."""
import os
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401 (sys.path)
import world_fixture
from riftstone import install, loader, mod
from riftstone.errors import BuildError, RiftError
from riftstone.index import Index


class LooseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        cls.home = os.environ.get("RIFTSTONE_HOME")
        os.environ["RIFTSTONE_HOME"] = str(base / "home")
        cls.game = world_fixture.make(base / "game")
        (cls.game.root / "dinput8.dll").write_bytes(b"MZ-fake-loader-" + loader.MARKER + b"-end")
        (cls.game.root / "riftstone_loader.ini").write_text("[loader]\n", encoding="ascii")
        cls.idx = Index(cls.game)
        cls.idx.refresh()
        cls.base = base

    @classmethod
    def tearDownClass(cls):
        cls.idx.close()
        cls.tmp.cleanup()
        if cls.home is None:
            os.environ.pop("RIFTSTONE_HOME", None)
        else:
            os.environ["RIFTSTONE_HOME"] = cls.home

    def new_mod(self, name: str, files: dict[str, bytes]) -> Path:
        root = mod.Mod.create(self.base / "mods" / name, name).root
        for rel, data in files.items():
            p = root / "loose" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        return root

    def test_collect(self):
        root = self.new_mod("Collect", {"compat/job09/m0009_cs01.lmt": b"LMT", "compat/test.skills": b"skill a\nend\n",
                                        "compat/job09/cs01_lv01.ocl": b"OCL"})
        got = mod.collect_loose(mod.Mod.load(root))
        self.assertEqual(sorted(got), ["compat/job09/cs01_lv01.ocl", "compat/job09/m0009_cs01.lmt", "compat/test.skills"])
        self.assertEqual(got["compat/job09/m0009_cs01.lmt"], b"LMT")

    def test_refusals(self):
        for name, files in (("BadExt", {"compat/x.notatype": b"x"}), ("DeepSkills", {"compat/sub/a.skills": b"x"}),
                            ("OutsideSkills", {"a.skills": b"x"}), ("TextSkills", {"compat/a.skills": "é".encode()}),
                            ("Archive", {"rom/game_main.arc": b"ARC"})):
            with self.subTest(name), self.assertRaises((BuildError, RiftError)):
                mod.collect_loose(mod.Mod.load(self.new_mod(name, files)))

    def test_install_update_remove(self):
        root = self.new_mod("Install", {"compat/job09/a.lmt": b"one", "compat/prog.skills": b"skill a\nend\n"})
        rep = install.apply(self.game, self.idx, [root])
        self.assertEqual(sorted(rep.loose_written), ["compat/job09/a.lmt", "compat/prog.skills"])
        target = self.game.overlay_dir / "compat" / "job09" / "a.lmt"
        self.assertEqual(target.read_bytes(), b"one")
        self.assertEqual(install.status(self.game)["loose"], ["compat/job09/a.lmt", "compat/prog.skills"])
        self.assertEqual(install.status(self.game)["drift"], [])
        again = install.apply(self.game, self.idx, [root])
        self.assertEqual(again.loose_written, [])
        (root / "loose" / "compat" / "job09" / "a.lmt").write_bytes(b"two")
        self.assertEqual(install.apply(self.game, self.idx, [root]).loose_written, ["compat/job09/a.lmt"])
        self.assertEqual(target.read_bytes(), b"two")
        gone = install.apply(self.game, self.idx, [])
        self.assertEqual(sorted(gone.loose_removed), ["compat/job09/a.lmt", "compat/prog.skills"])
        self.assertFalse(target.exists())
        self.assertFalse((self.game.overlay_dir / "compat").exists(), "empty folders go too")
        self.assertNotIn("loose", install.load_state(self.game))

    def test_restore_takes_them_out(self):
        root = self.new_mod("Restore", {"compat/r.lmt": b"r"})
        install.apply(self.game, self.idx, [root])
        done = install.restore_all(self.game)
        self.assertIn("loose/compat/r.lmt", done)
        self.assertFalse((self.game.overlay_dir / "compat" / "r.lmt").exists())

    def test_a_resource_an_archive_holds_is_refused(self):
        from riftstone import fsmap

        name, tid = self.idx.db.execute("SELECT name, type FROM res LIMIT 1").fetchone()
        root = self.new_mod("Held", {fsmap.encode_name(name, tid): b"x"})
        with self.assertRaises(BuildError):
            install.apply(self.game, self.idx, [root], dry_run=True)

    def test_someone_elses_file_is_kept(self):
        foreign = self.game.overlay_dir / "compat" / "theirs.lmt"
        foreign.parent.mkdir(parents=True, exist_ok=True)
        foreign.write_bytes(b"not ours")
        root = self.new_mod("Foreign", {"compat/theirs.lmt": b"ours"})
        with self.assertRaises(RiftError):
            install.apply(self.game, self.idx, [root])
        self.assertEqual(foreign.read_bytes(), b"not ours")
        foreign.unlink()

    def test_a_changed_file_is_not_removed(self):
        root = self.new_mod("Changed", {"compat/changed.lmt": b"mine"})
        install.apply(self.game, self.idx, [root])
        target = self.game.overlay_dir / "compat" / "changed.lmt"
        target.write_bytes(b"edited by hand")
        self.assertIn("loose/compat/changed.lmt", install.status(self.game)["drift"])
        with self.assertRaises(RiftError):
            install.apply(self.game, self.idx, [])
        self.assertEqual(target.read_bytes(), b"edited by hand")
        target.write_bytes(b"mine")
        install.apply(self.game, self.idx, [])

    def test_direct_mode_needs_the_loader(self):
        dll = self.game.root / "dinput8.dll"
        saved = dll.read_bytes()
        dll.unlink()
        try:
            root = self.new_mod("NoLoader", {"compat/n.lmt": b"n"})
            with self.assertRaises(RiftError):
                install.apply(self.game, self.idx, [root])
        finally:
            dll.write_bytes(saved)


if __name__ == "__main__":
    unittest.main()
