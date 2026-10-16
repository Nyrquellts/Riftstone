"""The 3D map's scenes (scene.py): a stage's collision and navigation mesh as compact meshes in world space.

A stand-in game: stage 424 with collision e and h and the navigation mesh it loads (stage 420's), stage 443's
collision (which 444 loads, as the game's stage table says) and one open-field cell whose collision is in the
cell's frame."""
import base64
import os
import struct
import tempfile
import unittest
from array import array
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import helpers
from riftstone import arc, nav, scene, terrain, typemap
from riftstone.errors import RiftError
from riftstone.game import Game
from riftstone.index import Index

setUpModule, tearDownModule = helpers.module_env("RIFTSTONE_HOME")

SBC = typemap.BY_EXT["sbc"]
E424 = [(-500.0, -350.0, -9000.0), (500.0, -350.0, -9000.0), (500.0, -350.0, -8000.0), (-500.0, -350.0, -8000.0)]
H424 = [(-500.0, -350.0, -9000.0), (-500.0, 150.0, -9000.0), (-500.0, 150.0, -8000.0)]
E443 = [(0.0, 0.0, 0.0), (300.0, 0.0, 0.0), (300.0, 0.0, 300.0)]
FIELD_CELL = terrain.Cell(54, 48)
FIELD_E = [(100.0, 5000.0, 200.0), (9800.0, 5100.0, 300.0), (400.0, 5200.0, 9700.0), (9900.0, 4900.0, 9900.0)]
FAR = [(0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (0.0, 0.0, 100.0), (2.0e7, 0.0, 0.0)]   # the last triangle reaches 200 km


def f32(v: float) -> float:
    return struct.unpack("<f", struct.pack("<f", v))[0]


def decode(layer: dict) -> tuple[list, list]:
    v, t = array("f"), array("I")
    v.frombytes(base64.b64decode(layer["verts"]))
    t.frombytes(base64.b64decode(layer["tris"]))
    return [tuple(v[i:i + 3]) for i in range(0, len(v), 3)], list(t)


def corners(layer: dict) -> list:
    verts, tris = decode(layer)
    return [tuple(verts[i] for i in tris[k:k + 3]) for k in range(0, len(tris), 3)]


class SceneTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import nav_fixture

        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name) / "game"
        os.environ["RIFTSTONE_HOME"] = str(Path(cls.tmp.name) / "home")
        stages = root / "nativePC" / "rom" / "stage" / "stage400"
        stages.mkdir(parents=True)
        (root / "DDDA.exe").write_bytes(b"stub")
        ent = lambda name, data, tid=SBC: arc.Entry.from_data(name.encode("latin-1"), tid, data)   # noqa: E731
        (stages / "stage424.arc").write_bytes(arc.Archive([
            ent("scr\\st424\\collision\\st424e_mrg00", helpers.cell_collision(E424)),
            ent("scr\\st424\\collision\\st424h_mrg00", helpers.cell_collision(H424)),
            ent("scr\\st424\\collision\\st424_odd", helpers.cell_collision(E443)),     # another shape counts as h
            ent("scr\\st426\\collision\\st426e_mrg00", helpers.cell_collision(FAR))]).build())
        (stages / "stage420_nav.arc").write_bytes(arc.Archive([
            ent("scr\\st420\\etc\\st420_nav", nav.build(nav_fixture.corridor_with_island()), typemap.BY_EXT["nav"])]).build())
        (stages / "stage443.arc").write_bytes(arc.Archive([
            ent("scr\\st443\\collision\\st443e_mrg00", helpers.cell_collision(E443))]).build())
        h, e = FIELD_CELL.collisions
        (root / "nativePC" / "rom" / "field.arc").write_bytes(arc.Archive([ent(e, helpers.cell_collision(FIELD_E)),
                                                                             ent(h, helpers.cell_collision(H424))]).build())
        cls.game = Game(root)
        cls.idx = Index(cls.game)
        cls.idx.refresh()

    @classmethod
    def tearDownClass(cls):
        cls.idx.close()
        cls.tmp.cleanup()

    def test_a_dungeon_is_its_collision_and_its_mesh_in_world_space(self):
        sc = scene.stage_scene(self.game, self.idx, 424)
        self.assertEqual(sc["kind"], "dungeon")
        layers = {ly["key"]: ly for ly in sc["layers"]}
        self.assertEqual(set(layers), {"e", "h", "nav"})
        self.assertEqual(sc["nav_stage"], 420)
        e = layers["e"]
        self.assertEqual((e["triangles"], e["vertices"], e["skipped"]), (2, 4, 0))   # shared corners sent once
        self.assertEqual(corners(e), [(E424[0], E424[1], E424[2]), (E424[1], E424[2], E424[3])])
        self.assertEqual(layers["h"]["triangles"], 1 + 1)                          # h, and the odd name with it
        mesh = nav.stage_mesh(self.game, self.idx, 424)
        self.assertEqual(layers["nav"]["triangles"], len(mesh.tris))
        for ly in sc["layers"]:
            verts, tris = decode(ly)
            self.assertEqual(len(verts), ly["vertices"])
            self.assertTrue(all(0 <= i < len(verts) for i in tris))
            for v in verts:
                self.assertTrue(all(sc["bounds"][k] <= v[k] <= sc["bounds"][k + 3] for k in range(3)))
        self.assertEqual(sc["bounds"][1], min(min(p[1] for p in E424), min(p[1] for p in mesh.positions)))

    def test_444_loads_443s_collision(self):
        self.assertEqual(scene.collision_stage(444), 443)
        self.assertEqual(scene.collision_stage(424), 424)
        sc = scene.stage_scene(self.game, self.idx, 444)
        self.assertEqual(sc["collision_stage"], 443)
        self.assertEqual(corners(sc["layers"][0]), [tuple(E443)])
        self.assertTrue(any("443's collision" in n for n in sc["notes"]))
        self.assertTrue(any("no navigation mesh" in n for n in sc["notes"]))

    def test_a_stage_with_nothing_says_so(self):
        sc = scene.stage_scene(self.game, self.idx, 427)     # no archive, no mesh (425 would load 420's)
        self.assertEqual((sc["layers"], sc["bounds"]), ([], [0.0] * 6))
        self.assertIn("stage 427 ships no collision", sc["notes"])

    def test_a_triangle_past_the_world_is_left_out_and_counted(self):
        sc = scene.stage_scene(self.game, self.idx, 426)
        e = sc["layers"][0]
        self.assertEqual((e["triangles"], e["skipped"]), (1, 1))
        self.assertTrue(any("left out" in n for n in sc["notes"]))

    def test_the_open_field_is_moved_by_its_cells_corner(self):
        x, z = FIELD_CELL.offset[0] + 4000, FIELD_CELL.offset[2] + 6000
        sc = scene.stage_scene(self.game, self.idx, 100, (x, z), 0)
        self.assertEqual((sc["kind"], sc["radius"], sc["cells"], sc["cell"]), ("field", 0, [[54, 48]], [54, 48]))
        self.assertEqual(sc["center"], [FIELD_CELL.offset[0] + 5000, FIELD_CELL.offset[2] + 5000])
        layers = {ly["key"]: ly for ly in sc["layers"]}
        d = FIELD_CELL.offset
        moved = [tuple(f32(p[k] + d[k]) for k in range(3)) for p in FIELD_E]
        self.assertEqual(corners(layers["e"]), [(moved[0], moved[1], moved[2]), (moved[1], moved[2], moved[3])])
        self.assertIn("h", layers)
        self.assertNotIn("nav", layers)
        nine = scene.stage_scene(self.game, self.idx, 100, (x, z), 1)
        self.assertEqual(len(nine["cells"]), 9)
        self.assertEqual(corners({ly["key"]: ly for ly in nine["layers"]}["e"]), corners(layers["e"]))
        empty = scene.stage_scene(self.game, self.idx, 100, (-499000.0, -499000.0), 2)
        self.assertEqual((len(empty["cells"]), empty["layers"]), (9, []))          # the grid's corner: 3 x 3 inside it
        self.assertTrue(any("no terrain cell" in n for n in empty["notes"]))
        with self.assertRaises(RiftError):
            scene.stage_scene(self.game, self.idx, 100)

    def test_field_cells_stay_in_the_grid_and_hold_the_point(self):
        for at in ((0.0, 0.0), (-499999.0, 499999.0), (499999.0, -1.0), (-5.0e6, 0.0)):
            for r in range(scene.RADIUS_MAX + 1):
                cells = scene.field_cells(at, r)
                self.assertTrue(all(0 <= c.m < 100 and 0 <= c.n < 100 for c in cells))
                self.assertLessEqual(len(cells), (2 * r + 1) ** 2)
                mid = terrain.cell_at(*at)
                if 0 <= mid.m < 100 and 0 <= mid.n < 100:
                    self.assertIn(mid, cells)
        with self.assertRaises(RiftError):
            scene.field_cells((0.0, 0.0), scene.RADIUS_MAX + 1)

    def test_too_many_triangles_are_refused(self):
        with mock.patch.object(scene, "TRIS_MAX", 2):
            with self.assertRaises(RiftError):
                scene.stage_scene(self.game, self.idx, 424)

    def test_online_is_not_read_yet(self):
        with self.assertRaises(RiftError):
            scene.stage_scene(SimpleNamespace(kind="ddo"), self.idx, 424)

    def test_where_and_how_many_cells(self):
        self.assertIsNone(scene.parse_at(""))
        self.assertIsNone(scene.parse_at(None))
        self.assertEqual(scene.parse_at(" 35000, -75000 "), (35000.0, -75000.0))
        for bad in ("1", "1,2,3", "a,b", "nan,1", "1,inf", "2e7,0", ",", "1,,2"):
            with self.assertRaises(RiftError, msg=bad):
                scene.parse_at(bad)
        self.assertEqual(scene.parse_radius(""), 1)
        self.assertEqual(scene.parse_radius(None, 2), 2)
        self.assertEqual(scene.parse_radius(" 0 "), 0)
        for bad in ("3", "-1", "x", "1.5", "²"):
            with self.assertRaises(RiftError, msg=bad):
                scene.parse_radius(bad)


if __name__ == "__main__":
    unittest.main()
