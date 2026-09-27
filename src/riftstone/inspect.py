"""Open any DDDA resource to the best available view.

The 44 XFS types (FSM, params, scenario, shell...), OCL collision and GMD
text become editable YAML; a text file lists its messages by id.
Every other type gets a structured read-out that turns hex into something a
modder can read: the magic, and a scan that pulls out NUL-terminated strings
(ids and asset paths), plausible float vectors (positions, scales, colours)
and integer fields, each with its byte offset. A few well-understood binary
types (LOT spawns, collision) get a typed summary on top.

This is deliberately read-first: it never claims to edit a format Riftstone
has not proven it can rebuild byte-exact. It answers "what is in this file
and where", which is the thing hex-editing costs the most time on.
"""
from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass, field

from . import typemap, xfs

_ID = re.compile(rb"[A-Za-z_][A-Za-z0-9_]{2,63}")
# a path starts where its first name does: tried at every offset of a long run of letters, the scan was
# quadratic (100,000 letters took 26 s)
_PATH = re.compile(rb"(?<![A-Za-z0-9_])[A-Za-z0-9_]{2,}[\\/][ -~]{2,190}")
_F = struct.Struct("<f")


@dataclass
class Field:
    offset: int
    kind: str          # "string" | "path" | "vec3" | "vec4" | "vec2" | "f32" | "u32" | "s32"
    value: object

    def render(self) -> str:
        v = self.value
        if isinstance(v, tuple):
            v = "[" + ", ".join(f"{x:g}" for x in v) + "]"
        elif isinstance(v, bytes):
            v = v.decode("latin-1")
        return f"  0x{self.offset:06x}  {self.kind:7}  {v}"


@dataclass
class Report:
    type_id: int
    ext: str
    cls: str
    size: int
    xfs: bool
    editable: bool
    magic: bytes
    summary: list[str] = field(default_factory=list)
    strings: list[Field] = field(default_factory=list)
    vectors: list[Field] = field(default_factory=list)
    note: str = ""

    def text(self, name: str = "") -> str:
        out = [f"{name or self.cls}",
               f"  type    {self.cls}  (.{self.ext}, id {self.type_id:08x})",
               f"  size    {self.size} bytes   magic {self.magic!r}",
               f"  status  {'editable as YAML (riftstone param)' if self.editable else 'read-only view (format not yet decoded for editing)'}"]
        if self.note:
            out.append(f"  note    {self.note}")
        for line in self.summary:
            out.append("  " + line)
        if self.strings:
            out.append(f"\n  strings / ids / paths ({len(self.strings)} shown):")
            out += [f.render() for f in self.strings]
        if self.vectors:
            out.append(f"\n  vectors / numbers ({len(self.vectors)} shown):")
            out += [f.render() for f in self.vectors]
        return "\n".join(out)


def _finite_vec(vals) -> bool:
    return all(math.isfinite(x) and (x == 0.0 or 1e-4 < abs(x) < 1e7) for x in vals) and any(x != 0 for x in vals)


def _scan(data: bytes, max_strings: int = 200, max_vecs: int = 400):
    strings, seen = [], set()
    for m in _PATH.finditer(data):
        s = m.group(0)
        if s not in seen:
            seen.add(s)
            strings.append(Field(m.start(), "path", s))
    for m in _ID.finditer(data):
        s = m.group(0)
        if s not in seen and not s.isdigit():
            seen.add(s)
            strings.append(Field(m.start(), "string", s))
    strings.sort(key=lambda f: f.offset)
    # float vectors: runs of 2-4 plausible floats on 4-byte alignment
    vecs = []
    n = len(data)
    off = 0
    while off + 12 <= n and len(vecs) < max_vecs:
        v3 = _F.unpack_from(data, off)[0], _F.unpack_from(data, off + 4)[0], _F.unpack_from(data, off + 8)[0]
        if _finite_vec(v3) and all(abs(x) < 1e6 for x in v3):
            kind, val, step = "vec3", tuple(round(x, 3) for x in v3), 12
            if off + 16 <= n:
                w = _F.unpack_from(data, off + 12)[0]
                if math.isfinite(w) and abs(w) < 1e6 and (0 <= w <= 1 or abs(w) > 1e-4):
                    pass  # leave as vec3; vec4 detection is noisy, keep it simple
            vecs.append(Field(off, kind, val))
            off += step
        else:
            off += 4
    return strings[:max_strings], vecs


