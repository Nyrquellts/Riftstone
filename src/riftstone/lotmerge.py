"""Layouts (``.lot``) that several mods change, merged when they are planned together (build, install, package).

A layout is one group's placements in one map cell.  ``riftstone spawns copy``, Studio's map (drag, Duplicate,
Remove) and hand edits change the game's own layouts, and every such mod carries the whole file, so as whole
resources the later mod's copy would win and the other mods' placements would vanish.  Planned together, the copies
are merged against the game's layout instead:

* **Records by id.**  A record's id is its first u32, the index of the loader's 1,024-entry table (``lot.MAX_ID``);
  every layout in the game but one uses each id once.  A record only one mod adds, changes or removes is taken from
  that mod; one several mods change the same way is taken once; where mods change one record differently its fields
  merge, and a field two mods set differently goes to the later mod (the higher priority), reported.
* **An id several mods add.**  Two copies in one layout made apart both take the largest id + 1 (``lot.free_id``).
  The first claimant keeps it; each other one gets an id free in the game's layout and in every mod's copy (the
  largest + 1, else the smallest unused, within 0..1023).  An enemy group's layouts are numbered together: the game
  finds a placement by its group and id (stage | group << 10, id; ``0x005BF46E``) and keeps one kill-record bit per
  id, which wraps past 31 (``0x004A653D``), and every group in the game uses each id once across its layouts, all
  below 32.  So two mods adding one id to different layouts of a group clash too, and a moved enemy placement takes
  the smallest id free in every layout of its group (the game's and every mod's), below 32 when one is.  Nothing in
  the game can refer to a record a mod adds; the mod's own state machines in the stage's folder can (a wave chain
  waits for its first group's placements by id), and follow the record; a machine of the mod elsewhere that names
  it may run in any stage, so the record keeps its id and the later mod's wins, reported.  Mods installed before
  claim first and a moved record keeps the id the last install gave it (``keep``), as groups do (``gplmerge``).
* The merged layout is the game's records in the game's order, then the ones mods add, in plan order.

Only layouts the game has are merged: a new layout belongs to its mod's new group, whose number ``gplmerge`` keeps
apart.  ``mSetID`` is not a record id (measured on the game's 6,342 layouts: -1 on 41,104 records, other values
that match no record on 750); the mods' files are never changed, the built archives carry the merge.  In game
UNKNOWN, as for any layout edit.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict

from . import lot, typemap
from .errors import RiftError
from .merging import MergeError, game_copy, game_names, machines, move_in_machines, pick, stuck_by_machine

KILL_BITS = 32          # an enemy group's kill record: one bit a placement id, past 31 the bit wraps (0x004A653D)


def _parse(data: bytes | None, who: str) -> lot.Lot:
    if data is None:
        raise MergeError(f"{who} has no such layout")
    try:
        doc = lot.parse(data)
    except RiftError as e:
        raise MergeError(f"{who}'s layout does not read: {e}") from None
    ids = [r.id for r in doc.records]
    if len(set(ids)) != len(ids) or any(not 0 <= i <= lot.MAX_ID for i in ids):
        raise MergeError(f"{who}'s layout does not use each id 0..{lot.MAX_ID} once")
    return doc


def _bytes(r: lot.Record, version: int) -> bytes:
    return lot.build(lot.Lot([r], version))


def merge(base: bytes, versions: list[tuple[str, bytes]]) -> tuple[bytes, list[tuple[str, str, str]]]:
    """The mods' copies of one layout (``(mod, bytes)`` in plan order) merged against the game's; returns the
    merged layout and the disagreements ``(loser, winner, what)``.  Raises MergeError when a copy cannot be merged
    (it does not read, or repeats an id)."""
    if len(versions) < 2:
        raise MergeError("nothing to merge")
    b = _parse(base, "the game")
    docs = [(m, _parse(data, m)) for m, data in versions]
    fights: list[tuple[str, str, str]] = []
    version = pick(b.version, [(m, d.version) for m, d in docs], "version", fights)
    b_recs = {r.id: r for r in b.records}
    recs = [(m, {r.id: r for r in d.records}) for m, d in docs]
    order = list(b_recs) + [r.id for _, d in docs for r in d.records if r.id not in b_recs]
    out = []
    for rid in dict.fromkeys(order):
        old = b_recs.get(rid)
        vals = [(m, rr.get(rid)) for m, rr in recs]
        changed = [(m, v) for m, v in vals if v != old]
        if not changed:
            rec = old
        elif all(v == changed[-1][1] for _, v in changed):
            rec = changed[-1][1]
        elif old is not None and all(v is not None and v.kind == old.kind for _, v in changed):
            rec = lot.Record(rid, old.kind, {k: pick(old.fields[k], [(m, v.fields[k]) for m, v in changed],
                                                       f"record {rid}: {k}", fights) for k in old.fields})
        else:                       # removed by one mod and changed by another, or added by several
            win, rec = changed[-1]
            both = " (both add it)" if old is None and rec is not None else ""
            fights.extend((m, win, f"record {rid}{both}") for m, v in changed[:-1] if v != rec)
        if rec is not None:
            out.append(rec)
    merged = lot.Lot(out, version)
    lot.check_ids(merged)
    data = lot.build(merged)
    lot.parse(data)                                 # it must read back
    return data, fights


def _units(index, by_name: dict, lot_type: int) -> list[tuple[tuple, list[bytes]]]:
    """The layouts numbered together: each game enemy group's layouts (every one the game has, whether a mod changes
    it or not), and any other layout alone.  A layout of a group the game does not have belongs to that new group,
    which ``gplmerge`` keeps apart, and is left alone."""
    groups: dict = defaultdict(set)
    alone = []
    for name in by_name:
        label = name.decode("latin-1")
        ln = lot.parse_name(label)
        if ln is not None and ln.type == "e" and str(ln) == label:
            groups[(ln.stage, ln.number)].add(name)
        else:
            alone.append(((0, label), [name]))
    out = []
    stages: dict = {}
    for (stage, number), names in groups.items():
        if stage not in stages:
            stages[stage] = defaultdict(set)
            for g in game_names(index, stage, lot_type):
                label = g.decode("latin-1")
                ln = lot.parse_name(label)
                if ln is not None and ln.type == "e" and str(ln) == label:
                    stages[stage][ln.number].add(g)
        if stages[stage].get(number):
            out.append(((1, stage, number), sorted(stages[stage][number] | names)))
    return sorted(alone + out)


def _free(used: set, enemy: bool) -> int | None:
    """A new record id: for an enemy placement the smallest free below 32 (the kill record's bits), else the largest
    + 1, else the smallest unused; within the game's 0..1023."""
    if enemy:
        low = next((x for x in range(KILL_BITS) if x not in used), None)
        if low is not None:
            return low
    nxt = max(used) + 1 if used else 0
    if nxt <= lot.MAX_ID:
        return nxt
    return next((x for x in range(lot.MAX_ID + 1) if x not in used), None)


def renumber(index, collected: list, keep: list | None = None, installed=()) -> tuple[list[dict], list[dict]]:
    """Give every record that several mods add under one id (with different contents) an id of its own, before the
    mods' changes are placed: in one of the game's layouts, or anywhere in the layouts of one of the game's enemy
    groups.  The record's id is rewritten in each copy of that layout its mod holds (``collected``:
    ``[(Mod, [Change])]`` in plan order; the mods' files are not touched), and in its mod's state machines in the
    stage's folder.  ``keep`` (the last install's moves) and ``installed`` (its mods) work as in
    ``gplmerge.renumber``.  Returns (moves, refusals): ``{"mod", "layout", "record", "as"}`` for each moved record,
    ``{"mod", "layout", "record", "winner", "why"}`` for one that had to keep an id another mod uses."""
    LOT = typemap.BY_EXT["lot"]
    by_name: dict = defaultdict(lambda: defaultdict(list))        # layout name -> mod position -> [Change]
    for i, (_m, changes) in enumerate(collected):
        for c in changes:
            if c.type_id == LOT:
                by_name[c.name][i].append(c)
    kept = {(k.get("mod"), k.get("layout"), k.get("record")): k.get("as")
            for k in (keep or []) if isinstance(k, dict) and "record" in k}
    installed = set(installed or ())
    mach = {i: machines(changes) for i, (_m, changes) in enumerate(collected)}
    moves: list[dict] = []
    refused: list[dict] = []
    cache: dict = {}
    for key, names in _units(index, by_name, LOT):
        if len({i for n in names for i in by_name.get(n, {})}) < 2:
            continue
        enemy = key[0] == 1
        stage, number = (key[1], key[2]) if enemy else (None, None)
        try:
            base: dict[bytes, lot.Lot] = {}
            for n in names:
                data = game_copy(index, n, LOT, cache)
                if data is not None:
                    base[n] = _parse(data, "the game")
                elif not enemy:
                    raise MergeError("the game has no such layout")
            docs: dict[int, dict[bytes, list]] = defaultdict(dict)
            for n in names:
                for i, cs in by_name.get(n, {}).items():
                    docs[i][n] = [(c, _parse(c.data, collected[i][0].name)) for c in cs]
        except MergeError:
            continue                # a layout the game does not have, or a copy that cannot be merged
        game_ids = {r.id for d in base.values() for r in d.records}
        # each mod's additions: (layout, id) of a record its copy of one of the game's layouts has and the game's
        # copy lacks (a layout only mods have belongs to it whole: its records are not moved, only counted)
        added = {i: sorted({(n, r.id) for n, cs in by.items() if n in base for _, d in cs for r in d.records
                            if r.id not in {x.id for x in base[n].records}}) for i, by in docs.items()}

        def sig(i, n, rid):
            return hashlib.sha256(b"".join(sorted(_bytes(r, d.version) for _, d in docs[i][n] for r in d.records
                                                  if r.id == rid))).hexdigest()

        def stuck(i, rid):
            if not enemy:
                return None
            return stuck_by_machine(mach.get(i, []), stage, lambda g, r: g == number and r == rid)

        name_of = {i: collected[i][0].name for i in docs}
        claim = sorted(docs, key=lambda i: (name_of[i] not in installed, i))
        taken: dict[int, tuple] = {}            # id -> (mod position, layout, original id, signature)
        chosen: list[tuple[int, bytes, int, int]] = []
        waiting = []
        for i in claim:
            for n, rid in added[i]:
                s = sig(i, n, rid)
                wants = [rid]
                was = kept.get((name_of[i], n.decode("latin-1"), rid))
                if (isinstance(was, int) and not isinstance(was, bool) and 0 <= was <= lot.MAX_ID and was != rid
                        and was not in game_ids and stuck(i, rid) is None):
                    wants.insert(0, was)
                for want in wants:
                    if want not in taken:
                        taken[want] = (i, n, rid, s)
                    elif taken[want][1:] != (n, rid, s):
                        continue
                    if want != rid:
                        chosen.append((i, n, rid, want))
                    break
                else:
                    waiting.append((i, n, rid))
        used = game_ids | set(taken) | {r.id for by in docs.values() for cs in by.values() for _, d in cs
                                        for r in d.records}
        for i, n, rid in waiting:
            why = stuck(i, rid)
            new = None
            if why is None:
                new = _free(used, enemy)
                if new is None:
                    why = f"the {'group' if enemy else 'layout'}'s ids 0..{lot.MAX_ID} are all in use"
            if why is not None:
                win = taken.get(rid)
                refused.append({"mod": name_of[i], "layout": n.decode("latin-1"), "record": rid,
                                "winner": name_of[win[0]] if win else None, "why": why})
                continue
            used.add(new)
            chosen.append((i, n, rid, new))
        mapping: dict[int, dict[tuple, int]] = defaultdict(dict)
        for i, n, rid, new in chosen:
            mapping[i][(n, rid)] = new
            moves.append({"mod": name_of[i], "layout": n.decode("latin-1"), "record": rid, "as": new})
        for i, ids in mapping.items():
            for n, cs in docs[i].items():
                for c, d in cs:
                    if not any((n, r.id) in ids for r in d.records):
                        continue
                    for r in d.records:
                        if (n, r.id) in ids:
                            r.id = ids[(n, r.id)]
                    lot.check_ids(d)
                    c.data = lot.build(d)
            if enemy:                       # the mod's own stage machines name its placements by group and id
                ren = {rid: new for (_n, rid), new in ids.items()}
                move_in_machines(mach[i], stage,
                                 lambda g, r, m=ren, num=number: (g, m.get(r, r)) if g == num else (g, r))
    return moves, refused


__all__ = ["merge", "renumber"]
