"""XFS: MT Framework's serialized object trees.

Dragon's Dogma keeps most tunable data in XFS resources: player/enemy
parameters, AI state machines (rAIFSM), goal planning, camera, sound
sequencing, quest flags, shops.  Layout, measured over all 4,601 distinct
vanilla XFS resources (20,000+ instances across 44 resource types):

    header (0x14 bytes): "XFS\\0", u16 0x0109, u16 class-specific version,
        u32 object count, u32 class count, u32 definition-block size
    definition block (starts at 0x14):
        u32 offset[class count]           (relative to the block start)
        class: u32 class hash, u32 engine value, u32 property count,
               property x count: u32 name offset, u8 type, u8 attr,
                                  u16 size, 16 zero bytes
        NUL-terminated UTF-8 names, zero-padded to 4 bytes
    object tree, pre-order, objects numbered 0, 1, 2 ...:
        u16 class index << 1 | 1, u16 object number,
        u32 size (from this field to the object's end),
        per property: u32 count, then count values

Values: fixed-size numbers, vectors and shapes are stored as-is; strings
are NUL-terminated; a class/classref value is a nested object, or FE FF 00 00
for none; a resource reference is 02, class name, NUL, path, NUL.

Dragon's Dogma Online writes version 0x000F (measured on all 5,907 of its XFS
instances): the header has one more u32 after the object count (zero in every
file, kept as ``extra['reserved']``), so it is 0x18 bytes, and a class record
has no engine value (u32 class hash, u32 property count, then the same 24-byte
properties). Objects and values are identical. ``ClassDef.engine_value`` is
None for such classes; ``Xfs.version`` says which layout to write.

Text in string values is UTF-8 in Dark Arisen and Shift-JIS (cp932) in Online
(``text_encoding``; measured 2026-09-25: DDDA's 40,350 non-ASCII values of
486,494 are all valid UTF-8; DDO's 9,172 of 405,859 all decode and re-encode
exactly as cp932 and none is valid UTF-8).  The parser keeps bytes;
``decode_text``/``encode_text`` turn them into text and back without loss.
Strings have no padding or alignment (only the name table is padded).
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field

from . import typemap, xfsclasses
from .errors import FormatError

MAGIC = b"XFS\0"
VERSION = 0x0109          # Dragon's Dogma: Dark Arisen
VERSION_DDO = 0x000F      # Dragon's Dogma Online
HEADER = struct.Struct("<4sHHIII")
HEADER_DDO = struct.Struct("<4sHHIIII")   # + u32 reserved after the object count
NULL_REF = b"\xfe\xff\x00\x00"
MAX_DEPTH = 256
MAX_CLASSES = 4096
MAX_PROPS = 4096
MAX_COUNT = 1 << 20

CLASS_NAMES: dict[int, str] = {typemap.jamcrc(n): n for n in xfsclasses.NAMES}
CLASS_NAMES.update({typemap.jamcrc(c): c for c, *_ in typemap.TYPES_BY_CLASS})
CLASS_IDS: dict[str, int] = {n: h for h, n in CLASS_NAMES.items()}

# type code -> (name, struct of one element or None when variable-length)
TYPES: dict[int, tuple[str, struct.Struct | None]] = {
    0x01: ("class", None),
    0x02: ("classref", None),
    0x03: ("bool", struct.Struct("<B")),
    0x04: ("u8", struct.Struct("<B")),
    0x05: ("u16", struct.Struct("<H")),
    0x06: ("u32", struct.Struct("<I")),
    0x07: ("u64", struct.Struct("<Q")),
    0x08: ("s8", struct.Struct("<b")),
    0x09: ("s16", struct.Struct("<h")),
    0x0A: ("s32", struct.Struct("<i")),
    0x0B: ("s64", struct.Struct("<q")),
    0x0C: ("f32", struct.Struct("<f")),
    0x0D: ("f64", struct.Struct("<d")),
    0x0E: ("string", None),
    0x13: ("matrix", struct.Struct("<16f")),
    0x14: ("vector3", struct.Struct("<4f")),
    0x15: ("vector4", struct.Struct("<4f")),
    0x16: ("quaternion", struct.Struct("<4f")),
    0x20: ("cstring", None),
    0x22: ("float2", struct.Struct("<2f")),
    0x23: ("float3", struct.Struct("<3f")),
    0x24: ("float4", struct.Struct("<4f")),
    0x2D: ("sphere", struct.Struct("<4f")),
    0x2F: ("aabb", struct.Struct("<8f")),
    0x30: ("obb", struct.Struct("<20f")),      # Online (quest scripts): 4x4 matrix + extent, 80 bytes
    0x31: ("cylinder", struct.Struct("<12f")),
    0x37: ("rangef", struct.Struct("<2f")),
    0x38: ("rangeu16", struct.Struct("<2H")),
    0x39: ("hermitecurve", struct.Struct("<16f")),
    0x80: ("resource", None),
}
TYPE_CODES: dict[str, int] = {v[0]: k for k, v in TYPES.items()}
OBJECT_TYPES = (0x01, 0x02)
STRING_TYPES = (0x0E, 0x20)
FLOAT_TYPES = {k for k, (n, st) in TYPES.items() if st is not None and st.format[-1] in "fd"}
_U32 = struct.Struct("<I")
_HH = struct.Struct("<HH")
_HHI = struct.Struct("<HHI")


def class_name(h: int) -> str:
    return CLASS_NAMES.get(h, f"0x{h:08x}")


# -- text in string values -----------------------------------------------------

TEXT_ENCODINGS = {VERSION: "utf-8", VERSION_DDO: "cp932"}


def text_encoding(version: int) -> str:
    """The encoding of a file's strings: UTF-8 in Dark Arisen (0x0109), Shift-JIS/cp932 in Online (0x000f)."""
    return TEXT_ENCODINGS.get(version, "utf-8")


