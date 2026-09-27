"""Encounter plans: many encounters from one JSON file, as NYR-Lang's `riftstone` target writes them
(<path> spawn rules such as `[stage == 330] and [hour >= 20] => E(100) x 100 | near = 35`).

    {"format": "riftstone-encounters/1", "game": "ddda", "encounters": [
        {"stage": 330, "enemy": "em0100", "total": 100, "at": "group:35", "points": 10, "spread": null,
         "hours": [20, 3], "story": "post", "group": null, "like": null, "skin": null, "rule": 0, "line": 3}]}

`riftstone encounters PLAN --mod MOD` plans each entry with encounter.plan and writes it into the mod
before planning the next, so encounters in one stage stack like repeated `riftstone encounter`
runs (the next free group number each time).  `--dry-run` does the same in a scratch copy of what
they read from the mod (each stage's enemy group lists, and the navigation mesh the stage loads when the mod
has its own), removed afterwards: it shows a real run's group numbers, refusals and spawn points and leaves
the mod as it was.  `at` is "x,y,z" as a list of three numbers or
"group:N"; hours are (first, last) whole hours 0..23, both ends as the game's own groups hold
them; null keeps what `riftstone encounter` would default to.  An optional "always": true clears the
copied group's lot-flag load condition (`riftstone encounter --always`; the level director writes it).
"rule" and "line" say where the entry came from.  Every entry's fields are checked before any is planned (a bad one is refused
with its index and nothing is written); an encounter the game data refuses (an unknown enemy, a
used group number) stops the run there, and the message says which earlier ones were written.
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .errors import RiftError

FORMAT = "riftstone-encounters/1"
MAX_ENTRIES = 1000
_KEYS = {"stage", "enemy", "total", "at", "points", "spread", "hours", "story", "group", "like", "skin", "rule",
         "line", "always"}


@dataclass(frozen=True)
class PlanEntry:
    stage: int
    enemy: str
    total: int
    at: str                             # "x,y,z" or "group:N", as encounter.parse_at reads it
    points: Optional[int]
    spread: Optional[float]
    hours: Optional[tuple]
    story: Optional[str]
    group: Optional[int]
    like: Optional[int]
    skin: Optional[int]
    source: str                         # "rule 3, line 12" when the plan says so
    always: bool = False                # clear the copied group's lot-flag load condition


def _int(value: Any, what: str, lo: int, hi: int, where: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi:
        raise RiftError(f"{where}: {what} is a whole number {lo}..{hi}, not {value!r}")
    return value


def _optional_int(item: dict, key: str, lo: int, hi: int, where: str) -> Optional[int]:
    return None if item.get(key) is None else _int(item[key], key, lo, hi, where)


def parse(text: str) -> list[PlanEntry]:
    """A plan's text -> its entries, every field checked.  Raises RiftError naming the entry."""
    try:
        doc = json.loads(text)
    except (ValueError, RecursionError) as e:            # RecursionError: nesting deeper than the decoder goes
        raise RiftError(f"the plan is not JSON: {e}") from None
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise RiftError(f'not an encounter plan: expected {{"format": "{FORMAT}", "encounters": [...]}} (NYR-Lang\'s '
                        "riftstone target writes one)")
    if doc.get("game", "ddda") != "ddda":
        raise RiftError(f"this plan is for {doc.get('game')!r}; encounter plans are for Dragon's Dogma: Dark Arisen")
    items = doc.get("encounters")
    if not isinstance(items, list) or not items:
        raise RiftError("the plan lists no encounters")
    if len(items) > MAX_ENTRIES:
        raise RiftError(f"the plan lists {len(items)} encounters; at most {MAX_ENTRIES}")
    out = []
    for i, item in enumerate(items):
        where = f"encounter {i}"
        if not isinstance(item, dict):
            raise RiftError(f"{where} is not an object")
        unknown = sorted(set(item) - _KEYS)
        if unknown:
            raise RiftError(f"{where} has unknown keys {unknown}")
        stage = _int(item.get("stage"), "stage", 0, 999, where)
        enemy = item.get("enemy")
        if not isinstance(enemy, str) or not enemy.strip() or len(enemy) > 64:
            raise RiftError(f"{where}: enemy is a name such as em0100")
        total = _int(item.get("total"), "total", 1, 9999, where)
        at = item.get("at")
        if isinstance(at, list):
            # a range check takes any integer (math.isfinite overflowed past a float's range) and refuses nan/inf
            if len(at) != 3 or not all(isinstance(c, (int, float)) and not isinstance(c, bool)
                                       and abs(c) < 1e6 for c in at):
                raise RiftError(f"{where}: at is [x, y, z] (finite numbers) or \"group:N\"")
            at_text = ",".join(repr(float(c)) for c in at)
        elif isinstance(at, str) and (m := re.fullmatch(r"group:0*([0-9]{1,3})", at)) and int(m.group(1)) <= 294:
            at_text = f"group:{int(m.group(1))}"
        else:
            raise RiftError(f"{where}: at is [x, y, z] or \"group:N\" (N 0..294), not {at!r}")
        spread = item.get("spread")
        if spread is not None:
            if not isinstance(spread, (int, float)) or isinstance(spread, bool) or not 0 < spread <= 5000:
                raise RiftError(f"{where}: spread is a distance 0..5000, not {spread!r}")
            spread = float(spread)
        hours = item.get("hours")
        if hours is not None:
            if not isinstance(hours, list) or len(hours) != 2:
                raise RiftError(f"{where}: hours is [first, last], whole hours 0..23")
            hours = (_int(hours[0], "hours", 0, 23, where), _int(hours[1], "hours", 0, 23, where))
        story = item.get("story")
        if story is not None and story not in ("any", "pre", "post"):
            raise RiftError(f"{where}: story is any, pre or post, not {story!r}")
        points = _optional_int(item, "points", 1, 31, where)
        if points is not None and points > total:
            raise RiftError(f"{where}: points is at most total ({total}), not {points}")
        rule, line = item.get("rule"), item.get("line")
        source = ", ".join(f"{k} {v}" for k, v in (("rule", rule), ("line", line))
                           if isinstance(v, int) and not isinstance(v, bool))
        always = item.get("always")
        if always is not None and not isinstance(always, bool):
            raise RiftError(f"{where}: always is true or false, not {always!r}")
        out.append(PlanEntry(stage, enemy.strip(), total, at_text, points, spread, hours, story,
                             _optional_int(item, "group", 0, 294, where), _optional_int(item, "like", 0, 294, where),
                             _optional_int(item, "skin", 1, 99, where), source, bool(always)))
    return out


