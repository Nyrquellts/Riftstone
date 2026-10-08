"""rCollision (``.sbc``): stage and object collision meshes, laid out as DDDA.exe's loader reads them.

Read from build 2364871 -- ``rCollision::loadCore`` 0x010A30D0, ``cBVHCollision::loadCore`` 0x0116D2F0,
``rCollision::bulkAllocateMemoryAll`` 0x0109A290 -- with field names from the PS3 build
(``rCollision::Header``, ``Triangle``, ``MaterialInfo``).  All 1,496 distinct vanilla files follow it: every
section adds up to the file's size (``tools/check_corpus.py --only sbc``).  Dragon's Dogma Online's files
have version 0x77DF43D8 and the same layout: all 4,042 distinct ones pass the same checks
(``--game ddo --only sbc``), so a mesh moves between the games by its version word (``for_game``).

  0x00  Header, 0x50: "SBC\\xff", u32 version 0x77DF2114, u32 space division of the parts and of each part's
        triangles (0 = bounding-volume tree in every vanilla file; 2 = grid, not read here), u16 parts,
        u16 materials, u32 leaves, u32 triangles, u32 vertices, u32 modify ids (runtime only, not in the file),
        MtAABB of everything at 0x30
  0x50  u32 the tree node memory the loader allocates (decided by the node counts; moving changes nothing)
  0x54  PartsInfo[parts], 0x50 each: MtAABB, three words the loader overwrites with pointers, then
        (start, count) of the part's leaves, triangles and vertices, an id, two more words
  ...   one tree per part (over its triangles), then one tree over the parts.  A tree: "BVHC", u32 version
        0x77B17B24, u32 node kind (1 = 4-wide nodes of 0x70 bytes -- every vanilla tree; 2 = binary nodes
        of 0x50 bytes, none shipped), MtAABB root at +0x10, u32 node count at +0x30; 0x40 bytes, then the
        nodes.  A 4-wide node: one mask byte four times (low nibble: lane j points at a node, high: at a
        leaf; neither: empty -- 1.4 million empty lanes, all holding real, finite bounds), u16 child[4],
        four 0xCD fill bytes, then minX[4] minY[4] minZ[4] maxX[4] maxY[4] maxZ[4]
  ...   Triangle[triangles], 0x20: normal, u16 vertex[3] (counted from the part's first vertex), u16 material,
        u32 attribute, u8 adjust[3], u8, u32 physics -- no plane distance, so a translation leaves triangles
        untouched
  ...   Vertex[vertices], 0x10: float3 position, u32 0
  ...   MaterialInfo[materials], 0x20 (attribute words); Leaf[leaves], 10 bytes (triangle numbers)

Measured on every vanilla file: each part's vertices lie inside its box and each part box inside the
header's (26,137 of 26,137); trees may reach about 1 cm past their part (the builder's margin); every one of the
2,920,986 triangles belongs to one part, names three of that part's vertices, and stores its corners' own normal.

What moves when a collision mesh is translated: the header box, each part box, each tree's root box, every
lane of every node (min and max per axis), and every vertex.  Everything else stays byte for byte.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .errors import FormatError, ParamError

MAGIC = b"SBC\xff"
VERSION = 0x77DF2114                         # Dark Arisen's
VERSION_DDO = 0x77DF43D8                     # Online's: the same layout
VERSIONS = {"ddda": VERSION, "ddo": VERSION_DDO}
TREE_TAG = b"BVHC"
TREE_VERSION = 0x77B17B24
QUAD, BINARY = 1, 2
NODE_SIZE = {QUAD: 0x70, BINARY: 0x50}
HEADER = struct.Struct("<4sIIIHHIIII")       # up to the modify count; MtAABB at 0x30
PART = 0x50
TRIANGLE, VERTEX, MATERIAL, LEAF = 0x20, 0x10, 0x20, 10
BOUNDS_TOLERANCE = 1.0                        # cm: float noise when checking containment


@dataclass
class Tree:
    offset: int
    kind: int
    nodes: int

    @property
    def first_node(self) -> int:
        return self.offset + 0x40


@dataclass
class Sbc:
    parts: int
    materials: int
    leaves: int
    triangles: int
    vertices: int
    trees: list[Tree] = field(default_factory=list)       # one per part, then the parts tree
    triangle_offset: int = 0
    vertex_offset: int = 0
    material_offset: int = 0
    leaf_offset: int = 0

    def part_offset(self, i: int) -> int:
        return 0x54 + i * PART


def parse(data: bytes) -> Sbc:
    """The layout of a collision file, or FormatError.  Checks everything the loader relies on."""
    if len(data) < 0x54 or data[:4] != MAGIC:
        raise FormatError("sbc", "not a collision file (SBC\\xff)")
    _m, ver, div_parts, div_tris, parts, mats, leaves, tris, verts, _modify = HEADER.unpack_from(data)
    if ver not in (VERSION, VERSION_DDO):
        raise FormatError("sbc", f"version 0x{ver:08x}; Dark Arisen's is 0x{VERSION:08x}, Online's 0x{VERSION_DDO:08x}")
    if div_parts > 1 or div_tris > 1:
        raise FormatError("sbc", "a grid-divided collision file (no vanilla file has one) is not read here")
    s = Sbc(parts, mats, leaves, tris, verts)
    o = 0x54 + parts * PART
    for _ in range(parts + 1):
        if o + 0x40 > len(data):
            raise FormatError("sbc", "a tree runs past the end of the file", o)
        tag, tver, kind = struct.unpack_from("<4sII", data, o)
        if tag != TREE_TAG or tver != TREE_VERSION or kind not in NODE_SIZE:
            raise FormatError("sbc", f"no tree where one belongs ({tag!r}, 0x{tver:08x}, kind {kind})", o)
        count = struct.unpack_from("<I", data, o + 0x30)[0]
        s.trees.append(Tree(o, kind, count))
        o += 0x40 + count * NODE_SIZE[kind]
    s.triangle_offset = o
    s.vertex_offset = o + tris * TRIANGLE
    s.material_offset = s.vertex_offset + verts * VERTEX
    s.leaf_offset = s.material_offset + mats * MATERIAL
    end = s.leaf_offset + leaves * LEAF
    if end != len(data):
        raise FormatError("sbc", f"the sections end at 0x{end:x} but the file is 0x{len(data):x} bytes")
    for i in range(parts):
        p = s.part_offset(i)
        ls, ln, ts, tn, vs, vn = struct.unpack_from("<6I", data, p + 0x2C)
        if ls + ln > leaves or ts + tn > tris or vs + vn > verts:
            raise FormatError("sbc", f"part {i} names leaves, triangles or vertices past the file's", p)
    for t in s.trees:
        for k in range(t.nodes if t.kind == QUAD else 0):
            b = t.first_node + k * NODE_SIZE[QUAD]
            if data[b:b + 4] != bytes([data[b]]) * 4:
                raise FormatError("sbc", "a tree node's mask bytes differ", b)
            m = data[b]
            kids = struct.unpack_from("<4H", data, b + 4)
            if any((m >> j) & 1 and kids[j] >= t.nodes for j in range(4)):
                raise FormatError("sbc", "a tree node points past its tree", b)
    return s


def for_game(data: bytes, game: str) -> bytes:
    """The same mesh as the other game ('ddda' / 'ddo') stores it: only the version word differs."""
    parse(data)
    return data[:4] + struct.pack("<I", VERSIONS[game]) + data[8:]


def _vec3_offsets(s: Sbc, data: bytes) -> list[int]:
    """Offsets of every float triple (x, y, z) that is a position: boxes and vertices."""
    offs = [0x30, 0x40]
    for i in range(s.parts):
        p = s.part_offset(i)
        offs += [p, p + 0x10]
    for t in s.trees:
        offs += [t.offset + 0x10, t.offset + 0x20]
    offs += [s.vertex_offset + k * VERTEX for k in range(s.vertices)]
    return offs


def positions(data: bytes) -> list[tuple[float, float, float]]:
    s = parse(data)
    return [struct.unpack_from("<3f", data, s.vertex_offset + k * VERTEX) for k in range(s.vertices)]


def triangles(data: bytes) -> list[tuple[tuple, tuple, tuple, tuple]]:
    """Every triangle as (corner, corner, corner, its stored normal).  A triangle's three u16 vertex numbers count
    from its part's first vertex (a file holds more vertices than a u16 names), so each part's triangles are read
    with that part's vertex start; one naming a vertex past its part's is a FormatError."""
    s = parse(data)
    verts = [v[:3] for v in struct.iter_unpack("<3fI", data[s.vertex_offset:s.vertex_offset + s.vertices * VERTEX])]
    out = []
    for i in range(s.parts):
        _ls, _ln, ts, tn, vs, vn = struct.unpack_from("<6I", data, s.part_offset(i) + 0x2C)
        for k in range(ts, ts + tn):
            o = s.triangle_offset + k * TRIANGLE
            nx, ny, nz, a, b, c = struct.unpack_from("<3f3H", data, o)
            if max(a, b, c) >= vn:
                raise FormatError("sbc", f"a triangle of part {i} names a vertex past the part's {vn}", o)
            out.append((verts[vs + a], verts[vs + b], verts[vs + c], (nx, ny, nz)))
    return out


def moved_floats(data: bytes) -> list[tuple[int, int]]:
    """(offset, axis) of every float a translation moves -- boxes, vertices and every node lane."""
    s = parse(data)
    if any(t.kind != QUAD for t in s.trees):
        raise ParamError("a binary-tree collision file (no vanilla file has one) is not moved")
    out = [(o + 4 * a, a) for o in _vec3_offsets(s, data) for a in range(3)]
    for t in s.trees:
        for k in range(t.nodes):
            b = t.first_node + k * NODE_SIZE[QUAD] + 16
            for a in range(3):                       # minX/Y/Z then maxX/Y/Z, four lanes each
                out += [(b + 16 * a + 4 * j, a) for j in range(4)]
                out += [(b + 16 * (a + 3) + 4 * j, a) for j in range(4)]
    return out


def translate(data: bytes, d: tuple[float, float, float]) -> bytes:
    """The collision moved by ``d``.  Each float rounds to the nearest float32, as a sum would."""
    out = bytearray(data)
    for off, axis in moved_floats(data):
        v = struct.unpack_from("<f", out, off)[0]
        try:
            struct.pack_into("<f", out, off, v + d[axis])
        except OverflowError:
            raise ParamError("moving the collision overflows a float") from None
    return bytes(out)


def bounds_problems(data: bytes) -> list[str]:
    """What breaks the containment every vanilla file keeps: a part's vertices outside its box, a part
    box outside the header's."""
    s = parse(data)
    out = []
    hlo, hhi = struct.unpack_from("<3f", data, 0x30), struct.unpack_from("<3f", data, 0x40)
    for i in range(s.parts):
        p = s.part_offset(i)
        plo, phi = struct.unpack_from("<3f", data, p), struct.unpack_from("<3f", data, p + 0x10)
        vs, vn = struct.unpack_from("<2I", data, p + 0x3C)
        pts = [struct.unpack_from("<3f", data, s.vertex_offset + k * VERTEX) for k in range(vs, vs + vn)]
        tol = BOUNDS_TOLERANCE
        if pts and any(min(q[a] for q in pts) < plo[a] - tol or max(q[a] for q in pts) > phi[a] + tol
                       for a in range(3)):
            out.append(f"part {i}'s vertices leave its box")
        if any(plo[a] < hlo[a] - tol or phi[a] > hhi[a] + tol for a in range(3)):
            out.append(f"part {i}'s box leaves the file's box")
        if len(out) >= 4:
            break
    return out
