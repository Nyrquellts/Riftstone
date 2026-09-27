"""Shared test fixtures: synthetic resources that exercise every supported type."""
from __future__ import annotations

import os
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from riftstone import cipher, typemap, xfs  # noqa: E402
from riftstone.errors import RiftError  # noqa: E402

# Riftstone ships no Online key: cipher.find_arc_key takes the one a player supplies (RIFTSTONE_DDO_KEY or
# %LOCALAPPDATA%\Riftstone\ddo.key), else reads it from the player's own client.  With Online on this PC the
# tests use its key, so the corpus tests can read the real client; without it, the ARCC archives the tests build
# and read use this stand-in, and the tests that need the real client skip (ddo_key_present).
TEST_ARC_KEY = b"Riftstone's tests only, not Dragon's Dogma Online's key"
try:
    cipher.arc_cipher()
    _REAL_DDO_KEY = True
except RiftError:
    cipher._key, cipher._default = TEST_ARC_KEY, None
    _REAL_DDO_KEY = False


def ddo_key_present() -> bool:
    """True when Online's real archive key is in use, not the tests' stand-in: the tests that read the real
    client need it and skip without it."""
    return _REAL_DDO_KEY


def module_env(*names: str):
    """(setUpModule, tearDownModule) for a test module whose classes set these environment variables: when the
    module's tests end they are as they were before it, so no later module inherits a stand-in RIFTSTONE_HOME
    (a test that needs the real index then read an empty one, depending on the order the modules ran in)."""
    saved: dict[str, str | None] = {}

    def set_up() -> None:
        saved.clear()
        saved.update({n: os.environ.get(n) for n in names})

    def tear_down() -> None:
        for n, v in saved.items():
            if v is None:
                os.environ.pop(n, None)
            else:
                os.environ[n] = v

    return set_up, tear_down


def stand_in_loader(game, idx, folder: Path) -> None:
    """Put a stand-in Riftstone loader into a stand-in game (Dark Arisen mods install only through the loader):
    a dinput8.dll with the loader's marker, installed by loader.install_loader as a built one would be."""
    from unittest import mock

    from riftstone import install, loader

    folder.mkdir(parents=True, exist_ok=True)
    (folder / "dinput8.dll").write_bytes(b"MZ " + loader.MARKER + b" RiftstoneLoaderVersion=0.3.1\0")
    with mock.patch.object(loader, "built_loader", return_value=folder), \
            mock.patch.object(install, "game_running", return_value=False):
        loader.install_loader(game, idx)


def prop(name: str, tname: str, attr: int = 0, size: int | None = None) -> xfs.Prop:
    code = xfs.TYPE_CODES[tname]
    st = xfs.TYPES[code][1]
    if size is None:
        size = st.size if st is not None else (36 if code == 0x0E else 4)
    return xfs.Prop(name, code, attr, size)


ROOT = xfs.ClassDef(typemap.jamcrc("rTestRoot"), 0x78, (
    prop("mFlag", "bool"), prop("mByte", "u8"), prop("mShort", "u16"), prop("mInt", "u32", 0x91),
    prop("mBig", "u64"), prop("mSByte", "s8"), prop("mSShort", "s16"), prop("mSInt", "s32"),
    prop("mSBig", "s64"), prop("mFloat", "f32"), prop("mDouble", "f64"), prop("mName", "string"),
    prop("mMatrix", "matrix"), prop("mPos", "vector3"), prop("mColor", "vector4"), prop("mRot", "quaternion"),
    prop("mTag", "cstring", 0x81), prop("mUV", "float2"), prop("mP3", "float3"), prop("mP4", "float4"),
    prop("mSphere", "sphere"), prop("mBox", "aabb"), prop("mCyl", "cylinder"), prop("mRange", "rangef"),
    prop("mRange16", "rangeu16", 0x20), prop("mCurve", "hermitecurve"), prop("mRes", "resource", 0x80),
    prop("mChild", "classref"), prop("mNone", "classref"), prop("mKids", "classref", 0xA0),
    prop("mFloats", "f32", 0x20), prop("mNames", "string", 0x20), prop("mDup", "u32"), prop("mDup", "u32"),
    prop("Japanese 名前", "u8"),
))
CHILD = xfs.ClassDef(typemap.jamcrc("rTestRoot::cChild"), 0x10, (prop("mValue", "f32"), prop("mNext", "classref")))


