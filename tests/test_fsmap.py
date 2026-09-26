import random
import unittest

import helpers  # noqa: F401  (sets sys.path)
from riftstone import fsmap, typemap
from riftstone.errors import UnsafePathError

TEX = typemap.BY_EXT["tex"]


class FsMapTest(unittest.TestCase):
    def roundtrip(self, name: bytes, type_id: int = TEX) -> str:
        path = fsmap.encode_name(name, type_id)
        self.assertEqual(fsmap.decode_path(path), (name, type_id), path)
        for part in path.split("/"):
            self.assertTrue(part, path)
            self.assertFalse(part.endswith((" ", ".")), path)
            self.assertFalse(any(c in part for c in '<>:"\\|?*'), path)
        return path

    def test_plain_name(self):
        self.assertEqual(self.roundtrip(b"model\\em\\e01\\e0100\\e0100"), "model/em/e01/e0100/e0100.tex")

    def test_vanilla_oddities(self):
        # both shapes exist in the shipped archives
        self.assertEqual(self.roundtrip(b"model\\em\\e02\\e0201\\d_e0201_body_MM "),
                         "model/em/e02/e0201/d_e0201_body_MM%20.tex")
        self.assertEqual(self.roundtrip(b"sound\\se\\vo_ev_jp\\\\st100ev05_vo_f0"),
                         "sound/se/vo_ev_jp/%_/st100ev05_vo_f0.tex")

    def test_reserved_and_forbidden(self):
        self.assertTrue(self.roundtrip(b"a\\CON").startswith("a/%43ON"))
        self.assertTrue(self.roundtrip(b"a\\nul.x").startswith("a/%6Eul.x"))
        self.roundtrip(b"a\\b:c*d?e\"f<g>h|i%j")
        self.roundtrip(b"a\\..\\.")
        self.roundtrip(b"\\leading\\")
        self.roundtrip(bytes(range(1, 64)))

    def test_unknown_type_uses_hex(self):
        path = self.roundtrip(b"x\\y", 0x12345678)
        self.assertTrue(path.endswith(".12345678"))

    def test_decode_refuses(self):
        for bad in ("/abs/x.tex", "C:/x.tex", "a/../b.tex", "a//b.tex", "a/./b.tex", "a/b", "a/b.nope",
                    "a/%4.tex", "a/%zz.tex", "a/é.tex", "a/" + "x" * 70 + ".tex"):
            with self.assertRaises(UnsafePathError, msg=bad):
                fsmap.decode_path(bad)

    def test_backslash_input(self):
        self.assertEqual(fsmap.decode_path("model\\pl\\x.mod"), (b"model\\pl\\x", typemap.BY_EXT["mod"]))

    def test_nul_refused(self):
        with self.assertRaises(UnsafePathError):
            fsmap.encode_name(b"a\0b", TEX)

    def test_random_names(self):
        rng = random.Random(1234)
        alphabet = bytes(b for b in range(1, 256))
        for _ in range(3000):
            n = rng.randint(1, 63)
            name = bytes(rng.choice(alphabet) for _ in range(n))
            self.roundtrip(name, rng.choice(list(typemap.BY_ID)))


if __name__ == "__main__":
    unittest.main()