def describe(data: bytes, type_id: int | None = None) -> Report:
    magic = data[:4]
    if type_id is None:
        type_id = 0
    is_xfs = data[:4] == b"XFS\0"
    known_xfs = typemap.is_xfs(type_id) if type_id else False
    rep = Report(type_id, typemap.extension(type_id) if type_id else "?",
                 typemap.class_name(type_id) if type_id else "(unknown)",
                 len(data), is_xfs, is_xfs and (known_xfs or True), magic)
    if is_xfs:
        try:
            x = xfs.parse(data)
            rep.cls = x.root_class.name
            rep.editable = True
            rep.summary.append(f"XFS: {sum(1 for _ in xfs.walk(x.root))} objects, {len(x.classes)} classes "
                               f"({', '.join(c.name for c in x.classes[:6])}{' ...' if len(x.classes) > 6 else ''})")
            rep.summary.append("edit with:  riftstone param <file>   (byte-exact round-trip)")
            if rep.cls == "rAIFSM":
                from . import fsm
                rep.note = "AI state machine; below as readable pseudo-code"
                rep.summary += [""] + fsm.decompile(x).rstrip("\n").split("\n")
            return rep
        except Exception:  # noqa: BLE001 - fall through to the raw scan
            rep.editable = False
            rep.note = "claims XFS but did not parse; showing a raw scan"
    rep.editable = False
    if magic == b"GMD\0" and _gmd_summary(rep, data):
        return rep
    if magic == b"ITL2" and _itl_summary(rep, data):
        return rep
    if magic in (b"ist\0", b"imx\0") and _table_summary(rep, data):
        return rep
    if magic == b"gpl\0" and _gpl_summary(rep, data):
        return rep
    if magic == b"TEX\0" and _tex_summary(rep, data):
        return rep
    if magic == b"MRL\0" and _mrl_summary(rep, data):
        return rep
    if magic == b"PRPZ" and _prp_summary(rep, data):
        return rep
    if magic == b"EAN\0" and _ean_summary(rep, data):
        return rep
    if magic == b"LMT\0" and _lmt_summary(rep, data):
        return rep
    if (magic in WEATHER_MAGICS or (type_id and typemap.extension(type_id) in WEATHER_TABLES)) \
            and _weather_summary(rep, data, type_id):
        return rep
    if magic == b"LCM\0" and _lcm_summary(rep, data):
        return rep
    if _is_sound(data) and _sound_summary(rep, data):
        return rep
    if bytes(magic) in _EFFECTS and _effect_summary(rep, data):
        return rep
    if _ddo_params_summary(rep, data, type_id):
        return rep
    if bytes(magic) in _MODULES and _module_summary(rep, data, *_MODULES[bytes(magic)]):
        return rep
    if _flat_summary(rep, data, type_id):
        return rep
    rep.strings, rep.vectors = _scan(data)
    _typed_summary(rep, data, type_id)
    return rep


def _gmd_summary(rep: Report, data: bytes, shown: int = 60) -> bool:
    """Text files list their messages by id; False when the bytes do not parse (raw scan instead)."""
    from . import gmd
    from .errors import RiftError

    try:
        g = gmd.parse(data)
    except RiftError as e:
        rep.note = f"claims to be text but does not parse ({e}); showing a raw scan"
        return False
    rep.cls = "rGUIMessage"
    rep.editable = True
    rep.note = "text: dialogue, item names and descriptions, menus"
    labelled = sum(m.label is not None for m in g.messages)
    rep.summary.append(f"{len(g.messages)} message(s) in {g.language_name}"
                       + (f", {labelled} with labels" if labelled else "") + ":")
    for i, m in enumerate(g.messages[:shown]):
        t = m.text.replace("\r\n", " / ").replace("\n", " / ").replace("\r", " / ")
        lab = f" [{m.label}]" if m.label is not None else ""
        rep.summary.append(f"  {i:>5}{lab}  {t[:110]}{'...' if len(t) > 110 else ''}")
    if len(g.messages) > shown:
        rep.summary.append(f"  ... and {len(g.messages) - shown} more (riftstone param <file> writes them all)")
    return True