def decode_text(raw: bytes, version: int = VERSION) -> str:
    """A stored string (value, or part of a resource reference) as text, without loss.

    Bytes the file's encoding cannot read -- and, for cp932, a character stored in another of its
    byte forms (NEC/IBM duplicates such as 87 90 for U+2252, whose canonical form is 81 E0) -- become
    surrogate escapes (U+DC80..U+DCFF, like Python's 'surrogateescape'), so encode_text gives the
    same bytes back.  A Shift-JIS trail byte 0x5C (ソ = 83 5C) is part of its character, never a
    backslash."""
    raw = bytes(raw)
    if text_encoding(version) != "cp932":
        return raw.decode("utf-8", "surrogateescape")
    try:
        text = raw.decode("cp932")
        if text.encode("cp932") == raw:
            return text
    except UnicodeDecodeError:
        pass
    out, i, n = [], 0, len(raw)
    while i < n:
        b = raw[i]
        width = 2 if (0x81 <= b <= 0x9F or 0xE0 <= b <= 0xFC) and i + 1 < n else 1
        chunk = raw[i:i + width]
        try:
            ch = chunk.decode("cp932")
        except UnicodeDecodeError:
            ch = ""
        if len(ch) == 1 and ch.encode("cp932") == chunk:
            out.append(ch)
            i += width
        else:           # only lead bytes (0x81..0xFC) get here: every single byte reads and writes back as itself
            out.append(chr(0xDC00 | b))
            i += 1
    return "".join(out)


def encode_text(text: str, version: int = VERSION) -> bytes:
    """Text -> the bytes a string stores (the inverse of decode_text).  UnicodeEncodeError when the
    file's encoding lacks a character (``unencodable`` names it)."""
    return text.encode(text_encoding(version), "surrogateescape")


def unencodable(text: str, version: int = VERSION) -> str | None:
    """The first character of text that the file's encoding cannot store, or None."""
    enc = text_encoding(version)
    for c in text:
        try:
            c.encode(enc, "surrogateescape")
        except UnicodeEncodeError:
            return c
    return None


