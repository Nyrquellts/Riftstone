"""rNavigationMesh (``.nav``): where a stage's AI can walk, as DDDA.exe's loader reads it -- and the
questions a mod asks of it: is this spot on walkable ground, can it be reached on foot from there, how far
is it by the mesh, how much room is around it.

Read from build 2364871: ``rNavigationMesh::load`` 0x01099E00 checks the header and calls the body reader
0x01099100, which reads the hierarchy through ``rAIPathBase``'s reader 0x011FB840; ``rNavigationMesh::save``
0x01099BA0 writes the same order.  Member names are the PS3 build's.  All 41 distinct Dark Arisen
files (42 names: st610 and st611 are the same bytes) and all 332 distinct Online files follow it to the last
byte (``tools/check_corpus.py --only nav``); both games use version 0x21.

The stream is packed little-endian, read field by field::

  mCoreHeader    "NAV\\0", u32 version 0x21, u32 0, u32 the u32 slots of mpNodeBuffer (every triangle's
                 lists together; the loader allocates that many and trusts it)
  mName          u32 n, then n + 1 bytes ending in NUL ("new Navigation" in 38 of 41 files)
  counts         u32 vertices, u32 triangles (mNumberOfNode), u32 node infos (0 in every file),
                 u8 1 when every vertex carries mpNearWall and mpWallDistance (1 in every file)
  vertex         float3 position (centimetres, stage space), then u8 near-wall and u16 wall distance
                 (0 and 0 in every file)
  triangle       (nodeData, 0x50 bytes in memory) s32 its own index, u32 n + u32[n] attributes (one
                 bitfield, ``getNodeAttribute``: 0 on 95%), u8 flag (0), float3 (0, 0, 1) and f32 (0) in every
                 file, u8 n + u32[n] areas (``getNodeArea``; none in any file), u32 n + s32[n] corners (three
                 vertex indices in every file), u32 n +
                 n links of (u32 neighbour, u32 0, u32 edge 0..2, f32 cost, f32 0, f32 0): the neighbour
                 across edge (corner e, corner e + 1); the cost is the distance between the two centroids in
                 metres (centimetres / 100: every one of Dark Arisen's 305,967 links within 0.1%, and all of
                 Online's 1,613,727 within 1% but 96 of one mesh, rm107, whose corners moved after); all links
                 but one in each game have their reverse
  hierarchy      (rAIPathBase::HierarchyArea) u16 areas, each: u16 id, u32 n + name, u32, u32 n geometries
                 of u8 kind (0 box: float3 min, float3 max; 1 oriented box: float3 extent, 4 x float4 rows;
                 2 sphere: f32 radius, float3 centre; another kind reads nothing), u16 first triangle,
                 u16 triangles, u16 parent, u8 n + children, u8 n + links; then mNumberOfTotalAreaChild and
                 mNumberOfTotalAreaLink (u16).  Every file has one area, "root": one box around the mesh,
                 triangles 0.., parent 0xFFFF
  totals         u32 mNumberOfTotalLink, u32 mNumberOfTotalAttribute, u32 mNumberOfTotalIndex
  node infos     u32 + 32 bytes each
  quadtree       (cAIQuadTree) u8 depth (7), u32 nodes ((4^depth - 1) / 3 = 5,461), float4 min, float4 max,
                 then per node u32 n + n x (u32 triangle, u32 0): the triangles under that square

Names: the mesh's members are the PS3 build's (mpPolygonVertex, mpNearWall, mpWallDistance,
mNumberOfTotal*); the node and area structs are defined in the MT Framework library, which the PS3 build
carries no debug records for, so their parts are named after its methods (``nodeData::getNodeAttribute``,
``getNodeArea``, ``HierarchyArea::getGeometry``, ``getChild``, ``getLink``).

Every count and index the loader uses unchecked (list sizes, vertex and neighbour indices, the buffer
total, the tree size) is checked here, so a mesh this module accepts is one the loader can read.

A stage does not always load its own mesh: the engine's stage table (0x01530348: 65 stage numbers, and
0x015303D0: 65 rows of 11 s16) names, in a row's seventh field, the stage whose ``_nav`` it loads (read at
0x00503368).  Eleven stages load another's: ``NAV_OF``.  Stages 100, 501 and 703 have no mesh (the
field's AI uses waypoints, ``.way``).
"""
from __future__ import annotations

import heapq
import math
import struct
from collections import defaultdict
from dataclasses import dataclass, field

