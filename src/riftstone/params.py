"""XFS resources <-> YAML parameter files.

A parameter file is the whole XFS object tree as YAML.  Every object names its
class (``_class``); the game's schema -- the class layouts every vanilla XFS
resource declares, shipped in data/xfs_schema.json -- decides each value's
type, so a file only has to carry values.  Converting vanilla XFS -> YAML ->
XFS reproduces the original bytes (tools/check_corpus.py proves it for every
distinct vanilla XFS resource).

    riftstone: xfs/1
    resource: param\\pl\\stamina\\PlStaminaMax.stm
    version: 1
    root:
      _class: rPlStamina
      mQuality: 2
      mArray:
        - _class: 'rPlStamina::cStaminaInfo'
          mStamina: 500.0
          ...

Properties repeated inside one class are written name, name#2, name#3.

Text values are shown as text in the file's encoding (xfs.text_encoding: UTF-8 for Dark Arisen,
Shift-JIS/cp932 for Online, whose YAML says ``xfs: 0x000f``) and written back in it; bytes that are
not valid text in it show as \\udcXX escapes and come back unchanged.
"""
from __future__ import annotations

import difflib
import json
import math
import re
import struct
from functools import lru_cache
from importlib import resources as _res

from . import typemap, xfs, yamlish
from .errors import ParamError
from .yamlish import Map, Scalar, Seq

TAG = "xfs/1"
_F32 = struct.Struct("<f")
F32_MAX = 3.4028234663852886e38
_INT_RANGES = {
    0x04: (0, 0xFF), 0x05: (0, 0xFFFF), 0x06: (0, 0xFFFFFFFF), 0x07: (0, 0xFFFFFFFFFFFFFFFF),
    0x08: (-0x80, 0x7F), 0x09: (-0x8000, 0x7FFF), 0x0A: (-0x80000000, 0x7FFFFFFF),
    0x0B: (-0x8000000000000000, 0x7FFFFFFFFFFFFFFF),
}
_VECTOR_TYPES = {t for t, (n, st) in xfs.TYPES.items() if st is not None and len(st.format) > 2}


# -- schema -------------------------------------------------------------------

@lru_cache(maxsize=1)
def schema_db() -> dict[str, xfs.ClassDef]:
    raw = json.loads(_res.files("riftstone").joinpath("data/xfs_schema.json").read_text(encoding="utf-8"))
    return {name: _classdef(name, c["engine_value"], c["props"]) for name, c in raw["classes"].items()}


def _class_id(name: str) -> int:
    """Class hash for a name: '0x1234abcd', a known engine name, or the JAMCRC of a new one."""
    if name.lower().startswith("0x"):
        v = int(name, 16)
        if not 0 <= v <= 0xFFFFFFFF:
            raise ValueError(f"class id {name} is not 32-bit")
        return v
    known = xfs.CLASS_IDS.get(name)
    if known is not None:
        return known
    if not name or not name.isascii() or not name.isprintable():
        raise ValueError(f"class name {name!r}: engine class names are printable ASCII")
    return typemap.jamcrc(name)


def _classdef(name: str, engine_value: int | None, props: list) -> xfs.ClassDef:
    out = []
    for p in props:
        pname, tname, attr, size = p[:4]
        enc = p[4] if len(p) > 4 else "utf-8"
        out.append(xfs.Prop(pname, xfs.TYPE_CODES[tname], attr, size, enc))
    return xfs.ClassDef(_class_id(name), engine_value, tuple(out))


def prop_keys(cdef: xfs.ClassDef) -> list[str]:
    """YAML keys for a class's properties: repeats become name#2, name#3 ..."""
    counts: dict[str, int] = {}
    names = {p.name for p in cdef.props}
    keys = []
    for p in cdef.props:
        k = counts.get(p.name, 0) + 1
        counts[p.name] = k
        key = p.name if k == 1 else f"{p.name}#{k}"
        while k > 1 and key in names:
            k += 1
            key = f"{p.name}#{k}"
        keys.append(key)
    return keys


# -- numbers ------------------------------------------------------------------

def fmt_f32(v) -> str:
    if isinstance(v, xfs.F32Bits):
        return f"nan:0x{v.bits:08x}"
    if v != v:
        return ".nan"
    if math.isinf(v):
        return ".inf" if v > 0 else "-.inf"
    # Fewest significant digits that still round-trip through float32, then
    # Python's own float repr of that value: 500.0 rather than 5e+02.
    target = _F32.pack(v)
    for p in range(1, 10):
        s = "%.*g" % (p, v)
        try:
            if _F32.pack(to_f32(float(s))) == target:
                return _floaty(repr(float(s)))
        except (OverflowError, ValueError):
            continue
    return _floaty(repr(v))     # the exact value; always round-trips


