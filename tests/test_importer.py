"""riftstone import (importer.py): changed archives become a mod of only what changed, on a stand-in game."""
import os
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401 (sys.path)
from riftstone import arc, gmd, importer, mod, typemap
from riftstone.game import Game
from riftstone.index import Index

setUpModule, tearDownModule = helpers.module_env("RIFTSTONE_HOME")

GMD = typemap.BY_EXT["gmd"]


def text(line: str) -> bytes:
    return gmd.build(gmd.Gmd(0, "TextWeb", [gmd.Message(line, "A")]))


def archive(entries) -> bytes:
    return arc.Archive([arc.Entry.from_data(n, GMD, data) for n, data in entries]).build()


class ImportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.old_home = os.environ.get("RIFTSTONE_HOME")
        os.environ["RIFTSTONE_HOME"] = str(self.base / "home")

    def tearDown(self):
        if self.old_home is None:
            os.environ.pop("RIFTSTONE_HOME", None)
        else:
            os.environ["RIFTSTONE_HOME"] = self.old_home
        self.tmp.cleanup()

    def test_a_change_one_archive_makes_and_another_adds_reaches_both(self):
        """Archive a (x's only holder) changes x and archive b (which never had it) adds x with the same bytes:
        x went to files/ alone, which the build applies to x's holders only, so b never got it."""
        root = self.base / "game"
        ui = root / "nativePC" / "rom" / "ui"
        ui.mkdir(parents=True)
        (root / "DDDA.exe").write_bytes(b"stub")
        (ui / "a.arc").write_bytes(archive([(b"ui\\x", text("old"))]))
        (ui / "b.arc").write_bytes(archive([(b"ui\\other", text("b"))]))
        out = self.base / "out" / "rom" / "ui"
        out.mkdir(parents=True)
        (out / "a.arc").write_bytes(archive([(b"ui\\x", text("new"))]))
        (out / "b.arc").write_bytes(archive([(b"ui\\other", text("b")), (b"ui\\x", text("new"))]))
        game = Game(root)
        idx = Index(game)
        try:
            idx.refresh()
            m = mod.Mod.create(self.base / "mod", "Imported")
            rep = importer.import_archives(game, idx, [self.base / "out"], m.root)
            self.assertEqual((rep.changed, rep.added, rep.unchanged), (1, 1, 1))
            self.assertEqual((rep.to_files, rep.to_archives), (0, 2))
            self.assertEqual(list((m.root / "files").rglob("x.gmd*")), [])
            for a in ("a", "b"):
                self.assertTrue((m.root / "archives" / "rom" / "ui" / f"{a}.arc" / "ui" / "x.gmd.yaml").is_file(), a)
            p = mod.plan(game, idx, [mod.Mod.load(m.root)])
            mod.check_plan(p)
            for a in ("rom/ui/a", "rom/ui/b"):
                built = arc.Archive.parse(mod.build_archive(game, a, p.archives[a]).data)
                self.assertEqual(gmd.parse(built.find(b"ui\\x", GMD).data()).messages[0].text, "new", a)
        finally:
            idx.close()


if __name__ == "__main__":
    unittest.main()
