"""rEffect2D ``.e2d``: screen-space (2D) effects -- sprites, lines and models drawn over the screen.

Same family as rEffectList (``effect_efl``): a 0x20-byte header, then ``mParamBuffSize`` bytes the
engine copies verbatim and walks by offset (``rEffect2D::load`` PS3 0x01026DB4 / DDDA.exe 0x011464F0,
``setupResourceInfo`` PS3 0x01026828, ``ResourceInfo::createParticleResources`` PS3 0x01026580):

  0x00 "E2D\\0"  0x04 version (DDDA 0x20110314, DDO 0x20120306)  0x08 mParamBuffSize (= file size - 0x20)
  0x0C u32 mListNum  0x10 f32 mBaseFps  0x14..0x1F three u32 (0 in every file; not read)
Param buffer:
  0x000  0x40 bytes (not named; kept as bytes)
  0x040  3 x 64-byte paths -> rRenderTargetTexture (the class's mpRTTexture[3])
  0x100  3 x 64-byte paths -> rTexture (mpBackTexture[3])
  0x1C0  mListNum x 16-byte entries:  GeneratorUnk:8 | GeneratorOffset:24,  ParticleType:8 | ParticleOffset:24,
         LifeType:8 | LifeOffset:24,  MoveType:8 | MoveOffset:24   (offset 0 = none)
  then the structures (E2D_GENERATOR 0x70, E2D_PARTICLE_*, E2D_LIFE_FRAME 0x10, E2D_MOVE_COMMON 0x30 --
  sizes from the PS3 build, which has no member names for them); each owns the bytes up to the next
  referenced offset (a region, as in .efl).

Particle kinds are ``cParticle2DGenerator::initParticle``'s dispatch (0 Sprite, 1 Polyline, 2 Texline,
3 Line, 4 Model); life kinds likewise (1-2 Frame, 3-4 Keyframe, 5-6 Hideframe); move 1 Add, 2 Mul.
createParticleResources loads, for types 0-2, three rTexture paths at +0x70/+0xB0/+0xF0 and an
rEffectAnim path at +0x130, and for type 4 an rModel path at +0x50 -- decoded as named 64-byte paths
(descriptive names, by the class loaded).

Proof (2026-09-25, every distinct file): parse -> build and the YAML round trip are byte-exact on DDDA
156/156 and DDO 52/52.  Named share (``coverage``: header, entry table, the six prefix path slots and
the particle path slots): DDDA 53.9%, DDO 45.2%; generator, life, move and the rest of each particle
stay bytes.  UNKNOWN: the 0x40-byte block, GeneratorUnk (0/1), every E2D struct's members, life type 7
(DDO, 2 uses).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .effect_efl import (Region, _fields_node, _hex_node, _w, _yf32, _yfields, _yhex, _yint, decode, encode,
                         schema_size)
from .errors import FormatError, ParamError

MAGIC = b"E2D\0"
VERSION_DDDA = 0x20110314
VERSION_DDO = 0x20120306
VERSIONS = (VERSION_DDDA, VERSION_DDO)
HEADER = 0x20
TABLE = 0x1C0            # entries start here
TAG = "e2d/1"
_HEAD = struct.Struct("<4sIIIIIII")
_U32 = struct.Struct("<I")
MAX_OFFSET = 0xFFFFFF

PARTICLE_KINDS = {0: "Sprite", 1: "Polyline", 2: "Texline", 3: "Line", 4: "Model"}
LIFE_KINDS = {1: "Frame", 2: "Frame", 3: "Keyframe", 4: "Keyframe", 5: "Hideframe", 6: "Hideframe"}
MOVE_KINDS = {1: "Add", 2: "Mul"}
PREFIX = (("raw00", "raw64"), ("RTTexturePath", "char64", 3), ("BackTexturePath", "char64", 3))
_PARTICLE = {t: (("raw00", "raw112"), ("TexturePath", "char64", 3), ("AnimPath", "char64")) for t in (0, 1, 2)}
_PARTICLE[4] = (("raw00", "raw80"), ("ModelPath", "char64"))


def particle_kind(t: int) -> str:
    return PARTICLE_KINDS.get(t, f"type {t} (UNKNOWN)")


@dataclass
class Entry:
    generator_unk: int
    generator: int | None
    particle_type: int
    particle: int | None
    life_type: int
    life: int | None
    move_type: int
    move: int | None


@dataclass
class E2d:
    version: int = VERSION_DDDA
    base_fps: int = 0x41F00000
    reserved: tuple = (0, 0, 0)
    prefix: bytes = bytes(TABLE)                   # the 0x1C0 bytes before the entry table
    entries: list[Entry] = field(default_factory=list)
    regions: list[Region] = field(default_factory=list)


def parse(data: bytes) -> E2d:
    data = bytes(data)
    if data[:4] != MAGIC:
        raise FormatError("e2d", f"not an .e2d file (magic {data[:4]!r}, expected {MAGIC!r})", 0)
    if len(data) < HEADER:
        raise FormatError("e2d", f"truncated header ({len(data)} bytes)", 0)
    _, version, size, ln, fps, *res = _HEAD.unpack_from(data, 0)
    if version not in VERSIONS:
        raise FormatError("e2d", f"version 0x{version:08x} is not one the games use (0x{VERSION_DDDA:08x}, "
                                 f"0x{VERSION_DDO:08x})", 4)
    buf = data[HEADER:]
    if size != len(buf):
        raise FormatError("e2d", f"mParamBuffSize {size} != {len(buf)} bytes after the header", 8)
    tab = TABLE + 16 * ln
    if ln > len(buf) or tab > len(buf):
        raise FormatError("e2d", f"{ln} entries need {tab} bytes; the buffer has {len(buf)}", 0x0C)
    words = [struct.unpack_from("<4I", buf, TABLE + 16 * i) for i in range(ln)]
    refs: dict[int, list] = {}
    for i, w in enumerate(words):
        for k, (role, typ) in enumerate((("generator", None), ("particle", w[1] & 0xFF), ("life", w[2] & 0xFF),
                                         ("move", w[3] & 0xFF))):
            off = w[k] >> 8
            if not off:
                continue
            if not tab <= off < len(buf):
                raise FormatError("e2d", f"{role} offset 0x{off:x} is outside the data area",
                                  HEADER + TABLE + 16 * i + 4 * k)
            refs.setdefault(off, [])
            if (role, typ) not in refs[off]:
                refs[off].append((role, typ))
    starts = sorted(refs)
    if len(buf) > tab and (not starts or starts[0] != tab):
        starts.insert(0, tab)
        refs.setdefault(tab, [])
    index = {o: k for k, o in enumerate(starts)}
    ends = starts[1:] + [len(buf)]
    regions = [Region(buf[o:end], refs[o]) for o, end in zip(starts, ends)]

    def rix(o):
        return index[o] if o else None

    entries = [Entry(w[0] & 0xFF, rix(w[0] >> 8), w[1] & 0xFF, rix(w[1] >> 8), w[2] & 0xFF, rix(w[2] >> 8),
                     w[3] & 0xFF, rix(w[3] >> 8)) for w in words]
    return E2d(version, fps, tuple(res), bytes(buf[:TABLE]), entries, regions)


def build(e: E2d) -> bytes:
    if e.version not in VERSIONS:
        raise FormatError("e2d", f"version 0x{e.version:08x} is not one the games use")
    if len(e.prefix) != TABLE:
        raise FormatError("e2d", f"the block before the entries is {TABLE} bytes, not {len(e.prefix)}")
    tab = TABLE + 16 * len(e.entries)
    offsets, p = [], tab
    for r in e.regions:
        offsets.append(p)
        p += len(r.data)

    def word(low, i, what):
        if not isinstance(low, int) or not 0 <= low <= 0xFF:
            raise FormatError("e2d", f"{what}: type {low!r} is out of range (0..255)")
        if i is None:
            return low
        if not isinstance(i, int) or not 0 <= i < len(e.regions):
            raise FormatError("e2d", f"{what} names region {i!r}; there are {len(e.regions)}")
        if not e.regions[i].data:
            raise FormatError("e2d", f"{what} names region {i}, which is empty")
        if offsets[i] > MAX_OFFSET:
            raise FormatError("e2d", f"{what}: offset does not fit 24 bits")
        return offsets[i] << 8 | low

    out = bytearray(e.prefix)
    for n, en in enumerate(e.entries):
        out += struct.pack("<4I", word(en.generator_unk, en.generator, f"entry {n} generator"),
                           word(en.particle_type, en.particle, f"entry {n} particle"),
                           word(en.life_type, en.life, f"entry {n} life"),
                           word(en.move_type, en.move, f"entry {n} move"))
    for r in e.regions:
        out += r.data
    try:
        head = _HEAD.pack(MAGIC, e.version, len(out), len(e.entries), e.base_fps, *e.reserved)
    except struct.error as exc:
        raise FormatError("e2d", f"a header value does not fit: {exc}") from None
    return head + bytes(out)


def region_schema(e: E2d, i: int):
    kinds = {(ro, t) for ro, t in e.regions[i].roles}
    if len(kinds) != 1:
        return None
    role, typ = next(iter(kinds))
    s = _PARTICLE.get(typ) if role == "particle" else None
    return s if s and schema_size(s) <= len(e.regions[i].data) else None


def prefix_fields(e: E2d) -> dict:
    return decode(PREFIX, e.prefix)


def resources(e: E2d) -> list[tuple[int | None, str, str, str]]:
    """(region or None for the prefix, slot, class, path) for every named resource."""
    out = []
    f = prefix_fields(e)
    for slot, cls in (("RTTexturePath", "rRenderTargetTexture"), ("BackTexturePath", "rTexture")):
        for n, p in enumerate(f[slot]):
            if isinstance(p, str) and p:
                out.append((None, f"{slot}[{n}]", cls, p))
    for i in range(len(e.regions)):
        s = region_schema(e, i)
        if not s:
            continue
        v = decode(s, e.regions[i].data)
        for slot, cls in (("TexturePath", "rTexture"), ("AnimPath", "rEffectAnim"), ("ModelPath", "rModel")):
            x = v.get(slot)
            for n, p in enumerate(x if isinstance(x, list) else [x]):
                if isinstance(p, str) and p:
                    out.append((i, f"{slot}[{n}]" if isinstance(x, list) else slot, cls, p))
    return out


def coverage(e: E2d) -> dict:
    raw = build(e)
    named = HEADER + (TABLE - 0x40) + 16 * len(e.entries)
    for i in range(len(e.regions)):
        s = region_schema(e, i)
        if s:
            named += sum(64 * (it[2] if len(it) > 2 else 1) for it in s if it[1] == "char64")
    return {"bytes": len(raw), "named": named, "opaque": len(raw) - named}


def info(e: E2d, limit: int = 16) -> str:
    game = "Dark Arisen" if e.version == VERSION_DDDA else "Online"
    lines = [f"rEffect2D 0x{e.version:08x} ({game}): {len(e.entries)} units, {len(e.regions)} structures"]
    for n, en in enumerate(e.entries[:limit]):
        life = LIFE_KINDS.get(en.life_type, "?") if en.life is not None else "-"
        move = MOVE_KINDS.get(en.move_type, "?") if en.move is not None else "-"
        lines.append(f"  unit {n:<3} {particle_kind(en.particle_type):<9} life {life}  move {move}")
    if len(e.entries) > limit:
        lines.append(f"  ... {len(e.entries) - limit} more units")
    res = resources(e)
    if res:
        lines.append("resources:")
        for p in dict.fromkeys((cls, path) for _, _, cls, path in res):
            lines.append(f"  {p[0]:<20} {p[1]}")
    return "\n".join(lines)


# -- YAML --------------------------------------------------------------------------------------------
def to_yaml(e: E2d, name: str | None = None) -> str:
    from . import yamlish
    from .params import f32_bits_text
    from .yamlish import Map, Scalar, Seq

    head = ["Riftstone 2D effect (.e2d)" + (f" -- {name}" if name else ""),
            "units: one emitter each -- the regions (by number) holding its generator, particle, life and",
            "move. prefix: the block before the units (render-target and back texture paths). Particle",
            "texture/anim/model paths are named; other bytes are hex. Rebuilds byte-for-byte when untouched."]
    items = [(Scalar("riftstone"), Scalar(TAG))]
    if name:
        items.append((Scalar("resource"), Scalar(name, "double")))
    items += [(Scalar("version"), Scalar(f"0x{e.version:08x}")),
              (Scalar("mBaseFps"), Scalar(f32_bits_text(e.base_fps)))]
    if e.reserved != (0, 0, 0):
        items.append((Scalar("reserved"), Seq([Scalar(f"0x{x:08x}") for x in e.reserved], flow=True)))
    items.append((Scalar("prefix"), _fields_node(PREFIX, prefix_fields(e))))

    def rn(v):
        return Scalar("null" if v is None else str(v))

    units = []
    for en in e.entries:
        units.append(Map([(Scalar(k), rn(v) if k in ("generator", "particle", "life", "move") else Scalar(str(v)))
                          for k, v in (("generator_unk", en.generator_unk), ("generator", en.generator),
                                       ("particle_type", en.particle_type), ("particle", en.particle),
                                       ("life_type", en.life_type), ("life", en.life),
                                       ("move_type", en.move_type), ("move", en.move))], flow=True))
    items.append((Scalar("units"), Seq(units)))
    regs = []
    for i, r in enumerate(e.regions):
        role = ", ".join(f"{ro} {particle_kind(t)}" if ro == "particle" else ro for ro, t in r.roles) or "unreferenced"
        kv = [(Scalar("region"), Scalar(str(i), comment=f"{role}, {len(r.data)} bytes"))]
        s = region_schema(e, i)
        if s:
            kv.append((Scalar("fields"), _fields_node(s, decode(s, r.data))))
        tail = r.data[schema_size(s):] if s else r.data
        if tail:
            kv.append((Scalar("tail"), _hex_node(tail)))
        regs.append(Map(kv))
    items.append((Scalar("regions"), Seq(regs)))
    return yamlish.emit(Map(items), head)


def from_yaml(text: str, source: str | None = None) -> E2d:
    from . import yamlish
    from .yamlish import Map, Scalar, Seq

    doc = yamlish.parse(text, source)
    if not isinstance(doc, Map) or not isinstance(doc.get("riftstone"), Scalar) or doc.get("riftstone").text != TAG:
        raise ParamError(f"not a Riftstone 2D effect (expected 'riftstone: {TAG}')", 1, 1, source)
    version = _yint(doc.get("version") or doc, "version", source)
    if version not in VERSIONS:
        raise ParamError(f"version must be 0x{VERSION_DDDA:08x} or 0x{VERSION_DDO:08x}",
                         *_w(doc.get("version"), source))
    e = E2d(version, _yf32(doc.get("mBaseFps") or doc, "mBaseFps", source))
    res = doc.get("reserved")
    if res is not None:
        if not isinstance(res, Seq) or len(res.items) != 3:
            raise ParamError("'reserved' is three numbers", *_w(res, source))
        e.reserved = tuple(_yint(x, "reserved", source) for x in res.items)
    try:
        e.prefix = encode(PREFIX, _yfields(doc.get("prefix") or doc, PREFIX, "prefix", source))
    except FormatError as exc:
        raise ParamError(f"prefix: {exc}", *_w(doc.get("prefix"), source)) from None
    units = doc.get("units")
    for n, u in enumerate(units.items if isinstance(units, Seq) else []):
        if not isinstance(u, Map):
            raise ParamError("each unit is a mapping", *_w(u, source))

        def g(k, hi=0xFF, null=False):
            v = u.get(k)
            if v is None:
                raise ParamError(f"unit {n}: '{k}' is missing", *_w(u, source))
            return _yint(v, k, source, 0, hi, allow_null=null)
        e.entries.append(Entry(g("generator_unk"), g("generator", MAX_OFFSET, True), g("particle_type"),
                               g("particle", MAX_OFFSET, True), g("life_type"), g("life", MAX_OFFSET, True),
                               g("move_type"), g("move", MAX_OFFSET, True)))
    regs = doc.get("regions")
    nodes = regs.items if isinstance(regs, Seq) else []
    for n, en in enumerate(e.entries):
        for what, i in (("generator", en.generator), ("particle", en.particle), ("life", en.life), ("move", en.move)):
            if i is not None and i >= len(nodes):
                raise ParamError(f"unit {n}: {what} names region {i}; there are {len(nodes)}",
                                 *_w(units.items[n], source))
    e.regions = [Region(b"") for _ in nodes]
    for en in e.entries:
        for role, i, t in (("generator", en.generator, None), ("particle", en.particle, en.particle_type),
                           ("life", en.life, en.life_type), ("move", en.move, en.move_type)):
            if i is not None and 0 <= i < len(e.regions) and (role, t) not in e.regions[i].roles:
                e.regions[i].roles.append((role, t))
    for i, r in enumerate(nodes):
        if not isinstance(r, Map):
            raise ParamError("each region is a mapping", *_w(r, source))
        tail = _yhex(r.get("tail"), "tail", source) if r.get("tail") is not None else b""
        fn = r.get("fields")
        if fn is None:
            e.regions[i].data = tail
            continue
        kinds = {(ro, t) for ro, t in e.regions[i].roles}
        s = _PARTICLE.get(next(iter(kinds))[1]) if len(kinds) == 1 and next(iter(kinds))[0] == "particle" else None
        if s is None:
            raise ParamError(f"region {i}: only Sprite/Polyline/Texline/Model particles have named fields",
                             *_w(r, source))
        try:
            e.regions[i].data = encode(s, _yfields(fn, s, f"region {i}", source)) + tail
        except FormatError as exc:
            raise ParamError(f"region {i}: {exc}", *_w(r, source)) from None
    try:
        build(e)
    except FormatError as exc:
        raise ParamError(str(exc), None, None, source) from None
    return e


def yaml_to_bytes(text: str, source: str | None = None) -> bytes:
    return build(from_yaml(text, source))