from .errors import FormatError, RiftError

MAGIC = b"NAV\0"
VERSION = 0x21
LINK_WORDS = 6              # a link fills six u32 slots of mpNodeBuffer
QUAD_DEPTH_MAX = 10         # 7 in every file; 10 would be 349,525 tree nodes
MAX_COUNT = 1 << 22         # sanity bound on any count (the biggest mesh has 11,238 triangles)
BOX, OBB, SPHERE = 0, 1, 2
_GEOMETRY_FLOATS = {BOX: 6, OBB: 3 + 16, SPHERE: 4}

# Stage -> the stage whose navigation mesh it loads, where they differ (the engine's stage table, row
# field 6, read at 0x00503368; tools/check_corpus.py --only nav compares this with DDDA.exe).  Proved on the
# data too: stage 424's enemies stand on stage 420's mesh as closely as stage 420's own (median 0 cm).
NAV_OF = {402: 401, 421: 420, 423: 420, 424: 420, 425: 420, 431: 430, 435: 430, 436: 430,
          444: 443, 445: 443, 447: 446}
STAGE_TABLE = 0x01530348    # u16[65] stage numbers; the row for stage number N is its index here
STAGE_ROWS = 0x015303D0     # s16[65][11]; field 5 the merged collision's stage, field 6 the mesh's
NAV_FIELD = 6


def stage_table(exe: bytes) -> dict:
    """{stage: the stage whose navigation mesh it loads}, read from DDDA.exe's own tables (build 2364871:
    STAGE_TABLE, STAGE_ROWS field NAV_FIELD) -- how ``NAV_OF`` is checked against the player's exe."""
    if len(exe) < 0x200 or exe[:2] != b"MZ":
        raise RiftError("not a Windows executable")
    pe = struct.unpack_from("<I", exe, 0x3C)[0]
    if exe[pe:pe + 4] != b"PE\0\0" or struct.unpack_from("<H", exe, pe + 0x18)[0] != 0x10B:
        raise RiftError("not a 32-bit Windows executable")
    base = struct.unpack_from("<I", exe, pe + 0x34)[0]
    nsec, optsz = struct.unpack_from("<H", exe, pe + 6)[0], struct.unpack_from("<H", exe, pe + 20)[0]
    sections = [struct.unpack_from("<IIII", exe, pe + 24 + optsz + 40 * i + 8) for i in range(nsec)]

    def read(va: int, n: int) -> bytes:
        rva = va - base
        for vsize, sva, rsize, rptr in sections:
            if sva <= rva and rva + n <= sva + min(vsize, rsize):
                return exe[rptr + rva - sva:rptr + rva - sva + n]
        raise RiftError(f"0x{va:08x} is not in the executable's file (another build?)")
    stages = struct.unpack("<65H", read(STAGE_TABLE, 130))
    if stages[0] != 100 or not all(100 <= s <= 999 for s in stages):
        raise RiftError("the stage table is not where build 2364871 keeps it (another build?)")
    out = {}
    for k, s in enumerate(stages):
        row = struct.unpack("<11h", read(STAGE_ROWS + 22 * k, 22))
        out.setdefault(s, row[NAV_FIELD])
    return out


def nav_stage(stage: int) -> int:
    """The stage whose ``scr\\st<N>\\etc\\st<N>_nav`` the game loads for this stage."""
    return NAV_OF.get(stage, stage)


def resource_name(stage: int) -> str:
    """The navigation mesh the game loads for this stage (another stage's, for the eleven in ``NAV_OF``)."""
    n = nav_stage(stage)
    return f"scr\\st{n:03d}\\etc\\st{n:03d}_nav"


# -- the file ------------------------------------------------------------------------------------------
@dataclass
class Link:
    to: int                          # the neighbouring triangle
    edge: int                        # which edge it lies across: corners (edge, edge + 1)
    cost: float                      # metres between the two triangles' centroids
    word: int = 0                    # u32 after the neighbour (0 in every file)
    tail: tuple = (0.0, 0.0)         # two f32 after the cost (0 in every file)


@dataclass
class Triangle:
    index: int                       # s32: its own position (every file)
    attributes: list[int]            # u32s; one bitfield in every file
    corners: list[int]               # s32 vertex indices; three in every file
    links: list[Link]
    flag: int = 0                    # u8
    vector: tuple = (0.0, 0.0, 1.0)  # float3, (0, 0, 1) in every file
    value: float = 0.0               # f32, 0 in every file
    areas: list[int] = field(default_factory=list)   # u8-counted u32s (getNodeArea); none in any file

    @property
    def attribute(self) -> int:
        return self.attributes[0] if self.attributes else 0


