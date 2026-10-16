"""Pawn Architect: the party in a save (`riftstone saves pawns`, docs/pawn-architect.md).

Each of the two copies of the player's data a save holds (``mPlayerDataManual``, ``mPlayerDataBase``) keeps the
Arisen (``mPlCmcEditAndParam`` > ``mPl``) and three pawn records (``mCmc``, ``cSAVE_DATA_CMC``): the main pawn
(``mPawnType`` 1) and the two hired slots (``mPawnType`` 0).  Each record holds its looks (``mEdit``,
cSAVE_DATA_EDIT, the same 44 fields for the Arisen and a pawn), its parameters (``mParam``, cSAVE_DATA_PARAM, with
the gear in ``mEquipItem``) and, for a pawn, who it belongs to (``mOnlinePawnInfo``: the owner's network id, the
owner's Arisen's name, level and vocation).  Measured on the owner's save (2026-10-13): a hired pawn's equipped
items carry its slot's owner number (``data.mOwnerId`` 2 and 3; the main pawn's 1, the Arisen's 0) and flag bit
0x80 (20 of 20 items; 0 of the main pawn's 10).

This module reads the party, puts a copy of the main pawn into a hired slot (as pawnlib does in memory), changes
any character's level, vocation, rank, discipline, stats and skills (``saves.ArisenEdit``), gives one character's
looks to another, and says what an online session would carry that the game did not make (``online_check``).
Every change is value text or whole records the game wrote, in both copies; everything else stays byte for
byte.  What the game does with a clone in play: UNKNOWN until played.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import saves
from .errors import FormatError, RiftError
from .saves import (_ATTR_NAME, _COPIES, _TAG, VOCATIONS, ArisenEdit, SkillTables, _gear, _scalar, _splice, _values,
                    _written, article, item_label, pack, unpack)

WHO = ("arisen", "main", "1", "2")
WHO_NAMES = {"arisen": "the Arisen", "main": "the main pawn", "1": "hired pawn 1", "2": "hired pawn 2"}
SLOTS = ("main", "1", "2")      # mCmc's records in order
NAME_BYTES = 25                 # u8 entries of a name (mNameStr, mArisenName's mEditName), NUL-terminated
NET_ID = 64                     # u8 entries of mNetUniqueId's mData
HIRED_FLAG = 0x80               # in each equipped item's data.mFlag on a hired pawn
#: the looks fields `copy_looks` leaves as they are: the name, what the character is called in speech, and the
#: personality setting, which are who the character is rather than how it looks
LOOKS_KEPT = ("mNickname", "mPersonality")
_NAMED = re.compile(rb'<(u8|u16|u32|s8|s16|s32|f32) name="([^"]+)" value="([^"]*)"/>')


@dataclass(frozen=True)
class Character:
    """Where one character's record and its parts sit in the XML."""
    copy: str
    who: str
    start: int
    end: int
    edit: tuple[int, int]       # mEdit
    param: tuple[int, int]      # mParam

    @property
    def whose(self) -> str:
        return f"{WHO_NAMES[self.who]}'s ({self.copy})"


def _record_copy(ancestors: list[tuple[bytes, int, bytes]], rec: tuple[bytes, int, bytes]) -> tuple[str, str] | None:
    """(copy, "pl" or "cmc") when ``rec`` is the Arisen's record or a pawn record of one of the two copies."""
    name, _start, attrs = rec
    names = [a[0] for a in ancestors]
    if (name == b"mPl" and b'type="cSAVE_DATA_PL"' in attrs and len(names) >= 2
            and names[-1] == b"mPlCmcEditAndParam" and names[-2] in _COPIES):
        return names[-2].decode('latin-1'), "pl"
    if (name == b"" and b'type="cSAVE_DATA_CMC"' in attrs and len(names) >= 3 and names[-1] == b"mCmc"
            and names[-2] == b"mPlCmcEditAndParam" and names[-3] in _COPIES):
        return names[-3].decode('latin-1'), "cmc"
    return None


