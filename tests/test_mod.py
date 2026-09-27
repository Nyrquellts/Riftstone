"""Mod projects (mod.py): a riftstone-mod.json of any shape is read or refused with a reason, and a new mod
is made only where a folder can be."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import SRC  # noqa: F401 -- puts src on sys.path

from riftstone import cli, mod, studio
from riftstone.errors import RiftError


class ModFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"RIFTSTONE_HOME": str(self.base / "home")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def write(self, root: Path, text: str) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        (root / mod.MOD_FILE).write_text(text, encoding="utf-8")
        return root

    def test_a_mod_file_of_any_shape_is_read_or_refused(self):
        """A riftstone-mod.json that is no object ([], "x", null, 5) failed on .get, and a priority int() refuses
        failed with ValueError, TypeError or OverflowError: every caller (install, build, package, Studio's
        mod list) expects a RiftError."""
        root = self.base / "M"
        head = f'{{"schema": "{mod.SCHEMA}", '
        for text in ("[]", '"x"', "null", "5", head + '"priority": "high"}', head + '"priority": ""}',
                     head + '"priority": null}', head + '"priority": [1]}', head + '"priority": 1e400}',
                     head + '"priority": NaN}', head + '"priority": {}}'):
            self.write(root, text)
            with self.assertRaisesRegex(RiftError, "riftstone-mod.json", msg=text):
                mod.Mod.load(root)
        for text, want in ((head + '"priority": 7}', 7), (head + '"priority": -2}', -2), (head + '"name": "N"}', 0)):
            self.write(root, text)
            self.assertEqual(mod.Mod.load(root).priority, want, text)

    def test_studio_lists_a_broken_mod_with_its_reason(self):
        """Studio's state (every refresh) answered 500 while one mod's riftstone-mod.json was an array."""
        ws = self.base / "mods"
        mod.Mod.create(ws / "Good", "Good")
        self.write(ws / "Broken", "[]")
        self.write(ws / "Odd Priority", json.dumps({"schema": mod.SCHEMA, "priority": "high"}))
        s = studio.Studio(None, ws)
        with mock.patch.object(studio, "find_game", side_effect=RiftError("not here")):
            st = s.api("GET", "state", {}, {})
        rows = {m["name"]: m for m in st["mods"]}
        self.assertIsNone(rows["Good"]["error"])
        self.assertIn("riftstone-mod.json", rows["Broken"]["error"])
        self.assertIn("priority", rows["Odd Priority"]["error"])

    def test_a_mod_is_made_only_where_a_folder_can_be(self):
        """`riftstone new <an existing file>` raised FileExistsError (a traceback, not a refusal)."""
        f = self.base / "notes.txt"
        f.write_text("mine", encoding="utf-8")
        for root in (f, f / "Sub"):
            with self.assertRaisesRegex(RiftError, "notes.txt", msg=root):
                mod.Mod.create(root, "M")
        self.assertEqual(f.read_text(encoding="utf-8"), "mine")
        folder = self.base / "Half"
        folder.mkdir()
        (folder / "archives").write_text("a file where a folder goes", encoding="utf-8")
        with self.assertRaisesRegex(RiftError, "archives"):
            mod.Mod.create(folder, "Half")
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["archives"])     # nothing made
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            self.assertEqual(cli.main(["new", str(f)]), 2)
        self.assertNotIn("Traceback", out.getvalue())


if __name__ == "__main__":
    unittest.main()