@dataclass
class Geometry:
    kind: int                        # 0 box, 1 oriented box, 2 sphere, else nothing follows
    values: tuple                    # floats, as the kind reads them


@dataclass
class Area:
    id: int
    name: bytes                      # with its NUL
    value: int
    geometries: list[Geometry]
    first: int                       # first triangle
    count: int                       # triangles
    parent: int                      # 0xFFFF: none
    children: bytes = b""
    links: bytes = b""


@dataclass
class Nav:
    name: bytes                      # with its NUL
    positions: list[tuple]           # float3 per vertex, cm
    triangles: list[Triangle]
    areas: list[Area]
    near_wall: list[int] | None = None       # u8 per vertex when the file carries them
    wall_distance: list[int] | None = None   # u16 per vertex
    area_totals: tuple = (0, 0)              # u16 children, u16 links after the areas
    infos: list[tuple] = field(default_factory=list)     # (u32, 32 bytes)
    depth: int = 7
    bounds: tuple = (0.0,) * 8                # float4 min, float4 max
    cells: list[list[tuple]] = field(default_factory=list)   # per tree node: [(triangle, u32)]
    version: int = VERSION
    word: int = 0                             # the header's third u32 (0 in every file)


class _Reader:
    __slots__ = ("d", "o")

    def __init__(self, data: bytes):
        self.d = data
        self.o = 0

    def need(self, n: int) -> int:
        o = self.o
        if n < 0 or o + n > len(self.d):
            raise FormatError("nav", "the file ends inside a record", o)
        self.o = o + n
        return o

    def u8(self) -> int:
        return self.d[self.need(1)]

    def u16(self) -> int:
        return struct.unpack_from("<H", self.d, self.need(2))[0]

    def u32(self) -> int:
        return struct.unpack_from("<I", self.d, self.need(4))[0]

    def count(self, what: str, limit: int = MAX_COUNT) -> int:
        o = self.o
        n = self.u32()
        if n > limit:
            raise FormatError("nav", f"{what}: {n} is more than any mesh holds", o)
        return n

    def f(self, n: int) -> tuple:
        return struct.unpack_from(f"<{n}f", self.d, self.need(4 * n))

    def raw(self, n: int) -> bytes:
        o = self.need(n)
        return self.d[o:o + n]

    def text(self) -> bytes:
        n = self.count("a name's length", 0xFFFF)
        return self.raw(n + 1)


def _finite(values, what: str, where: int) -> None:
    if not all(math.isfinite(v) for v in values):
        raise FormatError("nav", f"{what} is not a finite number", where)


