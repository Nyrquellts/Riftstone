"""A small, strict YAML subset for parameter files people edit by hand.

The parser returns syntax only -- mappings, sequences and scalars with their
text, quoting style and position.  It never guesses types: whether ``no`` is a
string or a boolean is decided by the game's schema (see params), so YAML's
implicit-typing traps cannot change a value.

Supported: block mappings and sequences (including "- key: value" items),
flow sequences ``[a, b]`` and mappings ``{k: v}`` (may span lines), plain,
'single-quoted' and "double-quoted" scalars, ``#`` comments, a leading
``---``.  Refused with a line/column error: tabs in indentation, anchors,
aliases, tags, block scalars (| >), multi-line plain scalars, duplicate keys.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .errors import ParamError


@dataclass
class Scalar:
    text: str
    style: str = "plain"      # plain | single | double
    line: int = 0
    col: int = 0
    comment: str | None = None    # emit only: written as "  # comment" after the value


@dataclass
class Seq:
    items: list = field(default_factory=list)
    line: int = 0
    col: int = 0
    flow: bool = False
    comment: str | None = None    # emit only, for a sequence written on one line


@dataclass
class Map:
    items: list = field(default_factory=list)     # [(Scalar key, node)]
    line: int = 0
    col: int = 0
    flow: bool = False

    def get(self, key: str):
        for k, v in self.items:
            if k.text == key:
                return v
        return None


Node = Scalar | Seq | Map

_ESCAPES = {"0": "\0", "a": "\a", "b": "\b", "t": "\t", "\t": "\t", "n": "\n", "v": "\v", "f": "\f",
            "r": "\r", "e": "\x1b", " ": " ", '"': '"', "/": "/", "\\": "\\", "N": "\x85",
            "_": "\xa0", "L": " ", "P": " "}
_HEXLEN = {"x": 2, "u": 4, "U": 8}
_UNSUPPORTED_START = {"&": "anchors", "*": "aliases", "!": "tags", "|": "block scalars", ">": "block scalars",
                      "%": "directives", "@": "reserved characters", "`": "reserved characters"}
MAX_DEPTH = 200
MAX_LINES = 5_000_000


@dataclass
class _Line:
    no: int         # 1-based
    indent: int
    text: str       # content after the indent, comment and trailing spaces removed
    raw: str


class _Parser:
    def __init__(self, text: str, source: str | None):
        self.source = source
        self.lines: list[_Line] = []
        self.i = 0
        self._split(text)

    # -- errors ----------------------------------------------------------
    def err(self, msg: str, line: int | None = None, col: int | None = None) -> ParamError:
        return ParamError(msg, line, col, self.source)

    # -- line preparation --------------------------------------------------
    def _split(self, text: str) -> None:
        if text.startswith("﻿"):
            text = text[1:]
        raw_lines = text.split("\n")
        if len(raw_lines) > MAX_LINES:
            raise self.err(f"file has {len(raw_lines)} lines; the limit is {MAX_LINES}")
        started = False
        for no, raw in enumerate(raw_lines, 1):
            raw = raw.rstrip("\r")
            lead = raw[:len(raw) - len(raw.lstrip(" \t"))]
            if "\t" in lead and raw.strip(" \t") and not raw.strip(" \t").startswith("#"):
                raise self.err("tab used for indentation; use spaces", no, lead.index("\t") + 1)
            stripped = raw.lstrip(" ")
            indent = len(raw) - len(stripped)
            content = _strip_comment(stripped, no, indent, self).rstrip(" \t")
            if not content:
                continue
            if not started and content in ("---",):
                started = True
                continue
            if content in ("---", "..."):
                raise self.err("only one document per file", no, indent + 1)
            started = True
            self.lines.append(_Line(no, indent, content, raw))

    # -- helpers ---------------------------------------------------------
    def cur(self) -> _Line | None:
        return self.lines[self.i] if self.i < len(self.lines) else None

    # -- grammar ---------------------------------------------------------
    def parse(self) -> Node:
        first = self.cur()
        if first is None:
            raise self.err("the file is empty")
        if first.indent != 0:
            raise self.err("the top level must not be indented", first.no, first.indent + 1)
        node = self.block(0, 0)
        extra = self.cur()
        if extra is not None:
            raise self.err("unexpected indentation or content", extra.no, extra.indent + 1)
        return node

    def block(self, indent: int, depth: int) -> Node:
        if depth > MAX_DEPTH:
            ln = self.cur()
            raise self.err(f"nested deeper than {MAX_DEPTH} levels", ln.no if ln else None)
        ln = self.cur()
        if _is_seq_item(ln.text):
            return self.seq(ln.indent, depth)
        if _find_map_colon(ln.text) is not None:
            return self.map(ln.indent, depth)
        # a lone scalar / flow node occupying the block
        self.i += 1
        node, end = self.inline_multiline(ln, 0)
        return node

    def map(self, indent: int, depth: int) -> Map:
        first = self.cur()
        m = Map([], first.no, indent + 1)
        seen: dict[str, int] = {}
        while True:
            ln = self.cur()
            if ln is None or ln.indent < indent:
                break
            if ln.indent > indent:
                raise self.err("unexpected indentation", ln.no, ln.indent + 1)
            if _is_seq_item(ln.text):
                break
            colon = _find_map_colon(ln.text)
            if colon is None:
                raise self.err("expected 'key: value'", ln.no, ln.indent + 1)
            key = self.key_scalar(ln.text[:colon].rstrip(" "), ln.no, ln.indent + 1)
            if key.text in seen:
                raise self.err(f"duplicate key {key.text!r} (first on line {seen[key.text]})", ln.no, ln.indent + 1)
            seen[key.text] = ln.no
            rest = ln.text[colon + 1:].lstrip(" ")
            rest_col = ln.indent + len(ln.text) - len(rest) + 1
            self.i += 1
            if rest == "":
                nxt = self.cur()
                if nxt is not None and nxt.indent > indent:
                    value = self.block(nxt.indent, depth + 1)
                elif nxt is not None and nxt.indent == indent and _is_seq_item(nxt.text):
                    value = self.seq(indent, depth + 1)
                else:
                    raise self.err(f"{key.text!r} has no value", ln.no, ln.indent + 1)
            else:
                value, _ = self.inline_multiline(ln, rest_col - ln.indent - 1, text=rest)
            m.items.append((key, value))
        return m

    def seq(self, indent: int, depth: int) -> Seq:
        first = self.cur()
        s = Seq([], first.no, indent + 1)
        while True:
            ln = self.cur()
            if ln is None or ln.indent < indent:
                break
            if ln.indent > indent:
                raise self.err("unexpected indentation", ln.no, ln.indent + 1)
            if not _is_seq_item(ln.text):
                break
            rest = ln.text[1:]
            spaces = len(rest) - len(rest.lstrip(" "))
            rest = rest.lstrip(" ")
            if rest == "":
                self.i += 1
                nxt = self.cur()
                if nxt is None or nxt.indent <= indent:
                    raise self.err("empty list item", ln.no, ln.indent + 1)
                s.items.append(self.block(nxt.indent, depth + 1))
                continue
            inner_indent = indent + 1 + spaces
            if _is_seq_item(rest) or _find_map_colon(rest) is not None:
                # compact form: "- key: value" or "- - x" continues at the item's column
                self.lines[self.i] = _Line(ln.no, inner_indent, rest, ln.raw)
                s.items.append(self.block(inner_indent, depth + 1))
                continue
            self.i += 1
            value, _ = self.inline_multiline(ln, inner_indent - ln.indent, text=rest)
            s.items.append(value)
        return s

    def key_scalar(self, text: str, line: int, col: int) -> Scalar:
        if not text:
            raise self.err("empty key", line, col)
        if text[0] in "'\"":
            sc, end = _quoted(text, 0, line, col, self)
            if text[end:].strip(" "):
                raise self.err("unexpected text after quoted key", line, col + end)
            return sc
        if text[0] in _UNSUPPORTED_START or text[0] in "[]{},?-" and (len(text) == 1 or text[1] == " "):
            raise self.err(f"key cannot start with {text[0]!r}; quote it", line, col)
        return Scalar(text, "plain", line, col)

    def inline_multiline(self, ln: _Line, offset: int, text: str | None = None):
        """Parse a value that starts on ln; flow collections may continue on later lines."""
        text = ln.text if text is None else text
        col = ln.indent + offset + 1
        if text[:1] in "[{":
            parts = [text]
            depth, quote = _scan_brackets(text, 0, None)   # incremental: each line is scanned once (linear)
            while depth > 0:
                nxt = self.cur()
                if nxt is None:
                    raise self.err("unclosed '[' or '{'", ln.no, col)
                piece = " " + nxt.text
                parts.append(piece)
                self.i += 1
                depth, quote = _scan_brackets(piece, depth, quote)
            buf = "".join(parts)
            node, end = _flow(buf, 0, ln.no, col, self, 0)
            if buf[end:].strip(" "):
                raise self.err("unexpected text after a closing bracket", ln.no, col + end)
            return node, end
        if text[0] in "'\"":
            sc, end = _quoted(text, 0, ln.no, col, self)
            if text[end:].strip(" "):
                raise self.err("unexpected text after a quoted value", ln.no, col + end)
            return sc, end
        if text[0] in _UNSUPPORTED_START:
            raise self.err(f"{_UNSUPPORTED_START[text[0]]} are not supported in Riftstone files", ln.no, col)
        nxt = self.cur()
        if nxt is not None and nxt.indent > ln.indent and not _is_seq_item(nxt.text) \
                and _find_map_colon(nxt.text) is None:
            raise self.err("a plain value cannot continue on the next line; quote it", nxt.no, nxt.indent + 1)
        return Scalar(text, "plain", ln.no, col), len(text)


def _strip_comment(s: str, no: int, indent: int, p: _Parser) -> str:
    """Remove a trailing '# comment' that is outside quotes."""
    quote = None
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if quote == "'":
            if c == "'":
                if i + 1 < n and s[i + 1] == "'":
                    i += 2
                    continue
                quote = None
        elif quote == '"':
            if c == "\\":
                i += 2
                continue
            if c == '"':
                quote = None
        else:
            if c == "#" and (i == 0 or s[i - 1] in " \t"):
                return s[:i]
            if c in "'\"" and (i == 0 or s[i - 1] in " \t[{,:-"):
                quote = c
        i += 1
    return s


def _is_seq_item(text: str) -> bool:
    return text == "-" or text.startswith("- ")


def _find_map_colon(text: str) -> int | None:
    """Index of the ':' that makes this line a 'key: value' entry, if any."""
    if not text or text[0] in "[{":
        return None
    i = 0
    n = len(text)
    if text[0] in "'\"":
        q = text[0]
        i = 1
        while i < n:
            if q == "'" and text[i] == "'":
                if i + 1 < n and text[i + 1] == "'":
                    i += 2
                    continue
                break
            if q == '"' and text[i] == "\\":
                i += 2
                continue
            if q == '"' and text[i] == '"':
                break
            i += 1
        i += 1
        rest = text[i:].lstrip(" ")
        if rest.startswith(":") and (len(rest) == 1 or rest[1] == " "):
            return n - len(rest)
        return None
    while i < n:
        if text[i] == ":" and (i + 1 == n or text[i + 1] == " "):
            return i
        i += 1
    return None


def _scan_brackets(s: str, depth: int, quote):
    """Continue counting unclosed [ { from a prior (depth, quote); returns the new (depth, quote).
    Carrying the quote state lets a flow collection be balanced one appended line at a time, in
    linear total time, instead of re-scanning the whole growing buffer each line."""
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if quote == "'":
            if c == "'":
                if i + 1 < n and s[i + 1] == "'":
                    i += 2
                    continue
                quote = None
        elif quote == '"':
            if c == "\\":
                i += 2
                continue
            if c == '"':
                quote = None
        elif c in "'\"":
            quote = c
        elif c in "[{":
            depth += 1
        elif c in "]}":
            depth -= 1
        i += 1
    return depth, quote


def _bracket_balance(s: str) -> int:
    return _scan_brackets(s, 0, None)[0]


def _quoted(s: str, i: int, line: int, col: int, p: _Parser) -> tuple[Scalar, int]:
    q = s[i]
    out = []
    j = i + 1
    n = len(s)
    while j < n:
        c = s[j]
        if q == "'":
            if c == "'":
                if j + 1 < n and s[j + 1] == "'":
                    out.append("'")
                    j += 2
                    continue
                return Scalar("".join(out), "single", line, col + i), j + 1
            out.append(c)
            j += 1
            continue
        if c == '"':
            return Scalar("".join(out), "double", line, col + i), j + 1
        if c == "\\":
            if j + 1 >= n:
                break
            e = s[j + 1]
            if e in _ESCAPES:
                out.append(_ESCAPES[e])
                j += 2
                continue
            if e in _HEXLEN:
                k = _HEXLEN[e]
                digits = s[j + 2:j + 2 + k]
                if len(digits) != k or any(ch not in "0123456789abcdefABCDEF" for ch in digits):
                    raise p.err(f"bad \\{e} escape; it needs {k} hex digits", line, col + j)
                cp = int(digits, 16)
                if cp > 0x10FFFF:
                    raise p.err("escape is beyond Unicode", line, col + j)
                out.append(chr(cp))
                j += 2 + k
                continue
            raise p.err(f"unknown escape \\{e}", line, col + j)
        out.append(c)
        j += 1
    raise p.err("unterminated quoted string", line, col + i)


def _flow(s: str, i: int, line: int, col: int, p: _Parser, depth: int) -> tuple[Node, int]:
    if depth > MAX_DEPTH:
        raise p.err(f"nested deeper than {MAX_DEPTH} levels", line, col + i)
    n = len(s)

    def skip(k: int) -> int:
        while k < n and s[k] == " ":
            k += 1
        return k

    opener = s[i]
    closer = "]" if opener == "[" else "}"
    node: Seq | Map = Seq([], line, col + i, flow=True) if opener == "[" else Map([], line, col + i, flow=True)
    j = skip(i + 1)
    if j < n and s[j] == closer:
        return node, j + 1
    seen: set[str] = set()
    while True:
        j = skip(j)
        if j >= n:
            raise p.err(f"unclosed '{opener}'", line, col + i)
        if opener == "{":
            key, j = _flow_scalar(s, j, line, col, p, stop=":,}")
            j = skip(j)
            if j >= n or s[j] != ":":
                raise p.err("expected ':' in a {key: value} mapping", line, col + j)
            if key.text in seen:
                raise p.err(f"duplicate key {key.text!r}", line, col + j)
            seen.add(key.text)
            j = skip(j + 1)
            value, j = _flow_value(s, j, line, col, p, depth, stop=",}")
            node.items.append((key, value))
        else:
            value, j = _flow_value(s, j, line, col, p, depth, stop=",]")
            node.items.append(value)
        j = skip(j)
        if j < n and s[j] == ",":
            j = skip(j + 1)
            if j < n and s[j] == closer:
                return node, j + 1
            continue
        if j < n and s[j] == closer:
            return node, j + 1
        raise p.err(f"expected ',' or '{closer}'", line, col + j)


def _flow_value(s, j, line, col, p, depth, stop):
    if j < len(s) and s[j] in "[{":
        return _flow(s, j, line, col, p, depth + 1)
    return _flow_scalar(s, j, line, col, p, stop)


def _flow_scalar(s: str, j: int, line: int, col: int, p: _Parser, stop: str) -> tuple[Scalar, int]:
    n = len(s)
    if j < n and s[j] in "'\"":
        return _quoted(s, j, line, col, p)
    if j < n and s[j] in _UNSUPPORTED_START:
        raise p.err(f"{_UNSUPPORTED_START[s[j]]} are not supported in Riftstone files", line, col + j)
    k = j
    while k < n and s[k] not in stop and s[k] not in "[]{}":
        if s[k] == ":" and ":" in stop and (k + 1 >= n or s[k + 1] in " ,}"):
            break
        k += 1
    text = s[j:k].rstrip(" ")
    if not text:
        raise p.err("missing value", line, col + j)
    return Scalar(text, "plain", line, col + j), k


def parse(text: str, source: str | None = None) -> Node:
    return _Parser(text, source).parse()


# -- emitting -----------------------------------------------------------------

# Used with fullmatch: a bare "$" would also accept a string ending in "\n" (fuzzer finding).
_PLAIN_SAFE = re.compile(r"[A-Za-z0-9_\-./\\()$+~^=;<\u0080-￿][A-Za-z0-9_\-./\\()$+~^=;<> \u0080-￿:,#]*")
_LOOKS_TYPED = re.compile(r"(?:[-+]?(?:\d[\d_]*(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?|0[xXoObB][0-9a-fA-F_]+|"
                          r"[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN)|~|null|Null|NULL|true|True|TRUE|false|False|"
                          r"FALSE|y|Y|yes|Yes|YES|n|N|no|No|NO|on|On|ON|off|Off|OFF)")


def quote(text: str, flow: bool = False) -> str:
    """A scalar string as plain text when that is unambiguous, else double-quoted.

    flow=True is for items inside [ ] or { }, where ',' and ':' also end a plain scalar.
    """
    if (text and _PLAIN_SAFE.fullmatch(text) and not _LOOKS_TYPED.fullmatch(text) and text == text.strip(" ")
            and ": " not in text and " #" not in text and not text.endswith(":") and not text.startswith("- ")
            and text not in ("-", ".")
            and not (flow and any(c in text for c in ",:[]{}"))
            and "﻿" not in text                   # a BOM starting the file is dropped when read (fuzz finding)
            and all(ord(c) < 0xD800 or ord(c) > 0xDFFF for c in text)):
        return text
    return dquote(text)


def dquote(text: str) -> str:
    out = ['"']
    for c in text:
        o = ord(c)
        if c == '"':
            out.append('\\"')
        elif c == "\\":
            out.append("\\\\")
        elif c == "\n":
            out.append("\\n")
        elif c == "\t":
            out.append("\\t")
        elif c == "\r":
            out.append("\\r")
        elif c == "\0":
            out.append("\\0")
        elif o < 0x20 or o == 0x7F or 0xD800 <= o <= 0xDFFF or o in (0x85, 0xA0, 0x2028, 0x2029, 0xFEFF):
            out.append(f"\\u{o:04x}" if o > 0xFF else f"\\x{o:02x}")
        else:
            out.append(c)
    out.append('"')
    return "".join(out)


def emit(node: Node, header: list[str] | None = None) -> str:
    """Text for a node tree.  Scalars are written exactly as given (callers format them)."""
    lines: list[str] = [("# " + h).rstrip() if h else "#" for h in (header or [])]
    _emit_block(node, 0, lines)
    return "\n".join(lines) + "\n"


def _scalar_text(sc: Scalar) -> str:
    if sc.style == "plain":
        return sc.text
    if sc.style == "single":
        return "'" + sc.text.replace("'", "''") + "'"
    return dquote(sc.text)


def _flow_text(node: Node) -> str:
    if isinstance(node, Scalar):
        return _scalar_text(node)
    if isinstance(node, Seq):
        return "[" + ", ".join(_flow_text(x) for x in node.items) + "]"
    return "{" + ", ".join(f"{_scalar_text(k)}: {_flow_text(v)}" for k, v in node.items) + "}"


def _inline(node: Node) -> bool:
    return isinstance(node, Scalar) or node.flow or not node.items


def _note(node: Node) -> str:
    """The trailing '  # comment' an inline node asks for (one line; never part of the value)."""
    c = getattr(node, "comment", None)
    return "  # " + " ".join(str(c).split()) if c else ""


def _emit_block(node: Node, indent: int, lines: list[str]) -> None:
    pad = " " * indent
    if isinstance(node, Map) and not _inline(node):
        for k, v in node.items:
            key = _scalar_text(k)
            if _inline(v):
                lines.append(f"{pad}{key}: {_flow_text(v)}{_note(v)}")
            else:
                lines.append(f"{pad}{key}:")
                _emit_block(v, indent + 2, lines)
    elif isinstance(node, Seq) and not _inline(node):
        for v in node.items:
            if _inline(v):
                lines.append(f"{pad}- {_flow_text(v)}{_note(v)}")
            elif isinstance(v, Map):
                sub: list[str] = []
                _emit_block(v, indent + 2, sub)
                sub[0] = pad + "- " + sub[0][indent + 2:]
                lines.extend(sub)
            else:
                lines.append(f"{pad}-")
                _emit_block(v, indent + 2, lines)
    else:
        lines.append(pad + _flow_text(node))
