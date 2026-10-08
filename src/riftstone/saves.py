"""DDDA.sav: the save file, every copy of it, and putting one back.

The game keeps one save per Steam account, written through Steam's remote storage to
``Steam\\userdata\\<account>\\367500\\remote\\DDDA.sav``.  Measured on a real save (2026-09-25):
it is always 524,288 bytes -- a 32-byte header, the save as zlib-compressed XML (about 20 MB
unpacked), then zeros.  The header, little-endian 32-bit words:

    +0   version (21 = Dark Arisen)     +16  0
    +4   XML size                       +20  0x334D4044
    +8   compressed size                +24  the compressed bytes' CRC-32, inverted
    +12  0x334D234D                     +28  0x40565235

Two kinds of copies, kept apart (``backups`` lists both):

* the loader copies the whole save folder when the game starts (and ``riftstone saves backup`` does
  by hand) to ``%LOCALAPPDATA%\\Riftstone\\saves\\DDDA\\<account>\\<YYYYMMDD-HHMMSS>\\``
  (runtime.backup_saves / restore_save);
* the save_backup plugin (native/plugins/save_backup) copies every complete new save while the game
  runs to ``<folder>\\<account>\\DDDA_<date>_<time>.sav`` (the save's own time), and writes the copy
  each game session started from into ``sessions.txt``.  <folder> is save_backup.ini's Folder, by
  default ``<home>\\saves`` (%LOCALAPPDATA%\\Riftstone, or $RIFTSTONE_HOME).

This module reads and checks saves and copies, makes a copy by hand and puts either kind back.  It
only writes a save in ``restore``/``restore_backup``, never while the game runs, never from a copy
that does not check all the way through, and keeps what it replaces.
"""
from __future__ import annotations

import configparser
import datetime as _dt
import math
import os
import re
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .errors import FormatError, RiftError
from .runtime import ini_text

SIZE = 524_288
HEADER = 32
VERSION = 21
MAX_XML = 64 << 20  # a real save unpacks to about 20 MB; a header claiming far more is refused
_WORDS = (0x334D234D, 0, 0x334D4044, 0x40565235)
APP_ID = "367500"
NAME = re.compile(r"DDDA_(\d{4})-(\d\d)-(\d\d)_(\d\d)-(\d\d)-(\d\d)(?:_(\d{1,2}))?\.sav", re.IGNORECASE)


@dataclass(frozen=True)
class Header:
    version: int
    xml_size: int
    packed_size: int
    checksum: int


def check(data: bytes, deep: bool = False) -> Header:
    """The header of a complete save, or FormatError saying what is wrong.  ``deep`` also unpacks the
    XML and checks it has the size the header states."""
    if len(data) != SIZE:
        raise FormatError("save", f"it is {len(data):,} bytes; a DDDA.sav is always {SIZE:,}")
    version, xml_size, packed, w1, w2, w3, crc, w4 = struct.unpack_from("<8I", data, 0)
    if (w1, w2, w3, w4) != _WORDS:
        raise FormatError("save", "the header is not a Dragon's Dogma save header", 12)
    if version != VERSION:
        raise FormatError("save", f"version {version}, not {VERSION} (Dark Arisen)", 0)
    if not 0 < xml_size <= MAX_XML or not 2 <= packed <= SIZE - HEADER:
        raise FormatError("save", "its sizes are out of range", 4)
    if zlib.crc32(data[HEADER:HEADER + packed]) ^ 0xFFFFFFFF != crc:
        raise FormatError("save", "its checksum does not match: the file is damaged or was written only in part", 24)
    header = Header(version, xml_size, packed, crc)
    if deep:
        unpack(data, header)
    return header


def unpack(data: bytes, header: Header | None = None) -> bytes:
    """The save as XML."""
    h = header or check(data)
    d = zlib.decompressobj()
    try:
        xml = d.decompress(data[HEADER:HEADER + h.packed_size], h.xml_size + 1)
    except zlib.error as e:
        raise FormatError("save", f"the compressed save does not unpack ({e})", HEADER) from None
    if len(xml) != h.xml_size or not d.eof:
        raise FormatError("save", f"it unpacks to {len(xml):,} bytes or more, not the {h.xml_size:,} its header states", 4)
    return xml


def pack(xml: bytes, level: int = 3) -> bytes:
    """A complete save holding ``xml``: header, zlib data, zeros to 524,288 bytes.  The game compresses
    at zlib level 3 (the real save's stream is level 3's output byte for byte), so packing a save's own
    XML gives back the game's file; XML that does not fit at 3 is tried at 9 before it is refused."""
    if not 0 < len(xml) <= MAX_XML:
        raise RiftError(f"a save's XML is 1 to {MAX_XML:,} bytes, not {len(xml):,}")
    packed = zlib.compress(xml, level)
    if len(packed) > SIZE - HEADER and level < 9:
        packed = zlib.compress(xml, 9)
    if len(packed) > SIZE - HEADER:
        raise RiftError(f"the XML compresses to {len(packed):,} bytes; a save holds at most {SIZE - HEADER:,}")
    crc = zlib.crc32(packed) ^ 0xFFFFFFFF
    head = struct.pack("<8I", VERSION, len(xml), len(packed), _WORDS[0], _WORDS[1], _WORDS[2], crc, _WORDS[3])
    return head + packed + bytes(SIZE - HEADER - len(packed))


# ---- where the saves and the copies are ---------------------------------------------------------

