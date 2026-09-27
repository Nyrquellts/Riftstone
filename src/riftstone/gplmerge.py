"""Group lists (``.gpl``) that several mods change, merged when they are installed together.

A stage's enemies are the groups of its group list (``scr\\st<S>\\etc\\st<S>_e``, and a DLC list such as
``st443_e_dlc01`` sharing its numbers); an encounter adds a group to it.  Every encounter mod carries the
whole list, so as whole resources the later mod's copy would win and the other mods' groups would vanish
(their layouts stay, but no group loads them).  Installed together, the lists are merged instead:

* **Group by group against the game's list.**  A group is its number's ``mGroupList`` slot and its record.
  A group only one mod adds, changes or removes is taken from that mod; one several mods change the same
  way is taken once; where mods disagree about a group, its fields merge (each field from the mod that
  changed it), and a field two mods set differently goes to the later mod -- the higher priority, as for
  any resource two mods change -- and is reported.  ``mSetBit``'s bits and the header merge the same way.
* **A number several mods add.**  Two encounters for one stage made apart both take the stage's first free
  number.  The first claimant keeps it; each other one gets the lowest number free in all of the stage's
  lists of that kind (and in every mod's), and its layouts (``st<S>_<X>m<Z>n_<t><N>``, the name the engine
  builds from the number, 0x01562268) are renamed with it.  Mods installed before claim first and keep the
  numbers they were given (``keep``), so adding a mod never moves an installed mod's groups.  The mod's own
  state machines in the stage's folder (``scr\\st<S>\\...``, a wave chain's among them) wait on the group by
  number, so their targets move with it.  A group is moved only when nothing else in its mod refers to its
  number (another group sharing its wander or kill area, or linking to it, other than the mod's own new
  groups; a mod ``mSetBit`` change in its list; a layout the game already has; a machine of the mod outside
  the stage's folder naming it, which may run in any stage): such a group keeps its number and the later
  mod's wins, reported.
* **A lot flag several mods gate new groups on.**  Two wave chains made apart both take the stage's highest
  free flags; merged, each chain would open and close the other's groups too.  That is reported (each
  group kept); a flag the game's own groups are gated on is shared on purpose and is not.

The merged list is the game's order with the added groups after it by number (every list in the game is
in number order).  Nothing here reads anything but the mods' files and the game's own lists and layout
names; the mods' folders are never changed -- the built archives carry the merge.  In game UNKNOWN, as for
any encounter: the files use only what the game's own lists use.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict

from . import gpl, lot, typemap
from .errors import RiftError
from .merging import NONE, MergeError, game_copy, game_names, machines, move_in_machines, pick, stuck_by_machine

SLOTS = 295                   # cArray<cOmGroupData, 295>: mGroupList has 295 entries (world.GROUP_SLOTS)
_LIST = re.compile(r"scr\\st(\d{3})\\etc\\st\1_([enpt])(?:_dlc(\d\d))?")
# fields that hold another group's number, with the flag that says they are in use
_SHARES = (("ShareWanderArea", "SharedWanderAreaGroup"), ("ShareKillArea", "SharedKillAreaGroup"))
_LINK = ("mSetCondition.mIsEmGroupLink", "mSetCondition.mLinkEmGroup")    # an n/p group waiting on an enemy group


def unit_of(name: bytes) -> tuple[int, str] | None:
    """(stage, kind letter) of a group list's name: the lists sharing one number space.
    ``scr\\st443\\etc\\st443_e_dlc01`` -> (443, 'e')."""
    m = _LIST.fullmatch(name.decode("latin-1"))
    return (int(m.group(1)), m.group(2)) if m else None


def _parse(data: bytes | None, who: str) -> gpl.Gpl:
    if data is None:
        raise MergeError(f"{who} has no such group list")
    try:
        doc = gpl.parse(data)
    except RiftError as e:
        raise MergeError(f"{who}'s group list does not read: {e}") from None
    nums = [g["mGroup"] for g in doc.groups]
    if len(doc.mGroupList) != SLOTS or len(set(nums)) != len(nums) or any(not 0 <= n < SLOTS for n in nums):
        raise MergeError(f"{who}'s group list is not the game's shape ({SLOTS} slots, each group number once)")
    wide = [g["mGroup"] for g in doc.groups if len(g["mUnitKindList"]) > gpl.UNIT_KINDS_MAX]
    if wide:                        # a list made for the unit expander: written as it is, never rebuilt here
        raise MergeError(f"{who}'s group {wide[0]} lists more than the game's {gpl.UNIT_KINDS_MAX} unit kinds")
    return doc


def merge(base: bytes | None, versions: list[tuple[str, bytes]]) -> tuple[bytes, list[tuple[str, str, str]]]:
    """The mods' copies of one group list (``(mod, bytes)`` in plan order) merged against the game's (None:
    the game has no such list).  Returns the merged list and the disagreements, each ``(loser, winner, what)``;
    raises MergeError when a copy cannot be merged."""
    if len(versions) < 2:
        raise MergeError("nothing to merge")
    docs = [(m, _parse(data, m)) for m, data in versions]
    b = _parse(base, "the game") if base is not None else None
    words = len(b.mSetBit) if b is not None else len(docs[0][1].mSetBit)
    if any(len(d.mSetBit) != words for _, d in docs):
        raise MergeError("the copies' mSetBit differ in length")
    fights: list[tuple[str, str, str]] = []
    head = {k: pick(getattr(b, k) if b is not None else NONE, [(m, getattr(d, k)) for m, d in docs], k, fights)
            for k in ("version", "mDLCNo")}
    set_bit = []
    for i in range(words):
        old = b.mSetBit[i] if b is not None else 0
        flip = 0
        for _, d in docs:
            flip |= d.mSetBit[i] ^ old          # a bit is either the game's or its opposite: no disagreement
        set_bit.append(old ^ flip)

    b_recs = {g["mGroup"]: g for g in b.groups} if b is not None else {}
    recs = [(m, {g["mGroup"]: g for g in d.groups}) for m, d in docs]
    slots, chosen = [], {}
    for n in range(SLOTS):
        b_entry = (b.mGroupList[n] if b is not None else 0, b_recs.get(n))
        entries = [(m, (d.mGroupList[n], r.get(n))) for (m, d), (_, r) in zip(docs, recs)]
        changed = [(m, e) for m, e in entries if e != b_entry]
        if not changed:
            slot, rec = b_entry
        elif all(e == changed[-1][1] for _, e in changed):
            slot, rec = changed[-1][1]
        else:
            touched = [(m, e) for m, e in changed if e[1] != b_entry[1]]
            if b_entry[1] is not None and touched and all(e[1] is not None for _, e in touched):
                # every mod that touched the group kept it: field by field
                base_rec = b_entry[1]
                rec = {k: pick(base_rec[k], [(m, e[1][k]) for m, e in touched], f"group {n}: {k}", fights)
                       for k in base_rec}
                slot = pick(b_entry[0], [(m, e[0]) for m, e in entries], f"group {n}: its mGroupList slot", fights)
            elif touched:
                # removed by one mod and changed by another, or added by several: the later mod's group whole
                win, (slot, rec) = touched[-1]
                both = " (both add it)" if b_entry[1] is None and rec is not None else ""
                fights.extend((m, win, f"group {n}{both}") for m, e in touched[:-1] if e != touched[-1][1])
            else:
                rec = b_entry[1]
                slot = pick(b_entry[0], [(m, e[0]) for m, e in entries], f"group {n}: its mGroupList slot", fights)
        slots.append(slot)
        if rec is not None:
            chosen[n] = rec
    order = [g["mGroup"] for g in (b.groups if b is not None else []) if g["mGroup"] in chosen]
    order += sorted(set(chosen) - set(order))
    fights.extend(_shared_gates(b, docs, b_recs))
    out = gpl.build(gpl.Gpl(head["version"], slots, set_bit, head["mDLCNo"], [chosen[n] for n in order]))
    gpl.parse(out)                              # it must read back
    return out, fights


def _gates(g: dict) -> list[int]:
    """The lot flags a group reads: its load gates and, with a delete condition, the first (as waves reads them)."""
    out = []
    if g.get("mLoadCondition.mLotFlag") or g.get("mDeleteCondition.mLotFlag"):
        out.append(g.get("mDataLotFlag.mFlagNo"))
    if g.get("mLoadCondition.mLotFlag2"):
        out.append(g.get("mDataLotFlag.mFlagNo2"))
    return [f for f in out if isinstance(f, int) and not isinstance(f, bool)]


def _shared_gates(b, docs, b_recs) -> list[tuple[str, str, str]]:
    """Lot flags new groups of several mods are gated on, where the game's own list gates none of its groups on
    the flag: (earlier mod, later mod, what) for each pair."""
    games = {f for g in (b.groups if b is not None else []) for f in _gates(g)}
    users: dict[int, dict[str, set]] = defaultdict(dict)       # flag -> mod -> its new groups gated on it
    for m, d in docs:
        for g in d.groups:
            if g["mGroup"] not in b_recs:
                for f in _gates(g):
                    if f not in games:
                        users[f].setdefault(m, set()).add(json.dumps(g, sort_keys=True))
    out = []
    for f, by in sorted(users.items()):
        mods = list(by)
        for k in range(1, len(mods)):
            if by[mods[k]] != by[mods[0]]:          # the same groups in two mods (one encounter twice) share it
                out.append((mods[0], mods[k], f"lot flag {f}: both add groups gated on it (a wave chain of either "
                                             "opens and closes the other's too)"))
    return out


# -- numbers several mods add -----------------------------------------------------------------------
def _sig(parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=repr).encode()).hexdigest()


def renumber(index, collected: list, keep: list | None = None, installed=()) -> tuple[list[dict], list[dict]]:
    """Give every group that several mods add under one number (with different contents) a number of its
    own, before the mods' changes are placed: the moved group's copies in its mod's group lists and its
    layouts' names are rewritten in ``collected`` (``[(Mod, [Change])]`` in plan order; the mods' files are
    not touched).  ``keep`` is the last install's moves and ``installed`` the mods it installed: they claim
    first.  Returns (moves, refusals): ``{"mod", "stage", "type", "group", "as"}`` for each moved group and
    ``{"mod", "stage", "type", "group", "winner", "why"}`` for one that had to keep a number another mod
    also uses."""
    GPL, LOT = typemap.BY_EXT["gpl"], typemap.BY_EXT["lot"]
    lists: dict = defaultdict(lambda: defaultdict(list))       # (stage, letter) -> mod position -> [Change]
    lays: dict = defaultdict(lambda: defaultdict(list))        # (stage, letter) -> mod position -> [Change]
    for i, (_m, changes) in enumerate(collected):
        for c in changes:
            if c.type_id == GPL and (u := unit_of(c.name)) is not None:
                lists[u][i].append(c)
            elif c.type_id == LOT and (ln := lot.parse_name(c.name.decode("latin-1"))) is not None and ln.type != "s":
                lays[(ln.stage, ln.type)][i].append(c)
    kept = {(k.get("mod"), k.get("stage"), k.get("type"), k.get("group")): k.get("as")
            for k in (keep or []) if isinstance(k, dict) and "group" in k}
    installed = set(installed or ())
    mach = {i: machines(changes) for i, (_m, changes) in enumerate(collected)}
    moves: list[dict] = []
    refused: list[dict] = []
    cache: dict = {}
    for unit in sorted(lists):
        by_mod = lists[unit]
        if len(by_mod) < 2:
            continue
        stage, letter = unit
        try:
            base = {}
            for name in game_names(index, stage, GPL):
                if unit_of(name) == unit:
                    base[name] = _parse(game_copy(index, name, GPL, cache), "the game")
            docs = {i: [(c, _parse(c.data, collected[i][0].name)) for c in cs] for i, cs in by_mod.items()}
        except MergeError:
            continue                            # a copy that does not read: the merge refuses it too
        base_nums = {g["mGroup"] for d in base.values() for g in d.groups}
        game_lays = set()
        for name in game_names(index, stage, LOT):
            ln = lot.parse_name(name.decode("latin-1"))
            if ln is not None and ln.type == letter:
                game_lays.add(ln.number)
        added = {i: sorted({g["mGroup"] for _, d in ds for g in d.groups} - base_nums) for i, ds in docs.items()}

        def sig(i, n):
            recs = sorted(json.dumps(g, sort_keys=True) for _, d in docs[i] for g in d.groups if g["mGroup"] == n)
            own = sorted((c.name.hex(), c.sha256) for c in lays[unit].get(i, [])
                         if lot.parse_name(c.name.decode("latin-1")).number == n)
            return _sig([recs, own])

        name_of = {i: collected[i][0].name for i in docs}
        claim = sorted(docs, key=lambda i: (name_of[i] not in installed, i))
        taken: dict[int, tuple] = {}            # number -> (mod position, original number, signature)
        chosen: list[tuple[int, int, int]] = []   # (mod position, number, new number)
        waiting = []
        for i in claim:
            for n in added[i]:
                s = sig(i, n)
                wants = [n]
                was = kept.get((name_of[i], stage, letter, n))   # where the last install put it: stays there
                if (isinstance(was, int) and not isinstance(was, bool) and 0 <= was < SLOTS and was != n
                        and was not in base_nums and was not in game_lays
                        and _unmovable(i, n, unit, docs, base, lists, lays, game_lays, added[i], mach) is None):
                    wants.insert(0, was)
                for want in wants:
                    if want not in taken:
                        taken[want] = (i, n, s)
                    elif taken[want][1:] != (n, s):
                        continue
                    # a free number, or the same group another mod adds there too (merged as one)
                    if want != n:
                        chosen.append((i, n, want))
                    break
                else:
                    waiting.append((i, n))
        mod_lays = {ln.number for cs in lays[unit].values() for c in cs
                    if (ln := lot.parse_name(c.name.decode("latin-1"))) is not None}
        reserved = base_nums | game_lays | mod_lays | set(taken) | {n for ns in added.values() for n in ns}
        for i, n in waiting:
            why = _unmovable(i, n, unit, docs, base, lists, lays, game_lays, added[i], mach)
            if why is None:
                free = [x for x in range(SLOTS) if x not in reserved]
                if free:
                    reserved.add(free[0])
                    chosen.append((i, n, free[0]))
                    continue
                why = f"all {SLOTS} numbers of the stage's lists of this kind are in use"
            win = taken.get(n)
            refused.append({"mod": name_of[i], "stage": stage, "type": letter, "group": n,
                            "winner": name_of[win[0]] if win else None, "why": why})
        plan_moves: dict[int, dict[int, int]] = defaultdict(dict)
        for i, n, target in chosen:
            plan_moves[i][n] = target
            moves.append({"mod": name_of[i], "stage": stage, "type": letter, "group": n, "as": target})
        for i, mapping in plan_moves.items():
            _apply(docs[i], base, mapping, added[i])
            if letter == "e":                   # the mod's own stage machines wait on its groups by number
                move_in_machines(mach[i], stage, lambda g, r, m=mapping: (m.get(g, g), r))
            for c in lays[unit].get(i, []):
                ln = lot.parse_name(c.name.decode("latin-1"))
                if ln.number in mapping:
                    c.name = lot.layout_name(stage, ln.x, ln.z, letter, mapping[ln.number]).encode("latin-1")
    return moves, refused


def _refs(g: dict, letter: str) -> set[int]:
    """The group numbers a group refers to: an area it shares, an enemy group it waits on."""
    out = {g[num] for flag, num in _SHARES if g.get(flag)}
    if letter in ("n", "p") and g.get(_LINK[0]):
        out.add(("e", g[_LINK[1]]))
    return out


def _unmovable(i, n, unit, docs, base, lists, lays, game_lays, added, mach) -> str | None:
    """Why mod i's added group n cannot take another number, or None."""
    stage, letter = unit
    if letter == "e":
        why = stuck_by_machine(mach.get(i, []), stage, lambda g, r: g == n)
        if why is not None:
            return why
    if n in game_lays:
        return f"the game already has layouts numbered {n}"
    for c in lays[unit].get(i, []):
        ln = lot.parse_name(c.name.decode("latin-1"))
        if ln.number == n and str(ln) != c.name.decode("latin-1"):
            return f"its layout {c.name.decode('latin-1')} is not named the way the game names layouts"
    for c, d in docs[i]:
        if any(g["mGroup"] == n for g in d.groups):
            old = base.get(c.name)
            if d.mSetBit != (old.mSetBit if old is not None else [0] * len(d.mSetBit)):
                return f"the mod changes mSetBit in {c.name.decode('latin-1')}, whose meaning is not known"
    # another group of the mod refers to it: in the same lists (a shared area) or, for an enemy group, an
    # NPC or object group of the stage waiting on it
    for (s, t), by_mod in lists.items():
        if s != stage:
            continue
        for c in by_mod.get(i, []):
            try:
                d = gpl.parse(c.data)
            except RiftError:
                continue
            for g in d.groups:
                if t == letter and g["mGroup"] in added:
                    continue                    # the mod's own new groups: their references move with it
                refs = _refs(g, t)
                if (t == letter and n in refs) or (letter == "e" and ("e", n) in refs):
                    return f"group {g['mGroup']} of {c.name.decode('latin-1')} refers to it"
    return None


def _apply(docs: list, base: dict, mapping: dict[int, int], added: list[int]) -> None:
    """Move one mod's groups to their new numbers in every copy of its lists (records, their cells' group
    fields, the mGroupList slots, and its new groups' references to moved ones) and rebuild the copies."""
    for c, d in docs:
        old = base.get(c.name)
        present = {g["mGroup"] for g in d.groups}
        marks = {n: d.mGroupList[n] for n in mapping if n in present}   # all read first: a new number may be
        for n in marks:                                                  # another moved group's old one
            d.mGroupList[n] = old.mGroupList[n] if old is not None else 0
        for n, mark in marks.items():
            d.mGroupList[mapping[n]] = mark
        for g in d.groups:
            if g["mGroup"] in added:
                for flag, num in _SHARES:
                    if g.get(flag) and g[num] in mapping:
                        g[num] = mapping[g[num]]
            if g["mGroup"] in mapping:
                new = mapping[g["mGroup"]]
                for la in g["mLayoutIDArray"]:
                    if la["mGroup"] == g["mGroup"]:
                        la["mGroup"] = new
                g["mGroup"] = new
        c.data = gpl.build(d)
        gpl.parse(c.data)


__all__ = ["MergeError", "merge", "renumber", "unit_of"]