def _itl_summary(rep: Report, data: bytes, shown: int = 40) -> bool:
    """The item list: count, then id, weight and prices (names need the game: riftstone items list)."""
    from . import itl
    from .errors import RiftError

    try:
        t = itl.parse(data)
    except RiftError as e:
        rep.note = f"claims to be the item list but does not parse ({e}); showing a raw scan"
        return False
    rep.cls = "rItemList"
    rep.editable = True
    rep.note = ("the item list; names: riftstone items list, new items: riftstone items new, an item's stats at each "
                "enhancement level: riftstone items stats")
    rep.summary.append(f"{len(t.records)} items (id, weight, buy, sell, mKind):")
    for i, r in enumerate(t.records[:shown]):
        buy, sell = itl.ItemList.prices(r)
        rep.summary.append(f"  {i:>5}  {itl.ItemList.weight(r):>8.2f} kg  buy {buy:>7}  sell {sell:>7}  "
                           f"kind {itl.BY_NAME['mKind'].get(r)}")
    if len(t.records) > shown:
        rep.summary.append(f"  ... and {len(t.records) - shown} more")
    return True


def _gpl_summary(rep: Report, data: bytes, shown: int = 40) -> bool:
    """Enemy group placement: each group's enemies (mUnitKindList) and its spawn cap."""
    from . import gpl as gpl_mod
    from . import gpl_ddo
    from .errors import RiftError

    if gpl_ddo.is_ddo_gpl(data):
        try:
            gd = gpl_ddo.parse(data)
        except RiftError as e:
            rep.note = f"claims to be a DDO group list but does not parse ({e}); showing a raw scan"
            return False
        rep.cls, rep.editable = "rLayoutGroupParamList", True
        rep.note = ("Dragon's Dogma Online group list (no enemy list: the server spawns them); "
                    "edit with: riftstone param <file>")
        rep.summary += gpl_ddo.summary(gd, shown).split("\n")
        return True
    try:
        g = gpl_mod.parse(data)
    except RiftError as e:
        rep.note = f"claims to be enemy groups but does not parse ({e}); showing a raw scan"
        return False
    rep.cls = "rLayoutGroupParamList"
    rep.editable = True
    rep.note = "enemy group placement; edit with: riftstone param <file>"
    rep.summary.append(f"{len(g.groups)} group(s):")
    for i, grp in enumerate(g.groups[:shown]):
        kinds = ", ".join(u["name"] for u in grp["mUnitKindList"]) or "(none)"
        rep.summary.append(f"  group {grp['mGroup']:<4} cap {grp['mSetCountMax']:<3} priority {grp['mPriority']:<6} "
                           f"{kinds[:70]}")
    if len(g.groups) > shown:
        rep.summary.append(f"  ... and {len(g.groups) - shown} more")
    crowded = [grp["mGroup"] for grp in g.groups if len(grp["mUnitKindList"]) > gpl_mod.UNIT_KINDS_MAX]
    if crowded:
        rep.summary.append(f"  UNSAFE: group(s) {', '.join(map(str, crowded[:10]))} list more than "
                           f"{gpl_mod.UNIT_KINDS_MAX} unit kinds; the game loads them unchecked and overwrites its "
                           f"memory. Cut them to {gpl_mod.UNIT_KINDS_MAX} to save the file.")
    return True