def sample_xfs() -> xfs.Xfs:
    def child(v, nxt=None):
        return xfs.Obj(1, [[v], [nxt]])

    fields = [
        [1], [200], [65000], [0xDEADBEEF], [2**63 + 5], [-100], [-30000], [-2**31], [-2**62], [0.5], [1e300],
        [b"hello world"], [tuple(float(i) for i in range(16))], [(1.0, 2.0, 3.0, 0.0)], [(0.25, 0.5, 0.75, 1.0)],
        [(0.0, 0.0, 0.0, 1.0)], ["テスト".encode()], [(1.5, -1.5)], [(1.0, 2.0, 3.0)], [(1.0, 2.0, 3.0, 4.0)],
        [(0.0, 1.0, 2.0, 3.0)], [tuple(float(i) for i in range(8))], [tuple(float(i) for i in range(12))],
        [(-1.0, 1.0)], [(1, 65535), (0, 7)], [tuple(float(i) / 4 for i in range(16))],
        [xfs.ResourceRef(b"rSoundRequest", b"sound\\se\\em\\e07\\e0700\\e0700")],
        [child(3.25, child(-0.0))], [None], [child(1.0), None, child(2.0)],
        [0.125, -2.5, 3.4028234663852886e38], [b"", b"a: b # c", b"null"], [7], [8], [9],
    ]
    return xfs.Xfs(4, [ROOT, CHILD], xfs.Obj(0, fields))


def cell_model(points=None, meshes=None, groups: int = 1, envelopes: int = 2, version: int = 0xD4,
               bones: int = 0, env_bone: int = 255) -> bytes:
    """A small model in the shared section layout (``port.model_info``) shaped like a Gransys terrain
    cell: bone-less, float positions first in each vertex (the rest a 0xAB filler), group spheres and
    bone-less envelopes.  ``meshes``: (vertex format, stride, first vertex, count) each."""
    import struct
    pts = points or [(100.0, 5000.0, 200.0), (9800.0, 5100.0, 300.0), (400.0, 5200.0, 9700.0),
                     (9900.0, 4900.0, 9900.0), (5000.0, 6000.0, 5000.0)]
    meshes = meshes or [(0xD8297028, 24, 0, len(pts))]
    stride = meshes[0][1]
    vbuf = b"".join(struct.pack("<3f", *p) + b"\xab" * (stride - 12) for p in pts)
    lo = [min(p[i] for p in pts) for i in range(3)]
    hi = [max(p[i] for p in pts) for i in range(3)]
    centre = [(a + b) / 2 for a, b in zip(lo, hi)]
    radius = max(sum((p[i] - centre[i]) ** 2 for i in range(3)) ** 0.5 for p in pts)
    idx, recs = [], []
    for i, (fmt, st, first, count) in enumerate(meshes):
        recs.append(struct.pack("<HHIIIIIIIIBBHHHI", 0, count, 0, (st << 16) | 0x1000000, first, 0, fmt,
                                len(idx), count, 0, 0, 0, i, 0, count - 1, 0))
        idx += [first + k for k in range(count)]
    after_bones = 0x84 + bones * (24 + 64 + 64) + 0x100 if bones else 0x84
    groups_off = after_bones
    mats = groups_off + groups * 32 if groups else after_bones
    mesh_off = mats + 0x80
    vb = mesh_off + len(meshes) * 48 + envelopes * 144
    ib = vb + len(vbuf)
    end = ib + 2 * len(idx)
    end += (-end) % 4
    out = bytearray(end)
    struct.pack_into("<4sHHHHIII10I", out, 0, b"MOD\0", version, bones, len(meshes), 1, len(pts), len(idx), 0,
                     len(vbuf), 0, groups, 0x84 if bones else 0, groups_off if groups else 0, mats, mesh_off,
                     vb, ib, end)
    struct.pack_into("<4f", out, 0x40, *centre, radius)
    struct.pack_into("<4f", out, 0x50, *lo, 0.0)
    struct.pack_into("<4f", out, 0x60, *hi, 0.0)
    struct.pack_into("<I", out, 0x80, envelopes)
    for g in range(groups):
        o = groups_off + 32 * g
        struct.pack_into("<I12s4f", out, o, 42 + g, b"\xcd" * 12, *centre, radius)
    out[mats:mats + 4] = b"cell"
    for i, r in enumerate(recs):
        out[mesh_off + 48 * i:mesh_off + 48 * (i + 1)] = r
    for e in range(envelopes):
        o = mesh_off + len(meshes) * 48 + 144 * e
        rot = (0.0, 0.0, 1.0, 0.0, 0.6, 0.8, 0.0, 0.0, -0.8, 0.6, 0.0, 0.0)   # a rotated box: rows stay
        struct.pack_into("<I12s4f4f4f12f4f4f", out, o, env_bone, b"\xcd" * 12, *centre, radius, *lo, 0.0, *hi, 0.0,
                         *rot, *centre, 1.0, 10.0, 20.0, 30.0, 0.0)
    out[vb:ib] = vbuf
    struct.pack_into(f"<{len(idx)}H", out, ib, *idx)
    return bytes(out)