def parse(data: bytes) -> Nav:
    """A navigation mesh, or FormatError.  Checks everything the loader uses without checking."""
    if len(data) < 16 or data[:4] != MAGIC:
        raise FormatError("nav", "not a navigation mesh (NAV\\0)")
    _m, version, word, buffer_total = struct.unpack_from("<4sIII", data)
    if version != VERSION:
        raise FormatError("nav", f"version 0x{version:x}; both games' meshes are 0x{VERSION:x}")
    r = _Reader(data)
    r.o = 16
    name = r.text()
    nv, nt = r.count("vertices"), r.count("triangles")
    ni = r.count("node infos")
    extras = r.u8()
    positions, near, dist = [], ([] if extras == 1 else None), ([] if extras == 1 else None)
    for _ in range(nv):
        o = r.o
        p = r.f(3)
        _finite(p, "a vertex", o)
        positions.append(p)
        if extras == 1:
            near.append(r.u8())
            dist.append(r.u16())
    tris = []
    for i in range(nt):
        o = r.o
        index = struct.unpack_from("<i", data, r.need(4))[0]
        attributes = [r.u32() for _ in range(r.count("a triangle's attributes", 1 << 16))]
        flag = r.u8()
        vector = r.f(3)
        value = r.f(1)[0]
        areas = [r.u32() for _ in range(r.u8())]
        n = r.count("a triangle's corners", 1 << 16)
        corners = list(struct.unpack_from(f"<{n}i", data, r.need(4 * n)))
        if any(not 0 <= c < nv for c in corners):
            raise FormatError("nav", f"triangle {i} names a vertex past the mesh's {nv}", o)
        links = []
        for _ in range(r.count("a triangle's links", 1 << 16)):
            lo = r.o
            to, w, edge = struct.unpack_from("<3I", data, r.need(12))
            cost, t1, t2 = r.f(3)
            if to >= nt:
                raise FormatError("nav", f"triangle {i} links to triangle {to} past the mesh's {nt}", lo)
            _finite((cost,), "a link's cost", lo)
            if cost < 0:
                raise FormatError("nav", f"triangle {i}'s link to {to} has a negative cost (every game's is the "
                                         "distance between the two triangles)", lo)
            links.append(Link(to, edge, cost, w, (t1, t2)))
        tris.append(Triangle(index, attributes, corners, links, flag, vector, value, areas))
    used = sum(len(t.attributes) + len(t.areas) + len(t.corners) + LINK_WORDS * len(t.links) for t in tris)
    if used != buffer_total:
        raise FormatError("nav", f"the header reserves {buffer_total} buffer slots but the triangles fill {used}", 12)
    areas = []
    for _ in range(r.u16()):
        aid = r.u16()
        aname = r.text()
        avalue = r.u32()
        o = r.o
        ng = r.u32()
        if ng > 255:
            raise FormatError("nav", f"an area lists {ng} geometries; the loader keeps a byte's worth", o)
        geometries = []
        for _ in range(ng):
            kind = r.u8()
            geometries.append(Geometry(kind, r.f(_GEOMETRY_FLOATS[kind]) if kind in _GEOMETRY_FLOATS else ()))
        first, count, parent = r.u16(), r.u16(), r.u16()
        children = r.raw(r.u8())
        alinks = r.raw(r.u8())
        areas.append(Area(aid, aname, avalue, geometries, first, count, parent, children, alinks))
    area_totals = (r.u16(), r.u16())
    o = r.o
    totals = (r.u32(), r.u32(), r.u32())
    want = _totals(tris)
    if totals != want:
        raise FormatError("nav", f"the totals (links, attributes, corners) {totals} differ from the triangles' "
                                 f"{want}", o)
    infos = []
    for _ in range(ni):
        infos.append((r.u32(), r.raw(32)))
    o = r.o
    depth = r.u8()
    nodes = r.u32()
    if not 1 <= depth <= QUAD_DEPTH_MAX or nodes != (4 ** depth - 1) // 3:
        raise FormatError("nav", f"a quadtree of depth {depth} has {(4 ** depth - 1) // 3} nodes, not {nodes}", o)
    bounds = r.f(8)
    cells = []
    for _ in range(nodes):
        o = r.o
        n = r.count("a tree node's triangles", MAX_COUNT)
        flat = struct.unpack_from(f"<{2 * n}I", data, r.need(8 * n))
        pairs = list(zip(flat[::2], flat[1::2]))
        if any(t >= nt for t, _ in pairs):
            raise FormatError("nav", f"a tree node names a triangle past the mesh's {nt}", o)
        cells.append(pairs)
    if r.o != len(data):
        raise FormatError("nav", f"{len(data) - r.o} bytes follow the quadtree", r.o)
    return Nav(name, positions, tris, areas, near, dist, area_totals, infos, depth, bounds, cells, version, word)


def _totals(tris) -> tuple:
    return (sum(len(t.links) for t in tris), sum(len(t.attributes) for t in tris), sum(len(t.corners) for t in tris))