def _tex_summary(rep: Report, data: bytes) -> bool:
    """Texture: dimensions, mip levels, pixel format, and whether it exports to .dds."""
    from . import tex as tex_mod
    from .errors import RiftError

    try:
        t = tex_mod.parse(data)
    except RiftError as e:
        rep.note = f"claims to be a texture but does not parse ({e}); showing a raw scan"
        return False
    rep.cls = "rTexture"
    rep.editable = False
    rep.summary.append(tex_mod.info(t))
    if not t.is_cube and tex_mod._pixels_contiguous(t) is not None:
        rep.note = "texture; edit with: riftstone tex to-dds <file>  (then from-dds to import)"
    else:
        rep.note = "texture (cube map / special format); replace the raw .tex file"
    return True


def _mrl_summary(rep: Report, data: bytes) -> bool:
    """Material list: which shader each material uses and the textures it binds."""
    from . import mrl as mrl_mod
    from .errors import RiftError

    try:
        m = mrl_mod.parse(data)
    except RiftError as e:
        rep.note = f"claims to be a material list but does not parse ({e}); showing a raw scan"
        return False
    rep.cls = "rMaterial"
    rep.editable = False
    rep.note = "material list; textures editable with: riftstone mrl retex <file>"
    rep.summary += mrl_mod.info(m).split("\n")
    return True


def _prp_summary(rep: Report, data: bytes) -> bool:
    """Enemy/character parameters: the class and its recognised stats (HP, attack, scale)."""
    from . import prp as prp_mod
    from . import xfs as xfs_mod
    from .errors import RiftError

    try:
        doc = prp_mod.parse(data)
    except RiftError as e:
        rep.note = f"claims to be a prop-param file but does not parse ({e}); showing a raw scan"
        return False
    rep.cls = "rPropParam"
    rep.editable = True
    rep.note = "enemy/character parameters; edit with: riftstone param <file>  (byte-exact round-trip)"
    cdef = doc.root_class
    rep.summary.append(f"rPropParam class {cdef.name}: {len(cdef.props)} parameters")
    shown = 0
    for prop, vals in zip(cdef.props, doc.root.fields):
        label = prp_mod.GLOSS.get(prop.name)
        if not label:
            continue
        v = vals[0] if len(vals) == 1 else tuple(vals)
        if isinstance(v, xfs_mod.F32Bits):
            import struct as _s
            v = round(_s.unpack("<f", _s.pack("<I", v.bits))[0], 3)
        rep.summary.append(f"  {label:32} = {v}")
        shown += 1
    if not shown:
        rep.summary.append("  (no glossed parameters for this class; open as YAML to see all fields)")
    return True


def _ean_summary(rep: Report, data: bytes) -> bool:
    """Effect animation: frame count and payload size (the frame data is kept opaque)."""
    from . import ean as ean_mod
    from .errors import RiftError

    try:
        e = ean_mod.parse(data)
    except RiftError as exc:
        rep.note = f"claims to be an effect anim but does not parse ({exc}); showing a raw scan"
        return False
    rep.cls = "rEffectAnim"
    rep.editable = False
    rep.note = "effect UV animation; frame payload kept opaque (replace the whole .ean to retarget)"
    rep.summary.append(ean_mod.info(e))
    return True


def _lmt_summary(rep: Report, data: bytes, shown: int = 60) -> bool:
    """Motion list: each motion's length, loop, tracks, codecs and events."""
    from . import lmt as lmt_mod
    from .errors import RiftError

    try:
        m = lmt_mod.parse(data)
    except RiftError as exc:
        rep.note = f"claims to be a motion list but does not parse ({exc}); showing a raw scan"
        return False
    rep.cls = "rMotionList"
    rep.editable = False
    rep.note = ("skeletal animations (byte-exact codec; keyframes: riftstone lmt keys); "
                "bone ids are the model's joint ids")
    rep.summary.append(lmt_mod.info(m))
    rep.summary += lmt_mod.table(m, shown)
    return True


WEATHER_MAGICS = (b"wep\0", b"wfp\0", b"SKY ", b"WSI_")
WEATHER_TABLES = ("wtf", "wte", "wtl", "wta")          # DDO's magic-less weather tables, known by type