F32_ROUND_LIMIT = 3.4028235677973366e38   # values below this round to FLT_MAX, not infinity


def to_f32(v: float) -> float:
    """v as the float32 the game will store: rounds like IEEE, refuses what would overflow."""
    if math.isfinite(v) and abs(v) > F32_MAX:
        if abs(v) < F32_ROUND_LIMIT:
            return math.copysign(F32_MAX, v)
        raise ValueError("too large for a 32-bit float")
    if v != v:
        return v
    return _F32.unpack(_F32.pack(v))[0]


def f32_bits_text(bits: int) -> str:
    """YAML for a float32 field from its stored bits.  A NaN keeps its exact bits (nan:0x...):
    going through a Python float can change a NaN's payload."""
    bits &= 0xFFFFFFFF
    v = _F32.unpack(struct.pack("<I", bits))[0]
    return f"nan:0x{bits:08x}" if v != v else fmt_f32(v)


def f32_bits(text: str) -> int:
    """The bits to store for a float32 written in YAML (the inverse of f32_bits_text).  ValueError if
    it is not a number or does not fit."""
    t = text.strip().lower()
    if t.startswith("nan:0x"):
        bits = int(t[6:], 16)          # int() also takes a sign: "nan:0x-5" must not get through
        if not 0 <= bits <= 0xFFFFFFFF or (bits & 0x7F800000) != 0x7F800000 or not bits & 0x007FFFFF:
            raise ValueError("nan:0x... must hold the bits of a 32-bit NaN")
        return bits
    specials = {".nan": math.nan, "nan": math.nan, ".inf": math.inf, "+.inf": math.inf, "inf": math.inf,
                "-.inf": -math.inf, "-inf": -math.inf}
    v = specials[t] if t in specials else float(t)
    return struct.unpack("<I", _F32.pack(to_f32(v)))[0]


def fmt_f64(v) -> str:
    if isinstance(v, xfs.F32Bits):
        return f"nan:0x{v.bits:016x}"
    if v != v:
        return ".nan"
    if math.isinf(v):
        return ".inf" if v > 0 else "-.inf"
    return _floaty(repr(v))


def _floaty(s: str) -> str:
    if "e" in s:
        mant, exp = s.split("e")
        if "." not in mant:
            mant += ".0"
        return f"{mant}e{int(exp)}"
    if "." not in s:
        s += ".0"
    return s


def _parse_int(sc: Scalar, lo: int, hi: int, what: str, err) -> int:
    t = sc.text.strip().replace("_", "")
    try:
        neg = t.startswith("-")
        body = t[1:] if t[:1] in "+-" else t
        v = int(body, 16) if body.lower().startswith("0x") else int(body, 10)
        v = -v if neg else v
    except ValueError:
        raise err(f"{what} must be a whole number, not {sc.text!r}", sc) from None
    if not lo <= v <= hi:
        raise err(f"{what} must be between {lo} and {hi}; {v} is out of range", sc)
    return v


def _parse_float(sc: Scalar, single: bool, what: str, err):
    t = sc.text.strip()
    low = t.lower()
    if low.startswith("nan:0x"):
        # An exact NaN bit pattern, as fmt_f32/fmt_f64 write it.
        try:
            bits = int(low[6:], 16)
        except ValueError:
            raise err(f"{what}: {sc.text!r} is not a NaN bit pattern (nan:0x7fc00000)", sc) from None
        width, exp_mask, mant_mask = (32, 0x7F800000, 0x007FFFFF) if single else \
            (64, 0x7FF0000000000000, 0x000FFFFFFFFFFFFF)
        if not 0 <= bits < (1 << width) or (bits & exp_mask) != exp_mask or not bits & mant_mask:
            raise err(f"{what}: {sc.text!r} is not a {width}-bit NaN pattern", sc)
        return xfs.F32Bits(bits)
    if low in (".nan", "nan"):
        return float("nan")
    if low in (".inf", "+.inf", "inf", "+inf"):
        return float("inf")
    if low in ("-.inf", "-inf"):
        return float("-inf")
    try:
        v = float(t.replace("_", ""))
    except ValueError:
        raise err(f"{what} must be a number, not {sc.text!r}", sc) from None
    if math.isinf(v) and not low.endswith("inf"):
        raise err(f"{what}: {sc.text} is too large to store", sc)
    if single:
        try:
            v = to_f32(v)
        except ValueError:
            raise err(f"{what}: {sc.text} is too large for a 32-bit float (max about 3.4e38)", sc) from None
    return v