def cell_collision(points=None, parts: int = 1, tree_kind: int = 1, two_level: bool = False,
                   modify: int = 0) -> bytes:
    """A small collision mesh (``.sbc``) laid out as DDDA.exe's loader reads it (``sbc.py``): header, the
    node-memory word, parts (part 0 owns every vertex, triangle and leaf), one tree per part plus the
    parts tree, triangles, vertices, materials, leaves.  Trees are 4-wide unless ``tree_kind`` is 2."""
    import struct
    pts = points or [(100.0, 5000.0, 200.0), (9800.0, 5100.0, 300.0), (400.0, 5200.0, 9700.0),
                     (9900.0, 4900.0, 9900.0)]
    tris = [(i, (i + 1) % len(pts), (i + 2) % len(pts)) for i in range(max(1, len(pts) - 2))]
    lo = [min(p[i] for p in pts) for i in range(3)]
    hi = [max(p[i] for p in pts) for i in range(3)]
    box = struct.pack("<8f", *lo, 0.0, *hi, 0.0)
    node_size = 0x70 if tree_kind == 1 else 0x50

    def node(leaves: bool, kids) -> bytes:
        if tree_kind != 1:
            return box + struct.pack("<4I", *kids[:4]) + bytes(node_size - 48)
        mask = 0x50 if leaves else 0x03
        lanes = b"".join(struct.pack("<4f", *([v] * 4)) for v in (*lo, *hi))
        return bytes([mask]) * 4 + struct.pack("<4H", *kids) + b"\xcd" * 4 + lanes

    def tree(nleaf: int) -> bytes:
        nodes = [node(True, (0, 0, min(1, nleaf - 1), 0))]
        if two_level:
            nodes = [node(False, (1, 2, 0, 0)), node(True, (0, 0, 0, 0)), node(True, (min(1, nleaf - 1), 0, 0, 0))]
        head = b"BVHC" + struct.pack("<II", 0x77B17B24, tree_kind) + bytes(4) + box + struct.pack("<I", len(nodes))
        return head + bytes(12) + b"".join(nodes)

    out = bytearray(b"SBC\xff" + struct.pack("<IIIHHIIII", 0x77DF2114, 0, 0, parts, 1, len(tris), len(tris),
                                              len(pts), modify))
    out += bytes(0x30 - len(out)) + box
    trees = b"".join(tree(len(tris)) for _ in range(parts)) + tree(parts)
    out += struct.pack("<I", 3 * node_size * (parts + 1))                    # node memory (not checked)
    for i in range(parts):
        mine = i == 0
        out += box + struct.pack("<3I", 0x2694B470, 0, 0)
        out += struct.pack("<6I", 0, len(tris) if mine else 0, 0, len(tris) if mine else 0, 0,
                           len(pts) if mine else 0)
        out += struct.pack("<3I", 0x2F + i, 0, 0)
    out += trees
    for a, b, c in tris:
        out += struct.pack("<3f3HHIBBBBI", 0.0, 1.0, 0.0, a, b, c, 0, 2, 0, 0, 0, 0, 0)
    for p in pts:
        out += struct.pack("<3fI", *p, 0)
    out += struct.pack("<I", 1) + bytes(28)
    for t in range(len(tris)):
        out += struct.pack("<5H", t, 0xFFFF, 0xFFFF, 0xFFFF, 0)
    return bytes(out)


