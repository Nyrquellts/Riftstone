"""Where a stage model sits in the world: Gransys' terrain cells, and the other frames, measured.

Dark Arisen's open field (stage 100) streams its ground in 100 m cells.  The terrain model of cell
``st100_<m>m<n>n`` (``scr\\st100\\model\\m<M>0\\st100_<m>m<n>n``, in the archive
``rom/stage/stage100/split/m<M>0/n<N>0/st100_<m>m<n>n``) stores X and Z from the cell's corner and Y as
world height, and the engine adds the corner::

    world = local + (10000 * n - 500000, 0, 10000 * m - 500000)            (centimetres)

The number before ``m`` is the world Z cell, the number before ``n`` the world X cell; no rotation, no
scale.  Measured on build 2364871 (``tools/terrain_proof.py`` repeats every number):

* the static layouts (``_s00``, world space) stand on their cell's terrain under this rule: 5,829
  placements in 60 cells, median height gap 34 cm, 82% within 1 m; swapped axes, mirrored axes or one
  cell off give 4 to 11 m;
* the cell terrain moved by the rule meets the world-space area LOD models (``area_index*``): median
  74 cm over 13,736 sampled vertices; one cell off 15 m, swapped axes 10 m.

That is why importing Gransys into a 3D tool stacks its ~420 cells at the origin at their true height
(the owner's "all pieces at origin, in the sky"): a cell's place is its name, not its model.  Editing
a cell in world coordinates and saving the model without taking the corner off makes the engine add
the corner a second time.  ``localize``/``worldize`` move a cell model between the two frames by exactly
the corner, each value rounded to float32 (moved there and back: within 0.0078 cm, ``docs/terrain.md``)
(positions, bounds, group spheres, envelope volumes), for the float-position vertex formats every one
of the 417 vanilla cell models uses, and the cell's merged collision meshes too
(``scr\\st100\\collision\\m<M>0\\marge\\st100{h,e}_<m>m<n>n_mrg00``, the same archive and frame: boxes,
tree lanes and vertices, ``sbc.py``); ``check`` finds either saved in world space, and a mod holding one
does not build.

Other frames, from bounds and placements: the area LOD models (``st100_area*``) and the water models
(``scr\\st100\\model\\water\\``) are world space; object models (``split_sub``) are placed by the
static layouts (position, angle, scale; ``riftstone spawns``); the far-mountain models
(``fm_f*``) are centred on their own origin and nothing in the stage's archives names them, and the
``fmfore_*`` tiles carry cell names but do not follow the terrain closely enough to call: both
UNKNOWN.  Dragon's Dogma Online's field (stage 0100) has no cells: its terrain (``scr\\fd\\model``,
loaded through ``rom/scr/fd/sdl`` archives) is world space -- 12,464 of the stage's 14,163 placements
stand on its 99 field-terrain models with no transform (median 34 cm; moved 100 m: 17 m).
"""
from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass

from . import port, sbc
from .errors import FormatError, ParamError, RiftError

CELL = 10000.0          # a cell is 100 m; the game's unit is the centimetre
ORIGIN = -500000.0      # the corner of cell 0 on both axes
OVERHANG = 5000.0       # measured: the 417 cell models reach at most 5,000 past their cell's sides
COLLISION_OVERHANG = 10000.0    # measured: the 836 cell collisions reach at most one whole cell past them
MODEL_VERSIONS = (0xD4, 0xD2)   # DDDA 212, DDO 210 (one section layout, port.model_info)
# Vertex formats whose position is three floats at the start of the vertex (RevilLib's P3f_* names);
# every mesh of every vanilla cell model uses one of the first six.
FLOAT_POSITIONS = frozenset((0x5e7f202c, 0xd8297028, 0x49b4f029, 0x926fd02e, 0xafa6302d, 0x747d1031,
                             0x9399c033, 0x207d6037, 0xa7d7d036, 0xa14e003c, 0xa8fab018))
NO_BONE = 255           # an envelope bound to no bone (all 2,887 of the vanilla cell models')
_ENVELOPE = 144         # bone u32, pad, sphere vec4, AABB 2 x vec4, OBB 4x4 + extents vec4
_GROUP = 32             # index u32, pad, sphere vec4

_CELL = re.compile(r"(?:^|[\\/])st100[he]?_(\d\d)m(\d\d)n(?:_mrg\d\d)?(?=\.[^\\/]*$|$)", re.IGNORECASE)
_MODEL = re.compile(r"^scr\\st100\\model\\m(\d)0\\st100_(\d\d)m(\d\d)n$", re.IGNORECASE)
_COLLISION = re.compile(r"^scr\\st100\\collision\\m(\d)0\\marge\\st100[he]_(\d\d)m(\d\d)n_mrg\d\d$",
                        re.IGNORECASE)


