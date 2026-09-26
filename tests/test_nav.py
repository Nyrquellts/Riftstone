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

    def test_stage_table(self):
        self.assertEqual(nav.nav_stage(424), 420)
        self.assertEqual(nav.nav_stage(447), 446)
        self.assertEqual(nav.nav_stage(702), 702)
        self.assertEqual(nav.resource_name(435), "scr\\st430\\etc\\st430_nav")

    def test_info_names_the_regions(self):
        text = nav.info(nav.parse(self.data))
        self.assertIn("2 walkable regions", text)


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
