"""The loader's patch ledger: ``riftstone\\logs\\patches.txt`` (``native/loader/ledger.cpp``, docs/runtime.md
"Patch ledger").

The loader copies the game executable's code sections and import table when it starts, and after each step of its
start-up (its own import hooks, each DLL of ``riftstone\\legacy``, each plugin, the engine fixes, a chained
dinput8) writes down what changed since the step before, as that step's.  A late scan names what changed after
start-up by where it leads (the module a written jump or an import slot points into).  Two modules that changed the
same code bytes are a conflict; an import slot two modules hooked is a chain.

This module reads that file strictly (every field checked, every index in range), writes it back in the loader's
own form, and says what it holds in words.  It reads only; nothing here touches the game.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .errors import RiftError

FILE = "patches.txt"
MAX_LINES = 200_000
KINDS = ("game", "loader", "legacy", "plugin", "fixes", "chain", "late")
_HEX = re.compile(r"0x([0-9a-fA-F]{1,8})")
_BYTES = re.compile(r"(?:[0-9A-F]{2}){1,16}")
_TEXT = re.compile(r"[^\t\r\n]{1,400}")
_INT = re.compile(r"[0-9]{1,10}")
_REGION = re.compile(r"[\x21-\x7e]{1,8}(?:~[0-9]{1,2})?")    # a second section of the same name: ".text~2"


@dataclass
class Ledger:
    game: str = ""
    base: int = 0
    regions: list[dict] = field(default_factory=list)    # name, at, size
    owners: list[dict] = field(default_factory=list)     # kind, module ("" none), name
    ranges: list[dict] = field(default_factory=list)     # at, len, owner, region, to ("" nowhere known), before, after
    imports: list[dict] = field(default_factory=list)    # at, owner, name (dll!function), to, before, after
    conflicts: list[dict] = field(default_factory=list)  # at, len, first, then (owner numbers)
    chains: list[dict] = field(default_factory=list)     # the same, for an import slot
    late: dict | None = None                             # seconds, changes
    end: dict | None = None                              # ranges, imports, conflicts, chains, dropped

    @property
    def complete(self) -> bool:
        """The loader finished writing it, and its counts are what the file holds."""
        e = self.end
        return bool(e) and (e["ranges"], e["imports"], e["conflicts"], e["chains"]) == (
            len(self.ranges), len(self.imports), len(self.conflicts), len(self.chains))

    def owner_name(self, n: int) -> str:
        return self.owners[n]["name"]

    def as_dict(self) -> dict:
        def hexed(rows):
            return [{k: (v.hex().upper() if isinstance(v, bytes) else f"0x{v:08x}" if k == "at" else v)
                     for k, v in r.items()} for r in rows]
        return {"schema": "riftstone-patches/1", "game": self.game, "base": f"0x{self.base:08x}",
                "regions": hexed(self.regions), "owners": self.owners, "ranges": hexed(self.ranges),
                "imports": hexed(self.imports), "conflicts": hexed(self.conflicts), "chains": hexed(self.chains),
                "late": self.late, "end": self.end, "complete": self.complete}


def _num(text: str, what: str, line: int) -> int:
    if not _INT.fullmatch(text):
        raise RiftError(f"{FILE} line {line}: {what} {text!r} is not a number")
    v = int(text)
    if v > 0xFFFFFFFF:
        raise RiftError(f"{FILE} line {line}: {what} {v} is past 32 bits")
    return v


def _addr(text: str, what: str, line: int) -> int:
    m = _HEX.fullmatch(text)
    if not m:
        raise RiftError(f"{FILE} line {line}: {what} {text!r} is not an address (0x...)")
    return int(m.group(1), 16)


def _bytes(text: str, what: str, line: int) -> bytes:
    if text == "-":
        return b""
    if not _BYTES.fullmatch(text):
        raise RiftError(f"{FILE} line {line}: {what} {text!r} is not 1 to 16 bytes in hex")
    return bytes.fromhex(text)


def _text(text: str, what: str, line: int) -> str:
    if not _TEXT.fullmatch(text) or text != text.strip():
        raise RiftError(f"{FILE} line {line}: {what} {text!r} is empty, too long or padded")
    return text


def _to(text: str, line: int) -> str:
    return "" if text == "-" else _text(text, "the module it leads to", line)


_FIELDS = {"game": 2, "region": 3, "owner": 4, "range": 7, "import": 6, "conflict": 4, "chain": 4, "late": 2,
           "end": 5}


def parse(text: str) -> Ledger:
    """patches.txt -> a Ledger.  RiftError names the first line that is not the loader's."""
    if not isinstance(text, str):
        raise RiftError(f"{FILE} is not text")
    lines = text.split("\n")
    if len(lines) > MAX_LINES:
        raise RiftError(f"{FILE} has {len(lines)} lines; the loader writes at most {MAX_LINES}")
    led = Ledger()
    seen_game = False
    for i, raw in enumerate(lines, 1):
        line = raw[:-1] if raw.endswith("\r") else raw
        if not line or line.startswith(";"):
            continue
        if led.end is not None:
            raise RiftError(f"{FILE} line {i}: something after the end line")
        kind, _, rest = line.partition("\t")
        if kind not in _FIELDS:
            raise RiftError(f"{FILE} line {i}: {kind[:40]!r} is no line the loader writes")
        f = rest.split("\t") if rest else []
        if len(f) != _FIELDS[kind]:
            raise RiftError(f"{FILE} line {i}: a {kind} line has {_FIELDS[kind]} fields, not {len(f)}")
        if kind == "game":
            if seen_game:
                raise RiftError(f"{FILE} line {i}: a second game line")
            seen_game = True
            led.game, led.base = _text(f[0], "the game", i), _addr(f[1], "its base", i)
        elif kind == "region":
            if not _REGION.fullmatch(f[0]):
                raise RiftError(f"{FILE} line {i}: {f[0]!r} is no section name")
            if any(r["name"] == f[0] for r in led.regions):
                raise RiftError(f"{FILE} line {i}: region {f[0]} twice")
            led.regions.append({"name": f[0], "at": _addr(f[1], "its address", i), "size": _num(f[2], "its size", i)})
        elif kind == "owner":
            if _num(f[0], "the owner's number", i) != len(led.owners):
                raise RiftError(f"{FILE} line {i}: owner {f[0]} out of order (expected {len(led.owners)})")
            if f[1] not in KINDS:
                raise RiftError(f"{FILE} line {i}: {f[1]!r} is no kind of owner ({', '.join(KINDS)})")
            led.owners.append({"kind": f[1], "module": "" if f[2] == "-" else _text(f[2], "its module", i),
                               "name": _text(f[3], "its name", i)})
        elif kind == "range":
            n = _num(f[1], "the length", i)
            if n == 0:
                raise RiftError(f"{FILE} line {i}: a change of 0 bytes")
            rec = {"at": _addr(f[0], "the address", i), "len": n, "owner": _num(f[2], "the owner", i),
                   "region": f[3], "to": _to(f[4], i), "before": _bytes(f[5], "the bytes before", i),
                   "after": _bytes(f[6], "the bytes after", i)}
            if len(rec["before"]) != min(n, 16) or len(rec["after"]) != min(n, 16):
                raise RiftError(f"{FILE} line {i}: a change of {n} bytes shows {min(n, 16)} before and after")
            led.ranges.append(rec)
        elif kind == "import":
            rec = {"at": _addr(f[0], "the slot", i), "owner": _num(f[1], "the owner", i),
                   "name": _text(f[2], "the import", i), "to": _to(f[3], i),
                   "before": _bytes(f[4], "the slot before", i), "after": _bytes(f[5], "the slot after", i)}
            if len(rec["before"]) != 4 or len(rec["after"]) != 4:
                raise RiftError(f"{FILE} line {i}: an import slot is 4 bytes")
            led.imports.append(rec)
        elif kind in ("conflict", "chain"):
            rec = {"at": _addr(f[0], "the address", i), "len": _num(f[1], "the length", i),
                   "first": _num(f[2], "the first owner", i), "then": _num(f[3], "the later owner", i)}
            if rec["len"] == 0:
                raise RiftError(f"{FILE} line {i}: a {kind} of 0 bytes")
            (led.conflicts if kind == "conflict" else led.chains).append(rec)
        elif kind == "late":
            if led.late is not None:
                raise RiftError(f"{FILE} line {i}: a second late line")
            led.late = {"seconds": _num(f[0], "the seconds", i), "changes": _num(f[1], "the changes", i)}
        else:
            led.end = dict(zip(("ranges", "imports", "conflicts", "chains", "dropped"),
                               (_num(v, "a count", i) for v in f)))
    if not seen_game:
        raise RiftError(f"{FILE} has no game line")
    regions = {r["name"] for r in led.regions}
    count = len(led.owners)
    for r in led.ranges:
        if r["owner"] >= count:
            raise RiftError(f"{FILE}: a change at 0x{r['at']:08x} names owner {r['owner']}; there are {count}")
        if r["region"] not in regions:
            raise RiftError(f"{FILE}: a change at 0x{r['at']:08x} is in {r['region']!r}, no region of the file")
    for r in led.imports:
        if r["owner"] >= count:
            raise RiftError(f"{FILE}: the import at 0x{r['at']:08x} names owner {r['owner']}; there are {count}")
    for r in led.conflicts + led.chains:
        if r["first"] >= count or r["then"] >= count:
            raise RiftError(f"{FILE}: the overlap at 0x{r['at']:08x} names an owner past {count - 1}")
    return led