@dataclass(frozen=True)
class Prop:
    name: str
    type: int
    attr: int
    size: int
    enc: str = "utf-8"   # the name's encoding in the file; some Online names are Shift-JIS ("cp932")

    @property
    def type_name(self) -> str:
        return TYPES[self.type][0]

    @property
    def is_array(self) -> bool:
        return bool(self.attr & 0x20)


@dataclass(frozen=True)
class ClassDef:
    type_id: int
    engine_value: int | None  # per-class number DDDA writes (likely the object size); None in DDO files
    props: tuple[Prop, ...]

    @property
    def name(self) -> str:
        return class_name(self.type_id)


@dataclass(frozen=True)
class ResourceRef:
    cls: bytes
    path: bytes


@dataclass(frozen=True)
class F32Bits:
    """A float that must keep its exact bits (NaN payloads)."""
    bits: int


class Obj:
    __slots__ = ("cls", "fields")

    def __init__(self, cls: int, fields: list[list]):
        self.cls = cls
        self.fields = fields

    def __repr__(self) -> str:
        return f"Obj(cls={self.cls}, fields={self.fields!r})"

    def __eq__(self, other) -> bool:
        return isinstance(other, Obj) and self.cls == other.cls and self.fields == other.fields


@dataclass
class Xfs:
    minor: int
    classes: list[ClassDef]
    root: Obj
    extra: dict = field(default_factory=dict)
    version: int = VERSION    # VERSION (DDDA layout) or VERSION_DDO

    @property
    def root_class(self) -> ClassDef:
        return self.classes[self.root.cls]

    def class_index(self, type_id: int) -> int | None:
        for i, c in enumerate(self.classes):
            if c.type_id == type_id:
                return i
        return None


# -- parsing -----------------------------------------------------------------