def characters(xml: bytes) -> dict[tuple[str, str], Character]:
    """Every character of the two copies by (copy, who): the Arisen and the three pawn records, each with its
    looks and parameters, checked: one Arisen and three pawn records a copy, the first with mPawnType 1 and the
    others 0."""
    stack: list[tuple[bytes, int, bytes]] = []
    parts: dict[int, dict[str, tuple[int, int]]] = {}
    pawns: dict[str, int] = {}
    found: dict[tuple[str, str], Character] = {}
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
        name, start, attrs = stack.pop()
        if name in (b"mEdit", b"mParam") and stack and _record_copy(stack[:-1], stack[-1]):
            kind = b'type="cSAVE_DATA_EDIT"' if name == b"mEdit" else b'type="cSAVE_DATA_PARAM"'
            if kind not in attrs:
                raise FormatError("save", f"a character record's {name.decode('latin-1')} is not "
                                          f"{kind.decode('latin-1')[6:-1]}")
            got = parts.setdefault(stack[-1][1], {})
            if name.decode('latin-1') in got:
                raise FormatError("save", f"a character record holds two {name.decode('latin-1')}")
            got[name.decode('latin-1')] = (start, m.end())
            continue
        where = _record_copy(stack, (name, start, attrs))
        if where is None:
            continue
        copy, kind = where
        if kind == "pl":
            who = "arisen"
        else:
            i = pawns.get(copy, 0)
            pawns[copy] = i + 1
            if i >= len(SLOTS):
                raise FormatError("save", f"{copy}'s mCmc holds more than {len(SLOTS)} pawn records")
            who = SLOTS[i]
        got = parts.pop(start, {})
        if set(got) != {"mEdit", "mParam"}:
            raise FormatError("save", f"{WHO_NAMES[who]}'s record ({copy}) lacks its "
                                      + " and ".join(k for k in ("mEdit", "mParam") if k not in got))
        if (copy, who) in found:
            raise FormatError("save", f"{copy} holds two records of {WHO_NAMES[who]}")
        found[(copy, who)] = Character(copy, who, start, m.end(), got["mEdit"], got["mParam"])
    if stack:
        raise FormatError("save", "its XML leaves elements open")
    copies = sorted({c for c, _ in found}, key=lambda c: _COPIES.index(c.encode()))
    if not copies:
        raise RiftError("this save has no party (mPlayerDataManual > mPlCmcEditAndParam: the Arisen and mCmc)")
    for copy in copies:
        for who in WHO:
            if (copy, who) not in found:
                raise FormatError("save", f"{copy} has no record of {WHO_NAMES[who]}")
        for who in SLOTS:
            c = found[(copy, who)]
            pawn_type = _pawn_type(xml, c)
            if pawn_type[2] != (b"1" if who == "main" else b"0"):
                raise FormatError("save", f"{c.whose} mPawnType is {pawn_type[2].decode('latin-1')}; the game "
                                          "writes 1 for the main pawn and 0 for a hired slot")
    return dict(sorted(found.items(), key=lambda kv: (copies.index(kv[0][0]), WHO.index(kv[0][1]))))


def _pawn_type(xml: bytes, c: Character) -> tuple[int, int, bytes]:
    """A pawn record's own mPawnType (after its parameters, outside its online data)."""
    online = xml.find(b'<class name="mOnlinePawnInfo"', c.param[1], c.end)
    return _scalar(xml, c.param[1], online if online >= 0 else c.end, "mPawnType", "s32", c.whose)


def _online(xml: bytes, c: Character) -> tuple[int, int]:
    """A pawn record's mOnlinePawnInfo: from its head to the record's end (it is the record's last part)."""
    at = xml.find(b'<class name="mOnlinePawnInfo" type="cOnlinePawnInfo">', c.param[1], c.end)
    if at < 0:
        raise FormatError("save", f"{c.whose} record has no mOnlinePawnInfo")
    return at, c.end


def _owner_name(xml: bytes, c: Character) -> tuple[int, int]:
    """The owner's Arisen's name block (mArisenName, cName) of a pawn record."""
    s, e = _online(xml, c)
    at = xml.find(b'<class name="mArisenName" type="cName">', s, e)
    close = xml.find(b"</class>", at, e) if at >= 0 else -1
    if at < 0 or close < 0:
        raise FormatError("save", f"{c.whose} record has no mArisenName")
    return at, close