def format_ledger(led: Ledger) -> str:
    """A Ledger as the loader writes it (CRLF; the two comment lines are not kept)."""
    out = ["; Riftstone patch ledger (as read back by riftstone.patchledger)",
           f"game\t{led.game}\t0x{led.base:08x}"]
    out += [f"region\t{r['name']}\t0x{r['at']:08x}\t{r['size']}" for r in led.regions]
    out += [f"owner\t{i}\t{o['kind']}\t{o['module'] or '-'}\t{o['name']}" for i, o in enumerate(led.owners)]
    for r in led.ranges:
        out.append(f"range\t0x{r['at']:08x}\t{r['len']}\t{r['owner']}\t{r['region']}\t{r['to'] or '-'}\t"
                   f"{r['before'].hex().upper() or '-'}\t{r['after'].hex().upper() or '-'}")
    for r in led.imports:
        out.append(f"import\t0x{r['at']:08x}\t{r['owner']}\t{r['name']}\t{r['to'] or '-'}\t"
                   f"{r['before'].hex().upper()}\t{r['after'].hex().upper()}")
    out += [f"conflict\t0x{r['at']:08x}\t{r['len']}\t{r['first']}\t{r['then']}" for r in led.conflicts]
    out += [f"chain\t0x{r['at']:08x}\t{r['len']}\t{r['first']}\t{r['then']}" for r in led.chains]
    if led.late is not None:
        out.append(f"late\t{led.late['seconds']}\t{led.late['changes']}")
    if led.end is not None:
        e = led.end
        out.append(f"end\t{e['ranges']}\t{e['imports']}\t{e['conflicts']}\t{e['chains']}\t{e['dropped']}")
    return "\r\n".join(out) + "\r\n"