def steam_saves(roots: list[Path] | None = None) -> list[tuple[str, Path]]:
    """(account, path) of every Steam account's DDDA.sav.  Roots: $RIFTSTONE_STEAM_ROOT (the plugin
    reads it too), else Steam's install folders."""
    if roots is None:
        over = os.environ.get("RIFTSTONE_STEAM_ROOT")
        if over:
            roots = [Path(over)]
        else:
            from .game import steam_roots

            roots = steam_roots()
    found: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for root in roots:
        userdata = Path(root) / "userdata"
        if not userdata.is_dir():
            continue
        for account in sorted(userdata.iterdir()):
            name = account.name
            if not (name.isascii() and name.isdigit()) or name in seen:
                continue
            save = account / APP_ID / "remote" / "DDDA.sav"
            if save.is_file():
                seen.add(name)
                found.append((name, save))
    return found


def _ini_folder(ini: Path) -> str | None:
    """save_backup.ini's Folder as the plugin reads it (in the code page: runtime.ini_text)."""
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        cp.read_string(ini_text(ini.read_bytes()))
    except (OSError, configparser.Error):
        return None
    value = cp.get("backup", "Folder", fallback="").strip()
    if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":   # Windows drops a pair of quotes
        value = value[1:-1].strip()
    return None if not value or value.lower() == "auto" else os.path.expandvars(value)


def backup_root(game=None) -> Path:
    """Where the copies are: the installed plugin's Folder setting, else <home>\\saves."""
    if game is not None:
        folder = _ini_folder(game.state_dir / "plugins" / "save_backup.ini")
        if folder:
            return Path(folder)
    from .index import home

    return home() / "saves"


@dataclass(frozen=True)
class Copy:
    account: str
    path: Path
    time: _dt.datetime | None  # the save's own time, from the name
    session_start: bool        # a game session started from this save (sessions.txt)

    @property
    def name(self) -> str:
        return self.path.name


def _time_of(name: str) -> _dt.datetime | None:
    m = NAME.fullmatch(name)
    if not m:
        return None
    try:
        return _dt.datetime(*(int(g) for g in m.groups()[:6]))
    except ValueError:
        return None


def session_starts(folder: Path) -> list[str]:
    """The copy each recorded game session started from, oldest first (sessions.txt's last words)."""
    try:
        text = (folder / "sessions.txt").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [line.split()[-1] for line in text.splitlines() if line.split()]


def copies(root: Path, account: str | None = None) -> list[Copy]:
    """The copies under ``root``, grouped by account, newest first within each."""
    found: list[Copy] = []
    if not root.is_dir():
        return found
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        if account is not None and folder.name != account:
            continue
        starts = set(session_starts(folder))
        names = sorted((p for p in folder.iterdir() if p.is_file() and NAME.fullmatch(p.name)),
                       key=lambda p: p.name, reverse=True)
        found += [Copy(folder.name, p, _time_of(p.name), p.name in starts) for p in names]
    return found


# ---- every copy: the loader's (the whole save folder) and the plugin's (each save while playing) ----

FOLDER, BEFORE_RESTORE, IN_PLAY = "save folder", "before a restore", "while playing"
_STAMP = re.compile(r"(\d{4})(\d\d)(\d\d)-(\d\d)(\d\d)(\d\d)(?:-[\w-]+)?")


@dataclass(frozen=True)
class Backup:
    """One copy of an account's save.  ``kind`` is FOLDER (the whole save folder, copied by the loader
    when the game starts or by ``riftstone saves backup``), BEFORE_RESTORE (the folder as it was before
    a restore) or IN_PLAY (one save, copied while playing by the save_backup plugin)."""
    account: str
    kind: str
    path: Path                  # the folder, or the .sav file for IN_PLAY
    time: _dt.datetime | None
    session_start: bool = False

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def save(self) -> Path:
        return self.path if self.kind == IN_PLAY else self.path / "DDDA.sav"


def loader_root() -> Path:
    """Where the loader keeps its copies of the save folder."""
    from .runtime import save_backup_root

    return save_backup_root()


def _stamp_time(stamp: str) -> _dt.datetime | None:
    m = _STAMP.fullmatch(stamp)
    if not m:
        return None
    try:
        return _dt.datetime(*(int(g) for g in m.groups()))
    except ValueError:
        return None


def backups(plugin_root: Path, folders_root: Path | None = None, account: str | None = None) -> list[Backup]:
    """Every copy of every account's save: accounts in name order, newest first within each."""
    from .runtime import list_save_backups

    found = [Backup(c.account, IN_PLAY, c.path, c.time, c.session_start) for c in copies(plugin_root, account)]
    for r in list_save_backups(folders_root or loader_root()):
        if account is None or r["account"] == account:
            kind = BEFORE_RESTORE if r["stamp"].endswith("before-restore") else FOLDER
            found.append(Backup(r["account"], kind, r["path"], _stamp_time(r["stamp"])))
    found.sort(key=lambda b: (b.time or _dt.datetime.min, b.name), reverse=True)
    found.sort(key=lambda b: b.account)  # stable: newest first stays within each account
    return found


def pick(listed: list[Backup], which: str, account: str | None = None) -> Backup:
    """A copy by its number among its account's copies (1 = the newest) or by its name."""
    if account is not None:
        listed = [b for b in listed if b.account == account]
    if not listed:
        raise RiftError("no copies of the save" + (f" for account {account}" if account else "")
                        + "; 'riftstone saves list' shows where they are looked for")
    named = [b for b in listed if b.name.lower() == which.lower()]
    if len(named) == 1:
        return named[0]
    accounts = sorted({b.account for b in listed})
    if len(accounts) > 1:
        raise RiftError(f"copies of several accounts ({', '.join(accounts)}); choose one with --account")
    if which.isascii() and which.isdigit():
        n = int(which) if len(which) <= 9 else 0      # int() refuses more than 4,300 digits
        if not 1 <= n <= len(listed):
            raise RiftError(f"there are {len(listed)} copies; pick 1 (the newest) to {len(listed)}")
        return listed[n - 1]
    raise RiftError(f"no copy named {which!r}; 'riftstone saves list' shows them")