# -- XFS -> YAML ----------------------------------------------------------------

def _str_scalar(raw: bytes, flow: bool = False, version: int = xfs.VERSION) -> Scalar:
    """Stored text as a YAML scalar, read in the file's encoding (UTF-8, or Shift-JIS for Online)."""
    text = xfs.decode_text(raw, version)
    q = yamlish.quote(text, flow)
    return Scalar(text, "plain" if q == text else "double")


def _elem_node(prop: xfs.Prop, v, x: xfs.Xfs, flow: bool = False, gloss: dict | None = None):
    t = prop.type
    if t == 0x03:
        return Scalar("true" if v == 1 else "false" if v == 0 else str(v))
    if t in _INT_RANGES:
        return Scalar(str(v))
    if t == 0x0C:
        return Scalar(fmt_f32(v))
    if t == 0x0D:
        return Scalar(fmt_f64(v))
    if t in _VECTOR_TYPES:
        f = fmt_f32 if xfs.TYPES[t][1].format[-1] == "f" else str
        return Seq([Scalar(f(e)) for e in v], flow=True)
    if t in xfs.STRING_TYPES:
        return _str_scalar(v, flow, x.version)
    if t in xfs.OBJECT_TYPES:
        return Scalar("null") if v is None else _obj_node(v, x, gloss)
    return Map([(Scalar("type"), _str_scalar(v.cls, version=x.version)),
                (Scalar("path"), _str_scalar(v.path, version=x.version))])


def _values_node(prop: xfs.Prop, vals: list, x: xfs.Xfs, gloss: dict | None = None):
    t = prop.type
    if not prop.is_array and len(vals) == 1:
        return _elem_node(prop, vals[0], x, gloss=gloss)
    simple = t == 0x03 or t in _INT_RANGES or t in (0x0C, 0x0D)
    if simple:
        return Seq([_elem_node(prop, v, x) for v in vals], flow=True)
    return Seq([_elem_node(prop, v, x, gloss=gloss) for v in vals], flow=not vals)


def _obj_node(o: xfs.Obj, x: xfs.Xfs, gloss: dict | None = None) -> Map:
    cdef = x.classes[o.cls]
    items = [(Scalar("_class"), _str_scalar(cdef.name.encode()))]
    for key, prop, vals in zip(prop_keys(cdef), cdef.props, o.fields):
        k = yamlish.quote(key)
        node = _values_node(prop, vals, x, gloss)
        if gloss and gloss.get(prop.name) and (isinstance(node, Scalar) or isinstance(node, Seq) and node.flow):
            node.comment = gloss[prop.name]     # the field's English name beside it; the key stays the engine's
        items.append((Scalar(key, "plain" if k == key else "double"), node))
    return Map(items)


def to_yaml(x: xfs.Xfs, name: str | None = None, type_id: int | None = None, tag: str = TAG,
            gloss: dict | None = None) -> str:
    """XFS -> parameter YAML.  ``gloss`` (field name -> English) writes a known field's English name as a
    comment after its value (``攻撃力: 250.0  # Attack``); comments are ignored when the YAML is read back."""
    db = schema_db()
    root_cls = x.root_class.name
    label = name if name is not None else root_cls
    ext = typemap.extension(type_id) if type_id is not None else None
    header = [f"Riftstone parameter file -- {label}" + (f".{ext}" if ext else ""),
              f"Class {root_cls}. Edit values and save; keep the _class lines and the structure.",
              "Numbers: whole numbers for integer fields, decimals for float fields. Lists: '- item' or [a, b]."]
    if xfs.text_encoding(x.version) == "cp932":
        header.append("Text is stored in Shift-JIS (cp932), as the game reads it: characters it lacks are refused.")
    items = [(Scalar("riftstone"), Scalar(tag))]
    if name is not None:
        resource = name + (f".{ext}" if ext else "")
        items.append((Scalar("resource"), _str_scalar(resource.encode("latin-1"))))
    if x.version != xfs.VERSION:   # Dragon's Dogma Online's layout (0x000f)
        items.append((Scalar("xfs"), Scalar(f"0x{x.version:04x}")))
        if x.extra.get("reserved"):  # the header's spare u32: zero in every vanilla file, kept when it is not
            items.append((Scalar("reserved"), Scalar(f"0x{x.extra['reserved']:08x}")))
    items.append((Scalar("version"), Scalar(str(x.minor))))
    items.append((Scalar("root"), _obj_node(x.root, x, gloss)))
    custom = [c for c in x.classes if db.get(c.name) != c]
    if custom:
        sch = []
        for c in custom:
            props = Seq([Seq([_str_scalar(p.name.encode(), True), Scalar(p.type_name), Scalar(f"0x{p.attr:02x}"),
                              Scalar(str(p.size))] + ([Scalar(p.enc)] if p.enc != "utf-8" else []), flow=True)
                         for p in c.props])
            fields = [(Scalar("props"), props)]
            if c.engine_value is not None:
                fields.insert(0, (Scalar("engine_value"), Scalar(str(c.engine_value))))
            sch.append((_str_scalar(c.name.encode()), Map(fields)))
        items.append((Scalar("schema"), Map(sch)))
    return yamlish.emit(Map(items), header)