def _weather_summary(rep: Report, data: bytes, type_id: int | None) -> bool:
    """Weather effect / fog / sky / DDO weather table: what it holds; editable as YAML."""
    from . import weather
    from .errors import RiftError

    try:
        w = weather.parse(data, typemap.extension(type_id) if type_id else None)
    except RiftError as exc:
        rep.note = f"claims to be a weather/fog/sky resource but does not parse ({exc}); showing a raw scan"
        return False
    rep.cls = weather.KINDS[w.kind].cls
    rep.editable = True
    rep.summary.append(weather.info(w))
    rep.summary.append("edit with:  riftstone param <file>   (YAML, byte-exact round-trip)")
    return True


def _lcm_summary(rep: Report, data: bytes) -> bool:
    """Camera list: cameras, frames and track codecs; editable as YAML."""
    from . import camera
    from .errors import RiftError

    try:
        cl = camera.parse(data)
    except RiftError as exc:
        rep.note = f"claims to be a camera list but does not parse ({exc}); showing a raw scan"
        return False
    rep.cls = "rCameraList"
    rep.editable = True
    rep.summary.append(camera.info(cl))
    rep.summary.append("edit with:  riftstone param <file>   (YAML, byte-exact round-trip)")
    return True


_EFFECTS = {b"epv\0": ("effect", "rEffectProvider"), b"EFL\0": ("effect_efl", "rEffectList"),
            b"E2D\0": ("effect_e2d", "rEffect2D"), b"EFS\0": ("effect_efs", "rEffectStrip")}


def _effect_summary(rep: Report, data: bytes) -> bool:
    """Effect provider / list / 2D effect / strip: what it spawns and loads; the first three edit as YAML."""
    import importlib

    from .errors import RiftError

    module, cls = _EFFECTS[bytes(data[:4])]
    mod = importlib.import_module(f".{module}", __package__)
    try:
        x = mod.parse(data)
    except RiftError as exc:
        rep.note = f"claims to be {cls} but does not parse ({exc}); showing a raw scan"
        return False
    rep.cls = cls
    rep.editable = hasattr(mod, "to_yaml")
    rep.summary += mod.info(x).split("\n")
    if rep.editable:
        rep.summary.append("edit with:  riftstone param <file>   (YAML, byte-exact round-trip)")
    return True


_MODULES = {b"FCA\0": ("facial", "rFacialAnimation", "lip-sync / facial animation curves"),
            b"mss\0": ("msgset", "rMsgSet", "NPC conversation lines"),
            b"mgst": ("msgset", "rMsgSet", "NPC conversation groups"),
            b"msl\0": ("msgset", "rMsgSerial", "message serial list"),
            b"SDL\0": ("schedule", "rScheduler", "timeline of tracks and keys"),
            b"zon\0": ("schedule", "rZone", "stage zones: layouts, groups, grids")}


def _module_summary(rep: Report, data: bytes, module: str, cls: str, note: str) -> bool:
    """A format module with parse / info: what it holds; editable as YAML."""
    import importlib

    from .errors import RiftError

    mod = importlib.import_module(f".{module}", __package__)
    try:
        m = mod.parse(data)
    except RiftError as exc:
        rep.note = f"claims to be a {cls} but does not parse ({exc}); showing a raw scan"
        return False
    rep.cls, rep.editable, rep.note = cls, True, note
    rep.summary += mod.info(m).splitlines()
    rep.summary.append("edit with:  riftstone param <file>   (YAML, byte-exact round-trip)")
    return True


def _ddo_params_summary(rep: Report, data: bytes, type_id: int) -> bool:
    """DDO's enemy and stage parameters (cpe pep prs osp sti sal evtr ndp), by type (four have no magic)."""
    from . import ddo_params
    from .errors import RiftError

    kind = ddo_params.kind_for_type(type_id) if type_id else ddo_params.kind_of(data)
    if kind is None:
        return False
    try:
        m = ddo_params.parse(data, kind)
    except RiftError as exc:
        rep.note = f"claims to be a DDO {kind} parameter file but does not parse ({exc}); showing a raw scan"
        return False
    rep.cls = ddo_params.KINDS[kind].cls
    rep.editable = True
    rep.summary += ddo_params.summary(m).splitlines()
    rep.summary.append("edit with:  riftstone param <file>   (YAML, byte-exact round-trip)")
    return True