def _text(values: list[tuple[int, int, bytes]]) -> str:
    raw = bytes(int(t) for _, _, t in values).split(b"\0")[0]
    return raw.decode("utf-8", "replace")


def name_of(xml: bytes, c: Character) -> str:
    return _text(_values(xml, *c.edit, "(u8*)mNameStr", "u8", NAME_BYTES, c.whose))


def net_id(xml: bytes, c: Character) -> bytes:
    """The owner's network id a pawn record carries (mNetUniqueId's 64 bytes; all zero on some the Rift lent)."""
    s, e = _online(xml, c)
    return bytes(int(t) for _, _, t in _values(xml, s, e, "mData", "u8", NET_ID, c.whose))


def _read(xml: bytes, chars: dict, c: Character) -> dict:
    s, e = c.param
    job = int(_scalar(xml, s, e, "mJob", "u8", c.whose)[2])
    ranks = _values(xml, s, e, "mJobLevel", "u8", saves.VOCATION_SLOTS, c.whose)
    gear = _gear(xml, s, e, c.whose)
    r = {"who": c.who, "name": name_of(xml, c), "level": int(_scalar(xml, s, e, "mLevel", "u8", c.whose)[2]),
         "job": job, "vocation": VOCATIONS.get(job, (f"vocation {job}",))[0],
         "rank": int(ranks[job][2]) if 0 <= job < saves.VOCATION_SLOTS else None,
         "gear": sum(1 for g in gear if g.number >= 0)}
    if c.who == "arisen":
        return r
    mine = net_id(xml, chars[(c.copy, "main")])
    ident = net_id(xml, c)
    if c.who == "main":
        r["owner"] = "you"
    elif any(ident) and ident == mine:
        r["owner"], r["clone"] = "you", True
    else:
        s2, e2 = _owner_name(xml, c)
        o_s, o_e = _online(xml, c)
        r["owner"] = _text(_values(xml, s2, e2, "( u8* )mEditName", "u8", NAME_BYTES, c.whose)) or "?"
        r["owner_level"] = int(_scalar(xml, o_s, o_e, "mArisenLevel", "u32", c.whose)[2])
    r.setdefault("clone", False)
    return r


def party(data: bytes) -> dict[str, list[dict]]:
    """Each copy's characters: who, name, level, vocation and rank, how many items they wear, and for a pawn
    whose it is (``owner`` "you" for the main pawn and a clone of it, else the owner's Arisen's name)."""
    xml = unpack(data)
    chars = characters(xml)
    out: dict[str, list[dict]] = {}
    for (copy, _who), c in chars.items():
        out.setdefault(copy, []).append(_read(xml, chars, c))
    return out


def _slots(slots) -> tuple[str, ...]:
    picked = tuple(dict.fromkeys(str(s) for s in slots))
    if not picked or any(s not in ("1", "2") for s in picked):
        raise RiftError(f"hired slots are 1 and 2, not {', '.join(map(str, slots)) or 'none'}")
    return picked