# -- YAML -> XFS ----------------------------------------------------------------

class _Builder:
    def __init__(self, source: str | None, local: dict[str, xfs.ClassDef]):
        self.source = source
        self.local = local
        self.db = schema_db()
        self.classes: list[xfs.ClassDef] = []
        self.index: dict[int, int] = {}
        self.count = 0
        self.version = xfs.VERSION       # the file layout; decides how text is stored (xfs.text_encoding)

    def err(self, msg: str, node=None) -> ParamError:
        line = getattr(node, "line", None) or None
        col = getattr(node, "col", None) or None
        return ParamError(msg, line, col, self.source)

    def lookup(self, name: str, node) -> xfs.ClassDef:
        c = self.local.get(name) or self.db.get(name)
        if c is None:
            near = difflib.get_close_matches(name, list(self.db), n=3)
            hint = f" Did you mean {', '.join(near)}?" if near else ""
            raise self.err(f"unknown class {name!r}.{hint}", node)
        return c

    def obj(self, node, depth: int) -> xfs.Obj:
        if not isinstance(node, Map) or node.flow and not node.items:
            raise self.err("expected an object (a block of 'key: value' lines with a _class line)", node)
        if depth > xfs.MAX_DEPTH:
            raise self.err(f"objects nested deeper than {xfs.MAX_DEPTH}", node)
        cls_node = node.get("_class")
        if not isinstance(cls_node, Scalar):
            raise self.err("object has no '_class: ...' line", node)
        cdef = self.lookup(cls_node.text, cls_node)
        ci = self.index.get(cdef.type_id)
        if ci is None:
            ci = self.index[cdef.type_id] = len(self.classes)
            self.classes.append(cdef)
        elif self.classes[ci] != cdef:
            raise self.err(f"class {cdef.name} is declared twice with different layouts", cls_node)
        self.count += 1
        if self.count > 0xFFFF:
            raise self.err("more than 65535 objects; XFS object numbers are 16-bit", node)
        keys = prop_keys(cdef)
        known = set(keys)
        given = {}
        for k, v in node.items:
            if k.text == "_class":
                continue
            if k.text not in known:
                near = difflib.get_close_matches(k.text, keys, n=2)
                hint = f" Did you mean {', '.join(near)}?" if near else f" {cdef.name} has: {', '.join(keys)}"
                raise self.err(f"{cdef.name} has no property {k.text!r}.{hint}", k)
            given[k.text] = v
        fields = []
        for key, prop in zip(keys, cdef.props):
            if key not in given:
                raise self.err(f"{cdef.name} is missing '{key}' ({prop.type_name})", node)
            fields.append(self.values(prop, key, given[key], depth))
        return xfs.Obj(ci, fields)

    def values(self, prop: xfs.Prop, key: str, node, depth: int) -> list:
        t = prop.type
        if prop.is_array:
            if not isinstance(node, Seq):
                raise self.err(f"'{key}' is a list: write '- item' lines or [a, b] (use [] for none)", node)
            return [self.elem(prop, key, it, depth) for it in node.items]
        multi = isinstance(node, Seq) and (t not in _VECTOR_TYPES or all(isinstance(i, Seq) for i in node.items))
        if multi:
            return [self.elem(prop, key, it, depth) for it in node.items]
        return [self.elem(prop, key, node, depth)]

    def elem(self, prop: xfs.Prop, key: str, node, depth: int):
        t = prop.type
        what = f"'{key}' ({prop.type_name})"
        if t in xfs.OBJECT_TYPES:
            if isinstance(node, Scalar) and node.style == "plain" and node.text in ("null", "~", "Null", "NULL"):
                return None
            return self.obj(node, depth + 1)
        if t == 0x80:
            if not isinstance(node, Map):
                raise self.err(f"{what} is a resource reference: give 'type:' and 'path:' lines", node)
            ty, pa = node.get("type"), node.get("path")
            extra = [k.text for k, _ in node.items if k.text not in ("type", "path")]
            if not isinstance(ty, Scalar) or not isinstance(pa, Scalar) or extra:
                raise self.err(f"{what} needs exactly 'type:' and 'path:'", node)
            return xfs.ResourceRef(self.text_bytes(ty, what), self.text_bytes(pa, what))
        if not isinstance(node, (Scalar, Seq)):
            raise self.err(f"{what} takes a value, not a block", node)
        if t in xfs.STRING_TYPES:
            if not isinstance(node, Scalar):
                raise self.err(f"{what} takes text", node)
            return self.text_bytes(node, what)
        if t in _VECTOR_TYPES:
            st = xfs.TYPES[t][1]
            lanes = len(st.unpack(bytes(st.size)))
            if not isinstance(node, Seq) or len(node.items) != lanes or not all(isinstance(i, Scalar) for i in node.items):
                raise self.err(f"{what} takes {lanes} numbers in brackets, like [{', '.join(['0.0'] * lanes)}]", node)
            if st.format[-1] == "f":
                return tuple(_parse_float(i, True, what, self.err) for i in node.items)
            return tuple(_parse_int(i, 0, 0xFFFF, what, self.err) for i in node.items)
        if not isinstance(node, Scalar):
            raise self.err(f"{what} takes a single value; this property is not a list", node)
        if t == 0x03:
            low = node.text.strip().lower()
            if low == "true":
                return 1
            if low == "false":
                return 0
            return _parse_int(node, 0, 255, what + " (true/false)", self.err)
        if t in _INT_RANGES:
            lo, hi = _INT_RANGES[t]
            return _parse_int(node, lo, hi, what, self.err)
        if t == 0x0C:
            return _parse_float(node, True, what, self.err)
        if t == 0x0D:
            return _parse_float(node, False, what, self.err)
        raise self.err(f"{what}: unsupported type", node)

    def text_bytes(self, sc: Scalar, what: str) -> bytes:
        try:
            raw = xfs.encode_text(sc.text, self.version)
        except UnicodeEncodeError:
            bad = xfs.unencodable(sc.text, self.version) or "?"
            if xfs.text_encoding(self.version) == "cp932":
                raise self.err(f"{what}: {bad!r} (U+{ord(bad):04X}) is not in Shift-JIS (cp932), the encoding "
                               "Dragon's Dogma Online's files use; write the text without it", sc) from None
            raise self.err(f"{what}: {bad!r} (U+{ord(bad):04X}) cannot be stored as UTF-8", sc) from None
        if b"\0" in raw:
            raise self.err(f"{what}: text cannot contain a NUL character", sc)
        return raw