def parse(data: bytes) -> Xfs:
    data = bytes(data)
    n = len(data)
    if n < HEADER.size:
        raise FormatError("XFS", "file is shorter than the 20-byte header", 0)
    magic, version = struct.unpack_from("<4sH", data, 0)
    if magic != MAGIC:
        raise FormatError("XFS", f"not an XFS file (magic {magic!r})", 0)
    reserved = 0
    if version == VERSION:
        _, _, minor, obj_count, ncls, defsize = HEADER.unpack_from(data, 0)
        head, rec = HEADER.size, 12
    elif version == VERSION_DDO:
        if n < HEADER_DDO.size:
            raise FormatError("XFS", "file is shorter than the 24-byte header", 0)
        _, _, minor, obj_count, reserved, ncls, defsize = HEADER_DDO.unpack_from(data, 0)
        head, rec = HEADER_DDO.size, 8
    else:
        raise FormatError("XFS", f"version 0x{version:04x}; Dark Arisen uses 0x0109, Online 0x000f", 4)
    if not 1 <= ncls <= MAX_CLASSES:
        raise FormatError("XFS", f"class count {ncls} out of range", head - 8)
    base = head
    dend = base + defsize
    if defsize < 4 * ncls or dend > n:
        raise FormatError("XFS", f"definition block of {defsize} bytes does not fit", 0x10)
    offsets = struct.unpack_from(f"<{ncls}I", data, base)
    classes: list[ClassDef] = []
    names: dict[int, tuple[str, str]] = {}

    def name_at(off: int, where: int) -> tuple[str, str]:
        if off in names:
            return names[off]
        at = base + off
        if not base <= at < dend:
            raise FormatError("XFS", "property name outside the definition block", where)
        stop = data.find(b"\0", at, dend)
        if stop < 0:
            raise FormatError("XFS", "unterminated property name", at)
        raw = data[at:stop]
        for enc in ("utf-8", "cp932"):   # Online stores a few Japanese names in Shift-JIS
            try:
                text = raw.decode(enc)
            except UnicodeDecodeError:
                continue
            if text.encode(enc) == raw:
                names[off] = (text, enc)
                return names[off]
        raise FormatError("XFS", "property name is neither UTF-8 nor Shift-JIS", at)

    for ci, off in enumerate(offsets):
        at = base + off
        if at + rec > dend or off < 4 * ncls:
            raise FormatError("XFS", f"class {ci} definition outside the block", base + 4 * ci)
        if rec == 12:
            type_id, engine_value, nprops = struct.unpack_from("<III", data, at)
        else:
            (type_id, nprops), engine_value = struct.unpack_from("<II", data, at), None
        if nprops > MAX_PROPS or at + rec + nprops * 24 > dend:
            raise FormatError("XFS", f"class {ci}: {nprops} properties do not fit", at + rec - 4)
        props = []
        for k in range(nprops):
            q = at + rec + k * 24
            noff, t, attr, size = struct.unpack_from("<IBBH", data, q)
            if data[q + 8:q + 24] != bytes(16):
                raise FormatError("XFS", f"class {ci} property {k}: reserved bytes are not zero", q + 8)
            if t not in TYPES:
                raise FormatError("XFS", f"class {class_name(type_id)} property {k}: type 0x{t:02x} is not supported yet", q + 4)
            st = TYPES[t][1]
            if st is not None and st.size != size:
                raise FormatError("XFS", f"class {class_name(type_id)} property {k}: {TYPES[t][0]} with size {size}", q + 6)
            pname, penc = name_at(noff, q)
            props.append(Prop(pname, t, attr, size, penc))
        if any(c.type_id == type_id for c in classes):
            raise FormatError("XFS", f"class {class_name(type_id)} is declared twice", at)
        classes.append(ClassDef(type_id, engine_value, tuple(props)))

    counter = [0]

    def read_obj(pos: int, depth: int) -> tuple[Obj, int]:
        if depth > MAX_DEPTH:
            raise FormatError("XFS", f"objects nested deeper than {MAX_DEPTH}", pos)
        if pos + 8 > n:
            raise FormatError("XFS", "object header runs past the end", pos)
        tag, number, size = _HHI.unpack_from(data, pos)
        if not tag & 1:
            raise FormatError("XFS", f"object tag 0x{tag:04x} lacks its marker bit", pos)
        ci = tag >> 1
        if ci >= len(classes):
            raise FormatError("XFS", f"object uses class {ci} but only {len(classes)} are declared", pos)
        if number != counter[0]:
            raise FormatError("XFS", f"object number {number} where {counter[0]} was expected", pos + 2)
        counter[0] += 1
        end = pos + 4 + size
        if size < 4 or end > n:
            raise FormatError("XFS", f"object size {size} runs past the end", pos + 4)
        cur = pos + 8
        fields: list[list] = []
        for prop in classes[ci].props:
            if cur + 4 > end:
                raise FormatError("XFS", f"{classes[ci].name}.{prop.name}: count runs past the object", cur)
            count = _U32.unpack_from(data, cur)[0]
            cur += 4
            if count > MAX_COUNT:
                raise FormatError("XFS", f"{classes[ci].name}.{prop.name}: count {count} is implausible", cur - 4)
            values: list = []
            t = prop.type
            st = TYPES[t][1]
            if st is not None:
                need = st.size * count
                if cur + need > end:
                    raise FormatError("XFS", f"{classes[ci].name}.{prop.name}: {count} values run past the object", cur)
                for _ in range(count):
                    raw = st.unpack_from(data, cur)
                    if t in FLOAT_TYPES and any(v != v for v in raw):
                        raw = _nan_safe(data, cur, st)
                    values.append(raw[0] if len(raw) == 1 else raw)
                    cur += st.size
            elif t in STRING_TYPES:
                for _ in range(count):
                    stop = data.find(b"\0", cur, end)
                    if stop < 0:
                        raise FormatError("XFS", f"{classes[ci].name}.{prop.name}: unterminated string", cur)
                    values.append(data[cur:stop])
                    cur = stop + 1
            elif t in OBJECT_TYPES:
                for _ in range(count):
                    if data[cur:cur + 4] == NULL_REF:
                        values.append(None)
                        cur += 4
                        continue
                    child, cur = read_obj(cur, depth + 1)
                    if cur > end:
                        raise FormatError("XFS", f"{classes[ci].name}.{prop.name}: child runs past its parent", cur)
                    values.append(child)
            else:  # resource reference
                for _ in range(count):
                    if cur >= end or data[cur] != 2:
                        raise FormatError("XFS", f"{classes[ci].name}.{prop.name}: resource reference kind "
                                                 f"{data[cur] if cur < end else 'EOF'} (expected 2)", cur)
                    s1 = data.find(b"\0", cur + 1, end)
                    s2 = data.find(b"\0", s1 + 1, end) if s1 >= 0 else -1
                    if s1 < 0 or s2 < 0:
                        raise FormatError("XFS", f"{classes[ci].name}.{prop.name}: unterminated resource reference", cur)
                    values.append(ResourceRef(data[cur + 1:s1], data[s1 + 1:s2]))
                    cur = s2 + 1
            fields.append(values)
        if cur != end:
            raise FormatError("XFS", f"{classes[ci].name}: fields end at 0x{cur:x} but the object claims 0x{end:x}", pos)
        return Obj(ci, fields), end

    root, end = read_obj(dend, 0)
    if end != n:
        raise FormatError("XFS", f"{n - end} bytes after the root object", end)
    if counter[0] != obj_count:
        raise FormatError("XFS", f"header says {obj_count} objects but the tree has {counter[0]}", 8)
    x = Xfs(minor, classes, root, version=version)
    x.extra["def_raw"] = data[base:dend]
    if version == VERSION_DDO:
        x.extra["reserved"] = reserved
    return x


