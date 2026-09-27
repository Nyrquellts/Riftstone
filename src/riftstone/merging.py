"""What the merges of resources several mods change share (``gplmerge``, ``lotmerge``, ``servermerge``): the error
they refuse copies with, the three-way merge of one value, the game's own copy of a resource, and the enemy
placements a mod's state machines name (a merge that moves a group or a placement moves them too)."""
from __future__ import annotations

import re

from . import arc, typemap, xfs
from .errors import RiftError

NONE = object()               # no value: the game has no such resource, or a copy has no such key


class MergeError(RiftError):
    """Copies that cannot be merged (one does not read, or is not the resource's shape); the later mod's copy
    then wins whole, as for any resource two mods change."""


def pick(base, values: list[tuple[str, object]], what: str, fights: list):
    """Three-way merge of one value: the mods' values (plan order) against the game's (NONE: the game has none, so
    every mod's value is a change).  The later of the mods that changed it wins; each earlier one that set something
    else is a disagreement, appended to ``fights`` as (loser, winner, what)."""
    changed = [(m, v) for m, v in values if base is NONE or v != base]
    if not changed:
        return base
    win, value = changed[-1]
    fights.extend((m, win, what) for m, v in changed[:-1] if v != value)
    return value


def game_copy(index, name: bytes, type_id: int, cache: dict) -> bytes | None:
    """The game's own copy of a resource (from the first archive holding it), or None.  ``cache`` keeps the archives
    read, by name."""
    arcs = index.archives_with(name, type_id)
    if not arcs:
        return None
    a = arcs[0]
    if a not in cache:
        cache[a] = arc.Archive.read(index.game.vanilla_arc(a))
    e = cache[a].find(name, type_id)
    return e.data() if e is not None else None


def game_names(index, stage: int, type_id: int) -> list[bytes]:
    """Every resource of one type whose name starts with the stage's script folder, from the index."""
    return index.names_under(f"scr\\st{stage:03d}\\etc\\", type_id)


def kept(merged: dict) -> str:
    """What a merge (a ``merged`` entry of a plan or an install report) kept of every mod, for messages."""
    if merged["archive"] == "server":
        return "rows, drops and goods"
    return "placements" if merged["resource"].lower().endswith(".lot") else "groups"


# -- the enemy placements a state machine names --------------------------------------------------------------------
FSM = typemap.BY_EXT["fsm"]
# cLinkUnit::cTarget's mType for an enemy placement: mNo0 its group, mNo1 its record id, in the stage the machine
# runs in (EnemyCheck counts only these, 0x005BF522; docs/enemy-waves.md)
ENEMY_TARGET = 2
_STAGE_MACHINE = re.compile(r"scr\\st(\d{3})\\", re.IGNORECASE)


def machine_stage(name: bytes) -> int | None:
    """The stage a state machine runs in when its folder says so (``scr\\st<S>\\...``: the stage's own
    machines, a wave chain among them); None for any other, since a quest's or a character's machine can run in
    any stage."""
    m = _STAGE_MACHINE.match(name.decode("latin-1"))
    return int(m.group(1)) if m else None


def _targets(x: xfs.Xfs):
    for o in xfs.walk(x.root):
        c = x.classes[o.cls]
        if c.name == "cLinkUnit::cTarget":
            at = {p.name: k for k, p in enumerate(c.props)}
            if {"mType", "mNo0", "mNo1"} <= at.keys() and o.fields[at["mType"]] == [ENEMY_TARGET]:
                yield o, at["mNo0"], at["mNo1"]


def enemy_targets(data: bytes) -> set[tuple[int, int]] | None:
    """(group, record id) of every enemy placement a state machine names; None when it does not read."""
    try:
        x = xfs.parse(data)
    except RiftError:
        return None
    return {(o.fields[g][0], o.fields[r][0]) for o, g, r in _targets(x)}


def retarget(data: bytes, move) -> bytes:
    """A state machine with each enemy target (group, id) replaced by ``move(group, id)``; the same bytes when
    nothing moves."""
    x = xfs.parse(data)
    moved = False
    for o, g, r in _targets(x):
        old = (o.fields[g][0], o.fields[r][0])
        new = move(*old)
        if new != old:
            o.fields[g], o.fields[r] = [new[0]], [new[1]]
            moved = True
    if not moved:
        return data
    out = xfs.build(x)
    xfs.parse(out)                                  # it must read back
    return out


def machines(changes) -> list:
    """The state machines among one mod's changes."""
    return [c for c in changes if c.type_id == FSM]


def stuck_by_machine(mods_machines: list, stage: int, wants) -> str | None:
    """Why a group or placement of ``stage`` cannot move because of one of its mod's state machines, or None:
    a machine that does not read (what it names is not known), or one outside the stage's own folder naming it
    (``wants(group, id)``), since that machine may run in any stage and cannot be rewritten for this one."""
    for c in mods_machines:
        found = enemy_targets(c.data)
        where = machine_stage(c.name)
        if found is None:
            return f"its state machine {c.name.decode('latin-1')} does not read, so what it waits on is not known"
        if where is None and any(wants(g, r) for g, r in found):
            return (f"its state machine {c.name.decode('latin-1')} names it and may run in any stage, so it "
                    "cannot be moved with it")
    return None


def move_in_machines(mods_machines: list, stage: int, move) -> None:
    """Rewrite the enemy targets of a mod's machines that run in ``stage`` (its own folder) by ``move``."""
    for c in mods_machines:
        if machine_stage(c.name) == stage:
            c.data = retarget(c.data, move)


__all__ = ["ENEMY_TARGET", "MergeError", "NONE", "enemy_targets", "game_copy", "game_names", "kept", "machine_stage",
           "machines", "move_in_machines", "pick", "retarget", "stuck_by_machine"]