def _schema_int(sc: Scalar, lo: int, hi: int, what: str, source) -> int:
    try:
        v = int(sc.text.strip(), 0)
    except ValueError:
        raise ParamError(f"schema {what} must be a number", sc.line, sc.col, source) from None
    if not lo <= v <= hi:
        raise ParamError(f"schema {what} must be between {lo} and {hi}", sc.line, sc.col, source)
    return v


def _local_schema(node, source) -> dict[str, xfs.ClassDef]:
    if node is None:
        return {}
    if not isinstance(node, Map):
        raise ParamError("'schema' must be a block of class entries", node.line, node.col, source)
    out = {}
    ids: dict[int, str] = {}
    for k, v in node.items:
        ev = v.get("engine_value") if isinstance(v, Map) else None
        props = v.get("props") if isinstance(v, Map) else None
        if (ev is not None and not isinstance(ev, Scalar)) or not isinstance(props, Seq):
            raise ParamError(f"schema entry {k.text!r} needs props (and engine_value for Dark Arisen files)",
                             k.line, k.col, source)
        if not k.text or "\0" in k.text:
            raise ParamError("schema class names cannot be empty or contain NUL", k.line, k.col, source)
        plist = []
        for p in props.items:
            if not isinstance(p, Seq) or len(p.items) not in (4, 5) or not all(isinstance(i, Scalar) for i in p.items):
                raise ParamError("schema props are [name, type, attr, size] (plus cp932 for a Shift-JIS name)",
                                 getattr(p, "line", None), None, source)
            enc = p.items[4].text if len(p.items) == 5 else "utf-8"
            if enc not in ("utf-8", "cp932"):
                raise ParamError(f"a property name encoding is utf-8 or cp932, not {enc!r}", p.line, p.col, source)
            pname, tname = p.items[0].text, p.items[1].text
            if "\0" in pname:
                raise ParamError("schema property names cannot contain NUL", p.line, p.col, source)
            if tname not in xfs.TYPE_CODES:
                raise ParamError(f"unknown property type {tname!r}", p.line, p.col, source)
            attr = _schema_int(p.items[2], 0, 0xFF, "attr", source)
            size = _schema_int(p.items[3], 0, 0xFFFF, "size", source)
            st = xfs.TYPES[xfs.TYPE_CODES[tname]][1]
            if st is not None and st.size != size:
                raise ParamError(f"schema: a {tname} property is {st.size} bytes, not {size}", p.line, p.col, source)
            try:
                pname.encode(enc)
            except UnicodeEncodeError:
                raise ParamError(f"property name {pname!r} cannot be written as {enc}", p.line, p.col, source) from None
            plist.append([pname, tname, attr, size, enc])
        if k.text.lower().startswith("0x"):
            _schema_int(k, 0, 0xFFFFFFFF, "class id", source)
        try:
            cdef = _classdef(k.text, None if ev is None else _schema_int(ev, 0, 0xFFFFFFFF, "engine_value", source),
                             plist)
        except ValueError as e:
            raise ParamError(f"schema: {e}", k.line, k.col, source) from None
        if cdef.type_id in ids:
            raise ParamError(f"schema classes {ids[cdef.type_id]!r} and {k.text!r} have the same id", k.line, k.col, source)
        ids[cdef.type_id] = k.text
        out[k.text] = cdef
    return out