def build(nav: Nav) -> bytes:
    """The file the game reads, with every count and total computed from the content (as the game's own
    writer does)."""
    out = bytearray()
    p = out.extend
    tris = nav.triangles
    if (nav.near_wall is None) != (nav.wall_distance is None):
        raise FormatError("nav", "near-wall flags and wall distances go together")
    extras = nav.near_wall is not None
    if extras and not len(nav.near_wall) == len(nav.wall_distance) == len(nav.positions):
        raise FormatError("nav", "every vertex needs its near-wall flag and wall distance")
    buffer_total = sum(len(t.attributes) + len(t.areas) + len(t.corners) + LINK_WORDS * len(t.links) for t in tris)
    p(struct.pack("<4sIII", MAGIC, nav.version, nav.word, buffer_total))
    if not nav.name.endswith(b"\0"):
        raise FormatError("nav", "the mesh's name must end in NUL")
    p(struct.pack("<I", len(nav.name) - 1) + nav.name)
    p(struct.pack("<IIIB", len(nav.positions), len(tris), len(nav.infos), 1 if extras else 0))
    for i, pos in enumerate(nav.positions):
        p(struct.pack("<3f", *pos))
        if extras:
            p(struct.pack("<BH", nav.near_wall[i], nav.wall_distance[i]))
    nv, nt = len(nav.positions), len(tris)
    for i, t in enumerate(tris):
        if any(not 0 <= c < nv for c in t.corners) or any(not 0 <= lk.to < nt for lk in t.links):
            raise FormatError("nav", f"triangle {i} names a vertex or a neighbour the mesh does not have")
        if any(not (math.isfinite(lk.cost) and lk.cost >= 0) for lk in t.links):
            raise FormatError("nav", f"triangle {i} has a link whose cost is negative or not a number")
        if len(t.areas) > 255:
            raise FormatError("nav", f"triangle {i} lists more than 255 areas")
        p(struct.pack("<iI", t.index, len(t.attributes)))
        p(struct.pack(f"<{len(t.attributes)}I", *t.attributes))
        p(struct.pack("<B3ffB", t.flag, *t.vector, t.value, len(t.areas)))
        p(struct.pack(f"<{len(t.areas)}I", *t.areas))
        p(struct.pack(f"<I{len(t.corners)}i", len(t.corners), *t.corners))
        p(struct.pack("<I", len(t.links)))
        for lk in t.links:
            p(struct.pack("<3I3f", lk.to, lk.word, lk.edge, lk.cost, *lk.tail))
    p(struct.pack("<H", len(nav.areas)))
    for a in nav.areas:
        if not a.name.endswith(b"\0") or len(a.geometries) > 255 or len(a.children) > 255 or len(a.links) > 255:
            raise FormatError("nav", f"area {a.id} does not fit the loader (name ends in NUL, at most 255 each)")
        p(struct.pack("<HI", a.id, len(a.name) - 1) + a.name)
        p(struct.pack("<II", a.value, len(a.geometries)))
        for g in a.geometries:
            want = _GEOMETRY_FLOATS.get(g.kind, 0)
            if len(g.values) != want:
                raise FormatError("nav", f"a kind-{g.kind} area geometry has {want} numbers, not {len(g.values)}")
            p(struct.pack(f"<B{want}f", g.kind, *g.values))
        p(struct.pack("<HHHB", a.first, a.count, a.parent, len(a.children)) + a.children)
        p(struct.pack("<B", len(a.links)) + a.links)
    p(struct.pack("<HH", *nav.area_totals))
    p(struct.pack("<III", *_totals(tris)))
    for word, blob in nav.infos:
        if len(blob) != 32:
            raise FormatError("nav", "a node info is a word and 32 bytes")
        p(struct.pack("<I", word) + blob)
    if len(nav.cells) != (4 ** nav.depth - 1) // 3:
        raise FormatError("nav", f"a quadtree of depth {nav.depth} has {(4 ** nav.depth - 1) // 3} nodes")
    p(struct.pack("<BI8f", nav.depth, len(nav.cells), *nav.bounds))
    for cell in nav.cells:
        if any(not 0 <= t < nt for t, _ in cell):
            raise FormatError("nav", "a tree node names a triangle the mesh does not have")
        p(struct.pack(f"<I{2 * len(cell)}I", len(cell), *(v for pair in cell for v in pair)))
    return bytes(out)


def info(nav: Nav) -> str:
    m = Mesh(nav)
    comps = sorted(m.component_sizes().values(), reverse=True)
    name = nav.name.rstrip(b"\0").decode("latin-1")
    attrs = sum(1 for t in nav.triangles if t.attribute)
    lo = [min(p[a] for p in nav.positions) for a in range(3)] if nav.positions else [0.0] * 3
    hi = [max(p[a] for p in nav.positions) for a in range(3)] if nav.positions else [0.0] * 3
    size = " x ".join(f"{(hi[a] - lo[a]) / 100:.0f}" for a in (0, 2))
    return (f"rNavigationMesh {name!r}: {len(nav.triangles)} triangles over {len(nav.positions)} vertices, "
            f"{sum(len(t.links) for t in nav.triangles)} links, {size} m, {len(comps)} walkable region"
            f"{'' if len(comps) == 1 else 's'} (largest {comps[0] if comps else 0} triangles), "
            f"{attrs} triangles with attribute bits")


