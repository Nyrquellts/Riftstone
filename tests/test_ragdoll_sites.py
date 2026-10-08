"""The loader's ragdoll body-count family (native/loader/ragdoll_sites.inc) against the owner's DDDA.exe: every
site the guard patches must be build 2364871's bytes, in one of the three shapes the stub emitter knows, and the
table must be what tools/ragdoll_sites.py finds in the exe today (with capstone; docs/stability-membrane.md).
Skipped without the game."""
import re
import struct
import sys
import unittest
from pathlib import Path

import helpers  # noqa: F401 (sys.path)

ROOT = Path(__file__).resolve().parents[1]
INC = ROOT / "native" / "loader" / "ragdoll_sites.inc"
sys.path.insert(0, str(ROOT / "tools"))


def _game_exe() -> Path | None:
    try:
        from riftstone.game import find_game
        exe = find_game().root / "DDDA.exe"
        return exe if exe.is_file() else None
    except Exception:  # noqa: BLE001 -- no game here
        return None


def parse():
    sites = []
    for m in re.finditer(r"\{0x([0-9A-F]{8}), '([TCM])', (\d+), (\d+), \{([0-9A-Fx, ]+)\}\}",
                         INC.read_text(encoding="utf-8")):
        code = bytes(int(b, 16) for b in m.group(5).split(", "))
        sites.append((int(m.group(1), 16), m.group(2), int(m.group(3)), int(m.group(4)), code))
    return sites


class TableTest(unittest.TestCase):
    def test_shapes(self):
        sites = parse()
        self.assertEqual(len(sites), 62)
        self.assertEqual({k: sum(1 for s in sites if s[1] == k) for k in "TCM"}, {"T": 48, "C": 10, "M": 4})
        self.assertEqual(len({s[0] for s in sites}), len(sites))
        ends = sorted((va, va + n) for va, _, n, _, _ in sites)
        self.assertTrue(all(a[1] <= b[0] for a, b in zip(ends, ends[1:])), "patched spans overlap")
        for va, kind, n, pre, code in sites:
            self.assertEqual(len(code), n, hex(va))
            self.assertGreaterEqual(n, 5, hex(va))                     # room for the jump
            read = code[pre:]
            modrm = read[1]
            self.assertEqual(modrm >> 6, 1, hex(va))                   # [base + disp8]
            self.assertNotEqual(modrm & 7, 4, hex(va))                 # no SIB
            self.assertEqual(read[2], 0x68, hex(va))
            if kind == "T":
                self.assertEqual((n, pre, read[0], (modrm >> 3) & 7, read[3:]), (7, 0, 0xF7, 0, b"\0\xff\xff\xff"))
            elif kind == "C":
                self.assertEqual((n, pre, read[0], read[3], read[5]), (6, 0, 0x8B, 0xC1, 0x08), hex(va))
                self.assertEqual(read[4], 0xE8 | ((modrm >> 3) & 7), hex(va))   # shr the register just read
            else:
                self.assertEqual((n, read[0]), (pre + 3, 0x85), hex(va))
                self.assertEqual(code[:pre], b"\x33\xf6", hex(va))     # xor esi, esi: runs first in the stub

    def test_the_crashes_are_guarded(self):
        vas = {s[0] for s in parse()}
        self.assertIn(0x007945B4, vas)                                 # 2026-09-27, Gran Soren
        self.assertIn(0x00794AA2, vas)                                 # 2026-10-06, Devil's Firegrove

    def test_bytes_are_build_2364871s(self):
        exe = _game_exe()
        if exe is None:
            self.skipTest("no DDDA.exe here")
        import ragdoll_sites
        data, secs, text_va, text = ragdoll_sites.load(exe)
        if struct.unpack_from("<I", data, struct.unpack_from("<I", data, 0x3C)[0] + 8)[0] != 0x5A314C31:
            self.skipTest("not build 2364871")
        for va, _, n, _, code in parse():
            self.assertEqual(text[va - text_va:va - text_va + n], code, hex(va))

    def test_table_is_current(self):
        exe = _game_exe()
        if exe is None:
            self.skipTest("no DDDA.exe here")
        try:
            import capstone  # noqa: F401
        except ImportError:
            self.skipTest("capstone is not installed")
        import ragdoll_sites
        sites, reread = ragdoll_sites.scan(exe)
        self.assertEqual(INC.read_text(encoding="utf-8").replace("\r\n", "\n"), ragdoll_sites.render(sites, reread),
                         "run python tools/ragdoll_sites.py")


if __name__ == "__main__":
    unittest.main()