def from_yaml(text: str, source: str | None = None, tag: str = TAG) -> xfs.Xfs:
    doc = yamlish.parse(text, source)
    if not isinstance(doc, Map):
        raise ParamError(f"not a Riftstone parameter file (expected 'riftstone: {tag}' at the top)", 1, 1, source)
    allowed = {"riftstone", "resource", "xfs", "reserved", "version", "root", "schema"}
    for k, _ in doc.items:
        if k.text not in allowed:
            raise ParamError(f"unknown top-level key {k.text!r}", k.line, k.col, source)
    tagv = doc.get("riftstone")
    if not isinstance(tagv, Scalar) or tagv.text != tag:
        raise ParamError(f"not a Riftstone parameter file (expected 'riftstone: {tag}')", 1, 1, source)
    b = _Builder(source, _local_schema(doc.get("schema"), source))
    ver = doc.get("version")
    if not isinstance(ver, Scalar):
        raise ParamError("missing 'version:' (copy it from the original file)", 1, 1, source)
    minor = _parse_int(ver, 0, 0xFFFF, "version", b.err)
    xv = doc.get("xfs")
    layout = xfs.VERSION
    if xv is not None:
        if not isinstance(xv, Scalar):
            raise ParamError("'xfs:' is the file layout, 0x0109 (Dark Arisen) or 0x000f (Online)", 1, 1, source)
        layout = _parse_int(xv, 0, 0xFFFF, "xfs", b.err)
        if layout not in (xfs.VERSION, xfs.VERSION_DDO):
            raise ParamError("'xfs:' is 0x0109 (Dark Arisen) or 0x000f (Online)", xv.line, xv.col, source)
    rv = doc.get("reserved")
    reserved = 0
    if rv is not None:
        if layout != xfs.VERSION_DDO or not isinstance(rv, Scalar):
            raise ParamError("'reserved:' is the Online header's spare u32 (with 'xfs: 0x000f')",
                             rv.line, rv.col, source)
        reserved = _parse_int(rv, 0, 0xFFFFFFFF, "reserved", b.err)
    root = doc.get("root")
    if root is None:
        raise ParamError("missing 'root:'", 1, 1, source)
    b.version = layout
    obj = b.obj(root, 0)
    if layout == xfs.VERSION:
        missing = [c.name for c in b.classes if c.engine_value is None]
        if missing:
            raise ParamError(f"class {missing[0]} has no engine_value; a Dark Arisen file needs one", 1, 1, source)
    out = xfs.Xfs(minor, b.classes, obj, version=layout)
    if layout == xfs.VERSION_DDO:
        out.extra["reserved"] = reserved
    return out


