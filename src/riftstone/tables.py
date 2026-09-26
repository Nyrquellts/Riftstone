"""Item sets / drop tables (rItemSetTbl, ``.ist``) and crafting recipes (rItemMix, ``.imx``).

Both are a 12-byte header (magic, u32, u32 count) and fixed records.  Measured on the game's
files (``etc/item/itemSetTbl`` 2,800 sets, ``etc/item/ItemEmListSetTbl`` 1,116 sets,
``etc/item/itemMix`` 435 recipes):

``.ist`` record, 40 bytes = 20 x u16:
  +0x00  set id                    (itemSetTbl 0..2799; the enemy table's ids run to 12,565)
  +0x02  0xFFFF in every set
  +0x04  a small number            (itemSetTbl 1..8; the enemy table 0)       meaning UNKNOWN
  +0x06  a small number            (0..8)                                     meaning UNKNOWN
  +0x08  8 x item id               0xFFFF = no item
  +0x18  8 x weight                one per item slot; an empty slot with a weight is the chance of
                                   nothing.  All 2,800 itemSetTbl sets add up to exactly 100; the
                                   enemy table: 604 sets 100, 327 sets 0, the rest 5..20.
  Every item id in a slot names a real item (one exception, id 889).

``.imx`` record, 16 bytes = 4 x u32:  ingredient, ingredient, result, how many it makes (1..50:
Crimplecap + Pine Branch makes 30 Poison Arrows, a potion recipe makes 1).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .errors import FormatError, ParamError

HEADER = struct.Struct("<4sII")
NONE = 0xFFFF
IST_TAG, IMX_TAG = "ist/1", "imx/1"
_SET = struct.Struct("<20H")
_MIX = struct.Struct("<4I")


@dataclass
class Table:
    magic: bytes
    version: int
    rows: list[tuple] = field(default_factory=list)


def _parse(data: bytes, magic: bytes, rec: struct.Struct, what: str) -> Table:
    if len(data) < HEADER.size:
        raise FormatError(what, "file is shorter than the header", 0)
    m, version, count = HEADER.unpack_from(data, 0)
    if m != magic:
        raise FormatError(what, f"not a {what} file (magic)", 0)
    if HEADER.size + count * rec.size != len(data):
        raise FormatError(what, f"{count} records need {HEADER.size + count * rec.size} bytes, the file has "
                                f"{len(data)}", 8)
    return Table(m, version, [rec.unpack_from(data, HEADER.size + i * rec.size) for i in range(count)])


def parse_ist(data: bytes) -> Table:
    return _parse(data, b"ist\0", _SET, "item set table")


def parse_imx(data: bytes) -> Table:
    return _parse(data, b"imx\0", _MIX, "recipe table")


def build(t: Table) -> bytes:
    rec = _SET if t.magic == b"ist\0" else _MIX
    return HEADER.pack(t.magic, t.version, len(t.rows)) + b"".join(rec.pack(*r) for r in t.rows)


def parse(data: bytes) -> Table:
    return parse_ist(data) if data[:4] == b"ist\0" else parse_imx(data)


# -- YAML ------------------------------------------------------------------------------

def to_yaml(t: Table, name: str | None = None, names: list[str] | None = None) -> str:
    """names: item names by id (from itemName_eng), written after each item for orientation only."""
    from . import yamlish
    from .yamlish import Map, Scalar, Seq

    def item(i):
        return Scalar("none") if i == NONE else Scalar(str(i))

    def label(i):
        if names is None or i == NONE or i >= len(names):
            return []
        return [Scalar(names[i], "double")]

    is_set = t.magic == b"ist\0"
    head = ["Riftstone " + ("item set / drop table (.ist)" if is_set else "recipe table (.imx)")
            + (f" -- {name}" if name else "")]
    if is_set:
        head += [f"{len(t.rows)} sets. Each slot is [item, weight]; 'none' is no item (with a weight: the chance of",
                 "nothing). Weights are percent: 2,800 of 2,800 merchant/reward sets add up to 100.",
                 "A name after the weight is only for reading. f04/f06: two numbers whose meaning is not known."]
    else:
        head += [f"{len(t.rows)} recipes: mix [ingredient, ingredient] makes [result], count of them",
                 "(Crimplecap + Pine Branch makes 30 Poison Arrows). A name after an id is only for reading."]
    items = [(Scalar("riftstone"), Scalar(IST_TAG if is_set else IMX_TAG))]
    if name:
        items.append((Scalar("resource"), Scalar(name, "double")))
    items.append((Scalar("version"), Scalar(str(t.version))))
    rows = []
    for r in t.rows:
        if is_set:
            slots = Seq([Seq([item(i), Scalar(str(w))] + label(i), flow=True) for i, w in zip(r[4:12], r[12:20])],
                        flow=True)
            m = [(Scalar("id"), Scalar(str(r[0])))]
            if r[1] != NONE:
                m.append((Scalar("f02"), Scalar(str(r[1]))))
            m += [(Scalar("f04"), Scalar(str(r[2]))), (Scalar("f06"), Scalar(str(r[3]))), (Scalar("slots"), slots)]
        else:
            m = [(Scalar("mix"), Seq([Scalar(str(r[0]))] + label(r[0]) + [Scalar(str(r[1]))] + label(r[1]), flow=True)),
                 (Scalar("makes"), Seq([Scalar(str(r[2]))] + label(r[2]), flow=True)),
                 (Scalar("count"), Scalar(str(r[3])))]
        rows.append(Map(m))
    items.append((Scalar("sets" if is_set else "recipes"), Seq(rows)))
    return yamlish.emit(Map(items), head)


def _num(node, what, top, source):
    from .yamlish import Scalar

    if not isinstance(node, Scalar):
        raise ParamError(f"{what} is a number", getattr(node, "line", None), getattr(node, "col", None), source)
    try:
        v = int(node.text.strip(), 0)
    except ValueError:
        raise ParamError(f"{what} is a whole number, not {node.text!r}", node.line, node.col, source) from None
    if not 0 <= v <= top:
        raise ParamError(f"{what} must be between 0 and {top}", node.line, node.col, source)
    return v


def _values(seq, n, what, source) -> list:
    """The n values of a flow list, skipping the quoted names written for reading."""
    from .yamlish import Scalar, Seq

    if not isinstance(seq, Seq):
        raise ParamError(f"{what} is a [ ... ] list", getattr(seq, "line", None), None, source)
    vals = [x for x in seq.items if not (isinstance(x, Scalar) and x.style != "plain")]
    if len(vals) != n:
        raise ParamError(f"{what} holds {n} number(s)", seq.line, seq.col, source)
    return vals


def _ids(seq, n, what, top, source):
    return [_num(v, what, top, source) for v in _values(seq, n, what, source)]


def _slot(seq, source) -> tuple[int, int]:
    """[item, weight]: the item may be 'none'; the weight is always a number."""
    from .yamlish import Scalar

    it, w = _values(seq, 2, "a slot", source)
    item = NONE if isinstance(it, Scalar) and it.text == "none" else _num(it, "an item", 0xFFFF, source)
    return item, _num(w, "a weight", 0xFFFF, source)


def from_yaml(text: str, source: str | None = None) -> Table:
    from . import yamlish
    from .yamlish import Map, Scalar, Seq

    doc = yamlish.parse(text, source)
    tag = doc.get("riftstone") if isinstance(doc, Map) else None
    if not isinstance(tag, Scalar) or tag.text not in (IST_TAG, IMX_TAG):
        raise ParamError(f"not a Riftstone item table (expected 'riftstone: {IST_TAG}' or '{IMX_TAG}')", 1, 1, source)
    is_set = tag.text == IST_TAG
    t = Table(b"ist\0" if is_set else b"imx\0", 0)
    if doc.get("version") is not None:
        t.version = _num(doc.get("version"), "version", 0xFFFFFFFF, source)
    rows = doc.get("sets" if is_set else "recipes")
    if not isinstance(rows, Seq):
        raise ParamError(f"{'sets' if is_set else 'recipes'} is a list", getattr(rows, "line", None), None, source)
    allowed = ("id", "f02", "f04", "f06", "slots") if is_set else ("mix", "makes", "count")
    for row in rows.items:
        if not isinstance(row, Map):
            raise ParamError("each entry is a block of fields", getattr(row, "line", None), None, source)
        for k, _ in row.items:
            if k.text not in allowed:
                raise ParamError(f"an entry has {', '.join(allowed)}, not {k.text!r}", k.line, k.col, source)
        if is_set:
            slots = row.get("slots")
            if not isinstance(slots, Seq) or len(slots.items) != 8:
                raise ParamError("slots is a list of 8 [item, weight] pairs", getattr(slots, "line", row.line), None,
                                 source)
            pairs = [_slot(s, source) for s in slots.items]
            head =[_num(row.get("id"), "id", 0xFFFF, source) if row.get("id") is not None else 0,
                    _num(row.get("f02"), "f02", 0xFFFF, source) if row.get("f02") is not None else NONE,
                    _num(row.get("f04"), "f04", 0xFFFF, source) if row.get("f04") is not None else 0,
                    _num(row.get("f06"), "f06", 0xFFFF, source) if row.get("f06") is not None else 0]
            t.rows.append(tuple(head + [i for i, _ in pairs] + [w for _, w in pairs]))
        else:
            a, b = _ids(row.get("mix"), 2, "mix", 0xFFFFFFFF, source)
            (c,) = _ids(row.get("makes"), 1, "makes", 0xFFFFFFFF, source)
            n = _num(row.get("count"), "count", 0xFFFFFFFF, source) if row.get("count") is not None else 1
            t.rows.append((a, b, c, n))
    return t


def yaml_to_bytes(text: str, source: str | None = None) -> bytes:
    return build(from_yaml(text, source))
