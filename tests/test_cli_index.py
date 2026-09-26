"""The command line closes every resource index a command opened, whether the command succeeds or is refused
(an index is an open SQLite database: left open it leaks a handle in anything that runs commands in-process)."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401 (sys.path)
import world_fixture
from riftstone import cli, index


class IndexClosedTest(unittest.TestCase):
    def test_commands_close_what_they_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = world_fixture.make(Path(tmp) / "game")
            opened = []
            real = index.Index

            class Tracked(real):
                def __init__(self, *a, **k):
                    super().__init__(*a, **k)
                    self.closed = False
                    opened.append(self)

                def close(self):
                    self.closed = True
                    super().close()

            with mock.patch.dict(os.environ, {"RIFTSTONE_HOME": str(Path(tmp) / "home")}), \
                    mock.patch.object(index, "Index", Tracked):
                self.assertEqual(cli.main(["find", "em0100", "--game", str(game.root)]), 0)
                self.assertEqual(cli.main(["open", "no/such/resource", "--game", str(game.root)]), 2)   # refused
                self.assertEqual(cli.main(["build", str(Path(tmp) / "no mod"), "--game", str(game.root)]), 2)
            self.assertGreaterEqual(len(opened), 2)
            self.assertTrue(all(i.closed for i in opened), "an index a command opened is still open")
            self.assertEqual(cli._OPENED, [])


if __name__ == "__main__":
    unittest.main()