def decode_text(data: bytes, source: str | None = None) -> str:
    """YAML file bytes -> text; a file that is not UTF-8 gets a clear error, not a crash."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        line = data[:e.start].count(b"\n") + 1
        raise ParamError("the file is not UTF-8 text (save it as UTF-8 in your editor)", line, None, source) from None


_TAG_LINE = re.compile(r"^riftstone:[ \t]*['\"]?([\w./-]+)", re.M)

# Binary formats with a YAML form besides XFS: magic -> (module, extension).  A module has
# parse(bytes), to_yaml(obj, name) and yaml_to_bytes(text, source); its YAML is tagged
# "<extension>/<version>".  Adding a decoded format is one line here.
FORMATS = {
    b"\x25\x12\x12\x20": ("ocl", "ocl"),     # collision, magic 0x20121225
    b"GMD\0": ("gmd", "gmd"),                # text
    b"ITL2": ("itl", "itl"),                 # the item list
    b"lot\0": ("lot", "lot"),                # spawn and object layouts
    b"ist\0": ("tables", "ist"),             # item sets and drop tables
    b"imx\0": ("tables", "imx"),             # crafting recipes
    b"gpl\0": ("gpl", "gpl"),                # enemy group placement
    b"PRPZ": ("prp", "prp"),                 # rPropParam: enemy/character parameters (XFS body)
    b"COL\0": ("ocl_ddo", "ocl-ddo"),        # Dragon's Dogma Online's collision (hit shapes, attack params)
    b"wep\0": ("weather", "wep"),            # weather effect colours by time of day (DDDA v1, DDO v3)
    b"wfp\0": ("weather", "wfp"),            # fog by time of day (DDDA)
    b"SKY ": ("weather", "sky"),             # the physical sky (both games)
    b"WSI_": ("weather", "wsi"),             # DDO's per-stage sky and star settings
    b"LCM\0": ("camera", "lcm"),             # camera lists (DDDA v3 per-frame, DDO v5 compressed tracks)
    b"SREQ": ("sound", "srq"),               # sound-effect cues (DDDA)
    b"SRQR": ("sound", "srq-ddo"),           # sound-effect cues (DDO)
    b"STRQ": ("sound", "stq"),               # streamed cues: music, voice (DDDA)
    b"STQR": ("sound", "stq-ddo"),           # streamed cues (DDO)
    b"DNRS": ("sound", "srd"),               # weighted random picks between cues (DDDA; magic 'SRND' as a u32)
    b"SMX\0": ("sound", "smx"),              # sub-mixer faders (DDDA)
    b"SMXR": ("sound", "smx-ddo"),           # sub-mixer faders (DDO)
    b"SPL\0": ("sound", "spl"),              # physics-sound lists (DDDA)
    b"SBKR": ("sound", "sbkr"),              # sound banks: programs -> waves (DDO)
    b"sar\0": ("sound", "sar"),              # per-area music, zones and surface sounds (DDO)
    b"epv\0": ("effect", "epv"),             # effect providers: which effects a model/skill spawns, and where
    b"EFL\0": ("effect_efl", "efl"),         # effect lists: emitters, particles, textures
    b"E2D\0": ("effect_e2d", "e2d"),         # 2D (screen) effects
    b"FCA\0": ("facial", "fca"),             # lip-sync / facial animation curves (DDDA)
    b"mss\0": ("msgset", "mss"),             # NPC conversations (DDDA)
    b"mgst": ("msgset", "mss-ddo"),          # NPC conversation groups (DDO, same type as DDDA's .mss)
    b"msl\0": ("msgset", "msl"),             # message serial lists (DDDA)
    b"SDL\0": ("schedule", "sdl"),           # schedulers: timelines of tracks and keys (both games)
    b"zon\0": ("schedule", "zon"),           # stage zones: layouts, groups, grids (both games)
}

# Formats without a magic, known by their resource type: extension -> module (DDO's weather tables).
TYPED = {"wtf": "weather", "wte": "weather", "wtl": "weather", "wta": "weather"}


def _ddo_param_kind(data: bytes, type_id: int | None) -> str | None:
    """DDO's enemy and stage parameters (ddo_params: cpe pep prs osp sti sal evtr ndp) by type id; by magic
    only when the type is not known (four of the seven have none)."""
    from . import ddo_params
    if type_id:
        return ddo_params.kind_for_type(type_id)
    return ddo_params.kind_of(data)


def _format(module: str):
    import importlib
    return importlib.import_module(f".{module}", __package__)


def _flat_ext(data: bytes, type_id: int | None) -> str | None:
    """The flat-format extension for this resource: by magic, or by type for the magic-less ones."""
    from . import flat
    e = flat.magic_ext(data)
    if e:
        return e
    if type_id is not None and typemap.extension(type_id) in flat.SCHEMAS:
        return typemap.extension(type_id)
    return None


def _format_of(data: bytes) -> tuple[str, str] | None:
    """(module, tag) for a FORMATS resource; a layout picks its module by version (16 DDDA, 138 DDO)."""
    if bytes(data[:4]) == b"lot\0" and len(data) >= 8 and int.from_bytes(data[4:8], "little") == 138:
        return ("lot_ddo", "lot-ddo")
    if bytes(data[:4]) == b"gpl\0" and len(data) >= 8 and int.from_bytes(data[4:8], "little") == 70:
        return ("gpl_ddo", "gpl-ddo")          # DDO's group lists (DDDA's are version 158)
    return FORMATS.get(bytes(data[:4]))


def resource_to_yaml(data: bytes, name: str | None = None, type_id: int | None = None) -> str | None:
    """YAML for any resource Riftstone can edit (XFS, the FORMATS modules, and the flat formats), or None."""
    if data[:4] == b"XFS\0":
        return xfs_to_yaml_bytes(data, name, type_id)
    fmt = _format_of(data)
    if fmt is not None:
        m = _format(fmt[0])
        return m.to_yaml(m.parse(data), name)
    typed = typemap.extension(type_id) if type_id is not None else None
    if typed in TYPED:
        m = _format(TYPED[typed])
        return m.to_yaml(m.parse(data, typed), name)
    kind = _ddo_param_kind(data, type_id)
    if kind is not None:
        m = _format("ddo_params")
        return m.to_yaml(m.parse(data, kind), name)
    ext = _flat_ext(data, type_id)
    if ext is not None:
        from . import flat
        return flat.to_yaml(flat.parse(data, ext), name)
    return None


def yaml_tag(text: str) -> str:
    """The format a Riftstone YAML file declares on its 'riftstone:' line (xfs/1 when there is none,
    so XFS files keep their existing error messages).  Comments cannot fake it: the line must start
    with the key."""
    m = _TAG_LINE.search(text[:4096])
    return m.group(1) if m else TAG


def yaml_to_resource(text: str, source: str | None = None) -> bytes:
    """Compile an editable YAML file back to resource bytes, by its tag."""
    tag = yaml_tag(text)
    from . import flat
    if tag.split("/")[0] in flat.SCHEMAS:
        return flat.yaml_to_bytes(text, source)
    if tag.startswith("lot-ddo/"):
        return _format("lot_ddo").yaml_to_bytes(text, source)
    if tag.startswith("gpl-ddo/"):
        return _format("gpl_ddo").yaml_to_bytes(text, source)
    if tag.split("/")[0] in TYPED:
        return _format(TYPED[tag.split("/")[0]]).yaml_to_bytes(text, source)
    head = tag.split("/")[0]
    if head.endswith("-ddo") and head[:-4] in _format("ddo_params").KINDS:     # cpe-ddo/1 ... evtr-ddo/1
        return _format("ddo_params").yaml_to_bytes(text, source)
    for module, ext in FORMATS.values():
        if tag.startswith(ext + "/"):
            return _format(module).yaml_to_bytes(text, source)
    return yaml_to_xfs_bytes(text, source)


def is_editable_resource(data: bytes, type_id: int | None = None) -> bool:
    if data[:4] == b"XFS\0" or bytes(data[:4]) in FORMATS:
        return True
    if type_id is not None and typemap.extension(type_id) in TYPED:
        return True
    if _ddo_param_kind(data, type_id) is not None:
        return True
    return _flat_ext(data, type_id) is not None


def has_yaml_form(type_id: int) -> bool:
    """Whether resources of this type are edited as YAML (a mod may hold them as <name>.<ext>.yaml)."""
    from . import flat
    ext = typemap.extension(type_id)
    return (typemap.is_xfs(type_id) or ext in {e for _, e in FORMATS.values()} or ext in TYPED
            or ext in flat.SCHEMAS or _format("ddo_params").kind_for_type(type_id) is not None)


def xfs_to_yaml_bytes(data: bytes, name: str | None = None, type_id: int | None = None) -> str:
    return to_yaml(xfs.parse(data), name, type_id)


def yaml_to_xfs_bytes(text: str, source: str | None = None) -> bytes:
    return xfs.build(from_yaml(text, source))
