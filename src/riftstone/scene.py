"""A stage in three dimensions, for Studio's 3D map: the game's own collision and navigation mesh as compact meshes.

Read-only; nothing is written.  Every layer is the game's data in world coordinates (centimetres, Y up):

- ``e`` and ``h``: the stage's merged collision, ``scr\\st<S>\\collision\\st<S>{e,h}_mrgNN`` (``.sbc``, ``sbc.py``).
  Measured on the game: ``e`` is the denser mesh (floors and walls; 27-44 % of its triangles face up in the stages
  measured) and ``h`` the coarser one (41-55 %); on the open field ``e`` lies on its cell's terrain and
  ``terrain.Ground`` reads the field's ground from it (docs/terrain.md).  What the engine tests against each: UNKNOWN.
  A dungeon's are in world space; the open field's are per terrain cell in the cell's frame and are moved by the
  cell's corner (``terrain.Cell.offset``).  Every triangle's corners turn counter-clockwise about its stored normal.
- ``nav``: the navigation mesh the stage loads (``nav.stage_mesh``; none on the open field, 501 and 703).

Two stages load another stage's merged collision, as eleven load another's navigation mesh: the engine's stage
table, row field 5 (``nav.STAGE_ROWS``), names 443 for 444 and 445 (``COLLISION_OF``) and each other stage itself.
The open field ships 836 collision meshes, so a scene of it is the cells around one point (``RADIUS_MAX`` each way).

A layer goes out as ``verts`` (float32 x, y, z, base64) and ``tris`` (uint32 vertex numbers, base64): each corner
the triangles share is sent once.  A triangle with a corner that is not a finite number, or one past ``COORD_MAX``,
is left out and counted in ``skipped``."""
from __future__ import annotations

import base64
import math
import re
import sys
from array import array

from . import nav, sbc, terrain, typemap
from .errors import RiftError

COLLISION_OF = {444: 443, 445: 443}   # DDDA.exe's stage table, row field 5 (0x015303D0): the merged collision's stage
FIELD = 100
RADIUS_MAX = 2                      # the open field: at most 5 x 5 cells (100 m each) in one scene
COORD_MAX = 1.0e7                   # cm: 100 km; the field spans 5 km each way from its origin
TRIS_MAX = 1_500_000                # all layers of one scene together (the largest dungeon has about 45,000)
_KIND = re.compile(r"\\st\d{3}([eh])_mrg\d\d$", re.IGNORECASE)
LABELS = {"e": "collision e: the denser mesh (the open field's ground is read from it)",
          "h": "collision h: the coarser mesh",
          "nav": "navigation mesh: where the game's walkers go"}


def collision_stage(stage: int) -> int:
    """The stage whose merged collision the game loads for this stage."""
    return COLLISION_OF.get(stage, stage)


class _Layer:
    """One mesh being gathered: each distinct corner once, triangles as three vertex numbers."""

    def __init__(self, key: str):
        self.key = key
        self.verts = array("f")
        self.tris = array("I")
        self._ix: dict = {}
        self.skipped = 0
        self.lo = [math.inf] * 3
        self.hi = [-math.inf] * 3

    def add(self, a, b, c, d=(0.0, 0.0, 0.0)) -> None:
        corners = []
        for p in (a, b, c):
            q = (p[0] + d[0], p[1] + d[1], p[2] + d[2])
            if not all(math.isfinite(v) and abs(v) <= COORD_MAX for v in q):
                self.skipped += 1
                return
            corners.append(q)
        for q in corners:
            i = self._ix.get(q)
            if i is None:
                i = self._ix[q] = len(self.verts) // 3
                self.verts.extend(q)
                for k in range(3):
                    self.lo[k] = min(self.lo[k], q[k])
                    self.hi[k] = max(self.hi[k], q[k])
            self.tris.append(i)

    @property
    def triangles(self) -> int:
        return len(self.tris) // 3

    def out(self) -> dict:
        v, t = array("f", self.verts), array("I", self.tris)
        if sys.byteorder != "little":
            v.byteswap()
            t.byteswap()
        return {"key": self.key, "label": LABELS[self.key], "vertices": len(self.verts) // 3,
                "triangles": self.triangles, "skipped": self.skipped,
                "verts": base64.b64encode(v.tobytes()).decode("ascii"),
                "tris": base64.b64encode(t.tobytes()).decode("ascii")}


def _collision_names(idx, stage: int) -> dict[str, list[bytes]]:
    """A dungeon's merged collision by kind: {"e": [...], "h": [...]} (a name of another shape counts as h)."""
    s = collision_stage(stage)
    out: dict[str, list[bytes]] = {"e": [], "h": []}
    for name in sorted(idx.names_under(f"scr\\st{s:03d}\\collision\\", typemap.BY_EXT["sbc"])):
        m = _KIND.search(name.decode("latin-1"))
        out[m.group(1).lower() if m else "h"].append(name)
    return out


