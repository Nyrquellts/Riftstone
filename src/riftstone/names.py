"""Plain names for Dark Arisen's files and for what people type: "Goblins -- stats" where the game says
``charparam/em/em0100_cmn.prp``, and "goblin" meaning the Goblins, Hobgoblins, Grimgoblins ... with the
files that shape each (``riftstone find``).

Every name comes from the game itself; nothing here is guessed:

  enemies  the id in a path (``em0100``, ``Em0100Shl``) or its model folder (``model/em/e01/e0100/``),
           named by the game's enemy name table (world.enemy_names, the table the world map uses: its
           labels carry these ids, ``e0100_goblin``)
  stages   a ``st###`` in a path that is one of the game's stages (a ``scr/st###`` folder), with the rooms
           its place list names (``.spn``, docs/stage-map.md)
  kinds    what a file is: a plain word for the formats a modder edits most, each taken from what Riftstone
           already says about the format (help.py, prp.py), else the engine's own class name in words

A path with no known enemy or stage keeps its own name.  Labels are for reading only: resource names stay
engine identifiers everywhere else.  Dragon's Dogma Online names its enemies and places on its server
(ddo.py), so its files get plain kinds here but no enemy or stage names.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import fsmap, typemap

# extension -> what the file is, in a player's words (the formats a modder edits most)
KINDS: dict[str, str] = {
    "prp": "stats",                    # prp.py: attack, defence, magick, weight, size, resistances, EXP
    "ajp": "stat adjustments",         # help.py: enemy stat adjustments (a float array)
    "shl": "shells (projectiles)",     # help.py, XFS: shells (projectiles)
    "ocl": "hit shapes and attacks",   # help.py: groups of collision shapes, the attacks a hit carries
    "eap": "AI actions",               # help.py: which enemy AI action fires under which conditions
    "sap": "stage AI actions",         # help.py: stage AI actions, gated by scenario and hour
    "fsm": "AI state machine",         # help.py: AI state machine (rAIFSM)
    "rst": "health",                   # flat.py: mHPMax, a creature's base HP per region (docs/enemy-hp.md)
    "lot": "placements",               # help.py: where enemies, NPCs and objects stand
    "gpl": "groups",                   # help.py: a stage's groups
    "spn": "room names",               # help.py: the stage -> room-name map
    "stp": "start positions",          # its class, rStartPos (a stage's doors, docs/level-synthesis.md)
    "nav": "navigation mesh",          # help.py
    "gmd": "text",                     # help.py
    "tex": "texture",                  # help.py
    "mrl": "material",                 # help.py
    "mod": "model",                    # its class, rModel
    "lmt": "animations",               # help.py
    "itl": "item list",                # help.py
    "ist": "item sets and drops",      # help.py: item sets / drop tables
    "imx": "recipes",                  # help.py
}
# the files that shape an enemy, in the order a modder reaches for them: stats and health, attacks, AI, body
ENEMY_FILES = ("prp", "rst", "ajp", "shl", "ocl", "eap", "gop", "sn2", "stg", "fsm", "mod")
FOLDER_FILES = ("ocl", "mod")          # taken from the enemy's model folder too: the body's hit shapes, its models
LANGUAGES = {"eng": "English", "fre": "French", "ger": "German", "ita": "Italian", "spa": "Spanish",
             "jpn": "Japanese", "zht": "Chinese"}      # the text files' language codes (world.LANGS)

_EM = re.compile(r"(?<![a-z0-9])em(\d{4})(?!\d)", re.I)                  # em0100, Em0100Shl
_FOLDER = re.compile(r"(?:^|/)em/e\d\d/e(\d{4})(?=/|$)", re.I)          # model/em/e01/e0100/...
_ST = re.compile(r"(?<![a-z0-9])st(\d{3})(?!\d)", re.I)
_LANG = re.compile(r"_(eng|fre|ger|ita|spa|jpn|zht)$", re.I)
_GPL = re.compile(r"^scr/st(\d{3})/etc/st\1_([enpt])(_dlc\d\d)?$", re.I)
_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


def class_words(cls: str) -> str:
    """An engine class name as words: rWeatherEffectParam -> 'weather effect param', rAISensorExt ->
    'AI sensor ext'."""
    name = cls[1:] if len(cls) > 1 and cls[0] == "r" and cls[1].isupper() else cls
    words = _WORD.findall(name)
    return " ".join(w if len(w) > 1 and w.isupper() else w.lower() for w in words) or cls


def kind_of(ext: str) -> str:
    """What a file of this extension is: a plain word (KINDS), else its engine class in words."""
    ext = str(ext).lower()
    if ext in KINDS:
        return KINDS[ext]
    tid = typemap.type_for_extension(ext) if ext else None
    if tid is None or tid not in typemap.BY_ID:
        return f".{ext} file" if ext else "file"
    return class_words(typemap.class_name(tid))


def _plain(text: str) -> str:
    from .world import _words
    return " ".join(_words(text))


@dataclass
class Names:
    """Dark Arisen's enemy and stage names.  Empty tables give files their own names, never an error."""
    enemies: dict[str, str] = field(default_factory=dict)         # em0100 -> "Goblins"
    rooms: dict[int, list[str]] = field(default_factory=dict)      # 220 -> ["Urban Quarter", ...]
    stages: set[int] = field(default_factory=set)                  # every stage (its scr/st### folder)

    @classmethod
    def load(cls, game, idx) -> "Names":
        """The names in this game (a fraction of a second: the name tables and the place lists)."""
        if game.kind != "ddda":
            return cls()
        from . import world

        enemies = {em.lower(): " ".join(n.split()) for em, n in world.enemy_names(game, idx).items() if n.strip()}
        rooms = world.stage_rooms(game, idx)
        return cls(enemies, rooms, stage_numbers(idx) | set(rooms))

    # -- one path ---------------------------------------------------------------------------------
    def describe(self, path: str) -> dict:
        """An engine path (``charparam/em/em0100_cmn.prp``; a mod's ``.yaml`` copy too) -> its title
        ("Goblins -- stats"), kind, the enemy and stage it is about, and where that stage is (its rooms)."""
        p = str(path).replace("\\", "/")
        if p.lower().endswith(".yaml"):
            p = p[:-5]
        p = p.strip("/")
        base = p.rsplit("/", 1)[-1]
        stem, dot, ext = base.rpartition(".")
        if not dot:
            stem, ext = base, ""
        kind = kind_of(ext) if ext else "file"
        name = p[:len(p) - len(ext) - 1] if ext else p
        out = {"title": "", "kind": kind, "enemy": None, "stage": None, "where": ""}
        for m in (*_EM.finditer(p), *_FOLDER.finditer(p)):
            em = "em" + m.group(1)
            if em in self.enemies:
                out["enemy"] = em
                break
        for m in _ST.finditer(p):
            if int(m.group(1)) in self.stages:
                out["stage"] = int(m.group(1))
                rooms = self.rooms.get(out["stage"], [])
                out["where"] = ", ".join(rooms[:3]) + (", ..." if len(rooms) > 3 else "")
                break
        detail = []
        e = ext.lower()
        if e == "lot":
            from . import lot
            ln = lot.parse_name(name)
            if ln is not None:
                detail.append(lot.TYPES[ln.type] + ("" if ln.type == "s" else f", group {ln.number}"))
        elif e == "gpl":
            g = _GPL.match(name)
            if g:
                from . import lot
                detail.append(lot.TYPES[g.group(2).lower()] + (", DLC list" if g.group(3) else ""))
        elif e == "gmd":
            lang = _LANG.search(stem)
            if lang:
                detail.append(LANGUAGES[lang.group(1).lower()])
                stem = stem[:lang.start()]
        if out["enemy"]:
            subject = self.enemies[out["enemy"]]
            if out["stage"] is not None:
                detail.append(f"stage {out['stage']}")
        elif out["stage"] is not None and (e in ("lot", "gpl", "spn", "stp", "nav")
                                           or stem.lower() == f"st{out['stage']:03d}"):
            subject = f"Stage {out['stage']}"
        else:
            subject = stem or base or "?"
            if out["stage"] is not None:
                detail.append(f"stage {out['stage']}")
        out["title"] = f"{subject} -- {kind}" + (f" ({', '.join(detail)})" if detail else "")
        return out

    # -- what a search means --------------------------------------------------------------------
    def match(self, query: str, most: int = 8) -> dict:
        """The enemies and stages a search names: 'goblin' -> Goblins, then the names holding it
        (Hobgoblins, Greater Goblins ...); 'em0100' or '0100'; a room ('urban quarter') -> the stages with
        it; '220', 'st220' or 'stage 220'.  Both lists at most ``most`` long."""
        from .world import match_names

        q = " ".join(str(query).split())
        out = {"enemies": [], "stages": []}
        m = re.fullmatch(r"(?:em)?(\d{4})", q, re.I)
        if m:
            em = "em" + m.group(1)
            if em in self.enemies:
                out["enemies"] = [{"id": em, "name": self.enemies[em]}]
        elif len(q) >= 2:
            exact, part = match_names(q, self.enemies)
            ids = sorted(exact) + (sorted(part) if len(q) >= 3 else [])
            out["enemies"] = [{"id": em, "name": self.enemies[em]} for em in ids[:most]]
        m = re.fullmatch(r"(?:st|stage ?)?(\d{1,3})", q, re.I)
        if m:
            n = int(m.group(1))
            if n in self.stages:
                out["stages"] = [{"stage": n, "rooms": self.rooms.get(n, []), "matched": []}]
        elif len(_plain(q)) >= 3:
            key = _plain(q)
            for n, rooms in sorted(self.rooms.items()):
                hit = [r for r in rooms if key in _plain(r)]
                if hit:
                    out["stages"].append({"stage": n, "rooms": rooms, "matched": hit})
            out["stages"] = out["stages"][:most]
        return out


