"""Gransys terrain cells: the cell rule, moving a cell model between frames, and the build guard."""
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers
from riftstone import cli, port, terrain
from riftstone.errors import BuildError, FormatError, ParamError, RiftError
from riftstone.mod import Mod, collect

C = terrain.Cell(47, 35)            # adds (-150000, 0, -30000)


def positional(data: bytes) -> set[int]:
    p = terrain._parts(data)
    return {off + k for off in p.bounds + p.groups + p.envelopes + p.vertices for k in range(12)}


class CellRuleTest(unittest.TestCase):
    def test_offset_and_names(self):
        self.assertEqual(C.offset, (-150000.0, 0.0, -30000.0))
        self.assertEqual(C.name, "st100_47m35n")
        self.assertEqual(C.model, "scr\\st100\\model\\m40\\st100_47m35n")
        self.assertEqual(C.archive, "rom/stage/stage100/split/m40/n30/st100_47m35n")
        self.assertEqual(terrain.Cell(50, 50).offset, (0.0, 0.0, 0.0))

    def test_parse(self):
        for text in ("47m35n", "st100_47m35n", C.model, "C:\\mods\\x\\files\\scr\\st100\\model\\m40\\st100_47m35n.mod",
                     "st100_47m35n.world.mod", "rom/stage/stage100/split/m40/n30/st100_47m35n", "ST100_47M35N"):
            self.assertEqual(terrain.parse_cell(text), C, text)
        for bad in ("", "47m35", "st100_4m35n", "st200_47m35n", "xst100_47m35n.mod", "st100_47m35nx"):
            with self.assertRaises(ParamError, msg=bad):
                terrain.parse_cell(bad)

    def test_only_cell_terrain_models_are_cells(self):
        self.assertEqual(terrain.cell_of_model(C.model.encode()), C)
        self.assertIsNone(terrain.cell_of_model("scr\\st100\\model\\m50\\st100_47m35n"))   # folder != m // 10
        self.assertIsNone(terrain.cell_of_model("scr\\st100\\model\\water\\st100_w04_f09_lake1"))
        self.assertIsNone(terrain.cell_of_model("scr\\st100\\model\\fm_m50\\fmfore_50m42n"))

    def test_cell_at(self):
        # the spawn example in the CLI help stands in st100_45m55n (its layout's cell)
        self.assertEqual(terrain.cell_at(58600, -45360), terrain.Cell(45, 55))
        dx, _, dz = C.offset
        self.assertEqual(terrain.cell_at(dx, dz), C)
        self.assertEqual(terrain.cell_at(dx + 9999.9, dz + 9999.9), C)

    def test_cell_at_refuses_what_is_not_a_position(self):
        # int(nan) raised ValueError and int(inf) OverflowError (riftstone terrain where nan,0)
        for x, z in ((float("nan"), 0.0), (0.0, float("inf")), (float("-inf"), 1.0)):
            with self.subTest(x=x, z=z), self.assertRaises(ParamError):
                terrain.cell_at(x, z)

    def test_frames(self):
        self.assertEqual(terrain.frame(C.model).kind, "cell")
        self.assertEqual(terrain.frame("scr\\st100\\model\\st100_area00_h").kind, "world")
        self.assertEqual(terrain.frame("scr\\st100\\model\\water\\st100_sea00").kind, "world")
        self.assertEqual(terrain.frame("scr\\st100\\model\\fm_f00\\fm_f00_01_h").kind, "unknown")
        self.assertEqual(terrain.frame("model\\om\\om0590\\model\\om0593").kind, "placed")
        self.assertEqual(terrain.frame("scr\\fd\\model\\ma\\ma000_00_md00", "ddo").kind, "world")
        self.assertEqual(terrain.frame(C.model, "ddo").kind, "unknown")

    def test_cells_are_the_shipped_split_archives(self):
        cs = terrain.cells()
        self.assertEqual(len(cs), 418)
        self.assertIn(C, cs)
        self.assertTrue(all(42 <= c.m <= 71 and 35 <= c.n <= 63 for c in cs))