def read(game_root: Path) -> Ledger | None:
    """The last session's ledger of the game in game_root, or None when the loader wrote none."""
    p = Path(game_root) / "riftstone" / "logs" / FILE
    if not p.is_file():
        return None
    return parse(p.read_text(encoding="utf-8", errors="replace"))


def owner_at(led: Ledger, address: int) -> str | None:
    """Who last changed the game's code at address, as a crash report's "patched by" line says it."""
    for r in reversed(led.ranges):
        if r["at"] <= address < r["at"] + r["len"]:
            return led.owner_name(r["owner"])
    return None


def by_owner(led: Ledger) -> list[dict]:
    """Per owner that changed anything: its kind and name, code places and bytes, import slots."""
    rows = [{"owner": i, "kind": o["kind"], "name": o["name"], "places": 0, "bytes": 0, "imports": 0}
            for i, o in enumerate(led.owners)]
    for r in led.ranges:
        rows[r["owner"]]["places"] += 1
        rows[r["owner"]]["bytes"] += r["len"]
    for r in led.imports:
        rows[r["owner"]]["imports"] += 1
    return [r for r in rows if r["places"] or r["imports"]]


def summary(led: Ledger) -> list[str]:
    """What the ledger says, line by line, in words."""
    out = []
    code = sum(r["len"] for r in led.ranges)
    out.append(f"{led.game}: {len(led.ranges)} change(s) to its code ({code:,} bytes) and {len(led.imports)} import "
               f"slot(s) hooked, by {len(by_owner(led))} module(s); {len(led.conflicts)} conflict(s)"
               + ("" if led.complete else "; the file is not complete (the loader did not finish writing it)"))
    for row in by_owner(led):
        parts = []
        if row["places"]:
            parts.append(f"{row['places']} place(s), {row['bytes']:,} byte(s) of code")
        if row["imports"]:
            parts.append(f"{row['imports']} import(s)")
        out.append(f"{row['name']} ({row['kind']}): " + ", ".join(parts))
    for c in led.conflicts:
        out.append(f"CONFLICT at 0x{c['at']:08x} ({c['len']} bytes): {led.owner_name(c['then'])} changed bytes "
                   f"{led.owner_name(c['first'])} had changed; {led.owner_name(c['then'])}'s are what runs")
    names = {r["at"]: r["name"] for r in led.imports}
    for c in led.chains:
        slot = names.get(c["at"]) or f"0x{c['at']:08x}"
        out.append(f"chain on {slot}: {led.owner_name(c['first'])} hooked it, then "
                   f"{led.owner_name(c['then'])} (the game calls {led.owner_name(c['then'])}'s first)")
    if led.late is not None:
        out.append(f"late scan {led.late['seconds']} s after start: {led.late['changes']} change(s) since start-up")
    if led.end and led.end["dropped"]:
        out.append(f"{led.end['dropped']} change(s) past what the ledger keeps were counted, not written down")
    return out
