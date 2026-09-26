"""GMD text files (rGUIMessage, ``.gmd``): dialogue, item names and descriptions, menus.

Layout, version 1.2.1 -- measured on all 7,954 distinct files in the game:

  0x00  "GMD\\0"
  0x04  u32 version, 0x00010201
  0x08  u32 language (0 jpn, 1 eng, 2 fre, 3 spa, 4 ger, 5 ita, 7 zht; 6 is unused)
  0x0C  8 bytes, zero in every file
  0x14  u32 label count
  0x18  u32 message count
  0x1C  u32 label block size
  0x20  u32 message block size
  0x24  u32 name length; then the name and a NUL ("TextWeb" in 7,852 files)
  then  label count x (u32 message index, u32 pointer).  The pointer is the authoring
        tool's address of the label text: one base per file plus the label's offset in
        the label block (consistent in all 1,824 files that have labels).
  then  the label block: NUL-terminated labels, in entry order
  then  the message block: NUL-terminated UTF-8 messages

Labels name messages in strictly increasing order (all 1,824 files), so a message
has at most one label.  Other resources refer to a message by its position, which
is why the YAML form keeps an ``id`` on every message and refuses a file whose ids
moved: a line inserted in the middle would silently renumber every line after it.

Version 1.3.2 (Dragon's Dogma Online) -- measured on all 6,859 files in the client:
the same header (the 8 bytes at 0x0C are a u64 update time, zero), then, when there
are labels ("keys"), key count x (u32 index, u32 ~crc32(key*2), u32 ~crc32(key*3),
u32 key offset, u32 next key in the same bucket) and u32 bucket[256] (first key whose
~crc32(key) & 0xFF falls in it; key 0 is written 0xFFFFFFFF), then the key block and
the message block. Key i labels message i (no file has more keys than messages), and
every table is derived: ``build`` recomputes them and the corpus proves that matches.
"""
from __future__ import annotations

import re
import struct
import zlib
from dataclasses import dataclass, field

from .errors import FormatError, ParamError

MAGIC = b"GMD\0"
VERSION = 0x00010201       # DDDA
VERSION_DDO = 0x00010302   # Dragon's Dogma Online
HEADER = struct.Struct("<4sII8sIIIII")
LABEL = struct.Struct("<II")
KEYREC = struct.Struct("<IIIII")
TAG = "gmd/1"
VERSION_NAMES = {VERSION: "1.2.1", VERSION_DDO: "1.3.2"}

LANGUAGES = {0: "japanese", 1: "english", 2: "french", 3: "spanish", 4: "german", 5: "italian", 7: "chinese"}
SUFFIXES = {0: "jpn", 1: "eng", 2: "fre", 3: "spa", 4: "ger", 5: "ita", 7: "zht"}
_BY_NAME = {v: k for k, v in LANGUAGES.items()}


@dataclass
class Message:
    text: str
    label: str | None = None


@dataclass
class Gmd:
    language: int = 1
    name: str = "TextWeb"
    messages: list[Message] = field(default_factory=list)
    label_base: int = 0          # the stored pointer base, kept so an unchanged file rebuilds byte for byte
    reserved: bytes = bytes(8)   # zero in the game; kept for exactness
    version: int = VERSION       # VERSION (DDDA) or VERSION_DDO

    @property
    def language_name(self) -> str:
        return LANGUAGES.get(self.language, str(self.language))


def _jam(b: bytes) -> int:
    return zlib.crc32(b) ^ 0xFFFFFFFF


def _ddo_tables(keys: list[bytes]) -> tuple[list[tuple[int, int, int, int, int]], list[int]]:
    recs, buckets, last, link, off = [], [0] * 256, {}, [0] * len(keys), 0
    for i, k in enumerate(keys):
        b = _jam(k) & 0xFF
        if b in last:
            link[last[b]] = i
        else:
            buckets[b] = 0xFFFFFFFF if i == 0 else i
        last[b] = i
    for i, k in enumerate(keys):
        recs.append((i, _jam(k * 2), _jam(k * 3), off, link[i]))
        off += len(k) + 1
    return recs, buckets


