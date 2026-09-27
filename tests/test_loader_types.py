"""native/loader/restypes.inc is what tools/gen_loader_types.py writes from the type map (the loader's archive
guard finds a resource by the extension of the loose path the game asks for)."""
import sys
import unittest
from pathlib import Path

import helpers  # noqa: F401 (sys.path)
from riftstone import typemap

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import gen_loader_types  # noqa: E402


class LoaderTypesTest(unittest.TestCase):
    def test_the_table_is_current(self):
        have = gen_loader_types.TARGET.read_bytes().decode("utf-8").replace("\r\n", "\n")
        self.assertEqual(have, gen_loader_types.render().replace("\r\n", "\n"),
                         "run python tools/gen_loader_types.py")

    def test_every_type_but_the_archive_is_there(self):
        text = gen_loader_types.render()
        self.assertNotIn('{L"arc"', text)
        self.assertIn(f'{{L"gmd", 0x{typemap.BY_EXT["gmd"]:08X}u}}', text)       # the ending's credits text
        self.assertEqual(text.count("{L"), len(typemap.TYPES_BY_CLASS) - 1)


if __name__ == "__main__":
    unittest.main()