def _clone_record(xml: bytes, chars: dict, copy: str, slot: str) -> bytes:
    """The main pawn's record of ``copy`` as hired slot ``slot`` holds a pawn: mPawnType 0, the slot's hire time
    kept, its gear marked as the slot's (owner number, the hired flag), and the Arisen named as its owner (name,
    what pawns call the Arisen, level, vocation and rank), as a hired pawn's record names its owner's Arisen."""
    main, dst, arisen = chars[(copy, "main")], chars[(copy, slot)], chars[(copy, "arisen")]
    edits: list[tuple[int, int, bytes]] = []

    def put(at: tuple[int, int, bytes], new: bytes) -> None:
        if at[2] != new:
            edits.append((at[0], at[1], new))

    put(_pawn_type(xml, main), b"0")
    on_s, on_e = _online(xml, main)
    hire = _scalar(xml, main.param[1], on_s, "mEmployGamePlayTime", "u32", main.whose)
    put(hire, _scalar(xml, dst.param[1], _online(xml, dst)[0], "mEmployGamePlayTime", "u32", dst.whose)[2])
    owner = str(int(slot) + 1).encode()
    for g in _gear(xml, *main.param, main.whose):
        if g.number >= 0:
            put(g.flag, str(int(g.flag[2]) | HIRED_FLAG).encode())
            put(g.owner, owner)
    a_s, a_e = arisen.param
    job = int(_scalar(xml, a_s, a_e, "mJob", "u8", arisen.whose)[2])
    ranks = _values(xml, a_s, a_e, "mJobLevel", "u8", saves.VOCATION_SLOTS, arisen.whose)
    put(_scalar(xml, on_s, on_e, "mArisenLevel", "u32", main.whose),
        _scalar(xml, a_s, a_e, "mLevel", "u8", arisen.whose)[2])
    put(_scalar(xml, on_s, on_e, "mArisenJob", "s32", main.whose), str(job).encode())
    if 0 <= job < saves.VOCATION_SLOTS:
        put(_scalar(xml, on_s, on_e, "mArisenJobLevel", "u32", main.whose), ranks[job][2])
    n_s, n_e = _owner_name(xml, main)
    for at, src in zip(_values(xml, n_s, n_e, "( u8* )mEditName", "u8", NAME_BYTES, main.whose),
                       _values(xml, *arisen.edit, "(u8*)mNameStr", "u8", NAME_BYTES, arisen.whose)):
        put(at, src[2])
    call = _scalar(xml, *arisen.edit, "mNickname", "u32", arisen.whose)[2]
    if int(call) > 0xFFFF:
        raise FormatError("save", f"{arisen.whose} mNickname {call.decode('latin-1')} does not fit a cName's "
                                  "u16 mNicknameID")
    put(_scalar(xml, n_s, n_e, "mNicknameID", "u16", main.whose), call)
    rec = xml[main.start:main.end]
    return _splice(rec, [(s - main.start, e - main.start, t) for s, e, t in edits])


def clone_main(data: bytes, slots=("1", "2")) -> tuple[bytes, int]:
    """The save with the main pawn copied into the hired slots ``slots`` of both copies (each copy's own main
    pawn, each slot's record replaced whole; see ``_clone_record``).  Returns the new save and how many records
    were replaced (0 when every slot already holds that clone)."""
    picked = _slots(slots)
    xml = unpack(data)
    chars = characters(xml)
    edits = []
    for copy in dict.fromkeys(c for c, _ in chars):
        for slot in picked:
            dst = chars[(copy, slot)]
            rec = _clone_record(xml, chars, copy, slot)
            if rec != xml[dst.start:dst.end]:
                edits.append((dst.start, dst.end, rec))
    if not edits:
        return data, 0
    return pack(_splice(xml, edits)), len(edits)


def set_character(data: bytes, who: str, edit: ArisenEdit, tables: SkillTables | None = None,
                  equip_jobs: dict[int, int] | None = None, names: dict[int, str] | None = None) -> tuple[bytes, int]:
    """``saves.set_arisen`` for any character: the Arisen, the main pawn or a hired slot, in both copies.  A pawn
    takes only the six pawn vocations."""
    if who not in WHO:
        raise RiftError(f"{who}: the characters are {', '.join(WHO)}")
    if who == "arisen":
        return saves.set_arisen(data, edit, tables, equip_jobs, names)
    if edit.job is not None and edit.job not in saves.PAWN_VOCATIONS:
        raise RiftError(f"a pawn cannot be {article(VOCATIONS[edit.job][0])}; pawn vocations are "
                        + ", ".join(f"{v} {VOCATIONS[v][0]}" for v in saves.PAWN_VOCATIONS))
    xml = unpack(data)
    chars = characters(xml)
    recs = [(c.copy, *c.param) for (copy, w), c in chars.items() if w == who]
    edits = saves.set_params(xml, recs, edit, tables, equip_jobs, f"{WHO_NAMES[who]}'s", names)
    if not edits:
        return data, 0
    return pack(_splice(xml, edits)), len(edits)


def _looks(xml: bytes, c: Character) -> list[tuple[bytes, bytes, tuple[int, int, bytes]]]:
    """(kind, name, value) of each value of a record's mEdit outside its name array, in order."""
    s, e = c.edit
    out = []
    for m in _NAMED.finditer(xml, s, e):
        kind, name = m.group(1), m.group(2)
        text = m.group(3)
        _written(text, kind.decode('latin-1'), name.decode('latin-1'), c.whose)
        out.append((kind, name, (m.start(3), m.end(3), text)))
    return out