def stage_numbers(idx) -> set[int]:
    """Every stage of the game: the numbers of its ``scr\\st###`` folders."""
    rows = idx.db.execute("SELECT DISTINCT substr(name_lc, 7, 3) FROM res WHERE name_lc LIKE 'scr\\st___\\%'")
    return {int(s) for (s,) in rows if s.isdigit()}


def _rows(idx, like: str) -> list[tuple[bytes, int]]:
    return idx.db.execute("SELECT DISTINCT name, type FROM res WHERE name_lc LIKE ?", (like,)).fetchall()


def enemy_files(idx, em: str) -> list[dict]:
    """The files that shape one enemy -- stats, attacks, AI, its body -- as [{"path", "ext", "kind"}] in
    ENEMY_FILES order: every file whose name carries its id (``em0100``), and its model folder's hit shapes
    and models (``collision/em/e01/e0100/``, ``model/em/e01/e0100/``).  The ``_cmn`` stats come first:
    they hold the numbers (docs/re-size-scaling.md), then the health (``em0100.rst``, docs/enemy-hp.md)."""
    m = re.fullmatch(r"em(\d{4})", str(em).lower())
    if not m:
        return []
    digits = m.group(1)
    token = re.compile(rf"(?<![a-z0-9])em{digits}(?!\d)", re.I)
    folder = re.compile(rf"(?:^|\\)em\\e\d\d\\e{digits}(?:\\|$)", re.I)
    order = {typemap.BY_EXT[e]: i for i, e in enumerate(ENEMY_FILES)}
    folder_types = {typemap.BY_EXT[e] for e in FOLDER_FILES}
    found = {}
    rows = idx.db.execute(f"SELECT DISTINCT name, type FROM res WHERE type IN ({','.join('?' * len(order))})",
                          list(order))
    for n, t in rows:
        s = n.decode("latin-1")
        if token.search(s) or (t in folder_types and folder.search(s)):
            found[(n, t)] = s
    rows = sorted(found, key=lambda k: (order[k[1]], not found[k].lower().endswith("_cmn"), found[k].lower()))
    return [{"path": fsmap.encode_name(n, t), "ext": typemap.extension(t), "kind": kind_of(typemap.extension(t))}
            for n, t in rows]


def stage_files(idx, stage: int) -> list[dict]:
    """A stage's group lists and place list: [{"path", "ext", "kind", "detail"}]."""
    from . import lot

    gpl, spn = typemap.BY_EXT["gpl"], typemap.BY_EXT["spn"]
    out = []
    for n, t in sorted(_rows(idx, f"scr\\st{stage:03d}\\etc\\st{stage:03d}%")):
        name = n.decode("latin-1").replace("\\", "/")
        if t == gpl and (g := _GPL.match(name)):
            detail = lot.TYPES[g.group(2).lower()] + (", DLC list" if g.group(3) else "")
        elif t == spn and name.lower() == f"scr/st{stage:03d}/etc/st{stage:03d}":
            detail = ""
        else:
            continue
        out.append({"path": fsmap.encode_name(n, t), "ext": typemap.extension(t), "kind": kind_of(typemap.extension(t)),
                    "detail": detail})
    return out