def load(path: Path) -> list[PlanEntry]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        raise RiftError(f"cannot read {path}: {e.strerror or e}") from None
    except UnicodeDecodeError:
        raise RiftError(f"{path} is not UTF-8 text") from None
    return parse(text)


def apply(game, idx, w, mod_root: Path, entries: list[PlanEntry], dry_run: bool = False) -> list:
    """Plan and write every entry in order, each written before the next is planned.  Returns [(entry, Encounter,
    files written)]; a dry run writes into a scratch copy of the mod instead (``_destination``) and lists no files."""
    from . import encounter

    with _destination(w, mod_root, {e.stage for e in entries}, dry_run) as into:
        done = []
        for n, e in enumerate(entries):
            try:
                enc = encounter.plan(game, idx, w, into, e.stage, e.enemy, e.total, e.at, e.points,
                                     250.0 if e.spread is None else e.spread, e.group, e.story, e.like, e.skin,
                                     e.hours, always=e.always)
            except RiftError as err:
                written = sum(1 for _, _, files in done if files)
                kept = f" (encounters 0..{n - 1} are already in the mod)" if written else ""
                why = str(err).replace(str(into), str(mod_root))   # a dry run names the mod's file, not its copy's
                raise RiftError(f"encounter {n}{f' ({e.source})' if e.source else ''}: {why}{kept}") from None
            files = encounter.write(enc, into)
            done.append((e, enc, [] if dry_run else files))
        return done


@contextmanager
def _destination(w, mod_root: Path, stages, dry_run: bool):
    """Where a plan's encounters are written: the mod, or for a dry run a scratch mod that starts with a copy of
    what they read from it (each stage's enemy group lists, and the navigation mesh the stage loads when the mod
    has its own, in the form the mod keeps them) and is removed afterwards.  Each encounter is planned after the
    ones before it are written, so a dry run numbers the groups, refuses a used number and puts spawn points on
    the ground exactly as the real run does, and the mod is not touched."""
    if not dry_run:
        yield mod_root
        return
    from . import encounter, modfiles, nav, typemap

    gpl_type, nav_type = typemap.BY_EXT["gpl"], typemap.BY_EXT["nav"]
    with tempfile.TemporaryDirectory(prefix="riftstone-plan-", ignore_cleanup_errors=True) as tmp:
        scratch = Path(tmp)
        for s in sorted(stages):
            wanted = [(n.encode("latin-1"), gpl_type) for n in encounter.group_list_names(w, s)]
            wanted.append((nav.resource_name(s).encode("latin-1"), nav_type))    # nav.stage_mesh reads the mod's first
            for name, tid in wanted:
                for have, copy in zip(modfiles.paths(mod_root, name, tid), modfiles.paths(scratch, name, tid)):
                    if have.is_file():
                        copy.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(have, copy)
        yield scratch