def _parse_ddo(data: bytes, lang: int, reserved: bytes, nkeys: int, nmsgs: int, ksize: int, msize: int,
               namelen: int) -> Gmd:
    p = HEADER.size
    need = p + namelen + 1 + (KEYREC.size * nkeys + 1024 if nkeys else 0) + ksize + msize
    if need != len(data):
        raise FormatError("GMD", f"sizes in the header add up to {need} bytes, the file has {len(data)}", 0x14)
    if data[p + namelen] != 0 or 0 in data[p:p + namelen]:
        raise FormatError("GMD", "the name is not NUL-terminated", p)
    name = _utf8(data[p:p + namelen], "the name", p)
    p += namelen + 1
    recs = [KEYREC.unpack_from(data, p + i * KEYREC.size) for i in range(nkeys)]
    p += KEYREC.size * nkeys
    buckets = list(struct.unpack_from("<256I", data, p)) if nkeys else None
    if nkeys:
        p += 1024
    keys = data[p:p + ksize].split(b"\0")
    if keys.pop() != b"" or len(keys) != nkeys:
        raise FormatError("GMD", f"the key block holds {len(keys)} keys, the header says {nkeys}", p)
    texts = data[p + ksize:p + ksize + msize].split(b"\0")
    if texts.pop() != b"" or len(texts) != nmsgs:
        raise FormatError("GMD", f"the message block holds {len(texts)} messages, the header says {nmsgs}", p + ksize)
    if nkeys > nmsgs:
        raise FormatError("GMD", f"{nkeys} keys for {nmsgs} messages: a key must label a message", 0x14)
    exp_recs, exp_buckets = _ddo_tables(keys)
    if recs != exp_recs or (nkeys and buckets != exp_buckets):
        raise FormatError("GMD", "the key hash tables do not follow the game's rule", HEADER.size + namelen + 1)
    msgs = [Message(_utf8(t, f"message {i}", p + ksize)) for i, t in enumerate(texts)]
    for i, k in enumerate(keys):
        msgs[i].label = _utf8(k, f"key {i}", p)
    return Gmd(lang, name, msgs, 0, reserved, VERSION_DDO)


def _build_ddo(g: Gmd) -> bytes:
    n = 0
    while n < len(g.messages) and g.messages[n].label is not None:
        n += 1
    if any(m.label is not None for m in g.messages[n:]):
        raise ParamError("in a version 1.3.2 (Dragon's Dogma Online) text file labels must be on the first "
                         "messages without gaps (label i names message i)")
    keys = [_text_bytes(m.label, f"the label of message {i}") for i, m in enumerate(g.messages[:n])]
    name = _text_bytes(g.name, "the name")
    kblock = b"".join(k + b"\0" for k in keys)
    mblock = b"".join(_text_bytes(m.text, f"message {i}") + b"\0" for i, m in enumerate(g.messages))
    if not (0 <= g.language <= 0xFFFFFFFF and len(g.reserved) == 8):
        raise ParamError("language must be a 32-bit number and reserved 8 bytes")
    out = bytearray(HEADER.pack(MAGIC, VERSION_DDO, g.language, g.reserved, n, len(g.messages),
                                len(kblock), len(mblock), len(name)))
    out += name + b"\0"
    if n:
        recs, buckets = _ddo_tables(keys)
        out += b"".join(KEYREC.pack(*r) for r in recs) + struct.pack("<256I", *buckets)
    return bytes(out + kblock + mblock)


def _utf8(b: bytes, what: str, at: int) -> str:
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        raise FormatError("GMD", f"{what} is not UTF-8 text", at) from None


def parse(data: bytes) -> Gmd:
    if len(data) < HEADER.size:
        raise FormatError("GMD", "file is shorter than the header", 0)
    magic, version, lang, reserved, nlabels, nmsgs, lsize, msize, namelen = HEADER.unpack_from(data, 0)
    if magic != MAGIC:
        raise FormatError("GMD", "not a GMD file (magic)", 0)
    if version == VERSION_DDO:
        return _parse_ddo(data, lang, reserved, nlabels, nmsgs, lsize, msize, namelen)
    if version != VERSION:
        raise FormatError("GMD", f"version {version:#x} is neither Dragon's Dogma 1.2.1 nor Online's 1.3.2", 4)
    p = HEADER.size
    need = p + namelen + 1 + LABEL.size * nlabels + lsize + msize
    if need != len(data):
        raise FormatError("GMD", f"sizes in the header add up to {need} bytes, the file has {len(data)}", 0x14)
    if data[p + namelen] != 0 or 0 in data[p:p + namelen]:
        raise FormatError("GMD", "the name is not NUL-terminated", p)
    name = _utf8(data[p:p + namelen], "the name", p)
    p += namelen + 1
    entries = [LABEL.unpack_from(data, p + i * LABEL.size) for i in range(nlabels)]
    p += LABEL.size * nlabels
    lblock, mblock = data[p:p + lsize], data[p + lsize:p + lsize + msize]
    labels = lblock.split(b"\0")
    if labels.pop() != b"" or len(labels) != nlabels:
        raise FormatError("GMD", f"the label block holds {len(labels)} labels, the header says {nlabels}", p)
    texts = mblock.split(b"\0")
    if texts.pop() != b"" or len(texts) != nmsgs:
        raise FormatError("GMD", f"the message block holds {len(texts)} messages, the header says {nmsgs}", p + lsize)
    msgs = [Message(_utf8(t, f"message {i}", p + lsize)) for i, t in enumerate(texts)]
    base, off, last = 0, 0, -1
    for i, ((index, ptr), lab) in enumerate(zip(entries, labels)):
        if i == 0:
            base = (ptr - off) & 0xFFFFFFFF
        elif ptr != (base + off) & 0xFFFFFFFF:
            raise FormatError("GMD", f"label {i} pointer does not follow the others", p)
        if not last < index < nmsgs:
            raise FormatError("GMD", f"label {i} names message {index}: labels must name messages in order", p)
        msgs[index].label = _utf8(lab, f"label {i}", p)
        last, off = index, off + len(lab) + 1
    return Gmd(lang, name, msgs, base, reserved)


