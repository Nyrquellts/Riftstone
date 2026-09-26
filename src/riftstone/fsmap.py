"""Engine resource names <-> safe Windows file paths, reversibly.

Resource names are engine identifiers ("model\\em\\e01\\e0100\\e0100" plus a type
id), not paths.  Vanilla has names that cannot be files as-is: trailing spaces
("d_e0201_body_MM "), doubled separators ("vo_ev_jp\\\\st100..."), and any
name could in principle hold a reserved device name or a character Windows
forbids.  Each backslash-separated component is encoded on its own:

* ``%XX`` for forbidden characters, ``%`` itself, control and non-ASCII bytes,
  and trailing spaces or dots;
* ``%_`` for an empty component (a doubled separator);
* ``%XX`` on the first letter of a reserved device name (CON, NUL, COM1...).

The extension comes from the type id (see typemap), so decoding splits the
last dot of the final component.  decode_path(encode_name(n, t)) == (n, t)
for every name without a NUL byte; tests and the fuzzer hold it to that.
"""
from __future__ import annotations

from . import typemap
from .errors import UnsafePathError

_FORBIDDEN = set(b'<>:"/\\|?*%')
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}
_HEX = "0123456789ABCDEF"


def _encode_component(raw: bytes) -> str:
    if raw == b"":
        return "%_"
    out = []
    n = len(raw)
    # Trailing run of spaces/dots must be encoded (Windows strips them).
    tail = n
    while tail > 0 and raw[tail - 1] in (0x20, 0x2E):
        tail -= 1
    for i, b in enumerate(raw):
        if b < 0x20 or b >= 0x7F or b in _FORBIDDEN or i >= tail:
            out.append("%" + _HEX[b >> 4] + _HEX[b & 15])
        else:
            out.append(chr(b))
    text = "".join(out)
    stem = text.split(".", 1)[0].upper()
    if stem in _RESERVED:
        b = raw[0]
        text = "%" + _HEX[b >> 4] + _HEX[b & 15] + text[1:]
    return text


def _decode_component(text: str) -> bytes:
    if text == "%_":
        return b""
    out = bytearray()
    i = 0
    while i < len(text):
        c = text[i]
        if c == "%":
            pair = text[i + 1:i + 3]
            if len(pair) != 2 or any(ch not in "0123456789abcdefABCDEF" for ch in pair):
                raise UnsafePathError(f"bad escape in {text!r}; use %XX (two hex digits) or %_")
            out.append(int(pair, 16))
            i += 3
            continue
        o = ord(c)
        if o < 0x20 or o >= 0x7F:
            raise UnsafePathError(f"non-ASCII or control character in {text!r}; engine names are ASCII")
        out.append(o)
        i += 1
    return bytes(out)


def encode_name(name: bytes, type_id: int) -> str:
    """Relative path (forward slashes) for an engine name + type id."""
    if b"\0" in name:
        raise UnsafePathError("resource name contains a NUL byte")
    parts = [_encode_component(p) for p in name.split(b"\\")]
    return "/".join(parts) + "." + typemap.extension(type_id)


def decode_path(rel: str) -> tuple[bytes, int]:
    """Inverse of encode_name.  Accepts / or \\ separators; refuses anything ambiguous."""
    rel = rel.replace("\\", "/")
    if rel.startswith("/") or (len(rel) > 1 and rel[1] == ":"):
        raise UnsafePathError(f"{rel!r} is absolute; give a path relative to the mod folder")
    parts = rel.split("/")
    last = parts[-1]
    if "." not in last:
        raise UnsafePathError(f"{rel!r} has no extension, so its resource type is unknown")
    stem, ext = last.rsplit(".", 1)
    type_id = typemap.type_for_extension(ext)
    if type_id is None:
        raise UnsafePathError(f"{rel!r}: .{ext} is not a Dragon's Dogma resource type")
    parts[-1] = stem
    for p in parts:
        if p in (".", "..") or p == "":
            raise UnsafePathError(f"{rel!r} contains an empty, '.' or '..' component")
    name = b"\\".join(_decode_component(p) for p in parts)
    if len(name) > 63:
        raise UnsafePathError(f"{rel!r}: engine names hold at most 63 bytes, this one is {len(name)}")
    return name, type_id