# -- the queries -----------------------------------------------------------------------------------------
GRID = 400.0                 # cm: the lookup grid's cell (a few triangles each)
SPAN_MAX = 20000.0           # cm: a triangle wider than 200 m is refused by the queries (the games' widest: 75 m)
STAND_ABOVE = 60.0           # a spot counts as standing on a triangle up to 60 cm under its surface...
STAND_BELOW = 300.0          # ...or 3 m over it (the game's walkers stand at median 0 cm, 95% within 36 cm)


@dataclass(frozen=True)
class Spot:
    """A point on the mesh: its triangle, the point on the surface, and how far the asked point was above it."""
    triangle: int
    point: tuple
    gap: float


class Mesh:
    """Queries over one navigation mesh: ground under a point, walkable regions, distances by the mesh."""

    def __init__(self, nav: Nav):
        self.nav = nav
        self.positions = nav.positions
        self.tris = []
        for i, t in enumerate(nav.triangles):
            if len(t.corners) != 3:
                raise RiftError(f"navigation triangle {i} has {len(t.corners)} corners; queries need three")
            self.tris.append(tuple(t.corners))
        self.attr = [t.attribute for t in nav.triangles]
        self.adj = [[(lk.to, lk.cost) for lk in t.links] for t in nav.triangles]
        self._grid = defaultdict(list)
        for i, (a, b, c) in enumerate(self.tris):
            pa, pb, pc = self.positions[a], self.positions[b], self.positions[c]
            if max(max(p[k] for p in (pa, pb, pc)) - min(p[k] for p in (pa, pb, pc)) for k in (0, 2)) > SPAN_MAX:
                raise RiftError(f"navigation triangle {i} is wider than {SPAN_MAX / 100:.0f} m (the games' widest is "
                                "75 m); this mesh cannot be queried")
            for gx in range(math.floor(min(pa[0], pb[0], pc[0]) / GRID), math.floor(max(pa[0], pb[0], pc[0]) / GRID) + 1):
                for gz in range(math.floor(min(pa[2], pb[2], pc[2]) / GRID),
                                math.floor(max(pa[2], pb[2], pc[2]) / GRID) + 1):
                    self._grid[(gx, gz)].append(i)
        self._comp = None
        self._walls = None

    # geometry
    def corners(self, t: int) -> tuple:
        return tuple(self.positions[i] for i in self.tris[t])

    def centroid(self, t: int) -> tuple:
        a, b, c = self.corners(t)
        return tuple((a[k] + b[k] + c[k]) / 3 for k in range(3))

    def area(self, t: int) -> float:
        """The triangle's area seen from above, m^2."""
        a, b, c = self.corners(t)
        return abs((b[0] - a[0]) * (c[2] - a[2]) - (c[0] - a[0]) * (b[2] - a[2])) / 2 / 1e4

    def height(self, t: int, x: float, z: float) -> float | None:
        """The surface height of triangle t over (x, z), or None when (x, z) is outside it."""
        a, b, c = self.corners(t)
        d = (b[2] - c[2]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[2] - c[2])
        if abs(d) < 1e-9:
            return None
        l1 = ((b[2] - c[2]) * (x - c[0]) + (c[0] - b[0]) * (z - c[2])) / d
        l2 = ((c[2] - a[2]) * (x - c[0]) + (a[0] - c[0]) * (z - c[2])) / d
        l3 = 1.0 - l1 - l2
        if min(l1, l2, l3) < -1e-6:
            return None
        return l1 * a[1] + l2 * b[1] + l3 * c[1]

    def _near(self, x: float, z: float, r: float):
        seen = set()
        for gx in range(math.floor((x - r) / GRID), math.floor((x + r) / GRID) + 1):
            for gz in range(math.floor((z - r) / GRID), math.floor((z + r) / GRID) + 1):
                for t in self._grid.get((gx, gz), ()):
                    if t not in seen:
                        seen.add(t)
                        yield t

    def locate(self, p, above: float = STAND_ABOVE, below: float = STAND_BELOW) -> Spot | None:
        """The ground under p: the highest triangle surface from ``below`` under p to ``above`` over it."""
        x, y, z = p
        best = None
        for t in self._near(x, z, 0.0):
            h = self.height(t, x, z)
            if h is not None and y - below <= h <= y + above and (best is None or h > best[0]):
                best = (h, t)
        return None if best is None else Spot(best[1], (x, best[0], z), y - best[0])

    def nearest(self, p, radius: float, above: float = STAND_ABOVE, below: float = STAND_BELOW,
                within: set | None = None) -> Spot | None:
        """The closest point of the mesh to p (by distance across the ground) within ``radius`` cm and the
        same height window as ``locate``; ``within`` limits it to those triangles."""
        x, y, z = p
        best = None
        for t in self._near(x, z, radius):
            if within is not None and t not in within:
                continue
            qx, qz = _closest_in_triangle(self.corners(t), x, z)
            d = math.hypot(qx - x, qz - z)
            if d > radius or (best is not None and d >= best[0]):
                continue
            h = self.height(t, qx, qz)
            if h is None or not y - below <= h <= y + above:
                continue
            best = (d, t, (qx, h, qz))
        return None if best is None else Spot(best[1], best[2], y - best[2][1])

    # regions and distances
    def component(self, t: int) -> int:
        """The walkable region triangle t belongs to (triangles joined by links)."""
        if self._comp is None:
            parent = list(range(len(self.tris)))

            def find(i):
                while parent[i] != i:
                    parent[i] = parent[parent[i]]
                    i = parent[i]
                return i
            for i, links in enumerate(self.adj):
                for j, _ in links:
                    ri, rj = find(i), find(j)
                    if ri != rj:
                        parent[ri] = rj
            self._comp = [find(i) for i in range(len(self.tris))]
        return self._comp[t]

    def component_sizes(self) -> dict:
        sizes = defaultdict(int)
        for t in range(len(self.tris)):
            sizes[self.component(t)] += 1
        return dict(sizes)

    def main_component(self) -> int | None:
        sizes = self.component_sizes()
        return max(sizes, key=lambda c: (sizes[c], -c)) if sizes else None

    def distances(self, sources, limit: float = math.inf) -> dict:
        """Metres by the mesh from the source triangles to every triangle within ``limit`` (Dijkstra over
        the links' own costs)."""
        dist = {}
        heap = [(0.0, s) for s in sources]
        heapq.heapify(heap)
        while heap:
            d, t = heapq.heappop(heap)
            if t in dist:
                continue
            dist[t] = d
            for u, c in self.adj[t]:
                nd = d + c
                if u not in dist and nd <= limit:
                    heapq.heappush(heap, (nd, u))
        return dist

    def tree(self, sources) -> tuple[dict, dict]:
        """(metres, parent) by the mesh from the source triangles to every triangle they reach: the tree of
        shortest paths (a source's parent is -1)."""
        dist, parent = {}, {}
        heap = [(0.0, s, -1) for s in sources]
        heapq.heapify(heap)
        while heap:
            d, t, p = heapq.heappop(heap)
            if t in dist:
                continue
            dist[t], parent[t] = d, p
            for u, c in self.adj[t]:
                if u not in dist:
                    heapq.heappush(heap, (d + c, u, t))
        return dist, parent

    # room
    def walls(self):
        """Every triangle edge no link crosses: the mesh's outline (where walking stops)."""
        if self._walls is None:
            walls = []
            for t, (tri, links) in enumerate(zip(self.nav.triangles, self.adj)):
                crossed = {lk.edge for lk in tri.links}
                c = self.tris[t]
                for e in range(3):
                    if e not in crossed:
                        walls.append((self.positions[c[e]], self.positions[c[(e + 1) % 3]]))
            grid = defaultdict(list)
            for i, (a, b) in enumerate(walls):
                for gx in range(math.floor(min(a[0], b[0]) / GRID), math.floor(max(a[0], b[0]) / GRID) + 1):
                    for gz in range(math.floor(min(a[2], b[2]) / GRID), math.floor(max(a[2], b[2]) / GRID) + 1):
                        grid[(gx, gz)].append(i)
            self._walls = (walls, grid)
        return self._walls

    def clearance(self, p, limit: float = 2000.0, rise: float = 250.0) -> float:
        """Centimetres from p (across the ground) to the nearest edge of the mesh within ``rise`` of p's
        height, up to ``limit``: how much room a large enemy has there."""
        walls, grid = self.walls()
        x, y, z = p
        best = limit
        seen = set()
        for gx in range(math.floor((x - limit) / GRID), math.floor((x + limit) / GRID) + 1):
            for gz in range(math.floor((z - limit) / GRID), math.floor((z + limit) / GRID) + 1):
                for i in grid.get((gx, gz), ()):
                    if i in seen:
                        continue
                    seen.add(i)
                    a, b = walls[i]
                    if min(a[1], b[1]) > y + rise or max(a[1], b[1]) < y - rise:
                        continue
                    best = min(best, _segment_distance(a, b, x, z))
        return best