class MoveTest(unittest.TestCase):
    def test_everything_positional_moves_and_nothing_else(self):
        # two meshes read the same vertices (moved once), plus a group and two envelopes
        d = helpers.cell_model(meshes=[(0xD8297028, 24, 0, 5), (0xD8297028, 24, 0, 5)])
        w = terrain.worldize(d, C)
        dx, dy, dz = C.offset
        for a, b in zip(terrain.positions(d), terrain.positions(w)):
            self.assertEqual((b[0] - a[0], b[1] - a[1], b[2] - a[2]), (dx, dy, dz))
        for off in (0x40, 0x50, 0x60):
            a, b = struct.unpack_from("<3f", d, off), struct.unpack_from("<3f", w, off)
            self.assertEqual((b[0] - a[0], b[1] - a[1], b[2] - a[2]), (dx, dy, dz))
        self.assertEqual(struct.unpack_from("<f", d, 0x4C), struct.unpack_from("<f", w, 0x4C))   # radius stays
        moved = positional(d)
        self.assertEqual(len(terrain._parts(d).vertices), 5)
        self.assertTrue(all(d[i] == w[i] for i in range(len(d)) if i not in moved), "a non-positional byte changed")
        p = terrain._parts(d)
        for off in p.groups + p.envelopes:
            a, b = struct.unpack_from("<3f", d, off), struct.unpack_from("<3f", w, off)
            self.assertEqual((b[0] - a[0], b[2] - a[2]), (dx, dz))

    def test_localize_undoes_worldize(self):
        d = helpers.cell_model(points=[(1234.5678, 40000.25, 9876.54321), (-3000.125, 41000.0, 12000.0),
                                       (0.0078125, 42000.0, 5000.5)])
        w = terrain.worldize(d, C)
        back = terrain.localize(w, C)
        self.assertEqual(terrain.worldize(back, C), w)            # localize is exact
        for a, b in zip(terrain.positions(d), terrain.positions(back)):
            self.assertTrue(all(abs(x - y) <= 1 / 64 for x, y in zip(a, b)), (a, b))
        self.assertEqual(terrain.localize(terrain.worldize(d, terrain.Cell(50, 50)), terrain.Cell(50, 50)), d)

    def test_refusals(self):
        with self.assertRaises(ParamError):                        # bones: not moved as a whole
            terrain.worldize(helpers.cell_model(bones=2), C)
        with self.assertRaises(ParamError):                        # packed (int16) positions
            terrain.worldize(helpers.cell_model(meshes=[(0x14D40020, 24, 0, 5)]), C)
        with self.assertRaises(ParamError):                        # an envelope bound to a bone
            terrain.worldize(helpers.cell_model(env_bone=3), C)
        with self.assertRaises(RiftError):                         # not the shared layout
            terrain.worldize(helpers.cell_model()[:0x90], C)
        bad = bytearray(helpers.cell_model())                      # an index past the mesh's vertices
        h = port.MOD_HEADER.unpack_from(bad)
        struct.pack_into("<H", bad, h[16], 99)
        with self.assertRaises(FormatError):
            terrain.worldize(bytes(bad), C)
        clash = helpers.cell_model(meshes=[(0xD8297028, 24, 0, 5), (0x49B4F029, 28, 0, 4)])
        with self.assertRaises(FormatError):                       # one vertex run read with two layouts
            terrain.worldize(clash, C)


class CheckTest(unittest.TestCase):
    def test_vanilla_like_cell_passes(self):
        self.assertEqual(terrain.check(helpers.cell_model(), C), ([], []))

    def test_world_space_cell_is_an_error(self):
        errors, notes = terrain.check(terrain.worldize(helpers.cell_model(), C), C)
        self.assertEqual(len(errors), 1)
        self.assertIn("world coordinates", errors[0])
        self.assertIn("localize", errors[0])

    def test_vertices_outside_their_bounds_are_an_error(self):
        d = bytearray(helpers.cell_model())
        struct.pack_into("<3f", d, 0x60, 5000.0, 5000.0, 5000.0)    # bounds too small
        errors, _ = terrain.check(bytes(d), C)
        self.assertTrue(any("leave its own bounds" in e for e in errors))

    def test_far_reach_is_only_a_note(self):
        d = helpers.cell_model(points=[(0.0, 0.0, 0.0), (25000.0, 10.0, 100.0)])
        self.assertEqual(terrain.check(d, C)[0], [])
        self.assertTrue(terrain.check(d, C)[1])

    def test_unreadable_is_only_a_note(self):
        errors, notes = terrain.check(helpers.cell_model(bones=1), C)
        self.assertEqual(errors, [])
        self.assertTrue(notes and "cannot be checked" in notes[0])


class BuildGuardTest(unittest.TestCase):
    def build(self, data: bytes, game: str = "ddda", rel: str = "scr/st100/model/m40/st100_47m35n.mod"):
        with tempfile.TemporaryDirectory() as d:
            m = Mod.create(Path(d) / "m", "Terrain", game=game)
            f = m.root / "files" / rel
            f.parent.mkdir(parents=True)
            f.write_bytes(data)
            return collect(m)

    def test_world_space_cell_does_not_build(self):
        with self.assertRaises(BuildError) as cm:
            self.build(terrain.worldize(helpers.cell_model(), C))
        self.assertIn("world coordinates", str(cm.exception))

    def test_cell_in_its_frame_builds(self):
        self.assertEqual(len(self.build(helpers.cell_model())), 1)

    def test_other_models_and_ddo_are_not_checked(self):
        w = terrain.worldize(helpers.cell_model(), C)
        self.assertEqual(len(self.build(w, rel="model/om/om0590/model/om0593.mod")), 1)
        self.assertEqual(len(self.build(w, game="ddo")), 1)


class CliTest(unittest.TestCase):
    def run_cli(self, *argv):
        with mock.patch.dict(os.environ, {"RIFTSTONE_GAME": ""}):
            return cli.main(list(argv))

    def test_check_localize_worldize(self):
        with tempfile.TemporaryDirectory() as d:
            local = Path(d) / "st100_47m35n.mod"
            local.write_bytes(helpers.cell_model())
            self.assertEqual(self.run_cli("terrain", "check", str(local)), 0)
            self.assertNotEqual(self.run_cli("terrain", "localize", str(local)), 0)   # not world: refused
            self.assertEqual(self.run_cli("terrain", "worldize", str(local)), 0)
            world = Path(d) / "st100_47m35n.world.mod"
            self.assertEqual(self.run_cli("terrain", "check", str(world)), 1)
            back = Path(d) / "back.mod"
            self.assertEqual(self.run_cli("terrain", "localize", str(world), "--cell", "47m35n", "-o", str(back)), 0)
            self.assertEqual(terrain.worldize(back.read_bytes(), C), world.read_bytes())
            self.assertNotEqual(self.run_cli("terrain", "check", str(local), "--game", "ddo"), 0)

    def test_where(self):
        self.assertEqual(self.run_cli("terrain", "where", "47m35n"), 0)
        self.assertEqual(self.run_cli("terrain", "where", "58600,42716,-45360"), 0)
        self.assertNotEqual(self.run_cli("terrain", "where"), 0)


if __name__ == "__main__":
    unittest.main()