def copy_looks(data: bytes, src: str, dst: str) -> tuple[bytes, int]:
    """The save with ``dst``'s looks made ``src``'s, in both copies: every value of mEdit but the name and
    LOOKS_KEPT.  Returns the new save and how many values changed."""
    for w in (src, dst):
        if w not in WHO:
            raise RiftError(f"{w}: the characters are {', '.join(WHO)}")
    if src == dst:
        raise RiftError("the looks come from one character and go to another")
    xml = unpack(data)
    chars = characters(xml)
    edits = []
    for copy in dict.fromkeys(c for c, _ in chars):
        a, b = _looks(xml, chars[(copy, src)]), _looks(xml, chars[(copy, dst)])
        if [(k, n) for k, n, _ in a] != [(k, n) for k, n, _ in b]:
            raise FormatError("save", f"{chars[(copy, src)].whose} and {chars[(copy, dst)].whose} looks hold "
                                      "different fields")
        if not a:
            raise FormatError("save", f"{chars[(copy, src)].whose} looks hold no values")
        for (_k, name, at_a), (_k2, _n2, at_b) in zip(a, b):
            if name.decode('latin-1') not in LOOKS_KEPT and at_a[2] != at_b[2]:
                edits.append((at_b[0], at_b[1], at_a[2]))
    if not edits:
        return data, 0
    return pack(_splice(xml, edits)), len(edits)


def online_check(data: bytes, equip_jobs: dict[int, int] | None = None,
                 names: dict[int, str] | None = None) -> list[str]:
    """What the party in the save carries that the game itself would not have made, in each copy of the player's
    data: a clone of the main pawn in a hired slot, a pawn in a vocation pawns cannot take, a level outside the
    game's, a hired pawn's gear not marked as its slot's, gear the vocation cannot wear (with ``equip_jobs``).
    Empty when there is nothing to report.  It reads the save only: it neither hides nor changes anything."""
    xml = unpack(data)
    chars = characters(xml)
    found: list[str] = []
    for (copy, who), c in chars.items():
        _report(xml, chars, copy, who, c, equip_jobs, names, found)
    return found


def _report(xml: bytes, chars: dict, copy: str, who: str, c: Character, equip_jobs: dict[int, int] | None,
            names: dict[int, str] | None, found: list[str]) -> None:
    mine = net_id(xml, chars[(copy, "main")])
    s, e = c.param
    label = f"{copy}: {WHO_NAMES[who]} ({name_of(xml, c) or 'no name'})"
    level = int(_scalar(xml, s, e, "mLevel", "u8", c.whose)[2])
    job = int(_scalar(xml, s, e, "mJob", "u8", c.whose)[2])
    if not 1 <= level <= saves.LEVEL_MAX:
        found.append(f"{label} is level {level}; the game's levels are 1-{saves.LEVEL_MAX}")
    if job not in VOCATIONS:
        found.append(f"{label} holds vocation {job}, which the game does not have")
    elif who != "arisen" and job not in saves.PAWN_VOCATIONS:
        found.append(f"{label} is {article(VOCATIONS[job][0])}, which pawns cannot be")
    gear = _gear(xml, s, e, c.whose)
    if who in ("1", "2"):
        if any(mine) and net_id(xml, c) == mine:
            found.append(f"{label} is a copy of your main pawn (it carries your network id); the game itself "
                         "never puts your own pawn in a hired slot")
        owner = str(int(who) + 1).encode()
        odd = [g.number for g in gear if g.number >= 0 and (g.owner[2] != owner or not int(g.flag[2]) & HIRED_FLAG)]
        if odd:
            found.append(f"{label} wears {', '.join(item_label(n, names) for n in odd)} not marked as its slot's")
    if equip_jobs is not None and job in VOCATIONS:
        bad = [g.number for g in gear if g.number >= 0 and not equip_jobs.get(g.number, 0) >> job & 1]
        if bad:
            found.append(f"{label} wears {', '.join(item_label(n, names) for n in bad)}, which "
                         f"{article(VOCATIONS[job][0])} cannot")