def field_cells(at: tuple[float, float], radius: int) -> list[terrain.Cell]:
    """The open field's cells around a world point (x, z): ``radius`` cells each way, inside the 100 x 100 grid."""
    if not 0 <= radius <= RADIUS_MAX:
        raise RiftError(f"radius is 0..{RADIUS_MAX} cells")
    c = terrain.cell_at(at[0], at[1])
    return [terrain.Cell(m, n) for m in range(c.m - radius, c.m + radius + 1)
            for n in range(c.n - radius, c.n + radius + 1) if 0 <= m < 100 and 0 <= n < 100]


def parse_at(text) -> tuple[float, float] | None:
    """'x,z' (world X and Z, centimetres) -> (x, z); '' or None -> None."""
    if text is None or str(text).strip() == "":
        return None
    parts = str(text).split(",")
    try:
        x, z = (float(p) for p in parts)
    except ValueError:
        raise RiftError("at is x,z (two numbers)") from None
    if not (math.isfinite(x) and math.isfinite(z)) or abs(x) > COORD_MAX or abs(z) > COORD_MAX:
        raise RiftError("at is x,z (two finite numbers within 100 km)")
    return x, z


def parse_radius(text, default: int = 1) -> int:
    if text is None or str(text).strip() == "":
        return default
    try:
        r = int(str(text).strip())
    except ValueError:
        raise RiftError(f"radius is a whole number of cells, 0..{RADIUS_MAX}") from None
    if not 0 <= r <= RADIUS_MAX:
        raise RiftError(f"radius is 0..{RADIUS_MAX} cells")
    return r


def stage_scene(game, idx, stage: int, at: tuple[float, float] | None = None, radius: int = 1) -> dict:
    """The 3D scene of one stage: its layers, the box they fill and notes on what it holds.  On the open field
    (stage 100) it is the cells around ``at`` (x, z), ``radius`` cells each way; elsewhere ``at`` is ignored."""
    if game.kind != "ddda":
        raise RiftError("the 3D map reads Dark Arisen's stages (Online's: not yet)")
    sbc_type = typemap.BY_EXT["sbc"]
    layers = {k: _Layer(k) for k in ("e", "h", "nav")}
    notes: list[str] = []
    total = 0

    def load(layer: _Layer, data: bytes, d=(0.0, 0.0, 0.0)) -> None:
        nonlocal total
        tris = sbc.triangles(data)
        if total + len(tris) > TRIS_MAX:
            raise RiftError(f"this scene would hold more than {TRIS_MAX:,} triangles; ask for fewer cells")
        total += len(tris)
        for a, b, c, _n in tris:
            layer.add(a, b, c, d)

    out: dict = {"stage": stage, "kind": "dungeon"}
    if stage == FIELD:
        if at is None:
            raise RiftError("the open field is shown a few cells at a time: say where (at=x,z)")
        cells = field_cells(at, radius)
        mid = terrain.cell_at(at[0], at[1])
        out.update(kind="field", radius=radius, cells=[[c.m, c.n] for c in cells], cell_size=terrain.CELL,
                   origin=terrain.ORIGIN, cell=[mid.m, mid.n],
                   center=[mid.offset[0] + terrain.CELL / 2, mid.offset[2] + terrain.CELL / 2])
        found = 0
        for c in cells:
            for key, name in zip("he", c.collisions):
                data = nav.game_resource(game, idx, name.encode("latin-1"), sbc_type)
                if data is not None:
                    found += 1
                    load(layers[key], data, c.offset)
        if not found:
            notes.append("no terrain cell around this point has collision (outside the map the game ships)")
        notes.append("the open field has no navigation mesh: its walkers stand on the walkable collision")
    else:
        names = _collision_names(idx, stage)
        if collision_stage(stage) != stage:
            out["collision_stage"] = collision_stage(stage)
            notes.append(f"stage {stage} loads stage {collision_stage(stage)}'s collision (the game's stage table)")
        for key, ns in names.items():
            for name in ns:
                data = nav.game_resource(game, idx, name, sbc_type)
                if data is not None:
                    load(layers[key], data)
        if not names["e"] and not names["h"]:
            notes.append(f"stage {stage} ships no collision")
        try:
            mesh = nav.stage_mesh(game, idx, stage)
        except RiftError as e:
            mesh = None
            notes.append(f"its navigation mesh cannot be read: {e}")
        if mesh is not None:
            if nav.nav_stage(stage) != stage:
                out["nav_stage"] = nav.nav_stage(stage)
            for a, b, c in mesh.tris:
                layers["nav"].add(mesh.positions[a], mesh.positions[b], mesh.positions[c])
        else:
            notes.append(f"stage {stage} has no navigation mesh")
    present = [ly for ly in layers.values() if ly.triangles]
    lo = [min((ly.lo[k] for ly in present), default=0.0) for k in range(3)]
    hi = [max((ly.hi[k] for ly in present), default=0.0) for k in range(3)]
    skipped = sum(ly.skipped for ly in layers.values())
    if skipped:
        notes.append(f"{skipped:,} triangle(s) with a corner that is not a usable number left out")
    out.update(bounds=[*lo, *hi], layers=[ly.out() for ly in present], notes=notes)
    return out