@dataclass(frozen=True)
class Cell:
    m: int      # the number before "m": the world Z cell
    n: int      # the number before "n": the world X cell

    @property
    def name(self) -> str:
        return f"st100_{self.m:02d}m{self.n:02d}n"

    @property
    def model(self) -> str:
        return f"scr\\st100\\model\\m{self.m // 10}0\\{self.name}"

    @property
    def collisions(self) -> tuple[str, str]:
        """The cell's merged collision meshes (same archive, same frame as its model): h and e."""
        return tuple(f"scr\\st100\\collision\\m{self.m // 10}0\\marge\\st100{k}_{self.m:02d}m{self.n:02d}n_mrg00"
                     for k in "he")

    @property
    def archive(self) -> str:
        return f"rom/stage/stage100/split/m{self.m // 10}0/n{self.n // 10}0/{self.name}"

    @property
    def offset(self) -> tuple[float, float, float]:
        """What the engine adds to the cell model's positions."""
        return (self.n * CELL + ORIGIN, 0.0, self.m * CELL + ORIGIN)


def parse_cell(text: str) -> Cell:
    """'47m35n', 'st100_47m35n', a cell model's or collision's engine name, or a path to its file -> Cell."""
    t = str(text).strip()
    m = re.fullmatch(r"(?:st100_)?(\d\d)m(\d\d)n", t, re.IGNORECASE) or _CELL.search(t)
    if not m:
        raise ParamError(f"{text!r} is not a Gransys cell (like 47m35n or st100_47m35n)")
    return Cell(int(m.group(1)), int(m.group(2)))


def _cell_of(pattern, name) -> Cell | None:
    if isinstance(name, bytes):
        name = name.decode("latin-1")
    m = pattern.match(name)
    if not m or int(m.group(1)) != int(m.group(2)) // 10:
        return None
    return Cell(int(m.group(2)), int(m.group(3)))


def cell_of_model(name: bytes | str) -> Cell | None:
    """The cell whose terrain model this engine name is, else None (every other resource)."""
    return _cell_of(_MODEL, name)


def cell_of_collision(name: bytes | str) -> Cell | None:
    """The cell whose merged collision (h or e) this engine name is, else None."""
    return _cell_of(_COLLISION, name)