def _is_sound(data: bytes) -> bool:
    from . import sound
    return sound.format_of(data) is not None


def _sound_summary(rep: Report, data: bytes) -> bool:
    """Sound cue resources (requests, stream requests, random tables, mixers, banks, area info)."""
    from . import sound
    from .errors import RiftError

    try:
        s = sound.parse(data)
    except RiftError as exc:
        rep.note = f"claims to be a sound cue resource but does not parse ({exc}); showing a raw scan"
        return False
    rep.cls = s.cls
    rep.editable = True
    rep.summary += sound.info(s).splitlines()
    rep.summary.append("edit with:  riftstone param <file>   (YAML, byte-exact round-trip)")
    return True


def _flat_summary(rep: Report, data: bytes, type_id: int) -> bool:
    """The flat parameter formats (flat.py): show scalars and the length of each record array."""
    from . import flat, params
    from .errors import RiftError

    ext = params._flat_ext(data, type_id or None)
    if ext is None:
        return False
    try:
        f = flat.parse(data, ext)
    except RiftError:
        return False
    rep.editable = True
    rep.note = f"{f.ext} parameter table (flat); edit with: riftstone param <file>"
    for fld in flat.SCHEMAS[f.ext][1]:
        name, val = fld[1], f.data.get(fld[1])
        if isinstance(val, list):
            rep.summary.append(f"  {name}: {len(val)} entries")
        elif fld[0] == "s" and not isinstance(val, list):
            rep.summary.append(f"  {name}: {val}")
    return True


def _table_summary(rep: Report, data: bytes, shown: int = 30) -> bool:
    """Item sets / drop tables and recipes: their rows by id (names need the game: riftstone items list)."""
    from . import tables
    from .errors import RiftError

    try:
        t = tables.parse(data)
    except RiftError as e:
        rep.note = f"does not parse as an item table ({e}); showing a raw scan"
        return False
    rep.editable = True
    if t.magic == b"ist\0":
        rep.cls, rep.note = "rItemSetTbl", "item sets / drop tables: [item, weight] slots, weights in percent"
        rep.summary.append(f"{len(t.rows)} sets:")
        for r in t.rows[:shown]:
            slots = ", ".join(f"{'none' if i == tables.NONE else i}:{w}" for i, w in zip(r[4:12], r[12:20]) if w or i != tables.NONE)
            rep.summary.append(f"  set {r[0]:>5}  {slots}")
    else:
        rep.cls, rep.note = "rItemMix", "crafting recipes: ingredient + ingredient makes result x count"
        rep.summary.append(f"{len(t.rows)} recipes:")
        for a, b, c, n in t.rows[:shown]:
            rep.summary.append(f"  {a:>5} + {b:<5} makes {c:>5} x{n}")
    if len(t.rows) > shown:
        rep.summary.append(f"  ... and {len(t.rows) - shown} more")
    return True


