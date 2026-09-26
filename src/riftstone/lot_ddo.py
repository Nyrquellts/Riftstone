"""Dragon's Dogma Online layouts (rLayout ``.lot`` version 138): every record, every field, byte-exact.

The grammar is DDO.exe's own, read from its loaders by ``tools/gen_lot_ddo.py`` into
``data/lot_ddo.json`` (53 kinds; each class's fields in file order with the engine's member names):

  "lot\\0"  u32 version (138)  88-byte block (kept as it is)  u32 record count
  record:  s32 id  u32 kind  then the fields of the class the kind names -- the most derived class's
           first, ... cSetInfoCoord's last (mName, unit id, position, angle, scale, area hit)

Types: u8 u16 s16 u32 s32 u64 f32 (kept as its exact 32-bit pattern), v3/v4 (3/4 x f32), str
(NUL-terminated), list<n> (u32 count, then count x n bytes).  Proved on all 22,983 distinct layouts in
the client (``check_corpus --game ddo --only lot``).  Enemies in DDO spawn from server data (EnemySpawn
.json); these layouts place the points they spawn at, objects, NPCs and gathering spots.
"""
from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass, field
from functools import lru_cache

from .errors import FormatError, ParamError

MAGIC = b"lot\0"
VERSION = 138
BLOCK = 0x58
TAG = "lot-ddo/1"
_FMT = {"u8": "<B", "u16": "<H", "s16": "<h", "u32": "<I", "s32": "<i", "u64": "<Q", "f32": "<I"}


@lru_cache(maxsize=1)
def _file_grammar() -> dict[int, dict]:
    from importlib import resources as _res

    raw = json.loads(_res.files("riftstone").joinpath("data/lot_ddo.json").read_text(encoding="utf-8"))
    return {int(k): v for k, v in raw["kinds"].items()}


_override: dict | None = None


def grammar() -> dict[int, dict]:
    return _override if _override is not None else _file_grammar()


def use_grammar(kinds: dict | None) -> None:
    """Parse with another kind table (tools/gen_lot_ddo.py checks the one it generates); None: the shipped one."""
    global _override
    _override = {int(k): v for k, v in kinds.items()} if kinds is not None else None
    _layout.cache_clear()


def _flat(fields: list) -> list[tuple[str, str]]:
    out = []
    for f in fields:
        if f[0] == "repeat":
            body = _flat(f[2])
            for i in range(f[1]):
                out.extend((f"{name}[{i}]", t) for name, t in body)
        else:
            out.append((f[0], f[1]))
    return out


@lru_cache(maxsize=None)
def _layout(kind: int) -> tuple[str, tuple[tuple[str, str], ...]]:
    g = grammar().get(kind)
    if g is None:
        raise FormatError("lot", f"record kind {kind} is not one DDO.exe loads")
    return g["class"], tuple(_flat(g["fields"]))


@dataclass
class Record:
    id: int
    kind: int
    values: list            # in field order: int, f32 bits (int), (bits, bits, bits[, bits]), str, list

    @property
    def cls(self) -> str:
        return _layout(self.kind)[0]

    @property
    def fields(self) -> tuple[tuple[str, str], ...]:
        return _layout(self.kind)[1]

    def get(self, name: str):
        for (n, _), v in zip(self.fields, self.values):
            if n == name:
                return v
        return None


@dataclass
class LotDdo:
    block: bytes = bytes(BLOCK)
    records: list[Record] = field(default_factory=list)


def _text(b: bytes):
    """A string field: text when cp932 decodes and re-encodes it exactly, else the raw bytes."""
    try:
        t = b.decode("cp932")
        if t.encode("cp932") == b:
            return t
    except UnicodeError:
        pass
    return bytes(b)