def _text_bytes(s: str, what: str) -> bytes:
    if "\0" in s:
        raise ParamError(f"{what} cannot contain a NUL character (\\0)")
    try:
        return s.encode("utf-8")
    except UnicodeEncodeError:
        raise ParamError(f"{what} holds a character that is not valid text (a lone surrogate)") from None


def build(g: Gmd) -> bytes:
    if g.version == VERSION_DDO:
        return _build_ddo(g)
    if g.version != VERSION:
        raise ParamError(f"text file version {g.version:#x} is not one Riftstone writes (1.2.1 or 1.3.2)")
    name = _text_bytes(g.name, "the name")
    labels = [(i, _text_bytes(m.label, f"the label of message {i}")) for i, m in enumerate(g.messages)
              if m.label is not None]
    lblock = b"".join(lab + b"\0" for _, lab in labels)
    mblock = b"".join(_text_bytes(m.text, f"message {i}") + b"\0" for i, m in enumerate(g.messages))
    if not (0 <= g.language <= 0xFFFFFFFF and len(g.reserved) == 8):
        raise ParamError("language must be a 32-bit number and reserved 8 bytes")
    out = bytearray(HEADER.pack(MAGIC, VERSION, g.language, g.reserved, len(labels), len(g.messages),
                                len(lblock), len(mblock), len(name)))
    out += name + b"\0"
    off = 0
    for index, lab in labels:
        out += LABEL.pack(index, (g.label_base + off) & 0xFFFFFFFF)
        off += len(lab) + 1
    return bytes(out + lblock + mblock)


# -- YAML ------------------------------------------------------------------------

def to_yaml(g: Gmd, name: str | None = None) -> str:
    from . import yamlish
    from .yamlish import Map, Scalar, Seq

    head = ["Riftstone text file (.gmd)" + (f" -- {name}" if name else ""),
            f"{len(g.messages)} messages in {g.language_name}.",
            "Edit a text between its quotes. Line breaks are \\r\\n (the game's usual) or \\n.",
            "Tags such as <ICON ...>, <ITNO ...> and {Herr}{Herrin} belong to the game; keep them.",
            "Add a message with a new '- text:' line at the END: other files find messages",
            "by their id, so ids must not move (Riftstone checks)."]
    items = [(Scalar("riftstone"), Scalar(TAG))]
    if name:
        items.append((Scalar("resource"), Scalar(name, "double")))
    if g.version != VERSION:
        items.append((Scalar("version"), Scalar(VERSION_NAMES.get(g.version, f"{g.version:#x}"))))
    items.append((Scalar("language"), Scalar(g.language_name)))
    items.append((Scalar("name"), Scalar(yamlish.quote(g.name))))    # quote() gives plain or "quoted" text
    if g.version == VERSION and (any(m.label is not None for m in g.messages) or g.label_base):
        items.append((Scalar("label_base"), Scalar(f"{g.label_base:#010x}")))
    if g.reserved != bytes(8):
        items.append((Scalar("reserved"), Scalar(g.reserved.hex(), "double")))
    msgs = []
    for i, m in enumerate(g.messages):
        fields = [(Scalar("id"), Scalar(str(i)))]
        if m.label is not None:
            fields.append((Scalar("label"), Scalar(m.label, "double")))
        fields.append((Scalar("text"), Scalar(m.text, "double")))
        msgs.append(Map(fields))
    items.append((Scalar("messages"), Seq(msgs)))
    return yamlish.emit(Map(items), head)