def game_root() -> Path | None:
    """The installed game for corpus tests, or None (tests skip)."""
    from riftstone.game import find_game
    from riftstone.errors import RiftError
    if os.environ.get("RIFTSTONE_SKIP_GAME"):
        return None
    try:
        return find_game().root
    except RiftError:
        return None


def pe_file(machine: int = 0x14C, dll: bool = True, laa: bool = False, exports=("Direct3DCreate9",),
            plus: bool = False, extra: bytes = b"", stamp: int = 0x5A314C31) -> bytes:
    """A small but well-formed PE file: one section holding an export table with ``exports`` (none: no export
    directory), then ``extra`` bytes (e.g. a marker string)."""
    import struct

    opt_size = 0xF0 if plus else 0xE0
    lfanew, raw, va = 0x80, 0x400, 0x1000
    names = [n.encode("ascii") + b"\0" for n in exports]
    ptrs_at = 40
    ords_at = ptrs_at + 4 * len(names)
    funcs_at = ords_at + 2 * len(names)
    blob = bytearray(funcs_at + 4 * max(1, len(names)))
    rvas = []
    for n in names:
        rvas.append(va + len(blob))
        blob += n
    dll_name = va + len(blob)
    blob += b"test.dll\0" + extra
    struct.pack_into("<IIIIIIIIII", blob, 0, 0, stamp, 0, dll_name, 1, len(names), len(names), va + funcs_at,
                     va + ptrs_at, va + ords_at)
    for i, r in enumerate(rvas):
        struct.pack_into("<I", blob, ptrs_at + 4 * i, r)
        struct.pack_into("<H", blob, ords_at + 2 * i, i)
        struct.pack_into("<I", blob, funcs_at + 4 * i, 0x2000 + i)
    size = len(blob)
    data = bytearray(raw + (size + 0x1FF) // 0x200 * 0x200)
    data[0:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, lfanew)
    data[lfanew:lfanew + 4] = b"PE\0\0"
    chars = 0x0002 | (0x2000 if dll else 0) | (0x0020 if laa else 0) | (0 if plus else 0x0100)
    struct.pack_into("<HHIIIHH", data, lfanew + 4, machine, 1, stamp, 0, 0, opt_size, chars)
    opt = lfanew + 24
    struct.pack_into("<H", data, opt, 0x20B if plus else 0x10B)
    count_at = opt + (108 if plus else 92)
    struct.pack_into("<I", data, count_at, 16)
    if names:
        struct.pack_into("<II", data, count_at + 4, va, size)
    section = opt + opt_size
    data[section:section + 8] = b".edata\0\0"
    struct.pack_into("<IIII", data, section + 8, size, va, len(data) - raw, raw)
    data[raw:raw + size] = blob
    return bytes(data)
