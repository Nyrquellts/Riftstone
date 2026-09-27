"""The resource index (index.py) on the stand-in game of tests/world_fixture.py."""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401 (sys.path)
import world_fixture
from riftstone import cli, typemap
from riftstone.index import Index

setUpModule, tearDownModule = helpers.module_env("RIFTSTONE_HOME")


class SearchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        cls.old_home = os.environ.get("RIFTSTONE_HOME")
        os.environ["RIFTSTONE_HOME"] = str(base / "home")
        cls.game = world_fixture.make(base / "game", extras=True, cells=True)
        cls.idx = Index(cls.game)
        cls.idx.refresh()

    @classmethod
    def tearDownClass(cls):
        cls.idx.close()
        if cls.old_home is None:
            os.environ.pop("RIFTSTONE_HOME", None)
        else:
            os.environ["RIFTSTONE_HOME"] = cls.old_home
        cls.tmp.cleanup()

    def names(self, text, type_id=None):
        return sorted(r["name"].decode("latin-1") for r in self.idx.search(text, type_id))

    def test_a_path_matches_the_names_it_spells(self):
        """The engine's separator '\\' is also the LIKE escape: 'scr\\st424' matched 'scrst424' and none of
        the stage's names, so a path find printed (scr/st424/etc/st424_e.gpl) found nothing."""
        lists = ["scr\\st424\\etc\\st424_e", "scr\\st424\\etc\\st424_e_dlc01"]
        self.assertEqual(self.names("st424_e", typemap.BY_EXT["gpl"]), lists)
        for typed in ("scr/st424/etc/st424_e.gpl", "scr\\st424\\etc\\st424_e.gpl", "SCR/ST424/ETC/st424_e.gpl"):
            self.assertEqual(self.names(typed), lists, typed)
        stage = self.names("scr/st424")
        self.assertIn("scr\\st424\\etc\\st424_05m02n_e06", stage)
        self.assertEqual(len(stage), len(self.names("st424")))          # every name of the stage is under scr\st424
        for literal in ("st424%e", "st424_e_dlc0_", "scr/%", "etc\\\\st424"):  # wildcards and doubled separators stay literal
            self.assertEqual(self.names(literal), [], literal)

    def test_find_takes_the_path_it_prints(self):
        import gc

        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["find", "scr/st424/etc/st424_e.gpl", "--game", str(self.game.root)])
        gc.collect()        # find leaves its index open; its connection (a cycle with its statement cache) goes here
        self.assertEqual(code, 0)
        self.assertIn("scr/st424/etc/st424_e.gpl", out.getvalue())


if __name__ == "__main__":
    unittest.main()