def from_yaml(text: str, source: str | None = None) -> Gmd:
    from . import yamlish
    from .yamlish import Map, Scalar, Seq

    doc = yamlish.parse(text, source)
    if not isinstance(doc, Map) or not isinstance(doc.get("riftstone"), Scalar) or doc.get("riftstone").text != TAG:
        raise ParamError(f"not a Riftstone text file (expected 'riftstone: {TAG}')", 1, 1, source)

    def at(node):
        return getattr(node, "line", None), getattr(node, "col", None)

    def scalar(key, required=False):
        node = doc.get(key)
        if node is None:
            if required:
                raise ParamError(f"'{key}' is missing", None, None, source)
            return None
        if not isinstance(node, Scalar):
            raise ParamError(f"'{key}' must be a single value", *at(node), source)
        return node

    g = Gmd()
    ver = scalar("version")
    if ver is not None:
        by_name = {v: k for k, v in VERSION_NAMES.items()}
        if ver.text not in by_name:
            raise ParamError(f"version is 1.2.1 (Dark Arisen) or 1.3.2 (Online), not {ver.text!r}",
                             ver.line, ver.col, source)
        g.version = by_name[ver.text]
    lang = scalar("language", True)
    if lang.text in _BY_NAME:
        g.language = _BY_NAME[lang.text]
    else:
        try:
            g.language = int(lang.text, 0)
        except ValueError:
            raise ParamError(f"language is one of {', '.join(_BY_NAME)} (or a number), not {lang.text!r}",
                             lang.line, lang.col, source) from None
        if not 0 <= g.language <= 0xFFFFFFFF:
            raise ParamError("language number is out of range", lang.line, lang.col, source)
    nm = scalar("name")
    if nm is not None:
        _checked(nm, "the name", source)
        g.name = nm.text
    base = scalar("label_base")
    if base is not None:
        try:
            g.label_base = int(base.text, 0)
        except ValueError:
            raise ParamError("label_base is a number such as 0x3b2f0de0", base.line, base.col, source) from None
        if not 0 <= g.label_base <= 0xFFFFFFFF:
            raise ParamError("label_base is out of range", base.line, base.col, source)
    res = scalar("reserved")
    if res is not None:
        try:
            g.reserved = bytes.fromhex(res.text)
        except ValueError:
            raise ParamError("reserved is 8 bytes of hex", res.line, res.col, source) from None
        if len(g.reserved) != 8:
            raise ParamError("reserved is 8 bytes of hex", res.line, res.col, source)
    msgs = doc.get("messages")
    if msgs is None:
        msgs = Seq([])
    if not isinstance(msgs, Seq):
        raise ParamError("messages is a list of '- text: ...' entries", *at(msgs), source)
    for i, item in enumerate(msgs.items):
        if isinstance(item, Scalar):             # a bare "- some text" line
            g.messages.append(Message(item.text))
            continue
        if not isinstance(item, Map):
            raise ParamError("each message is '- text: ...'", *at(item), source)
        for k, _ in item.items:
            if k.text not in ("id", "label", "text"):
                raise ParamError(f"a message has id, label and text, not {k.text!r}", k.line, k.col, source)
        mid = item.get("id")
        if mid is not None:
            if not isinstance(mid, Scalar) or not re.fullmatch(r"\d{1,9}", mid.text) or int(mid.text) != i:
                line, col = at(mid)
                raise ParamError(f"this is message {i}, but its id says {getattr(mid, 'text', '?')}. Ids must not "
                                 "move: other files find messages by id. Add new messages at the end, and leave "
                                 "a message's text empty (\"\") instead of deleting it", line, col, source)
        t = item.get("text")
        if not isinstance(t, Scalar):
            line, col = at(t) if t is not None else at(item)
            raise ParamError("each message needs a text: \"...\"", line, col, source)
        lab = item.get("label")
        if lab is not None and not isinstance(lab, Scalar):
            raise ParamError("a label is a single value", *at(lab), source)
        _checked(t, f"message {i}", source)
        if lab is not None:
            _checked(lab, f"the label of message {i}", source)
        g.messages.append(Message(t.text, lab.text if lab is not None else None))
    return g


def _checked(node, what: str, source: str | None) -> None:
    """A NUL or a lone surrogate cannot be written; say so with the line it is on."""
    try:
        _text_bytes(node.text, what)
    except ParamError as e:
        raise ParamError(str(e), node.line, node.col, source) from None


def yaml_to_bytes(text: str, source: str | None = None) -> bytes:
    return build(from_yaml(text, source))


def language_of(name: str) -> int | None:
    """The language a resource name's suffix names (``..._eng`` -> 1), if any."""
    m = re.search(r"_([a-z]{3})$", name)
    if m:
        for lang, suf in SUFFIXES.items():
            if suf == m.group(1):
                return lang
    return None