def _typed_summary(rep: Report, data: bytes, type_id: int) -> None:
    ext = rep.ext
    if ext == "lot" or data[:4] == b"lot\0":
        from . import lot as lot_mod
        from .errors import RiftError
        rep.note = "enemy/object/NPC spawn layout"
        if data[:4] == b"lot\0" and len(data) >= 12 and int.from_bytes(data[4:8], "little") == 138:
            from . import lot_ddo
            try:
                lt = lot_ddo.parse(data)
            except RiftError as e:
                rep.summary.append(f"does not parse as a DDO layout ({e})")
                return
            rep.editable = True
            rep.note = "Online layout: spawn points, objects, NPCs, gathering spots (enemies come from the server)"
            rep.summary.extend(lot_ddo.summary(lt).splitlines())
            return
        if data[:4] == b"lot\0" and len(data) >= 12:
            try:
                lt = lot_mod.parse(data)
            except RiftError as e:
                rep.summary.append(f"does not parse as a layout ({e})")
                return
            rep.editable = True
            classes = {}
            for r in lt.records:
                classes[r.cls] = classes.get(r.cls, 0) + 1
            rep.summary.append(f"{lt.count} record(s), every field editable as YAML: "
                               + ", ".join(f"{n} {c}" for c, n in classes.items()))
            for r in lt.records[:40]:
                p = r.vec()
                if p is None:
                    rep.summary.append(f"    id {r.id:<4} {r.cls}")
                    continue
                a, s = r.vec("mAngle"), r.vec("mScale")
                rep.summary.append(f"    id {r.id:<4} {r.name or '':<20} at [{p[0]:.1f}, {p[1]:.1f}, {p[2]:.1f}]  "
                                   f"turn {math.degrees(a[1]):.0f} deg  scale {s[0]:g}")
    elif ext == "ocl" and bytes(data[:4]) == b"COL\0":
        from . import ocl_ddo
        from .errors import RiftError
        rep.note = "object collision (Dragon's Dogma Online): hit shapes and attack params"
        try:
            o = ocl_ddo.parse(data)
        except RiftError as e:
            rep.summary.append(f"does not parse as DDO collision ({e})")
            return
        rep.editable = True
        rep.summary.append("every field editable as YAML (riftstone param <file>):")
        rep.summary.extend(ocl_ddo.summary(o).splitlines())
    elif ext == "ocl":
        from . import ocl as ocl_mod
        from .errors import RiftError
        rep.note = "object collision (Dark Arisen): collision shapes and attacks"
        try:
            o = ocl_mod.parse(data)
        except RiftError as e:
            rep.summary.append(f"does not parse as Dark Arisen collision ({e})")
            return
        rep.editable = True
        rep.summary.append("every field editable as YAML (riftstone param <file>):")
        rep.summary.extend(ocl_mod.summary(o).splitlines())
    elif data[:4] == b"ARCS":
        from . import arcref
        from .errors import RiftError
        try:
            ref = arcref.parse(data)
        except RiftError as e:
            rep.summary.append(f"does not parse as an archive reference ({e})")
            return
        rep.note = "archive reference: the archive this resource names is pulled in, with these resources"
        rep.summary.append(f"{len(ref.entries)} resource(s) of the referenced archive (name hash, type):")
        for h, t in ref.entries[:30]:
            rep.summary.append(f"    {h:08x}  {typemap.extension(t)}")
        if len(ref.entries) > 30:
            rep.summary.append(f"    ... and {len(ref.entries) - 30} more")
    elif ext == "nav" or bytes(data[:4]) == b"NAV\0":
        from . import nav as nav_mod

        try:
            n = nav_mod.parse(data)
        except RiftError as e:
            rep.summary.append(f"does not parse as a navigation mesh ({e})")
            return
        rep.cls = "rNavigationMesh"
        rep.note = "navigation mesh: where the stage's AI walks (docs/formats.md NAV; riftstone nav <stage>)"
        rep.summary.append(nav_mod.info(n))
    elif ext in ("sbc", "ccl", "ctc", "rbd"):
        rep.note = {"sbc": "stage/model collision mesh", "ccl": "chain collision",
                    "ctc": "tiny chain collision", "rbd": "rigid body"}[ext]
        rep.summary.append("collision/physics: mesh data. Blender round-trip via Albam (sbc); "
                           "Riftstone shows structure only.")
    elif ext in ("mod", "mrl", "tex", "lmt"):
        rep.note = {"mod": "model mesh", "mrl": "material", "tex": "texture", "lmt": "animation"}[ext]
        rep.summary.append("mesh/material/texture/animation: use Albam (Blender 4.x) to import/export.")


def _lot_placements(data: bytes):
    """(name, position) of each placed record in a layout file (see lot.py)."""
    from . import lot as lot_mod

    if data[:4] != b"lot\0" or len(data) < 12:
        return []
    lt = lot_mod.parse(data)
    return [(r.name, r.vec()) for r in lt.placements]