def _segment_distance(a, b, x: float, z: float) -> float:
    dx, dz = b[0] - a[0], b[2] - a[2]
    n = dx * dx + dz * dz
    u = 0.0 if n == 0 else max(0.0, min(1.0, ((x - a[0]) * dx + (z - a[2]) * dz) / n))
    return math.hypot(a[0] + u * dx - x, a[2] + u * dz - z)


def _closest_in_triangle(corners, x: float, z: float) -> tuple:
    """The closest point to (x, z) of a triangle seen from above, nudged a hair inside it."""
    (ax, _, az), (bx, _, bz), (cx, _, cz) = corners

    def side(px, pz, qx, qz):
        return (qx - px) * (z - pz) - (qz - pz) * (x - px)
    s1, s2, s3 = side(ax, az, bx, bz), side(bx, bz, cx, cz), side(cx, cz, ax, az)
    if (s1 >= 0 and s2 >= 0 and s3 >= 0) or (s1 <= 0 and s2 <= 0 and s3 <= 0):
        return x, z
    best = None
    for (px, pz), (qx, qz) in (((ax, az), (bx, bz)), ((bx, bz), (cx, cz)), ((cx, cz), (ax, az))):
        dx, dz = qx - px, qz - pz
        n = dx * dx + dz * dz
        u = 0.0 if n == 0 else max(0.0, min(1.0, ((x - px) * dx + (z - pz) * dz) / n))
        q = (px + u * dx, pz + u * dz)
        d = (q[0] - x) ** 2 + (q[1] - z) ** 2
        if best is None or d < best[0]:
            best = (d, q)
    mx, mz = (ax + bx + cx) / 3, (az + bz + cz) / 3
    qx, qz = best[1]
    return qx + (mx - qx) * 1e-3, qz + (mz - qz) * 1e-3