def _stamp(t: float) -> str:
    return _dt.datetime.fromtimestamp(t).strftime("%Y-%m-%d_%H-%M-%S")


def backup(save: Path, root: Path, account: str) -> tuple[Path, bool]:
    """Copy the save as the plugin does: named by its time, skipped when the newest copy (or a copy
    of that name) already holds the same bytes.  Returns the copy and whether it is new."""
    data = save.read_bytes()
    check(data)
    folder = root / account
    folder.mkdir(parents=True, exist_ok=True)
    existing = sorted(p for p in folder.iterdir() if p.is_file() and NAME.fullmatch(p.name))
    if existing and existing[-1].read_bytes() == data:
        return existing[-1], False
    st = save.stat()
    stamp = _stamp(st.st_mtime)
    for n in range(1, 100):
        target = folder / (f"DDDA_{stamp}.sav" if n == 1 else f"DDDA_{stamp}_{n}.sav")
        if target.exists():
            if target.read_bytes() == data:
                return target, False
            continue
        tmp = target.with_name(target.name + ".tmp")
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.utime(tmp, (st.st_atime, st.st_mtime))
        try:
            os.rename(tmp, target)  # never over an existing copy
        except FileExistsError:
            tmp.unlink()
            continue
        return target, True
    raise RiftError(f"too many copies named DDDA_{stamp}*.sav in {folder}")


def _refuse_while_running(game_running: Callable[[], bool] | None) -> None:
    if game_running is None:
        from .game import KINDS
        from .install import exe_running

        def game_running() -> bool:
            return exe_running(KINDS["ddda"]["exe"])
    if game_running():
        raise RiftError("Dragon's Dogma is running. Close it first: the game would write over the restored save.")


def _complete(path: Path, what: str) -> bytes:
    """The bytes of a save that checks all the way through, or RiftError: nothing is changed."""
    try:
        data = path.read_bytes()
    except OSError:
        raise RiftError(f"{what} has no readable {path.name}; nothing was changed") from None
    try:
        check(data, deep=True)
    except FormatError as e:
        raise RiftError(f"{what} is not a complete save ({e}); nothing was changed") from None
    return data


def restore_backup(b: Backup, save: Path, plugin_root: Path,
                   game_running: Callable[[], bool] | None = None) -> Path | None:
    """Put a copy back.  A copy of the save folder puts back every file of the folder; a copy made
    while playing puts back the save.  Refuses while the game runs and when the copy's save is not
    complete.  What it replaces is kept first; returns that (None when the save already matched)."""
    _refuse_while_running(game_running)
    _complete(b.save, b.name)
    if b.kind == IN_PLAY:
        return restore(b.path, save, plugin_root, b.account, game_running=lambda: False)
    from .runtime import restore_save

    return restore_save(b.path, save.parent, game_running=False)


def restore(copy: Path, save: Path, root: Path, account: str,
            game_running: Callable[[], bool] | None = None) -> Path | None:
    """Put a copy back as the save.  Refuses while the game runs (it would write over it) and when
    the copy is not a complete save.  The save it replaces is copied first; returns that copy."""
    _refuse_while_running(game_running)
    data = _complete(copy, copy.name)
    kept = None
    if save.is_file():
        current = save.read_bytes()
        if current == data:
            return None
        try:
            kept, _ = backup(save, root, account)
        except FormatError:
            # The current save is damaged; keep its bytes anyway, under a name `copies` leaves out.
            folder = root / account
            folder.mkdir(parents=True, exist_ok=True)
            kept = folder / f"replaced_{_stamp(save.stat().st_mtime)}.sav"
            kept.write_bytes(current)
    _write_save(save, data)
    return kept