def f32(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def parse(data: bytes) -> LotDdo:
    if len(data) < 8 + BLOCK + 4 or data[:4] != MAGIC:
        raise FormatError("lot", "not a lot\\0 layout")
    (ver,) = struct.unpack_from("<I", data, 4)
    if ver != VERSION:
        raise FormatError("lot", f"version {ver}; Dragon's Dogma Online layouts are {VERSION}")
    block = bytes(data[8:8 + BLOCK])
    p = 8 + BLOCK
    (count,) = struct.unpack_from("<I", data, p)
    p += 4
    recs = []
    try:
        for _ in range(count):
            rid, kind = struct.unpack_from("<iI", data, p)
            p += 8
            vals = []
            for _name, t in _layout(kind)[1]:
                if t == "str":
                    e = data.index(b"\0", p)
                    vals.append(_text(data[p:e]))
                    p = e + 1
                elif t in ("v3", "v4"):
                    n = 3 if t == "v3" else 4
                    vals.append(struct.unpack_from(f"<{n}I", data, p))
                    p += 4 * n
                elif t.startswith("list"):
                    size = int(t[4:])
                    (n,) = struct.unpack_from("<I", data, p)
                    raw = bytes(data[p + 4:p + 4 + n * size])
                    if len(raw) != n * size:
                        raise FormatError("lot", "a list runs past the end of the file", p)
                    vals.append(list(struct.unpack(f"<{n}I", raw)) if size == 4 else raw)
                    p += 4 + n * size
                else:
                    (v,) = struct.unpack_from(_FMT[t], data, p)
                    vals.append(v)
                    p += struct.calcsize(_FMT[t])
            recs.append(Record(rid, kind, vals))
    except (struct.error, ValueError) as e:
        raise FormatError("lot", f"record {len(recs)} runs past the end of the file ({e})") from None
    if p != len(data):
        raise FormatError("lot", f"{len(data) - p} bytes after the last record", p)
    return LotDdo(block, recs)


def build(lot: LotDdo) -> bytes:
    if len(lot.block) != BLOCK:
        raise FormatError("lot", f"the header block must be {BLOCK} bytes")
    out = bytearray(MAGIC + struct.pack("<I", VERSION) + lot.block + struct.pack("<I", len(lot.records)))
    for r in lot.records:
        fields = _layout(r.kind)[1]
        if len(r.values) != len(fields):
            raise FormatError("lot", f"record {r.id} ({r.cls}) has {len(r.values)} values for {len(fields)} fields")
        try:
            out += struct.pack("<iI", r.id, r.kind)
        except struct.error:
            raise FormatError("lot", f"record id {r.id} is out of range") from None
        for (name, t), v in zip(fields, r.values):
            try:
                if t == "str":
                    b = v if isinstance(v, bytes) else v.encode("cp932")
                    if b"\0" in b:
                        raise FormatError("lot", f"{name}: a NUL inside a string")
                    out += b + b"\0"
                elif t in ("v3", "v4"):
                    if len(v) != (3 if t == "v3" else 4):
                        raise FormatError("lot", f"{name}: {t} needs {3 if t == 'v3' else 4} numbers")
                    out += struct.pack(f"<{len(v)}I", *v)
                elif t.startswith("list"):
                    size = int(t[4:])
                    if size == 4:
                        out += struct.pack(f"<I{len(v)}I", len(v), *v)
                    else:
                        if len(v) % size:
                            raise FormatError("lot", f"{name}: {len(v)} bytes is not a whole number of {size}-byte items")
                        out += struct.pack("<I", len(v) // size) + bytes(v)
                else:
                    out += struct.pack(_FMT[t], v)
            except struct.error:
                raise FormatError("lot", f"{name}: {v!r} does not fit a {t}") from None
            except UnicodeError:
                raise FormatError("lot", f"{name}: the text has characters the game's encoding (Shift-JIS) lacks") from None
    return bytes(out)


# -- YAML ------------------------------------------------------------------------------------------
def _fnum(bits: int) -> str:
    v = f32(bits)
    if math.isnan(v) or math.isinf(v) or struct.pack("<f", float(repr(v))) != struct.pack("<I", bits):
        return f"0x{bits:08x}"   # exact bits when a decimal would not come back the same
    return repr(v)


def _fbits(text: str, where) -> int:
    t = text.strip()
    if t.lower().startswith("0x"):
        try:
            v = int(t, 16)
        except ValueError:
            raise ParamError(f"{t!r} is not a number", *where) from None
        if not 0 <= v <= 0xFFFFFFFF:
            raise ParamError(f"{t!r} is not a 32-bit pattern", *where)
        return v
    try:
        return struct.unpack("<I", struct.pack("<f", float(t)))[0]
    except (ValueError, OverflowError):
        raise ParamError(f"{t!r} is not a number", *where) from None


def to_yaml(lot: LotDdo, name: str | None = None) -> str:
    from . import yamlish
    from .yamlish import Map, Scalar, Seq

    head = ["Riftstone layout (.lot, Dragon's Dogma Online)" + (f" -- {name}" if name else ""),
            f"{len(lot.records)} records. Field names are the engine's; positions are x, y, z.",
            "Copy a record to add one (give it an unused id), delete one to remove it; the kind",
            "decides the fields, so keep a record's fields as they are."]
    items = [(Scalar("riftstone"), Scalar(TAG))]
    if name:
        items.append((Scalar("resource"), Scalar(name, "double")))
    if lot.block != bytes(BLOCK):
        items.append((Scalar("block"), Scalar(lot.block.hex(), "double")))
    recs = []
    for r in lot.records:
        fields = [(Scalar("id"), Scalar(str(r.id))), (Scalar("kind"), Scalar(str(r.kind), comment=r.cls))]
        for (fname, t), v in zip(r.fields, r.values):
            if t == "str" and isinstance(v, bytes):
                node = Map([(Scalar("hex"), Scalar(v.hex(), "double"))], flow=True)
            elif t == "str":
                node = Scalar(v, "double")
            elif t == "f32":
                node = Scalar(_fnum(v))
            elif t in ("v3", "v4"):
                node = Seq([Scalar(_fnum(b)) for b in v], flow=True)
            elif t.startswith("list"):
                node = Seq([Scalar(str(x)) for x in v], flow=True) if isinstance(v, list) else Scalar(v.hex(), "double")
            else:
                node = Scalar(str(v))
            fields.append((Scalar(fname), node))
        recs.append(Map(fields))
    items.append((Scalar("records"), Seq(recs)))
    return yamlish.emit(Map(items), head)


def _one(node, fname: str, where) -> str:
    """The text of a single value; lists and mappings where a number belongs are refused."""
    from .yamlish import Scalar

    if not isinstance(node, Scalar):
        raise ParamError(f"'{fname}' holds a list or mapping where a single value belongs", *where)
    return node.text


def from_yaml(text: str, source: str | None = None) -> LotDdo:
    from . import yamlish
    from .yamlish import Map, Scalar, Seq

    doc = yamlish.parse(text, source)
    if not isinstance(doc, Map) or not isinstance(doc.get("riftstone"), Scalar) or doc.get("riftstone").text != TAG:
        raise ParamError(f"not a Riftstone DDO layout (expected 'riftstone: {TAG}')", 1, 1, source)
    lot = LotDdo()
    b = doc.get("block")
    if b is not None:
        try:
            lot.block = bytes.fromhex(b.text)
        except (ValueError, AttributeError):
            raise ParamError("block must be hex", getattr(b, "line", None), getattr(b, "col", None), source) from None
    recs = doc.get("records")
    if not isinstance(recs, Seq):
        raise ParamError("'records' must be a list", None, None, source)
    for node in recs.items:
        if not isinstance(node, Map):
            raise ParamError("each record is a mapping", node.line, node.col, source)
        where = (node.line, node.col, source)
        try:
            rid, kind = int(node.get("id").text, 0), int(node.get("kind").text, 0)
        except (AttributeError, ValueError):
            raise ParamError("a record needs a numeric id and kind", *where) from None
        cls, fields = _layout(kind)
        vals = []
        for fname, t in fields:
            v = node.get(fname)
            if v is None:
                raise ParamError(f"{cls} record {rid}: '{fname}' is missing", *where)
            w = (v.line, v.col, source)
            if t == "str" and isinstance(v, Map):
                h = v.get("hex")
                try:
                    vals.append(bytes.fromhex(h.text))
                except (AttributeError, ValueError):
                    raise ParamError(f"'{fname}': hex must be pairs of hex digits", *w) from None
            elif t == "str":
                if not isinstance(v, Scalar):
                    raise ParamError(f"'{fname}' must be text", *w)
                vals.append(v.text)
            elif t == "f32":
                vals.append(_fbits(_one(v, fname, w), w))
            elif t in ("v3", "v4"):
                n = 3 if t == "v3" else 4
                if not isinstance(v, Seq) or len(v.items) != n:
                    raise ParamError(f"'{fname}' needs {n} numbers", *w)
                vals.append(tuple(_fbits(_one(x, fname, w), w) for x in v.items))
            elif t.startswith("list"):
                try:
                    if isinstance(v, Seq):
                        vals.append([int(_one(x, fname, w), 0) for x in v.items])
                    else:
                        vals.append(bytes.fromhex(v.text))
                except (AttributeError, ValueError):
                    raise ParamError(f"'{fname}' must be a list of whole numbers (or hex bytes)", *w) from None
            else:
                try:
                    vals.append(int(_one(v, fname, w), 0))
                except (AttributeError, ValueError):
                    raise ParamError(f"'{fname}' must be a whole number", *w) from None
        lot.records.append(Record(rid, kind, vals))
    ids = [r.id for r in lot.records]
    if len(ids) != len(set(ids)):
        raise ParamError("two records share an id", None, None, source)
    try:
        build(lot)   # every value in range
    except FormatError as e:
        raise ParamError(str(e), None, None, source) from None
    return lot


def is_ddo_lot(data: bytes) -> bool:
    return len(data) >= 8 and data[:4] == MAGIC and struct.unpack_from("<I", data, 4)[0] == VERSION


def shown(v) -> str:
    """A string field for display (raw bytes as hex)."""
    return v if isinstance(v, str) else ("0x" + v.hex() if v else "") if isinstance(v, bytes) else ""


def summary(lot: LotDdo, limit: int = 40) -> str:
    from collections import Counter

    per = Counter(r.cls for r in lot.records)
    lines = [f"{len(lot.records)} record(s): " + ", ".join(f"{c} x{n}" for c, n in per.most_common())]
    for r in lot.records[:limit]:
        pos = r.get("mPosition")
        where = ", ".join(f"{f32(b):.1f}" for b in pos) if pos else ""
        lines.append(f"  id {r.id:<4} {r.cls:<28} {shown(r.get('mName')):<16} at {where}")
    if len(lot.records) > limit:
        lines.append(f"  ... {len(lot.records) - limit} more")
    return "\n".join(lines)


def yaml_to_bytes(text: str, source: str | None = None) -> bytes:
    return build(from_yaml(text, source))


def copy(lot: LotDdo, index: int, at: tuple[float, float, float] | None = None) -> LotDdo:
    """A copy of record `index` appended with the next free id (moved to `at` when given)."""
    import copy as _copy

    if not 0 <= index < len(lot.records):
        raise ParamError(f"record {index} does not exist (0..{len(lot.records) - 1})")
    new = _copy.deepcopy(lot)
    rec = _copy.deepcopy(lot.records[index])
    used = {r.id for r in lot.records}
    rec.id = max(used, default=-1) + 1
    if rec.id > 0x7FFFFFFF:   # the top id is taken: the lowest free one
        rec.id = next(i for i in range(len(used) + 1) if i not in used)
    if at is not None:
        names = [n for n, _ in rec.fields]
        if "mPosition" not in names:
            raise ParamError(f"a {rec.cls} record has no position")
        rec.values[names.index("mPosition")] = tuple(struct.unpack("<I", struct.pack("<f", v))[0] for v in at)
    new.records.append(rec)
    return new


def remove(lot: LotDdo, index: int) -> LotDdo:
    import copy as _copy

    if not 0 <= index < len(lot.records):
        raise ParamError(f"record {index} does not exist (0..{len(lot.records) - 1})")
    new = _copy.deepcopy(lot)
    del new.records[index]
    return new
