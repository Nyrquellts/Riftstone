"""Collision meshes (.sbc): the loader's layout, what a translation moves, and the cell-collision checks."""
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers
from riftstone import cli, sbc, terrain
from riftstone.errors import BuildError, FormatError, ParamError
from riftstone.mod import Mod, collect

C = terrain.Cell(47, 35)            # adds (-150000, 0, -30000)


class LayoutTest(unittest.TestCase):
    def test_parse(self):
        d = helpers.cell_collision(parts=2, two_level=True)
        s = sbc.parse(d)
        self.assertEqual((s.parts, s.materials, s.leaves, s.triangles, s.vertices), (2, 1, 2, 2, 4))
        self.assertEqual([(t.kind, t.nodes) for t in s.trees], [(1, 3), (1, 3), (1, 3)])
        self.assertEqual(s.leaf_offset + 10 * s.leaves, len(d))
        self.assertEqual(sbc.positions(d)[1], (9800.0, 5100.0, 300.0))

    def test_modify_ids_are_not_in_the_file(self):
        # 452 vanilla files count modify ids; the loader reads none of them from the stream
        self.assertEqual(sbc.parse(helpers.cell_collision(modify=5)).vertices, 4)

    def test_refusals(self):
        good = helpers.cell_collision()
        s = sbc.parse(good)
        cases = {
            "magic": b"SBD\xff" + good[4:],
            "version": good[:4] + struct.pack("<I", 1) + good[8:],
            "grid": good[:8] + struct.pack("<I", 2) + good[12:],
            "tree tag": good[:s.trees[0].offset] + b"XXXX" + good[s.trees[0].offset + 4:],
            "size": good + b"\0",
            "short": good[:-1],
        }
        part = bytearray(good)
        struct.pack_into("<I", part, 0x54 + 0x3C, 99)          # vertex start past the file's vertices
        cases["part range"] = bytes(part)
        mask = bytearray(good)
        mask[s.trees[0].first_node + 1] ^= 0xFF                   # the four mask bytes must agree
        cases["mask"] = bytes(mask)
        kid = bytearray(helpers.cell_collision(two_level=True))
        s2 = sbc.parse(bytes(kid))
        struct.pack_into("<H", kid, s2.trees[0].first_node + 4, 7)   # an internal lane past the tree
        cases["child"] = bytes(kid)
        for what, data in cases.items():
            with self.assertRaises(FormatError, msg=what):
                sbc.parse(data)

    def test_online_version(self):
        # Dragon's Dogma Online's collision (all 4,042 files): the same layout under another version word
        from riftstone import port, typemap

        d = helpers.cell_collision(parts=2, two_level=True)
        o = sbc.for_game(d, "ddo")
        self.assertEqual((o[:4], struct.unpack_from("<I", o, 4)[0], o[8:]), (d[:4], sbc.VERSION_DDO, d[8:]))
        self.assertEqual(sbc.positions(o), sbc.positions(d))
        self.assertEqual(sbc.translate(o, C.offset)[8:], sbc.translate(d, C.offset)[8:])
        p = port.convert(o, typemap.type_for_extension("sbc"), "ddo", "ddda")
        self.assertEqual(p.data, d)
        self.assertTrue(any("UNKNOWN" in n for n in p.notes))
        with self.assertRaises(FormatError):
            sbc.for_game(d[:-1], "ddo")

    def test_binary_trees_read_but_not_moved(self):
        d = helpers.cell_collision(tree_kind=2)
        self.assertEqual(sbc.parse(d).trees[0].kind, sbc.BINARY)
        with self.assertRaises(ParamError):
            sbc.translate(d, (1.0, 0.0, 0.0))


class MoveTest(unittest.TestCase):
    def test_moved_floats_are_exactly_the_positions(self):
        d = helpers.cell_collision(parts=2, two_level=True)
        s = sbc.parse(d)
        got = sbc.moved_floats(d)
        nodes = sum(t.nodes for t in s.trees)
        self.assertEqual(len(got), 3 * (2 + 2 * s.parts + 2 * len(s.trees) + s.vertices) + 24 * nodes)
        self.assertEqual(len({o for o, _ in got}), len(got))                    # nothing moved twice
        tri_end = s.triangle_offset + 0x20 * s.triangles
        for off, _ in got:                                                        # never triangles or later tables
            self.assertFalse(s.triangle_offset <= off < tri_end or off >= s.material_offset, hex(off))

    def test_translate_moves_positions_and_nothing_else(self):
        d = helpers.cell_collision(parts=2, two_level=True)
        w = sbc.translate(d, (-150000.0, 0.0, -30000.0))
        moved = {off + k for off, _ in sbc.moved_floats(d) for k in range(4)}
        self.assertTrue(all(w[i] == d[i] for i in range(len(d)) if i not in moved))
        for a, b in zip(sbc.positions(d), sbc.positions(w)):
            self.assertEqual((b[0] - a[0], b[1] - a[1], b[2] - a[2]), (-150000.0, 0.0, -30000.0))
        s = sbc.parse(w)
        b = s.trees[0].first_node + 16                                           # minX[4] of the first node
        self.assertEqual(struct.unpack_from("<4f", w, b), (100.0 - 150000.0,) * 4)
        self.assertEqual(struct.unpack_from("<4f", w, b + 16), (4900.0,) * 4)    # Y lanes stay
        self.assertEqual(sbc.bounds_problems(w), [])

    def test_bounds_problems(self):
        d = bytearray(helpers.cell_collision())
        struct.pack_into("<3f", d, 0x54 + 0x10, 5000.0, 5000.0, 5000.0)       # part box too small
        self.assertTrue(any("vertices leave" in p for p in sbc.bounds_problems(bytes(d))))
        e = bytearray(helpers.cell_collision())
        struct.pack_into("<3f", e, 0x40, 5000.0, 5000.0, 5000.0)              # header box too small
        self.assertTrue(any("file's box" in p for p in sbc.bounds_problems(bytes(e))))


