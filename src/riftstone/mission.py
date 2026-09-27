"""Missions: what a dungeon asks of the player, in order, before anything is placed -- written as a small
grammar in the manner of J. Dormans' mission grammars (a mission is generated first, then mapped onto
space; here the space is a stage's own walkable ground, dungeon.py).

A mission is a sequence of beats.  Five kinds exist, each one rules/beats.nyr sizes by its depth:

    Fight      a few common enemies on the way through
    Ambush     a pack beside the way (just off the main path)
    Horde      the game's horde setting: many common enemies from a few spawn points
    Guardian   one big monster holding the way
    Boss       the strongest enemy there is, at the deepest place with room for it

A beat written ``?Kind`` is a side beat: it goes off the main path (a dead end, a side room), after the main
beat before it.  A Boss, when there is one, ends the main path.

Grammars are JSON (``riftstone-mission/1``); every name that is not a beat kind is a rule, expanded left to
right by a weighted choice of its productions (``random.Random(seed)``: the same seed, the same mission)::

    {"format": "riftstone-mission/1", "start": "Dungeon",
     "rules": {"Dungeon": [["Approach", "Trial", "Depths", "Boss"]],
               "Approach": [["Fight"], ["Fight", "Fight"], ["Fight", "?Ambush"]],
               "Trial": [["Guardian"], ["Horde"]],
               "Depths": [["Fight", "Fight"], ["Horde", "?Fight"]]},
     "weights": {"Approach": [2, 3, 2]}}

A rule prefixed with ``?`` makes every beat it expands to a side beat.  Expansion stops with an error past
MAX_BEATS beats or MAX_STEPS rewrites (a grammar that never finishes), so every accepted mission is finite.
"""
from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass

from .errors import RiftError

FORMAT = "riftstone-mission/1"
KINDS = {"Fight": 1, "Ambush": 2, "Horde": 3, "Guardian": 4, "Boss": 5}   # rules/beats.nyr's beat codes
MAX_BEATS = 40
MAX_STEPS = 2000
MAX_RULES = 64
MAX_PRODUCTIONS = 16
MAX_SYMBOLS = 16
_NAME = re.compile(r"\??[A-Za-z][A-Za-z0-9_]{0,31}")     # fullmatch: "$" alone let "Fight\n" through

DEFAULT = {
    "format": FORMAT, "start": "Dungeon",
    "rules": {
        "Dungeon": [["Approach", "Trial", "Depths", "Boss"]],
        "Approach": [["Fight"], ["Fight", "Fight"], ["Fight", "?Ambush"]],
        "Trial": [["Guardian"], ["Horde"], ["Fight", "?Guardian"]],
        "Depths": [["Fight", "Fight"], ["Fight", "Ambush", "Fight"], ["Horde", "?Fight"]],
    },
    "weights": {"Approach": [2, 3, 2], "Trial": [2, 2, 1], "Depths": [3, 2, 1]},
}


@dataclass(frozen=True)
class Beat:
    kind: str                # a key of KINDS
    side: bool               # off the main path
    after: int               # side beats: the main beat they follow (-1: before the first); main: their own place

    @property
    def code(self) -> int:
        return KINDS[self.kind]


@dataclass(frozen=True)
class Grammar:
    start: str
    rules: dict              # name -> tuple of productions (tuples of symbols)
    weights: dict            # name -> tuple of weights


def parse(text: str) -> Grammar:
    try:
        doc = json.loads(text)
    except (ValueError, RecursionError) as e:            # RecursionError: nesting deeper than the decoder goes
        raise RiftError(f"the mission grammar is not JSON: {e}") from None
    return grammar(doc)