def _nan_safe(data: bytes, at: int, st: struct.Struct) -> tuple:
    """Unpack float lanes, keeping NaN lanes as exact bit patterns."""
    lane = struct.Struct("<f") if st.format[-1] == "f" else struct.Struct("<d")
    ilane = struct.Struct("<I") if lane.size == 4 else struct.Struct("<Q")
    count = st.size // lane.size
    out = []
    for i in range(count):
        v = lane.unpack_from(data, at + i * lane.size)[0]
        out.append(F32Bits(ilane.unpack_from(data, at + i * lane.size)[0]) if v != v else v)
    return tuple(out)


# -- building ----------------------------------------------------------------

def build_definitions(classes: list[ClassDef], version: int = VERSION) -> bytes:
    """Definition block in the vanilla layout: offsets, classes, names in first-use order, 4-byte pad."""
    ncls = len(classes)
    rec = 12 if version == VERSION else 8
    body_size = 4 * ncls + sum(rec + 24 * len(c.props) for c in classes)
    strings: dict[tuple[str, str], int] = {}
    table = bytearray()
    for c in classes:
        for p in c.props:
            if (p.name, p.enc) not in strings:
                strings[(p.name, p.enc)] = body_size + len(table)
                table += p.name.encode(p.enc) + b"\0"
    out = bytearray()
    cursor = 4 * ncls
    offs = []
    for c in classes:
        offs.append(cursor)
        cursor += rec + 24 * len(c.props)
    out += struct.pack(f"<{ncls}I", *offs)
    for c in classes:
        if rec == 12:
            if c.engine_value is None:
                raise FormatError("XFS", f"class {c.name} has no engine value; a Dark Arisen (0x0109) file needs one")
            out += struct.pack("<III", c.type_id, c.engine_value, len(c.props))
        else:
            out += struct.pack("<II", c.type_id, len(c.props))
        for p in c.props:
            out += struct.pack("<IBBH", strings[(p.name, p.enc)], p.type, p.attr, p.size) + bytes(16)
    out += table
    out += bytes(-len(out) % 4)
    return bytes(out)


def _pack_float_lanes(st: struct.Struct, values: tuple) -> bytes:
    lane = "f" if st.format[-1] == "f" else "d"
    out = bytearray()
    for v in values:
        if isinstance(v, F32Bits):
            out += struct.pack("<I" if lane == "f" else "<Q", v.bits)
        else:
            out += struct.pack("<" + lane, v)
    return bytes(out)