# -- a stage's mesh and its doors --------------------------------------------------------------------------
_MESHES: dict = {}


def game_resource(game, idx, name: bytes, type_id: int) -> bytes | None:
    """One resource as the game ships it (the first archive holding it), reading only that entry."""
    from .corpus import directory, encrypted, payload

    arcs = idx.archives_with(name, type_id)
    if not arcs:
        return None
    path = game.vanilla_arc(arcs[0])
    for n, t, stored, _size, off in directory(path):
        if n == name and t == type_id:
            with open(path, "rb") as fh:
                fh.seek(off)
                return payload(fh.read(stored), encrypted(path))
    return None


def stage_mesh(game, idx, stage: int, mod_root=None) -> Mesh | None:
    """The navigation mesh the game loads for this stage (a mod's own binary copy first), or None when the
    stage has none (the open field, 501, 703)."""
    from . import modfiles, typemap

    name, tid = resource_name(stage).encode("latin-1"), typemap.BY_EXT["nav"]
    if mod_root is not None:
        own = modfiles.paths(mod_root, name, tid)[1]
        if own.is_file():
            return Mesh(parse(own.read_bytes()))
    key = (str(game.root).lower(), name)
    if key not in _MESHES:
        data = game_resource(game, idx, name, tid)
        _MESHES[key] = Mesh(parse(data)) if data is not None else None
    return _MESHES[key]


def start_positions(game, idx, stage: int) -> list[tuple]:
    """Where the player enters the stage: each ``rStartPos::Info.Pos`` of ``scr\\st<N>\\etc\\st<N>.stp`` (the
    doors the stage is reached by; on the mesh at median 0 cm in every stage that has one)."""
    from . import typemap, xfs

    data = game_resource(game, idx, f"scr\\st{stage:03d}\\etc\\st{stage:03d}".encode("latin-1"), typemap.BY_EXT["stp"])
    if data is None:
        return []
    doc = xfs.parse(data)
    out = []
    for obj in xfs.walk(doc.root):
        cls = doc.classes[obj.cls]
        if cls.name != "rStartPos::Info":
            continue
        for prop, vals in zip(cls.props, obj.fields):
            if prop.name == "Pos" and vals and len(vals[0]) >= 3 and all(math.isfinite(v) for v in vals[0][:3]):
                out.append(tuple(float(v) for v in vals[0][:3]))
    return out