def _write_save(save: Path, data: bytes) -> None:
    """Write next to the save, then swap it in with one rename."""
    save.parent.mkdir(parents=True, exist_ok=True)
    tmp = save.with_name(save.name + ".riftstone-tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, save)


# ---- the main pawn's knowledge of enemies ----------------------------------------------------------
#
# Each pawn counts, per enemy group (72), the frames it has spent fighting that group and its kills,
# and per special feat (116 slots) how often it has seen or done it.  Every frame the game turns the
# counters into knowledge levels 1-5 by fixed tables (PS3 sAIStudyCtrl::move 0x00020A9C runs
# updateStudyFlagCommon / updateStudyFlagUnique).  The enemy-knowledge books raise the counters with
# the game's own grant routines, which read the same tables; on the PC (build 2364871):
#   0x00417040 setStudyCommonData: an entry's group (entries), then a level's seconds x frames per
#              second and kills (the frames and kill counters only ever go up)
#   0x004170C0 setStudyUniqueData: a feat's slot and count (feats)
# The tables are game data, so they are read from the installed exe, never stored here, and only after
# the code that reads them is where this build has it.

KNOWLEDGE_CODE = (
    (0x00417040, bytes.fromhex("B9E8B65301")),               # mov ecx, entries
    (0x00417051, bytes.fromhex("3DE0020000")),               # cmp eax, 0x2E0: 92 entries of 8 bytes
    (0x00417084, bytes.fromhex("F30F108400C8B95301")),       # movss xmm0, seconds[group][level]
    (0x0041708D, bytes.fromhex("F30F590560A14E01")),         # mulss xmm0, frames per second
    (0x004170A5, bytes.fromhex("8B8068BF5301")),             # mov eax, kills[group][level]
    (0x004170D9, bytes.fromhex("B808C55301")),               # mov eax, feats
    (0x004170F9, bytes.fromhex("81F94C050000")),             # cmp ecx, 0x54C: 113 feats of 12 bytes
)
_ENTRIES, _SECONDS, _KILLS, _FEATS, _FPS = 0x0153B6E8, 0x0153B9C8, 0x0153BF68, 0x0153C508, 0x014EA160
GROUPS, LEVELS, FEAT_SLOTS = 72, 5, 116
_ENTRY_COUNT, _FEAT_COUNT = 92, 113   # the routines' own loop bounds (0x2E0 / 8, 0x54C / 12)
_COUNTERS = (("mStudyData.EncountFrame", "f32", GROUPS), ("mStudyData.KillCnt", "u32", GROUPS),
             ("mStudyData.UniqueCnt", "u8", FEAT_SLOTS))


@dataclass(frozen=True)
class KnowledgeTables:
    seconds: tuple[tuple[float, ...], ...]   # [group][level - 1]
    kills: tuple[tuple[int, ...], ...]       # [group][level - 1]
    feats: tuple[tuple[int, int], ...]       # (feat slot, count needed)
    groups: frozenset[int]                   # the groups some enemy counts toward
    fps: float


def _pe_reader(image: bytes) -> Callable[[int, int], bytes]:
    """read(va, n) over a PE file's sections, as the loader maps them (past raw data reads as zero)."""
    try:
        pe = struct.unpack_from("<I", image, 0x3C)[0]
        if image[pe:pe + 4] != b"PE\0\0":
            raise ValueError
        count = struct.unpack_from("<H", image, pe + 6)[0]
        optional = struct.unpack_from("<H", image, pe + 20)[0]
        base = struct.unpack_from("<I", image, pe + 0x34)[0]
        sections = [struct.unpack_from("<IIII", image, pe + 24 + optional + 40 * i + 8) for i in range(count)]
    except (struct.error, ValueError):
        raise RiftError("not a Windows program") from None

    def read(va: int, n: int) -> bytes:
        for vsize, rva, rsize, raw in sections:
            start = base + rva
            if start <= va and va + n <= start + max(vsize, rsize):
                rel = va - start
                got = image[raw + rel: raw + min(rel + n, rsize)] if rel < rsize else b""
                return got + bytes(n - len(got))
        raise RiftError(f"0x{va:08X} is not in the program")
    return read


def knowledge_tables(exe: Path) -> KnowledgeTables:
    """The game's knowledge thresholds, read from DDDA.exe (build 2364871 only)."""
    try:
        read = _pe_reader(Path(exe).read_bytes())
    except OSError as e:
        raise RiftError(f"cannot read {exe}: {e}") from None
    for at, want in KNOWLEDGE_CODE:
        if read(at, len(want)) != want:
            raise RiftError(f"{Path(exe).name} is not the build whose knowledge tables Riftstone knows "
                            f"(the code at 0x{at:08X} differs); nothing was read")
    entries = [struct.unpack("<II", read(_ENTRIES + 8 * i, 8)) for i in range(_ENTRY_COUNT)]
    groups = frozenset(g for _, g in entries if g < GROUPS)
    seconds = tuple(struct.unpack("<5f", read(_SECONDS + 20 * g, 20)) for g in range(GROUPS))
    kills = tuple(struct.unpack("<5I", read(_KILLS + 20 * g, 20)) for g in range(GROUPS))
    feats = tuple((slot, need) for _, slot, need in
                  (struct.unpack("<IIB", read(_FEATS + 12 * i, 9)) for i in range(_FEAT_COUNT)))
    fps = struct.unpack("<f", read(_FPS, 4))[0]
    if not (fps > 0 and all(slot < FEAT_SLOTS for slot, _ in feats)
            and all(s >= 0 and math.isfinite(_f32(s * fps)) for row in seconds for s in row)):
        raise RiftError(f"{Path(exe).name}: the knowledge tables do not read as expected; nothing was read")
    return KnowledgeTables(seconds, kills, feats, groups, fps)


_TAG = re.compile(rb"<(/?)(class|array)\b([^>]*?)(/?)>")
_ATTR_NAME = re.compile(rb'\bname="([^"]*)"')
_VALUE = re.compile(rb'<(f32|u32|u8|s16|s32) value="([^"]*)"/>')
# how the game writes each counter (every value in a real save matches: floats "%.6f", counts in digits)
_WRITTEN = {"f32": re.compile(rb"-?[0-9]+\.[0-9]{6}"), "u32": re.compile(rb"[0-9]{1,10}"), "s16": re.compile(rb"-?[0-9]{1,5}"),
            "s32": re.compile(rb"-?[0-9]{1,10}"),
            "u8": re.compile(rb"[0-9]{1,3}")}
_LIMIT = {"u32": 0xFFFFFFFF, "u8": 0xFF, "s16": 0x7FFF, "s32": 0x7FFFFFFF}
_FLOOR = {"s16": -0x8000, "s32": -0x80000000}
_ONLY = {k: re.compile(rb'(?:\s*<' + k.encode() + rb' value="[^"]*"/>)*\s*') for k in _WRITTEN}  # an array's body


def _f32(x: float) -> float:
    """x as a 32-bit float holds it (too large for one: infinity)."""
    try:
        return struct.unpack("<f", struct.pack("<f", x))[0]
    except OverflowError:
        return math.copysign(math.inf, x)


def _main_pawns(xml: bytes) -> list[tuple[str, int, int]]:
    """(copy, start, end) of the main pawn's record (mPawnType 1) in each of the two copies of the
    player's data a save holds, mPlayerDataManual and mPlayerDataBase, in file order."""
    stack: list[tuple[bytes, int, bytes]] = []    # (name, start, attributes)
    found = []
    for m in _TAG.finditer(xml):
        closing, _tag, attrs, empty = m.groups()
        if empty:
            continue
        if not closing:
            n = _ATTR_NAME.search(attrs)
            stack.append((n.group(1) if n else b"", m.start(), attrs))
            continue
        if not stack:
            raise FormatError("save", "its XML closes more elements than it opens")
        _name, start, attrs = stack.pop()
        if (b'type="cSAVE_DATA_CMC"' in attrs and len(stack) >= 3 and stack[-1][0] == b"mCmc"
                and stack[-2][0] == b"mPlCmcEditAndParam"
                and stack[-3][0] in (b"mPlayerDataManual", b"mPlayerDataBase")
                and b'<s32 name="mPawnType" value="1"/>' in xml[start:m.end()]):
            if found and found[-1][1] > start:   # records close in order, so a nested one ends inside this
                raise FormatError("save", "a pawn record holds another pawn record, which the game never writes")
            found.append((stack[-3][0].decode(), start, m.end()))
    if stack:
        raise FormatError("save", "its XML leaves elements open")
    return found


def _counters(xml: bytes, start: int, end: int, name: str, kind: str, count: int) -> list[tuple[int, int, bytes]]:
    """(value start, value end, text) of each element of one counter array inside a pawn's record."""
    return _values(xml, start, end, name, kind, count, "the main pawn's")


def _written(text: bytes, kind: str, name: str, whose: str) -> None:
    """Refuse a value the game would not write."""
    if (not _WRITTEN[kind].fullmatch(text) or (kind in _LIMIT and int(text) > _LIMIT[kind])
            or (kind in _FLOOR and int(text) < _FLOOR[kind])):
        raise FormatError("save", f"{whose} {name} holds {text.decode('latin-1')!r}, "
                                  f"which is not a {kind} value as the game writes one")


def _values(xml: bytes, start: int, end: int, name: str, kind: str, count: int,
            whose: str) -> list[tuple[int, int, bytes]]:
    """(value start, value end, text) of each element of one array inside a record."""
    head = f'<array name="{name}" type="{kind}" count="{count}">'.encode()
    at = xml.find(head, start, end)
    close = xml.find(b"</array>", at, end) if at >= 0 else -1
    if at < 0 or close < 0:
        raise FormatError("save", f"{whose} record has no {name} of {count} {kind} values")
    if not _ONLY[kind].fullmatch(xml, at + len(head), close):
        raise FormatError("save", f"{whose} {name} holds something besides {kind} values")
    vals = [(m.start(2), m.end(2), m.group(2)) for m in _VALUE.finditer(xml, at + len(head), close)]
    if len(vals) != count:
        raise FormatError("save", f"{whose} {name} holds {len(vals)} values, not {count}")
    for _, _, text in vals:
        _written(text, kind, name, whose)
    return vals


def _scalar(xml: bytes, start: int, end: int, name: str, kind: str, whose: str) -> tuple[int, int, bytes]:
    """(value start, value end, text) of one value inside a record."""
    head = f'<{kind} name="{name}" value="'.encode()
    at = xml.find(head, start, end)
    close = xml.find(b'"/>', at, end) if at >= 0 else -1
    if at < 0 or close < 0:
        raise FormatError("save", f"{whose} record has no {kind} {name}")
    text = xml[at + len(head):close]
    _written(text, kind, name, whose)
    return at + len(head), close, text


def _number(text: bytes, kind: str) -> float | int:
    """A counter's value as the game holds it."""
    return _f32(float(text)) if kind == "f32" else int(text)


def _frames_needed(t: KnowledgeTables, g: int, level: int) -> float:
    """The game's own product (mulss): a level's seconds x frames per second, rounded to a 32-bit float."""
    return _f32(t.seconds[g][level - 1] * t.fps)


def _level(t: KnowledgeTables, g: int, frames: float, kills: int) -> int:
    return sum(1 for lv in range(1, LEVELS + 1)
               if frames >= _frames_needed(t, g, lv) and kills >= t.kills[g][lv - 1])


def _frames_text(frames: float) -> bytes:
    """Frames the way the game writes them ("%.6f"), never below the value they stand for."""
    text = f"{frames:.6f}"
    while _f32(float(text)) < frames:
        text = f"{float(text) + 0.000001:.6f}"
    return text.encode()


def knowledge(data: bytes, t: KnowledgeTables) -> dict:
    """What the main pawn's counters allow, read from mPlayerDataManual: each enemy group's level (0-5)
    and how many special feats are reached.  The levels the pawn shows also come from books, and the
    game awards what the counters allow the next time the pawn is with you."""
    xml = unpack(data)
    pawns = _main_pawns(xml)
    if not pawns:
        raise RiftError("this save has no main pawn (a new game before the pawn was made?)")
    _copy, s, e = next((p for p in pawns if p[0] == "mPlayerDataManual"), pawns[0])
    frames, kills, seen = ([_number(v, c[1]) for _, _, v in _counters(xml, s, e, *c)] for c in _COUNTERS)
    levels = {g: _level(t, g, frames[g], kills[g]) for g in sorted(t.groups)}
    reached = sum(1 for slot, need in t.feats if seen[slot] >= need)
    return {"levels": levels, "feats": (reached, len(t.feats)),
            "complete": sum(1 for v in levels.values() if v == LEVELS)}


def grant_knowledge(data: bytes, t: KnowledgeTables) -> tuple[bytes, int]:
    """The save with the main pawn's counters raised to the top level of every enemy group and to
    every special feat, in both copies of the player's data (counters only go up; nothing else in the
    XML changes).  Returns the new save and how many values changed."""
    xml = unpack(data)
    pawns = _main_pawns(xml)
    if not pawns:
        raise RiftError("this save has no main pawn (a new game before the pawn was made?)")
    edits: list[tuple[int, int, bytes]] = []
    for _copy, s, e in pawns:
        frames, kills, seen = (_counters(xml, s, e, *c) for c in _COUNTERS)
        for g in sorted(t.groups):   # the most any level asks (the game's tables rise level by level)
            top, most = max(_frames_needed(t, g, lv) for lv in range(1, LEVELS + 1)), max(t.kills[g])
            if _number(frames[g][2], "f32") < top:
                edits.append((frames[g][0], frames[g][1], _frames_text(top)))
            if int(kills[g][2]) < most:
                edits.append((kills[g][0], kills[g][1], str(most).encode()))
        need: dict[int, int] = {}
        for slot, count in t.feats:
            need[slot] = max(need.get(slot, 0), count)
        for slot, count in sorted(need.items()):
            if int(seen[slot][2]) < count:
                edits.append((seen[slot][0], seen[slot][1], str(count).encode()))
    if not edits:
        return data, 0
    out, at = [], 0
    for s, e, text in sorted(edits):
        out += [xml[at:s], text]
        at = e
    out.append(xml[at:])
    return pack(b"".join(out)), len(edits)


def grant_knowledge_file(save: Path, root: Path, account: str, t: KnowledgeTables,
                         game_running: Callable[[], bool] | None = None) -> tuple[Path | None, int]:
    """Raise the main pawn's knowledge counters in a save file.  Refuses while the game runs and on a
    save that does not check; the save as it was is kept as a copy first (returned with the count of
    values changed; nothing is written when there is nothing to raise)."""
    _refuse_while_running(game_running)
    data = _complete(save, save.name)
    new, n = grant_knowledge(data, t)
    if not n:
        return None, 0
    check(new, deep=True)
    kept, _ = backup(save, root, account)
    _write_save(save, new)
    return kept, n


# --- the Arisen: level, vocation rank, discipline, stats and skills (`riftstone saves arisen`) -----------------------

#: the weapon palettes a character record keeps (`mWeaponSkill[nWeapon::X]`, six skill numbers each, -1 = empty),
#: in the save's own order; the game's weapon category is the index + 1 (category 0 is no weapon)
WEAPONS = ("SWORD", "MACE", "GSWORD", "DAGGER", "WAND", "WAND_DX", "HAMMER", "SHIELD", "SHIELD_L", "BOW", "BOW_L",
           "BOW_MG")
WEAPON_NAMES = {"SWORD": "sword", "MACE": "mace", "GSWORD": "longsword", "DAGGER": "daggers", "WAND": "staff",
                "WAND_DX": "archistaff", "HAMMER": "warhammer", "SHIELD": "shield", "SHIELD_L": "magick shield",
                "BOW": "shortbow", "BOW_L": "longbow", "BOW_MG": "magick bow"}
#: the vocations by the game's number (`mJob`) and the weapons each one wields
VOCATIONS = {1: ("Fighter", ("SWORD", "SHIELD")), 2: ("Strider", ("DAGGER", "BOW")), 3: ("Mage", ("WAND",)),
             4: ("Assassin", ("SWORD", "DAGGER", "SHIELD", "BOW")), 5: ("Magick Archer", ("DAGGER", "BOW_MG")),
             6: ("Mystic Knight", ("SWORD", "MACE", "SHIELD_L")), 7: ("Warrior", ("GSWORD", "HAMMER")),
             8: ("Ranger", ("DAGGER", "BOW_L")), 9: ("Sorcerer", ("WAND_DX",))}
LEVEL_MAX = 200          # the game's level cap
RANK_MAX = 9             # a vocation's top rank
VOCATION_SLOTS = 10      # entries of mJobLevel / mJobExp / mJobNextExp / mJobPoint (by vocation number; 0 unused)
PALETTE = 6              # skills a weapon palette holds
SKILL_WORDS = 14         # u32 words of mSkillLv1 / mSkillLv2: one bit per skill number (word n // 32, bit n % 32)
POINTS_MAX = 0x7FFFFFFF  # mJobPoint is s32
#: the f32 fields of the record a `--stat` may set
STATS = ("mHp", "mHpMax", "mHpMaxWhite", "mStamina", "mStaminaLv", "mBasicAttack", "mBasicDefend", "mBasicMgcAttack",
         "mBasicMgcDefend")
_COPIES = (b"mPlayerDataManual", b"mPlayerDataBase")


@dataclass(frozen=True)
class SkillTables:
    """Each weapon category's first skill number and how many it has (13 categories; 0 is no weapon), as
    `removeIllegalCstmSkill` reads them from DDDA.exe."""
    first: tuple[int, ...]
    count: tuple[int, ...]

    def numbers(self, weapon: str) -> range:
        c = WEAPONS.index(weapon) + 1
        return range(self.first[c], self.first[c] + self.count[c])


SKILL_CODE = ((0x00780609, bytes.fromhex("8B3485806C4F01")),   # mov esi, [eax*4 + 0x14F6C80]: the first number
              (0x00780614, bytes.fromhex("8B3C85B46C4F01")))   # mov edi, [eax*4 + 0x14F6CB4]: the count
_SKILL_FIRST, _SKILL_COUNT, _CATEGORIES = 0x014F6C80, 0x014F6CB4, 13


def skill_tables(exe: Path) -> SkillTables:
    """The skill numbers of each weapon category, read from DDDA.exe (build 2364871 only)."""
    try:
        read = _pe_reader(Path(exe).read_bytes())
    except OSError as e:
        raise RiftError(f"cannot read {exe}: {e}") from None
    for at, want in SKILL_CODE:
        if read(at, len(want)) != want:
            raise RiftError(f"{Path(exe).name} is not the build whose skill tables Riftstone knows "
                            f"(the code at 0x{at:08X} differs); nothing was read")
    first = struct.unpack(f"<{_CATEGORIES}I", read(_SKILL_FIRST, 4 * _CATEGORIES))
    count = struct.unpack(f"<{_CATEGORIES}I", read(_SKILL_COUNT, 4 * _CATEGORIES))
    if not all(0 < f + c <= 32 * SKILL_WORDS for f, c in zip(first, count)):
        raise RiftError(f"{Path(exe).name}: the skill tables do not read as expected; nothing was read")
    return SkillTables(first, count)


def _arisen_records(xml: bytes) -> list[tuple[str, int, int]]:
    """(copy, start, end) of the Arisen's parameter block (`mPl` > `mParam`, cSAVE_DATA_PARAM) in each copy of the
    player's data a save holds, mPlayerDataManual and mPlayerDataBase, in file order."""
    stack: list[tuple[bytes, int]] = []    # (name, start)
    found = []
    for m in _TAG.finditer(xml):
        closing, _tag, attrs, empty = m.groups()
        if empty:
            continue
        if not closing:
            n = _ATTR_NAME.search(attrs)
            stack.append((n.group(1) if n else b"", m.start()))
            continue
        if not stack:
            raise FormatError("save", "its XML closes more elements than it opens")
        name, start = stack.pop()
        if (name == b"mParam" and len(stack) >= 3 and stack[-1][0] == b"mPl"
                and stack[-2][0] == b"mPlCmcEditAndParam" and stack[-3][0] in _COPIES
                and xml.startswith(b'<class name="mParam" type="cSAVE_DATA_PARAM">', start)):
            found.append((stack[-3][0].decode(), start, m.end()))
    if stack:
        raise FormatError("save", "its XML leaves elements open")
    return found


def _read_record(xml: bytes, s: int, e: int, whose: str, tables: SkillTables | None) -> dict:
    level = int(_scalar(xml, s, e, "mLevel", "u8", whose)[2])
    job = int(_scalar(xml, s, e, "mJob", "u8", whose)[2])
    ranks = [int(t) for _, _, t in _values(xml, s, e, "mJobLevel", "u8", VOCATION_SLOTS, whose)]
    points = [int(t) for _, _, t in _values(xml, s, e, "mJobPoint", "s32", VOCATION_SLOTS, whose)]
    stats = {n: float(_scalar(xml, s, e, n, "f32", whose)[2]) for n in STATS}
    palettes = {w: [int(t) for _, _, t in _values(xml, s, e, f"mWeaponSkill[nWeapon::{w}]", "s16", PALETTE, whose)]
                for w in WEAPONS}
    lv1 = [int(t) for _, _, t in _values(xml, s, e, "mSkillLv1", "u32", SKILL_WORDS, whose)]
    lv2 = [int(t) for _, _, t in _values(xml, s, e, "mSkillLv2", "u32", SKILL_WORDS, whose)]
    name, weapons = VOCATIONS.get(job, (f"vocation {job}", ()))
    skills = {}
    for w in weapons:
        d: dict = {"equipped": palettes[w]}
        if tables is not None:
            nums = tables.numbers(w)
            d["learned"] = sum(1 for n in nums if (lv1[n // 32] | lv2[n // 32]) >> (n % 32) & 1)
            d["of"] = len(nums)
        skills[w] = d
    return {"level": level, "job": job, "vocation": name, "rank": ranks[job] if 0 <= job < VOCATION_SLOTS else None,
            "ranks": ranks, "points": points, "stats": stats, "skills": skills}


def arisen(data: bytes, tables: SkillTables | None = None) -> dict:
    """The Arisen's level, vocation and rank, discipline, stats and the palettes of the vocation's weapons (with
    how many of their skills are learned when the skill tables are given), from each copy of the player's data."""
    xml = unpack(data)
    recs = _arisen_records(xml)
    if not recs:
        raise RiftError("this save has no Arisen record (mPlayerDataManual > mPlCmcEditAndParam > mPl)")
    return {copy: _read_record(xml, s, e, f"the Arisen's ({copy})", tables) for copy, s, e in recs}


@dataclass(frozen=True)
class ArisenEdit:
    """What `set_arisen` changes; None leaves a field as it is."""
    level: int | None = None
    rank: int | None = None                 # of `vocation`; 9 also sets its next-rank EXP to 0, as the game does
    points: int | None = None               # discipline (every entry of mJobPoint, which the game keeps equal)
    stats: dict[str, float] = field(default_factory=dict)   # by field name (STATS)
    skills: bool = False                    # every skill of the weapons learned at both tiers; empty slots filled
    vocation: int | None = None             # whose rank and weapons; None = the first copy's own
    weapons: tuple[str, ...] = ()           # instead of the vocation's

    def __post_init__(self) -> None:
        if self.level is not None and not 1 <= self.level <= LEVEL_MAX:
            raise RiftError(f"level {self.level}: the game's levels are 1-{LEVEL_MAX}")
        if self.rank is not None and not 1 <= self.rank <= RANK_MAX:
            raise RiftError(f"rank {self.rank}: a vocation's ranks are 1-{RANK_MAX}")
        if self.points is not None and not 0 <= self.points <= POINTS_MAX:
            raise RiftError(f"{self.points} discipline: the save holds 0-{POINTS_MAX}")
        for name, value in self.stats.items():
            if name not in STATS:
                raise RiftError(f"{name}: not a stat of the record ({', '.join(STATS)})")
            if not (math.isfinite(value) and 0 <= value < 1e9):
                raise RiftError(f"{name} = {value}: not a value the game would hold")
        for w in self.weapons:
            if w not in WEAPONS:
                raise RiftError(f"{w}: not a weapon palette ({', '.join(WEAPONS)})")
        if self.vocation is not None and self.vocation not in VOCATIONS:
            raise RiftError(f"vocation {self.vocation}: the game's are " + ", ".join(f"{k} {v[0]}" for k, v in VOCATIONS.items()))

    @property
    def empty(self) -> bool:
        return self.level is None and self.rank is None and self.points is None and not self.stats and not self.skills


def set_arisen(data: bytes, edit: ArisenEdit, tables: SkillTables | None = None) -> tuple[bytes, int]:
    """The save with the Arisen's record changed in both copies of the player's data (only value text changes,
    written as the game writes it; everything else stays byte for byte).  Learning skills fills a palette's empty
    slots with the lowest unused numbers of its category and keeps the slots the player chose.  Returns the new
    save and how many values changed."""
    if edit.skills and tables is None:
        raise RiftError("learning skills needs the skill tables (read from DDDA.exe)")
    xml = unpack(data)
    recs = _arisen_records(xml)
    if not recs:
        raise RiftError("this save has no Arisen record (mPlayerDataManual > mPlCmcEditAndParam > mPl)")
    job = edit.vocation
    if job is None:
        job = int(_scalar(xml, recs[0][1], recs[0][2], "mJob", "u8", f"the Arisen's ({recs[0][0]})")[2])
        if job not in VOCATIONS:
            raise RiftError(f"the save's vocation {job} is not one the game has (1-{len(VOCATIONS)}); pass one")
    weapons = edit.weapons or VOCATIONS[job][1]
    edits: list[tuple[int, int, bytes]] = []

    def put(at: tuple[int, int, bytes], new: bytes) -> None:
        if at[2] != new:
            edits.append((at[0], at[1], new))

    for copy, s, e in recs:
        whose = f"the Arisen's ({copy})"
        if edit.level is not None:
            put(_scalar(xml, s, e, "mLevel", "u8", whose), str(edit.level).encode())
        if edit.rank is not None:
            put(_values(xml, s, e, "mJobLevel", "u8", VOCATION_SLOTS, whose)[job], str(edit.rank).encode())
            if edit.rank == RANK_MAX:
                put(_values(xml, s, e, "mJobNextExp", "u32", VOCATION_SLOTS, whose)[job], b"0")
        if edit.points is not None:
            for at in _values(xml, s, e, "mJobPoint", "s32", VOCATION_SLOTS, whose):
                put(at, str(edit.points).encode())
        for name, value in edit.stats.items():
            put(_scalar(xml, s, e, name, "f32", whose), f"{value:.6f}".encode())
        if edit.skills:
            lv1 = _values(xml, s, e, "mSkillLv1", "u32", SKILL_WORDS, whose)
            lv2 = _values(xml, s, e, "mSkillLv2", "u32", SKILL_WORDS, whose)
            w1, w2 = [int(t) for _, _, t in lv1], [int(t) for _, _, t in lv2]
            for w in weapons:
                nums = list(tables.numbers(w))
                for n in nums:
                    w1[n // 32] |= 1 << (n % 32)
                    w2[n // 32] |= 1 << (n % 32)
                slots = _values(xml, s, e, f"mWeaponSkill[nWeapon::{w}]", "s16", PALETTE, whose)
                have = {int(t) for _, _, t in slots}
                spare = [n for n in nums if n not in have]
                for at in slots:
                    if int(at[2]) < 0 and spare:
                        put(at, str(spare.pop(0)).encode())
            for words, at in ((w1, lv1), (w2, lv2)):
                for k, slot in enumerate(at):
                    put(slot, str(words[k]).encode())
    if not edits:
        return data, 0
    out, at = [], 0
    for s, e, text in sorted(edits):
        out += [xml[at:s], text]
        at = e
    out.append(xml[at:])
    return pack(b"".join(out)), len(edits)


def set_arisen_file(save: Path, root: Path, account: str, edit: ArisenEdit, tables: SkillTables | None = None,
                    game_running: Callable[[], bool] | None = None) -> tuple[Path | None, int]:
    """Change the Arisen's record in a save file.  Refuses while the game runs and on a save that does not check;
    the save as it was is kept as a copy first (returned with the count of values changed; nothing is written when
    there is nothing to change)."""
    _refuse_while_running(game_running)
    data = _complete(save, save.name)
    new, n = set_arisen(data, edit, tables)
    if not n:
        return None, 0
    check(new, deep=True)
    kept, _ = backup(save, root, account)
    _write_save(save, new)
    return kept, n
