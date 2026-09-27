"""rNavigationMesh: the format the loader reads (every count and index it trusts is checked), the stage table
(which stage's mesh a stage loads), and the questions asked of a mesh -- ground under a point, walkable regions,
distances by the mesh, room to the mesh's edge."""
import math
import struct
import unittest

import helpers  # noqa: F401 (sys.path)
import nav_fixture
from riftstone import nav
from riftstone.errors import FormatError


class FormatTest(unittest.TestCase):
    def setUp(self):
        self.nav = nav_fixture.corridor_with_island()
        self.data = nav.build(self.nav)

    def test_round_trip_is_exact(self):
        again = nav.parse(self.data)
        self.assertEqual(nav.build(again), self.data)
        self.assertEqual(len(again.triangles), len(self.nav.triangles))
        self.assertEqual(again.name, b"new Navigation\0")
        self.assertEqual(again.triangles[5].corners, list(self.nav.triangles[5].corners))

    def test_counts_are_computed_on_build(self):
        n = nav.parse(self.data)
        links = sum(len(t.links) for t in n.triangles)
        buffer = struct.unpack_from("<I", self.data, 12)[0]
        self.assertEqual(buffer, len(n.triangles) * (1 + 3) + 6 * links)

    def bad(self, data: bytes, needle: str):
        with self.assertRaises(FormatError) as cm:
            nav.parse(data)
        self.assertIn(needle, str(cm.exception))

    def test_refuses_what_the_loader_would_trust(self):
        d = self.data
        self.bad(b"NAX\0" + d[4:], "not a navigation mesh")
        self.bad(d[:4] + struct.pack("<I", 0x22) + d[8:], "version")
        self.bad(d[:12] + struct.pack("<I", struct.unpack_from("<I", d, 12)[0] + 1) + d[16:], "buffer slots")
        self.bad(d + b"\0", "follow the quadtree")
        self.bad(d[:-3], "ends inside")
        # a corner past the vertices: the first triangle's first corner
        n = nav.parse(d)
        n.triangles[0].corners[0] = len(n.positions) + 5
        with self.assertRaises(FormatError):
            nav.build(n)
        raw = bytearray(d)
        off = self._first_corner_offset(n)
        struct.pack_into("<i", raw, off, len(n.positions) + 5)
        self.bad(bytes(raw), "vertex past")

    def _first_corner_offset(self, n) -> int:
        o = 16 + 4 + len(n.name) + 13 + len(n.positions) * 15
        t = n.triangles[0]
        return o + 4 + 4 + 4 * len(t.attributes) + 1 + 12 + 4 + 1 + 4 * len(t.areas) + 4

    def test_non_finite_vertex_refused(self):
        raw = bytearray(self.data)
        o = 16 + 4 + len(self.nav.name) + 13
        struct.pack_into("<f", raw, o, float("nan"))
        self.bad(bytes(raw), "finite")

    def test_tree_size_must_match_its_depth(self):
        n = nav.parse(self.data)
        n.depth = 2
        with self.assertRaises(FormatError):
            nav.build(n)

    def test_links_must_name_triangles_that_exist(self):
        n = nav.parse(self.data)
        n.triangles[0].links[0].to = len(n.triangles)
        with self.assertRaises(FormatError):
            nav.build(n)

    def test_negative_link_costs_are_refused(self):
        """fuzz finding nav-invariant-51555b7285d8: a link cost of -5e37 made distances by the mesh negative
        (shortest paths need costs of at least 0; every game's cost is the distance between centroids)."""
        n = nav.parse(self.data)
        n.triangles[3].links[0].cost = -5.0e37
        with self.assertRaises(FormatError):
            nav.build(n)
        n.triangles[3].links[0].cost = 1.0
        raw = bytearray(nav.build(n))
        # the same link in the bytes: its cost is the first f32 after (neighbour, 0, edge)
        at = raw.find(struct.pack("<3If", n.triangles[3].links[0].to, 0, n.triangles[3].links[0].edge, 1.0))
        self.assertGreater(at, 0)
        struct.pack_into("<f", raw, at + 12, -5.0e37)
        self.bad(bytes(raw), "negative cost")

    def test_triangles_wider_than_the_games_are_refused_by_the_queries(self):
        n = nav.parse(self.data)
        n.positions[0] = (-1e9, n.positions[0][1], n.positions[0][2])
        from riftstone.errors import RiftError
        with self.assertRaises(RiftError):
            nav.Mesh(n)

    def test_stage_table_of_a_cut_executable_is_refused(self):
        """headers past the end of the file raised struct.error from stage_table (check_corpus, tools/nav_proof)."""
        from riftstone.errors import RiftError
        exe = bytearray(0x200)
        exe[:2] = b"MZ"
        struct.pack_into("<I", exe, 0x3C, 0x1FC)
        exe[0x1FC:0x200] = b"PE\0\0"
        with self.assertRaises(RiftError):
            nav.stage_table(bytes(exe))
        struct.pack_into("<I", exe, 0x3C, 0x40)                  # PE header in the file, its sections not
        exe[0x40:0x44] = b"PE\0\0"
        struct.pack_into("<H", exe, 0x40 + 6, 60)                # 60 sections...
        struct.pack_into("<H", exe, 0x40 + 20, 0xE0)             # ...after a 0xE0-byte optional header
        struct.pack_into("<H", exe, 0x40 + 0x18, 0x10B)
        with self.assertRaises(RiftError):
            nav.stage_table(bytes(exe))

    def test_a_damaged_game_entry_is_a_format_error(self):
        """nav.game_resource decompressed with zlib unchecked: a damaged entry of a game archive was a zlib.error
        (riftstone nav, dungeon, the bestiary), where modfiles.load says FormatError."""
        import tempfile
        from pathlib import Path

        from riftstone import arc, typemap
        from riftstone.game import Game
        from riftstone.index import Index
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "game"
            (root / "nativePC" / "rom").mkdir(parents=True)
            (root / "DDDA.exe").write_bytes(b"stub")
            name, tid = b"scr\\st420\\etc\\st420_nav", typemap.BY_EXT["nav"]
            good = arc.Entry.from_data(name, tid, self.data)
            bad = arc.Entry(name, tid, good.size, b"\x78\x9c" + b"\xff" * 64)
            (root / "nativePC" / "rom" / "nav.arc").write_bytes(arc.Archive([bad]).build())
            game = Game(root)
            idx = Index(game)
            try:
                idx.refresh()
                with self.assertRaises(FormatError):
                    nav.game_resource(game, idx, name, tid)
                (root / "nativePC" / "rom" / "nav.arc").write_bytes(arc.Archive([good]).build())
                idx.refresh()
                self.assertEqual(nav.game_resource(game, idx, name, tid), self.data)
                self.assertIsNone(nav.game_resource(game, idx, b"scr\\st421\\etc\\st421_nav", tid))
            finally:
                idx.close()

    def test_stage_table(self):
        self.assertEqual(nav.nav_stage(424), 420)
        self.assertEqual(nav.nav_stage(447), 446)
        self.assertEqual(nav.nav_stage(702), 702)
        self.assertEqual(nav.resource_name(435), "scr\\st430\\etc\\st430_nav")

    def test_info_names_the_regions(self):
        text = nav.info(nav.parse(self.data))
        self.assertIn("2 walkable regions", text)

    def test_info_answers_for_a_mesh_the_queries_cannot_use(self):
        """riftstone nav <file> and the open viewer describe any mesh parse accepts: a triangle of four corners
        parses (the loader reads any count), and info() used to build the query Mesh, which refuses it."""
        n = nav.parse(self.data)
        n.triangles[0].corners.append(n.triangles[0].corners[0])
        again = nav.parse(nav.build(n))
        from riftstone.errors import RiftError
        with self.assertRaises(RiftError):
            nav.Mesh(again)
        self.assertIn("2 walkable regions", nav.info(again))

    # the names: the loader reads each up to its first NUL into a 256-byte buffer on its stack (0x00CFEE30)
    def _with_name(self, name: bytes, n: int | None = None) -> bytes:
        d, old = self.data, len(self.nav.name)
        return d[:16] + struct.pack("<I", len(name) - 1 if n is None else n) + name + d[16 + 4 + old:]

    def test_a_name_is_its_characters_and_one_nul(self):
        longest = b"a" * 255 + b"\0"
        n = nav.parse(self._with_name(longest))
        self.assertEqual(n.name, longest)
        self.assertEqual(nav.build(n), self._with_name(longest))
        self.bad(self._with_name(b"a" * 256 + b"\0"), "buffer holds 255")
        self.bad(self._with_name(b"new\0Navigation\0"), "first NUL")       # the loader would stop at the first NUL
        self.bad(self._with_name(b"new Navigationx"), "first NUL")        # ...or read on past the name
        for name in (b"a" * 300 + b"\0", b"new\0Navigation\0", b"new Navigation"):
            n.name = name
            with self.assertRaises(FormatError):
                nav.build(n)

    def test_an_area_name_is_its_characters_and_one_nul(self):
        root = struct.pack("<HI", 0, 4) + b"root\0"
        self.assertEqual(self.data.count(root), 1)
        self.bad(self.data.replace(root, struct.pack("<HI", 0, 300) + b"r" * 300 + b"\0"), "buffer holds 255")
        self.bad(self.data.replace(root, struct.pack("<HI", 0, 4) + b"ro\0t\0"), "first NUL")
        n = nav.parse(self.data)
        n.areas[0].name = b"r" * 256 + b"\0"
        with self.assertRaises(FormatError):
            nav.build(n)

    def test_the_vertex_extras_byte_is_0_or_1(self):
        """the loader reads the extras only after a 1 (0x010992D6): any other byte parsed as 'none' and was
        written back as 0, so the file did not rebuild."""
        n = nav.parse(self.data)
        n.near_wall = n.wall_distance = None
        plain = nav.build(n)
        at = 16 + 4 + len(self.nav.name) + 12
        self.assertEqual((self.data[at], plain[at]), (1, 0))
        self.assertEqual(nav.build(nav.parse(plain)), plain)
        raw = bytearray(plain)
        raw[at] = 2
        self.bad(bytes(raw), "vertex-extras byte is 2")

    def test_numbers_that_would_not_rebuild_are_refused(self):
        """a signalling NaN (0x7F800001) came back from struct as a quiet one (0x7FC00001): every float the file
        carries must be finite, as the vertices and costs already were."""
        n = nav.parse(self.data)
        vertex0 = 16 + 4 + len(n.name) + 13
        tri0 = vertex0 + len(n.positions) * 15
        vector = tri0 + 4 + 4 + 4 * len(n.triangles[0].attributes) + 1
        t = n.triangles[0]
        first_link = vector + 16 + 1 + 4 * len(t.areas) + 4 + 4 * len(t.corners) + 4
        bounds = len(self.data) - sum(4 + 8 * len(c) for c in n.cells) - 32
        snan = bytes.fromhex("0100807f")
        for label, at in (("vector", vector), ("value", vector + 12), ("tail", first_link + 16), ("bounds", bounds)):
            for bits in (snan, struct.pack("<f", float("inf"))):
                raw = bytearray(self.data)
                raw[at:at + 4] = bits
                with self.subTest(label, bits=bits.hex()):
                    self.bad(bytes(raw), "finite")
        box = struct.pack("<B6f", nav.BOX, *n.areas[0].geometries[0].values)
        self.assertEqual(self.data.count(box), 1)
        self.bad(self.data.replace(box, box[:1] + snan + box[5:]), "finite")
        n.bounds = (float("nan"),) + n.bounds[1:]
        with self.assertRaises(FormatError):
            nav.build(n)
        n = nav.parse(self.data)
        n.triangles[0].vector = (0.0, 0.0, 1e39)          # past a float's range: struct.pack raised OverflowError
        with self.assertRaises(FormatError):
            nav.build(n)


class QueryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mesh = nav.Mesh(nav_fixture.corridor_with_island())

    def test_ground_under_a_point(self):
        s = self.mesh.locate((250.0, -300.0, -9750.0))
        self.assertIsNotNone(s)
        self.assertAlmostEqual(s.point[1], -350.0)
        self.assertAlmostEqual(s.gap, 50.0)
        self.assertIsNone(self.mesh.locate((250.0, 500.0, -9750.0)), "8.5 m over the floor is not standing on it")
        self.assertIsNone(self.mesh.locate((2000.0, -350.0, -9250.0)), "the hole has no ground")

    def test_nearest_ground(self):
        s = self.mesh.nearest((2000.0, -350.0, -9250.0), 600.0)
        self.assertIsNotNone(s)
        self.assertLess(math.dist(s.point, (2000.0, -350.0, -9250.0)), 260.0)
        self.assertIsNone(self.mesh.nearest((2000.0, -350.0, -9250.0), 100.0))

    def test_regions(self):
        floor = self.mesh.locate((0.0, -350.0, -9000.0)).triangle
        island = self.mesh.locate((8500.0, 650.0, 8500.0)).triangle
        self.assertNotEqual(self.mesh.component(floor), self.mesh.component(island))
        sizes = sorted(self.mesh.component_sizes().values())
        self.assertEqual(sizes, [8, 2 * (12 * 3 - 4)])
        self.assertEqual(self.mesh.main_component(), self.mesh.component(floor))

    def test_walking_round_the_hole_is_longer_than_the_straight_line(self):
        a = self.mesh.locate((2000.0, -350.0, -9750.0)).triangle      # row 0, beside the hole
        b = self.mesh.locate((2000.0, -350.0, -8750.0)).triangle      # row 2, across it
        d = self.mesh.distances([a])
        self.assertGreater(d[b], 10.0 + 5.0, "the walk goes round the 20 m hole")
        dist, parent = self.mesh.tree([a])
        self.assertEqual(dist, d)
        t, steps = b, 0
        while parent[t] != -1:
            self.assertIn(t, {u for u, _ in self.mesh.adj[parent[t]]})
            t = parent[t]
            steps += 1
        self.assertEqual(t, a)

    def test_room_to_the_edge(self):
        middle = self.mesh.clearance((-500.0 + 250.0, -350.0, -9250.0))      # row 1 beside the hole's west end
        edge = self.mesh.clearance((-950.0, -350.0, -9250.0))
        self.assertAlmostEqual(edge, 50.0, delta=1.0)
        self.assertGreater(middle, edge)
        self.assertGreater(len(self.mesh.walls()[0]), 0)


if __name__ == "__main__":
    unittest.main()