def grammar(doc) -> Grammar:
    """A checked grammar from its JSON form (every name, production and weight)."""
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise RiftError(f'not a mission grammar: expected {{"format": "{FORMAT}", "start": ..., "rules": {{...}}}}')
    unknown = sorted(set(doc) - {"format", "start", "rules", "weights"})
    if unknown:
        raise RiftError(f"the mission grammar has unknown keys {unknown}")
    rules = doc.get("rules")
    if not isinstance(rules, dict) or not rules or len(rules) > MAX_RULES:
        raise RiftError(f"rules is an object of 1..{MAX_RULES} named rules")
    out = {}
    for name, prods in rules.items():
        if not isinstance(name, str) or not _NAME.fullmatch(name) or name.startswith("?") or name in KINDS:
            raise RiftError(f"rule name {name!r}: a word (letters, digits, _), not a beat kind or ?name")
        if not isinstance(prods, list) or not prods or len(prods) > MAX_PRODUCTIONS:
            raise RiftError(f"rule {name}: 1..{MAX_PRODUCTIONS} productions, each a list of symbols")
        ps = []
        for p in prods:
            if not isinstance(p, list) or not p or len(p) > MAX_SYMBOLS or not all(isinstance(s, str) for s in p):
                raise RiftError(f"rule {name}: a production is a list of 1..{MAX_SYMBOLS} symbol names")
            for s in p:
                if not _NAME.fullmatch(s):
                    raise RiftError(f"rule {name}: {s!r} is not a symbol (a word, optionally starting with ?)")
            ps.append(tuple(p))
        out[name] = tuple(ps)
    for name, ps in out.items():
        for p in ps:
            for s in p:
                if s.lstrip("?") not in out and s.lstrip("?") not in KINDS:
                    raise RiftError(f"rule {name} uses {s!r}, which is neither a rule nor a beat kind "
                                    f"({', '.join(KINDS)})")
    start = doc.get("start")
    # not "?Rule": every beat would be a side beat, and a mission needs a main path
    if not isinstance(start, str) or not _NAME.fullmatch(start) or (start not in out and start not in KINDS):
        raise RiftError("start names a rule (or a beat kind)")
    weights = {}
    raw = doc.get("weights", {})
    if not isinstance(raw, dict):
        raise RiftError("weights is an object: rule name -> one positive number per production")
    for name, ws in raw.items():
        if name not in out:
            raise RiftError(f"weights for {name!r}, which is not a rule")
        if (not isinstance(ws, list) or len(ws) != len(out[name])
                or not all(isinstance(w, (int, float)) and not isinstance(w, bool) and 0 < w <= 1e6 for w in ws)):
            raise RiftError(f"weights of {name}: {len(out[name])} positive numbers, one per production")
        weights[name] = tuple(float(w) for w in ws)
    return Grammar(start, out, weights)


DEFAULT_GRAMMAR = grammar(DEFAULT)


def expand(g: Grammar, seed: int = 0) -> list[Beat]:
    """The mission a grammar makes with this seed: its beats in order, each main beat numbered by its place on
    the main path and each side beat by the main beat it follows."""
    rng = random.Random(seed)
    stack = [(g.start, g.start.startswith("?"))]
    flat: list[tuple[str, bool]] = []
    steps = 0
    while stack:
        steps += 1
        if steps > MAX_STEPS:
            raise RiftError(f"the grammar is still rewriting after {MAX_STEPS} steps; give its recursion a way out")
        sym, side = stack.pop(0)
        name = sym.lstrip("?")
        side = side or sym.startswith("?")
        if name in KINDS:
            flat.append((name, side))
            if len(flat) > MAX_BEATS:
                raise RiftError(f"the mission has more than {MAX_BEATS} beats; make the grammar smaller")
            continue
        prods = g.rules[name]
        ws = g.weights.get(name, (1.0,) * len(prods))
        pick = rng.choices(range(len(prods)), weights=ws)[0]
        stack[0:0] = [(s, side) for s in prods[pick]]
    beats = []
    main = -1
    for kind, side in flat:
        if side:
            beats.append(Beat(kind, True, main))
        else:
            main += 1
            beats.append(Beat(kind, False, main))
    mains = [b for b in beats if not b.side]
    if not mains:
        raise RiftError("the mission has no beat on the main path")
    if any(b.kind == "Boss" for b in mains[:-1]):
        raise RiftError("a Boss must be the last beat on the main path")
    if sum(1 for b in beats if b.kind == "Boss") > 1:
        raise RiftError("a mission has at most one Boss")
    return beats


def describe(beats: list[Beat]) -> str:
    return " -> ".join(("?" if b.side else "") + b.kind for b in beats)