def cell_at(x: float, z: float) -> Cell:
    """The cell a world position lies in."""
    if not (math.isfinite(x) and math.isfinite(z)):
        raise ParamError(f"({x}, {z}) is not a position: x and z must be finite numbers")
    return Cell(int((z - ORIGIN) // CELL), int((x - ORIGIN) // CELL))


def cells() -> list[Cell]:
    """Every terrain cell the game ships (its ``split`` archives, from the vanilla archive table)."""
    from .install import known_vanilla

    found = set()
    for a in known_vanilla() or {}:
        if a.startswith("rom/stage/stage100/split/"):
            try:
                found.add(parse_cell(a))
            except ParamError:
                pass
    return sorted(found, key=lambda c: (c.m, c.n))


# -- frames --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Frame:
    kind: str                           # "cell", "world", "placed" or "unknown"
    why: str
    cell: Cell | None = None


def frame(name: str, game: str = "ddda") -> Frame:
    """How a stage model's positions reach the world (engine name, as the archive lists it)."""
    n = name.replace("/", "\\")
    low = n.lower()
    if game == "ddo":
        if low.startswith("scr\\fd\\model\\"):
            return Frame("world", "Dragon's Dogma Online's field terrain is world space (no cells); "
                                  "stage 0100's placements stand on it with no transform")
        return Frame("unknown", "not measured for Dragon's Dogma Online")
    c = cell_of_model(n)
    if c is not None:
        dx, _, dz = c.offset
        return Frame("cell", f"terrain cell {c.name}: the engine adds ({dx:.0f}, 0, {dz:.0f}) cm; Y is world "
                             f"height", c)
    c = cell_of_collision(n)
    if c is not None:
        dx, _, dz = c.offset
        return Frame("cell", f"collision of terrain cell {c.name}: the same frame as its model; the engine adds "
                             f"({dx:.0f}, 0, {dz:.0f}) cm", c)
    if re.match(r"^model\\om\\om\d+\\collision\\", low):
        return Frame("placed", "an object's collision in its own space, placed with the object by layouts")
    if re.match(r"^scr\\st100\\model\\st100_area\d+_[hl]$", low):
        return Frame("world", "area LOD terrain: world space")
    if low.startswith("scr\\st100\\model\\water\\"):
        return Frame("world", "water: world space")
    if low.startswith("scr\\st100\\model\\fm_f"):
        return Frame("unknown", "far-mountain model centred on its own origin; nothing in the stage's archives "
                                "names it, so where the engine puts it is UNKNOWN")
    if low.startswith("scr\\st100\\model\\fm_m"):
        return Frame("unknown", "far-mountain tile with a cell name; it does not follow the terrain closely "
                                "enough to call its frame (UNKNOWN)")
    if low.startswith(("model\\om\\", "model\\sm\\", "model\\wp\\")):
        return Frame("placed", "an object model in its own space, placed by layouts (position, angle, scale)")
    return Frame("unknown", "not measured")


# -- models --------------------------------------------------------------------------------------
@dataclass
class _Parts:
    version: int
    bounds: list[int]                   # offsets of the header's sphere centre, AABB min, AABB max
    groups: list[int]                   # offsets of each group sphere's centre
    envelopes: list[int]                # offsets of each envelope's sphere, AABB min/max, OBB translation
    vertices: list[int]                 # offset of each vertex's position (each once)


def _parts(data: bytes) -> _Parts:
    """The offsets of everything positional in a bone-less model with float positions, or a refusal."""
    info = port.model_info(data)        # the shared section layout, or FormatError-like RiftError
    if info.version not in MODEL_VERSIONS:
        raise FormatError("mod", f"model revision {info.version}; Dragon's Dogma models are 212 and 210")
    if info.bones:
        raise ParamError(f"the model has {info.bones} bone(s); a bone-bound model is not moved as a whole")
    h = port.MOD_HEADER.unpack_from(data)
    (_, ver, _nb, nm, _nmat, _nv, ni, _ne, vbs, _nt, ng, _bones, groups, _mats, meshes, vb, ib, _end) = h
    nenv = struct.unpack_from("<I", data, 0x80)[0]
    env0 = meshes + nm * port.MOD_MESH.size
    envs = []
    for i in range(nenv):
        e = env0 + i * _ENVELOPE
        if struct.unpack_from("<I", data, e)[0] != NO_BONE:
            raise ParamError(f"envelope {i} is bound to a bone; not moving it")
        envs += [e + 16, e + 32, e + 48, e + 64 + 48]
    seen: dict[int, int] = {}
    runs = []
    foreign = set()
    for i in range(nm):
        o = meshes + i * port.MOD_MESH.size
        count = struct.unpack_from("<H", data, o + 2)[0]
        stride = (struct.unpack_from("<I", data, o + 8)[0] >> 16) & 0xFF
        vstart, vso, fmt = struct.unpack_from("<III", data, o + 12)
        istart, nidx, ivo = struct.unpack_from("<III", data, o + 24)
        if fmt not in FLOAT_POSITIONS:
            foreign.add(fmt)
            continue
        base = vb + vstart * stride + vso + ivo * stride
        if stride < 12 or base < vb or base + count * stride > vb + vbs:
            raise FormatError("mod", f"mesh {i}'s vertices lie outside the vertex buffer")
        if istart + nidx > ni:
            raise FormatError("mod", f"mesh {i}'s indices lie outside the index buffer")
        if nidx:
            idx = struct.unpack_from(f"<{nidx}H", data, ib + 2 * istart)
            if min(idx) < vstart or max(idx) - vstart >= count:
                raise FormatError("mod", f"mesh {i} draws vertices it does not own")
        for b, e, s, j in runs:        # meshes may share vertices, never read them with another layout
            if base < e and b < base + count * stride and (s != stride or (base - b) % stride):
                raise FormatError("mod", f"meshes {j} and {i} read the same vertex bytes with different layouts")
        runs.append((base, base + count * stride, stride, i))
        for k in range(count):
            seen[base + k * stride] = stride
    if foreign:
        raise ParamError("mesh vertex format(s) " + ", ".join(f"0x{f:08x}" for f in sorted(foreign)) +
                         " do not store positions as floats; not moving the model")
    return _Parts(ver, [0x40, 0x50, 0x60], [groups + i * _GROUP + 16 for i in range(ng)], envs, sorted(seen))


def _is_collision(data: bytes) -> bool:
    return data[:4] == sbc.MAGIC


def positions(data: bytes) -> list[tuple[float, float, float]]:
    """Every vertex position of a cell model or collision mesh ``localize`` can move."""
    if _is_collision(data):
        return sbc.positions(data)
    p = _parts(data)
    return [struct.unpack_from("<3f", data, off) for off in p.vertices]


def _translate_model(data: bytes, d: tuple[float, float, float]) -> bytes:
    p = _parts(data)
    out = bytearray(data)
    for off in p.bounds + p.groups + p.envelopes + p.vertices:
        x, y, z = struct.unpack_from("<3f", out, off)
        try:
            struct.pack_into("<3f", out, off, x + d[0], y + d[1], z + d[2])
        except OverflowError:
            raise ParamError("moving the model overflows a float") from None
    return bytes(out)


def translate(data: bytes, d: tuple[float, float, float]) -> bytes:
    """A model or a collision mesh (``.sbc``) moved by ``d``.  A model: vertex positions, the header's
    sphere and box, every group sphere and every envelope's sphere, box and oriented box (rotations and
    extents stay).  A collision mesh: its boxes, tree roots, every node lane and every vertex
    (``sbc.moved_floats``).  A float32 sum rounds to the nearest float, as the engine's own would."""
    if _is_collision(data):
        return sbc.translate(data, d)
    return _translate_model(data, d)


def localize(data: bytes, cell: Cell) -> bytes:
    """A cell model or collision saved in world coordinates, with the cell's corner taken off (what the
    game wants).  Exact for positions inside the world: the corner is a multiple of 16 cm, so the
    difference of two floats that close is a float."""
    dx, dy, dz = cell.offset
    return translate(data, (-dx, -dy, -dz))


def worldize(data: bytes, cell: Cell) -> bytes:
    """A cell model or collision in world coordinates (to edit it next to its neighbours); ``localize``
    brings it back.  Adding the corner rounds each position to float32 (within 0.008 cm in Gransys)."""
    return translate(data, cell.offset)


def _box(pts):
    return ([min(p[i] for p in pts) for i in range(3)], [max(p[i] for p in pts) for i in range(3)])


def check(data: bytes, cell: Cell) -> tuple[list[str], list[str]]:
    """(errors, notes) for a cell model or a cell's collision mesh.

    Errors put it in the wrong place for certain: it was saved in world coordinates (the engine would add
    the corner again), or its vertices leave its own bounds (a model's box, a collision part's box; every
    vanilla file keeps them inside).  Notes are unusual but possible: reaching past the cell further than
    any vanilla file, bounds that fit both frames (a small piece in a cell next to the origin cell), or a
    layout Riftstone cannot read positions from (every vanilla cell file has bone-less float positions)."""
    collision = _is_collision(data)
    what, reach = ("collision", COLLISION_OVERHANG) if collision else ("model", OVERHANG)
    errors, notes = [], []
    try:
        pts = positions(data)
    except RiftError as e:      # an exporter's own section order, bones, packed positions: not provably wrong
        return errors, [f"its placement cannot be checked: {e}"]
    if not pts:
        return errors, notes
    lo, hi = _box(pts)
    if collision:
        errors += [f"{p}; re-export it so its boxes hold its vertices" for p in sbc.bounds_problems(data)]
    else:
        bmin = struct.unpack_from("<3f", data, 0x50)
        bmax = struct.unpack_from("<3f", data, 0x60)
        tol = 1.0
        if any(lo[i] < bmin[i] - tol or hi[i] > bmax[i] + tol for i in range(3)):
            errors.append(f"its vertices (x {lo[0]:.0f}..{hi[0]:.0f}, y {lo[1]:.0f}..{hi[1]:.0f}, z {lo[2]:.0f}.."
                          f"{hi[2]:.0f}) leave its own bounds (x {bmin[0]:.0f}..{bmax[0]:.0f}, y {bmin[1]:.0f}.."
                          f"{bmax[1]:.0f}, z {bmin[2]:.0f}..{bmax[2]:.0f}); re-export it so the bounds hold them")
    dx, _, dz = cell.offset

    edge = 1.0                          # cm: vanilla reaches the window's edge exactly, float noise and all

    def fits(ox, oz):
        return (-reach - edge <= lo[0] - ox and hi[0] - ox <= CELL + reach + edge and
                -reach - edge <= lo[2] - oz and hi[2] - oz <= CELL + reach + edge)

    local, world = fits(0.0, 0.0), bool(dx or dz) and fits(dx, dz)
    if world and not local:
        errors.append(f"it is in world coordinates (x {lo[0]:.0f}..{hi[0]:.0f}, z {lo[2]:.0f}..{hi[2]:.0f} is cell "
                      f"{cell.name} in the world): the engine adds the cell's corner ({dx:.0f}, {dz:.0f}) again and "
                      "it would stand that far away. 'riftstone terrain localize' takes the corner off")
    elif world:
        notes.append(f"its bounds fit cell {cell.name} both in the cell's frame and in the world (a small piece "
                     "in a cell next to the origin cell), so the frame cannot be told from them; taken as the "
                     "cell's. If it was saved in world coordinates, 'riftstone terrain localize --force' fixes it")
    elif not local:
        notes.append(f"it reaches past its cell further than any vanilla cell {what} (x {lo[0]:.0f}..{hi[0]:.0f}, "
                     f"z {lo[2]:.0f}..{hi[2]:.0f}; vanilla stays within {-reach:.0f}..{CELL + reach:.0f}); the "
                     "engine still puts it at the cell's corner")
    return errors, notes
