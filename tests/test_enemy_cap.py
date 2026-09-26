"""The enemy_cap plugin's patch table (native/plugins/enemy_cap/src/sites.inc) against the owner's
DDDA.exe: every instruction it patches and every run it replaces must be build 2364871's, byte for byte,
and the table must hold what docs/re-enemy-cap.md says it holds.  Skipped without the game."""
import re
import struct
import unittest
from pathlib import Path

import helpers  # noqa: F401 (sys.path)

INC = Path(__file__).resolve().parents[1] / "native" / "plugins" / "enemy_cap" / "src" / "sites.inc"


def _game_exe() -> Path | None:
    try:
        from riftstone.game import find_game
        exe = find_game().root / "DDDA.exe"
        return exe if exe.is_file() else None
    except Exception:  # noqa: BLE001 -- no game here
        return None


def parse():
    text = INC.read_text(encoding="utf-8")
    sites = []
    for m in re.finditer(r"\{0x([0-9A-F]{8}), (\d+), (\d+), (\d+), (K_\w+), \{([0-9A-Fx, ]+)\}\}", text):
        code = bytes(int(b, 16) for b in m.group(6).split(", "))
        sites.append((int(m.group(1), 16), int(m.group(2)), int(m.group(3)), int(m.group(4)), m.group(5), code))
    blocks = {}
    for m in re.finditer(r"const uint8_t BLOCK_(\w+)\[(\d+)\] = \{([^}]*)\}", text):
        blocks[m.group(1)] = bytes(int(b, 16) for b in m.group(3).replace("\n", " ").split(",") if b.strip())
    spans = {m.group(1): (int(m.group(2), 16), int(m.group(3), 16))
             for m in re.finditer(r'\{"(\w+)", 0x([0-9A-F]{8}), 0x([0-9A-F]{8}), BLOCK_\w+\}', text)}
    return sites, blocks, spans


class TableTest(unittest.TestCase):
    def test_what_the_table_holds(self):
        sites, blocks, spans = parse()
        kinds = {}
        for s in sites:
            kinds[s[4]] = kinds.get(s[4], 0) + 1
        self.assertEqual(kinds, {"K_MOVE": 146, "K_NEG": 4, "K_COUNT": 8, "K_COUNT_M1": 1, "K_END": 1,
                                 "K_USABLE": 1, "K_ALLOC": 3})
        self.assertEqual(len({s[0] for s in sites}), len(sites))
        for at, n, field, size, kind, code in sites:
            self.assertEqual(len(code), n, hex(at))
            self.assertLessEqual(field + size, n, hex(at))
            value = code[field] if size == 1 else struct.unpack_from("<I", code, field)[0]
            if kind == "K_MOVE":
                self.assertTrue(0x844 <= value < 0x984, hex(at))          # a slot field
            elif kind == "K_NEG":
                self.assertTrue(0x804 <= (-value & 0xFFFFFFFF) < 0x984, hex(at))
            elif kind == "K_COUNT":
                self.assertEqual(value, 10, hex(at))
            elif kind == "K_COUNT_M1":
                self.assertEqual(value, 9, hex(at))
            elif kind == "K_END":
                self.assertEqual(value, 0x984, hex(at))
            elif kind == "K_USABLE":
                self.assertEqual(value, 10, hex(at))
            elif kind == "K_ALLOC":
                self.assertEqual(value, 0x1B950, hex(at))
        self.assertEqual(sorted(blocks), ["CLEAR", "CONSTRUCT", "FINAL", "RESET"])
        for name, (lo, hi) in spans.items():
            code = blocks[name.upper()]
            self.assertEqual(len(code), hi - lo, name)
            self.assertEqual(len(code) % 6, 0, name)                      # 6-byte stores only
            stores = [code[i:i + 6] for i in range(0, len(code), 6)]
            disps = [struct.unpack_from("<I", st, 2)[0] for st in stores]
            self.assertTrue(all(0x844 <= d < 0x984 for d in disps), name)
        self.assertEqual(len(blocks["CONSTRUCT"]) // 6, 80)               # 10 slots x 8 fields
        for name in ("CLEAR", "RESET", "FINAL"):
            self.assertEqual(len(blocks[name]) // 6, 30)                  # 10 slots x 3 fields

    def test_the_table_matches_the_executable(self):
        exe = _game_exe()
        if exe is None:
            self.skipTest("DDDA.exe not found")
        data = exe.read_bytes()
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        nsec = struct.unpack_from("<H", data, pe + 6)[0]
        opt = struct.unpack_from("<H", data, pe + 20)[0]
        secs = [struct.unpack_from("<8sIIII", data, pe + 24 + opt + 40 * i) for i in range(nsec)]

        def at(va, n):
            rva = va - 0x400000
            for _, vsize, vaddr, rsize, rptr in secs:
                if vaddr <= rva < vaddr + max(vsize, rsize):
                    return data[rptr + rva - vaddr:rptr + rva - vaddr + n]
            raise AssertionError(hex(va))

        sites, blocks, spans = parse()
        for va, n, _, _, kind, code in sites:
            self.assertEqual(at(va, n), code, f"{kind} at 0x{va:08X}")
        for name, (lo, hi) in spans.items():
            self.assertEqual(at(lo, hi - lo), blocks[name.upper()], name)
        # the objects the plugin writes: sSetManager::cUnitData's vtable, and the manager's constructor
        self.assertEqual(at(0x0049F9B2, 5), bytes.fromhex("b914245601"))    # mov ecx, 0x1562414


if __name__ == "__main__":
    unittest.main()