class CellCollisionTest(unittest.TestCase):
    def test_names(self):
        self.assertEqual(C.collisions, ("scr\\st100\\collision\\m40\\marge\\st100h_47m35n_mrg00",
                                        "scr\\st100\\collision\\m40\\marge\\st100e_47m35n_mrg00"))
        for name in C.collisions:
            self.assertEqual(terrain.cell_of_collision(name), C)
            self.assertEqual(terrain.frame(name).kind, "cell")
            self.assertEqual(terrain.parse_cell(name.split("\\")[-1] + ".sbc"), C)
        self.assertIsNone(terrain.cell_of_collision("scr\\st100\\collision\\m50\\marge\\st100h_47m35n_mrg00"))
        self.assertEqual(terrain.frame("model\\om\\om0020\\collision\\om0020_h00").kind, "placed")

    def test_check_and_move(self):
        d = helpers.cell_collision()
        self.assertEqual(terrain.check(d, C), ([], []))
        w = terrain.worldize(d, C)
        errors, _ = terrain.check(w, C)
        self.assertTrue(errors and "world coordinates" in errors[0])
        self.assertEqual(terrain.worldize(terrain.localize(w, C), C), w)
        for a, b in zip(sbc.positions(d), sbc.positions(terrain.localize(w, C))):
            self.assertTrue(all(abs(x - y) <= 1 / 64 for x, y in zip(a, b)))

    def test_a_whole_cell_past_the_edge_is_normal_for_collision(self):
        d = helpers.cell_collision(points=[(-10000.0, 0.0, 0.0), (20000.0, 10.0, 10000.0), (0.0, 5.0, 5000.0)])
        self.assertEqual(terrain.check(d, C), ([], []))

    def test_both_frames_fit_near_the_origin_cell(self):
        near = terrain.Cell(50, 51)                                             # adds (10000, 0, 0)
        w = terrain.worldize(helpers.cell_collision(), near)
        errors, notes = terrain.check(w, near)
        self.assertEqual(errors, [])
        self.assertTrue(notes and "both in the cell's frame and in the world" in notes[0])


class BuildGuardTest(unittest.TestCase):
    def build(self, data: bytes, game: str = "ddda",
              rel: str = "scr/st100/collision/m40/marge/st100h_47m35n_mrg00.sbc"):
        with tempfile.TemporaryDirectory() as d:
            m = Mod.create(Path(d) / "m", "Collision", game=game)
            f = m.root / "files" / rel
            f.parent.mkdir(parents=True)
            f.write_bytes(data)
            return collect(m)

    def test_world_space_collision_does_not_build(self):
        with self.assertRaises(BuildError) as cm:
            self.build(terrain.worldize(helpers.cell_collision(), C))
        self.assertIn("world coordinates", str(cm.exception))

    def test_collision_in_its_frame_builds(self):
        self.assertEqual(len(self.build(helpers.cell_collision())), 1)

    def test_object_collision_and_ddo_are_not_checked(self):
        w = terrain.worldize(helpers.cell_collision(), C)
        self.assertEqual(len(self.build(w, rel="model/om/om0020/collision/om0020_h00.sbc")), 1)
        self.assertEqual(len(self.build(w, game="ddo")), 1)


class CliTest(unittest.TestCase):
    def test_check_worldize_localize_a_collision_file(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"RIFTSTONE_GAME": ""}):
            local = Path(d) / "st100h_47m35n_mrg00.sbc"
            local.write_bytes(helpers.cell_collision())
            self.assertEqual(cli.main(["terrain", "check", str(local)]), 0)
            self.assertNotEqual(cli.main(["terrain", "localize", str(local)]), 0)
            self.assertEqual(cli.main(["terrain", "worldize", str(local)]), 0)
            world = Path(d) / "st100h_47m35n_mrg00.world.sbc"
            self.assertEqual(cli.main(["terrain", "check", str(world)]), 1)
            back = Path(d) / "back.sbc"
            self.assertEqual(cli.main(["terrain", "localize", str(world), "--cell", "47m35n", "-o", str(back)]), 0)
            self.assertEqual(terrain.worldize(back.read_bytes(), C), world.read_bytes())


if __name__ == "__main__":
    unittest.main()