def build(x: Xfs) -> bytes:
    defs = x.extra.get("def_raw")
    if defs is None or x.extra.get("classes_changed"):
        defs = build_definitions(x.classes, x.version)
    out = bytearray()
    counter = [0]

    def write_obj(o: Obj, depth: int) -> None:
        if depth > MAX_DEPTH:
            raise FormatError("XFS", f"objects nested deeper than {MAX_DEPTH}")
        if not 0 <= o.cls < len(x.classes):
            raise FormatError("XFS", f"object refers to undeclared class {o.cls}")
        cdef = x.classes[o.cls]
        if len(o.fields) != len(cdef.props):
            raise FormatError("XFS", f"{cdef.name}: {len(o.fields)} fields for {len(cdef.props)} properties")
        start = len(out)
        out.extend(_HHI.pack(o.cls << 1 | 1, counter[0] & 0xFFFF, 0))
        counter[0] += 1
        for prop, values in zip(cdef.props, o.fields):
            out.extend(_U32.pack(len(values)))
            t = prop.type
            st = TYPES[t][1]
            if st is not None:
                for v in values:
                    vals = v if isinstance(v, tuple) else (v,)
                    if t in FLOAT_TYPES:
                        out.extend(_pack_float_lanes(st, vals))
                    else:
                        out.extend(st.pack(*vals))
            elif t in STRING_TYPES:
                for v in values:
                    if b"\0" in v:
                        raise FormatError("XFS", f"{cdef.name}.{prop.name}: string contains a NUL byte")
                    out.extend(v + b"\0")
            elif t in OBJECT_TYPES:
                for v in values:
                    if v is None:
                        out.extend(NULL_REF)
                    else:
                        write_obj(v, depth + 1)
            else:
                for v in values:
                    if b"\0" in v.cls or b"\0" in v.path:
                        raise FormatError("XFS", f"{cdef.name}.{prop.name}: resource reference contains a NUL byte")
                    out.extend(b"\x02" + v.cls + b"\0" + v.path + b"\0")
        _U32.pack_into(out, start + 4, len(out) - start - 4)

    write_obj(x.root, 0)
    if counter[0] > 0xFFFF:
        raise FormatError("XFS", f"{counter[0]} objects; object numbers are 16-bit")
    if x.version == VERSION_DDO:
        head = HEADER_DDO.pack(MAGIC, VERSION_DDO, x.minor, counter[0], x.extra.get("reserved", 0),
                               len(x.classes), len(defs))
    elif x.version == VERSION:
        head = HEADER.pack(MAGIC, VERSION, x.minor, counter[0], len(x.classes), len(defs))
    else:
        raise FormatError("XFS", f"version 0x{x.version:04x} is not one Riftstone writes")
    return head + defs + bytes(out)


def canonical(x: Xfs) -> Xfs:
    """The same tree in the vanilla layout: classes in first-use order, unused ones dropped,
    definition block regenerated.  Every vanilla file already is canonical (build(canonical(x))
    reproduces it); YAML always round-trips to this form."""
    order: list[int] = []
    seen: set[int] = set()
    for o in walk(x.root):
        if o.cls not in seen:
            seen.add(o.cls)
            order.append(o.cls)
    remap = {old: new for new, old in enumerate(order)}

    def copy(o: Obj) -> Obj:
        return Obj(remap[o.cls], [[copy(v) if isinstance(v, Obj) else v for v in vals] for vals in o.fields])

    out = Xfs(x.minor, [x.classes[i] for i in order], copy(x.root), version=x.version)
    if "reserved" in x.extra:
        out.extra["reserved"] = x.extra["reserved"]
    return out


def is_canonical(x: Xfs) -> bool:
    raw = x.extra.get("def_raw")
    c = canonical(x)
    return c.classes == x.classes and (raw is None or build_definitions(x.classes, x.version) == raw)


def walk(o: Obj):
    """Every object in the tree, pre-order (the file's numbering order)."""
    stack = [o]
    while stack:
        cur = stack.pop()
        yield cur
        kids = [v for vals in cur.fields for v in vals if isinstance(v, Obj)]
        stack.extend(reversed(kids))
