"""Synthetic navigation meshes for the tests: floors of square cells, each two triangles, linked across every
shared edge the way the game's meshes are (the neighbour, the edge's number, the centroids' distance in metres),
with one root area and a one-node quadtree (valid for nav.parse; not a claim about the game's own tree)."""
from __future__ import annotations

import math

import helpers  # noqa: F401 (sys.path)
from riftstone import nav


def floor(x0: float, z0: float, nx: int, nz: int, cell: float, y: float, holes=()) -> tuple[list, list]:
    """(positions, corner triples) of an nx x nz floor of ``cell``-cm squares at height y, without the cells
    in ``holes`` ((i, j) pairs)."""
    positions, index, tris = [], {}, []

    def v(i, j):
        if (i, j) not in index:
            index[(i, j)] = len(positions)
            positions.append((x0 + i * cell, y, z0 + j * cell))
        return index[(i, j)]
    for i in range(nx):
        for j in range(nz):
            if (i, j) in holes:
                continue
            a, b, c, d = v(i, j), v(i + 1, j), v(i + 1, j + 1), v(i, j + 1)
            tris += [(a, c, b), (a, d, c)]
    return positions, tris


def build(parts) -> nav.Nav:
    """One mesh from several floors ((positions, tris) pairs); floors never share vertices, so each is its own
    walkable region unless its cells touch another's."""
    positions, tris = [], []
    for pos, tr in parts:
        base = len(positions)
        positions += pos
        tris += [tuple(base + k for k in t) for t in tr]
    edges = {}
    for t, corners in enumerate(tris):
        for e in range(3):
            key = frozenset((corners[e], corners[(e + 1) % 3]))
            edges.setdefault(key, []).append((t, e))

    def centroid(t):
        return tuple(sum(positions[k][a] for k in tris[t]) / 3 for a in range(3))
    links = [[] for _ in tris]
    for pair in edges.values():
        if len(pair) == 2:
            (t1, e1), (t2, e2) = pair
            cost = math.dist(centroid(t1), centroid(t2)) / 100.0
            links[t1].append(nav.Link(t2, e1, cost))
            links[t2].append(nav.Link(t1, e2, cost))
    triangles = [nav.Triangle(i, [0], list(c), links[i]) for i, c in enumerate(tris)]
    lo = tuple(min(p[a] for p in positions) for a in range(3))
    hi = tuple(max(p[a] for p in positions) for a in range(3))
    root = nav.Area(0, b"root\0", 0, [nav.Geometry(nav.BOX, lo + hi)], 0, len(tris), 0xFFFF)
    return nav.Nav(b"new Navigation\0", positions, triangles, [root], [0] * len(positions), [0] * len(positions),
                   depth=1, bounds=lo + (0.0,) + hi + (0.0,), cells=[[(t, 0) for t in range(len(tris))]])


def corridor_with_island() -> nav.Nav:
    """A 12 x 3 corridor of 5 m cells at height -350 whose middle row has a hole (cells 4..7 of row 1) -- walk
    round it -- beside a separate 2 x 2 platform 10 m up (another region)."""
    main = floor(-1000.0, -10000.0, 12, 3, 500.0, -350.0, holes={(4, 1), (5, 1), (6, 1), (7, 1)})
    island = floor(8000.0, 8000.0, 2, 2, 500.0, 650.0)
    return build([main, island])
