"""The game's world, mapped: every stage, its rooms, its group lists and groups, every layout file
and what it places, every enemy and where it spawns, what objects hold -- built from the installed
game (Riftstone ships none of it) and cached under %LOCALAPPDATA%\\Riftstone.

How the pieces link (each measured on build 2364871; docs/world-map.md has the evidence):

  stage S ── scr\\st<S>\\etc\\st<S>_<t>.gpl, t = e (enemies) n (NPCs, hostile humans) p (objects)
  │          t (AI sensor targets): the stage's groups, numbered 0..294 (the engine's group table)
  │   └ group N ── scr\\st<S>\\etc\\st<S>_<X>m<Z>n_<t>N.lot, one file per map cell (X, Z) it places
  │                something in (every e/p/t layout names a group that exists; n: 643 of 648)
  ├─ st<S>_<X>m<Z>n_s00.lot: a cell's static models (no group)
  └─ scr\\st<S>\\etc\\st<S>.spn: the stage's places -> id/DDN/message/common/map_placelist (room names)
  placement mName ── the unit it creates (em0100 a goblin, om1030 an object, em1000 a bandit)
  enemy placement mEmItemTable ── -1: the enemy's own drops, else a set of etc/item/ItemEmListSetTbl
  object placement mSetTableID / mSetItemNo ── a set of etc/item/itemSetTbl / one item id
  enemy id ── id/DDN/message/common/enemy_name_<lang>, whose labels carry the ids (e0100_goblin)
  archive A ── an rArchive resource named rom\\X inside A: A pulls in archive rom/X (arcref.py)
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
import zlib
from collections import defaultdict
from pathlib import Path

from . import gmd, gpl, lot, typemap
from .corpus import directory, encrypted, payload
from .errors import RiftError
from .game import Game

SCHEMA = 4
GROUP_SLOTS = 295           # cArray<cOmGroupData, 295>: a group's number indexes it (mGroupList has 295 entries)
GROUP_TYPES = ("e", "n", "p", "t")
_GPL_NAME = re.compile(r"^scr\\st(\d{3})\\etc\\st(\d{3})_([enpt])(_dlc01)?$", re.I)
_SPN_NAME = re.compile(r"^scr\\st(\d{3})\\etc\\st(\d{3})$", re.I)
LANGS = ("eng", "fre", "ger", "ita", "spa", "jpn", "zht")


def _home() -> Path:
    from .index import home
    return home()


# -- reading many resources, one archive open at a time ------------------------------------------
def _read_many(game: Game, wanted: dict[str, list[tuple[bytes, int]]], progress=None) -> dict:
    """{(name, type): bytes} for the (name, type) pairs wanted from each archive."""
    out = {}
    for arc_name, keys in wanted.items():
        path = game.vanilla_arc(arc_name)
        rows = {(n, t): (z, s, off) for n, t, z, s, off in directory(path)}
        enc = encrypted(path)
        with open(path, "rb") as fh:
            for key in keys:
                if key in out or key not in rows:
                    continue
                z, s, off = rows[key]
                fh.seek(off)
                out[key] = payload(fh.read(z), enc)
        if progress:
            progress.advance(1, arc_name)
    return out


def _first_archives(idx, type_id: int) -> dict[bytes, list[str]]:
    by = defaultdict(list)
    for n, a in idx.db.execute("SELECT name, arc FROM res WHERE type=? ORDER BY arc", (type_id,)):
        by[n].append(a)
    return by


# -- names from the game's own text ----------------------------------------------------------------
def _gmd_lines(game: Game, idx, name: bytes) -> list | None:
    tid = typemap.BY_EXT["gmd"]
    arcs = idx.archives_with(name, tid)
    if not arcs:
        return None
    got = _read_many(game, {arcs[0]: [(name, tid)]})
    data = got.get((name, tid))
    return gmd.parse(data).messages if data else None


_LABEL_ID = re.compile(r"^e(?:m)?(\d{4})")


def enemy_names(game: Game, idx, lang: str = "eng") -> dict[str, str]:
    """em id -> the game's name for it, from enemy_name_<lang>.  Its labels carry the ids the name
    table was written for (e0100_goblin, em0103_); the skeleton, lich/wight and mage lines use older
    ids than their archives, so those few are mapped by the archives' family (em2000.., em6000..)."""
    msgs = _gmd_lines(game, idx, f"id\\DDN\\message\\common\\enemy_name_{lang}".encode())
    if not msgs:
        return {}
    out = {}
    for m in msgs:
        mm = _LABEL_ID.match(m.label or "")
        if mm and m.text:
            out.setdefault(f"em{mm.group(1)}", m.text.strip())
    # measured: no placement uses these name-table ids; the archives that do carry their resources
    # (em2001 holds e0301 models, em2002 e0302, em2100 e0800, em2101 e0801, em6000 e5700, em6001 e5701;
    # em2003, the archers, reuses e0300/e0301)
    for old, new in (("em0300", "em2000"), ("em0301", "em2001"), ("em0302", "em2002"), ("em0303", "em2003"),
                     ("em0800", "em2100"), ("em0801", "em2101"), ("em5700", "em6000"), ("em5701", "em6001")):
        if old in out and new not in out:
            out[new] = out[old]
    return out


def place_names(game: Game, idx, lang: str = "eng") -> list[str]:
    msgs = _gmd_lines(game, idx, f"id\\DDN\\message\\common\\map_placelist_{lang}".encode())
    return [m.text or "" for m in msgs] if msgs else []


def _rooms(spn: bytes, places: list[str]) -> list[str]:
    """A stage's named places from its .spn, in the file's order, each once."""
    from . import flat

    seen, rooms = set(), []
    for rec in flat.parse(spn, "spn").data["mpPlace"]:
        pid = rec["mPlaceNameId"]
        if 0 <= pid < len(places) and places[pid] and pid not in seen:
            seen.add(pid)
            rooms.append(places[pid])
    return rooms


def stage_rooms(game: Game, idx, lang: str = "eng") -> dict[int, list[str]]:
    """Stage number -> its named rooms, read from the place lists alone (a fraction of a second; the same
    rooms the world map lists, without building it)."""
    spn = typemap.BY_EXT["spn"]
    spns = {n: a for n, a in _first_archives(idx, spn).items() if _SPN_NAME.match(n.decode("latin-1"))}
    wanted = defaultdict(list)
    for n, arcs in spns.items():
        wanted[arcs[0]].append((n, spn))
    data = _read_many(game, wanted)
    places = place_names(game, idx, lang)
    return {int(_SPN_NAME.match(n.decode("latin-1")).group(1)): _rooms(data[(n, spn)], places)
            for n in spns if (n, spn) in data}


# -- build -----------------------------------------------------------------------------------------
def _signature(idx) -> str:
    h = hashlib.sha1()
    for row in idx.db.execute("SELECT arc, size, mtime FROM arcs ORDER BY arc"):
        h.update(repr(row).encode())
    return h.hexdigest()


def cache_path(game: Game) -> Path:
    key = hashlib.sha1(str(game.root.resolve()).lower().encode()).hexdigest()[:12]
    return _home() / f"world-{key}.json"


def _f(bits_xyz) -> list[float]:
    import struct
    return [round(v, 2) for v in struct.unpack("<3f", struct.pack("<3I", *bits_xyz))]


def build(game: Game, idx, progress=None, lang: str = "eng") -> dict:
    """Read every group list, layout and place list in the game and link them (about a minute)."""
    t0 = time.time()
    LOT, GPL, SPN = typemap.BY_EXT["lot"], typemap.BY_EXT["gpl"], typemap.BY_EXT["spn"]
    lots = _first_archives(idx, LOT)
    gpls = _first_archives(idx, GPL)
    spns = _first_archives(idx, SPN)
    wanted = defaultdict(list)
    for by, tid in ((lots, LOT), (gpls, GPL), (spns, SPN)):
        for n, arcs in by.items():
            wanted[arcs[0]].append((n, tid))
    if progress:
        progress.total = len(wanted)
    data = _read_many(game, wanted, progress)

    places = place_names(game, idx, lang)
    stages: dict[int, dict] = defaultdict(lambda: {"rooms": [], "group_lists": {}, "layouts": 0, "placements": 0,
                                                   "units": defaultdict(int)})
    for n in spns:
        m = _SPN_NAME.match(n.decode("latin-1"))
        if m:
            stages[int(m.group(1))]["rooms"] = _rooms(data[(n, SPN)], places)

    groups = []
    for n, arcs in sorted(gpls.items()):
        name = n.decode("latin-1")
        m = _GPL_NAME.match(name)
        if not m:
            continue
        st, t, dlc = int(m.group(1)), m.group(3).lower(), bool(m.group(4))
        doc = gpl.parse(data[(n, GPL)])
        stages[st]["group_lists"][t + ("_dlc01" if dlc else "")] = {"resource": name, "archives": arcs,
                                                                     "groups": len(doc.groups)}
        for g in doc.groups:
            groups.append({
                "stage": st, "type": t, "dlc": dlc, "number": g["mGroup"], "list": name,
                "units": [u["name"] for u in g["mUnitKindList"]],
                "belong": [u["isBelong"] for u in g["mUnitKindList"]],
                "count_max": g["mSetCountMax"], "appear": [g["mAppearBgn"], g["mAppearEnd"]],
                "hours": [g["mDataSetHour.mSetHourBgn"], g["mDataSetHour.mSetHourEnd"]],
                "respawn": g["mRspnCondition.mRspnType"], "link": g["mSetCondition.mLinkEmGroup"]
                if g["mSetCondition.mIsEmGroupLink"] else None,
                "cells": sorted({(la["mSplitX"], la["mSplitZ"]) for la in g["mLayoutIDArray"]}),
                "priority": g["mPriority"], "class": g["mGroupClass"],
            })

    layouts = {}
    placements = []
    for n, arcs in sorted(lots.items()):
        name = n.decode("latin-1")
        ln = lot.parse_name(name)
        if ln is None:
            continue
        L = lot.parse(data[(n, LOT)])
        units = defaultdict(int)
        for i, r in enumerate(L.records):
            if r.name:
                units[r.name] += 1
            p = r.vec()
            row = [name, i, r.id, r.kind, r.name or "", _f(r.fields["mPosition"]) if p else None]
            extra = {}
            f = r.fields
            if "mEmItemTable" in f and f["mEmItemTable"] >= 0:
                extra["drop_set"] = f["mEmItemTable"]
            if "mExperienceOW" in f and f["mExperienceOW"]:
                extra["exp"] = f["mExperienceOW"]
            if f.get("mBossFlag"):
                extra["boss"] = 1
            if "mSetTableID" in f:
                for k, key in (("mSetTableID", "item_set"), ("mSetTableIDNight", "item_set_night"),
                               ("mSetItemNo", "item"), ("mSetItemNoNight", "item_night")):
                    if f[k] >= 0:
                        extra[key] = f[k]
            if f.get("mHumanEnemyKind"):
                extra["human_kind"] = f["mHumanEnemyKind"]
            if extra:
                row.append(extra)
            placements.append(row)
        layouts[name] = {"stage": ln.stage, "x": ln.x, "z": ln.z, "type": ln.type, "number": ln.number,
                         "archives": arcs, "records": L.count, "units": dict(units)}
        s = stages[ln.stage]
        s["layouts"] += 1
        s["placements"] += L.count
        for u, c in units.items():
            s["units"][u] += c

    names = enemy_names(game, idx, lang)
    enemies = defaultdict(lambda: {"placements": 0, "stages": defaultdict(int), "groups": 0, "kinds": set()})
    for row in placements:
        nm = row[4]
        if nm.startswith("em") and (lot.KINDS[row[3]][0].startswith("cSetInfoEnemy") or row[3] == 47):
            e = enemies[nm]
            e["placements"] += 1
            e["stages"][layouts[row[0]]["stage"]] += 1
            e["kinds"].add(row[3])
    for g in groups:
        if g["type"] == "e":
            for u in g["units"]:
                enemies[u]["groups"] += 1
    enemy_archives = {a.rsplit("/", 1)[-1]: a for (a,) in idx.db.execute(
        "SELECT arc FROM arcs WHERE arc LIKE 'rom/enemy/%'")}
    out_enemies = {}
    for em, e in sorted(enemies.items()):
        out_enemies[em] = {"name": names.get(em, ""), "archive": enemy_archives.get(em),
                           "placements": e["placements"], "groups": e["groups"],
                           "stages": {str(k): v for k, v in sorted(e["stages"].items())},
                           "classes": sorted(lot.KINDS[k][0] for k in e["kinds"])}

    # archive references (rArchive, "ARCS"): the holder archive pulls in the archive the resource names
    from . import arcref
    refs = sorted({(a, arcref.target(n)) for n, a in idx.db.execute(
        "SELECT name, arc FROM res WHERE type=?", (typemap.BY_EXT["arc"],))})

    return {
        "schema": SCHEMA, "signature": _signature(idx), "built": round(time.time() - t0, 1), "lang": lang,
        "archive_refs": [list(r) for r in refs],
        "stages": {str(k): {"rooms": v["rooms"], "group_lists": v["group_lists"], "layouts": v["layouts"],
                            "placements": v["placements"], "units": dict(v["units"])}
                   for k, v in sorted(stages.items())},
        "groups": groups, "layouts": layouts, "placements": placements, "enemies": out_enemies,
    }


# -- enemy names as people type them (both games: DDDA names its enemies in the plural, DDO in the singular)
def _words(text: str) -> list[str]:
    """Lower-case words without accents or punctuation: 'Foot-Biter' -> foot, biter; 'Stymphalídes' ->
    stymphalides; 'Black Dragon (Event)' -> black, dragon, event."""
    t = unicodedata.normalize("NFKD", text.casefold())
    t = unicodedata.normalize("NFKC", "".join(c for c in t if not 0x300 <= ord(c) <= 0x36F))   # Latin accents
    return re.findall(r"[^\W_]+", t)


# plural ending -> singular ending; each that fits adds a form (English, then Latin/Greek)
_PLURALS = (("s", ""), ("es", ""), ("ies", "y"), ("ves", "f"), ("ves", "fe"), ("xen", "x"), ("men", "man"),
            ("pes", "ps"), ("i", "us"), ("ae", "a"))


def _singulars(word: str) -> set[str]:
    """The word and each singular it may be the plural of: wolves -> wolf, harpies -> harpy, oxen -> ox,
    mermen -> merman, liches -> lich, cyclopes -> cyclops, succubi -> succubus, hydrae -> hydra.  A word
    that is already singular ('cyclops', 'undead') is among them as it is; the extra guesses ('cyclop')
    are harmless, since two names match only when their sets share a form."""
    out = {word}
    if len(word) < 3:
        return out
    for suffix, rep in _PLURALS:
        if word.endswith(suffix):
            one = word[:-len(suffix)] + rep
            if len(one) >= 2:
                out.add(one)
    return out


def name_keys(text: str) -> set[str]:
    """Every form a name can be matched in: its words run together (spaces and hyphens do not count,
    'dire wolf' = 'Direwolves'), the last word as written and in each possible singular."""
    words = _words(text)
    if not words:
        return set()
    head = "".join(words[:-1])
    return {head + w for w in _singulars(words[-1])}


def match_names(query: str, names: dict) -> tuple[list, list]:
    """(exact, partial) keys of `names` ({key: name}) for what someone typed: exact when the query and a
    name share a form (name_keys: case, accents, spaces, hyphens and singular/plural aside), partial when
    the query is inside a name.  Both games' enemy searches use it."""
    q = name_keys(query)
    if not q:
        return [], []
    inside = {k for k in q if len(k) >= 3} or q
    exact, partial = [], []
    for key, name in names.items():
        n = name_keys(name or "")
        if not n:
            continue
        if q & n:
            exact.append(key)
        elif any(a in b for a in inside for b in n):
            partial.append(key)
    return exact, partial


# -- the cached map ----------------------------------------------------------------------------------
class World:
    def __init__(self, data: dict):
        self.data = data
        self.stages = {int(k): v for k, v in data["stages"].items()}
        self.groups = data["groups"]
        self.layouts = data["layouts"]
        self.placements = data["placements"]
        self.enemies = data["enemies"]
        self._by_group = defaultdict(list)
        for name, lay in self.layouts.items():
            self._by_group[(lay["stage"], lay["type"], lay["number"])].append(name)

    # queries
    def stage(self, n: int) -> dict:
        if n not in self.stages:
            raise RiftError(f"stage {n} is not in this game (riftstone world stages lists them)")
        return self.stages[n]

    def groups_of(self, stage: int, type_: str | None = None) -> list[dict]:
        return [g for g in self.groups if g["stage"] == stage and (type_ is None or g["type"] == type_)]

    def group(self, stage: int, type_: str, number: int) -> dict | None:
        """The stage's group of that number: its own list's, else a DLC list's (st443_e_dlc01's 20-22), which share
        the number space."""
        dlc = None
        for g in self.groups:
            if (g["stage"], g["type"], g["number"]) == (stage, type_, number):
                if not g["dlc"]:
                    return g
                dlc = dlc or g
        return dlc

    def layouts_of(self, stage: int, type_: str, number: int) -> list[str]:
        return sorted(self._by_group.get((stage, type_, number), []))

    def placements_in(self, layout: str) -> list:
        return [p for p in self.placements if p[0] == layout]

    def free_groups(self, stage: int, type_: str = "e") -> list[int]:
        used = {g["number"] for g in self.groups_of(stage, type_)}
        return [n for n in range(GROUP_SLOTS) if n not in used]

    def find_enemy(self, key: str) -> str:
        """'em0100', '0100', 'goblin', 'wolves', 'Greater Goblins', 'dire wolf', 'succubus' -> an enemy id.
        Exact names (match_names) win over names that only contain the query; when several enemies
        match (em0500 and em0501 are both 'Undead'), they are listed and an id is asked for."""
        k = " ".join(key.strip().lower().split())
        if re.fullmatch(r"(em)?\d{4}[a-z_0-9]*", k):
            em = k if k.startswith("em") else "em" + k
            ids = {e.lower(): e for e in self.enemies}
            if em in ids:
                return ids[em]
        exact, part = match_names(key, {em: e["name"] for em, e in self.enemies.items() if e["name"]})
        for found in (exact, part):
            if len(found) == 1:
                return found[0]
            if found:
                listed = ", ".join(f"{em} ({self.enemies[em]['name']})" for em in found[:12])
                raise RiftError(f"{key!r} could be {listed}{' ...' if len(found) > 12 else ''}; "
                                f"say which by id (e.g. {found[0]})")
        raise RiftError(f"no enemy called {key!r} is placed anywhere in the game (riftstone world enemies lists them)")

    def spawns_of(self, em: str) -> list[dict]:
        """Every layout that places this enemy: stage, cell, group, count, and the group's settings."""
        rows = defaultdict(int)
        for p in self.placements:
            if p[4] == em:
                rows[p[0]] += 1
        out = []
        for name, n in sorted(rows.items()):
            lay = self.layouts[name]
            g = self.group(lay["stage"], lay["type"], lay["number"]) if lay["type"] != "s" else None
            out.append({"layout": name, "count": n, **{k: lay[k] for k in ("stage", "x", "z", "type", "number")},
                        "group": g})
        return out

    def deps(self, key: str) -> tuple[list[str], list[str], list[str]]:
        """Archive references: (the archives matching key, what they pull in, who pulls them in)."""
        refs = self.data.get("archive_refs", [])
        k = key.strip().replace("\\", "/").lower().removesuffix(".arc")
        names = sorted({a for pair in refs for a in pair})
        hit = [a for a in names if a.lower() == k] or [a for a in names if a.lower().endswith("/" + k)]
        if not hit:
            hit = [a for a in names if k in a.lower()][:20]
        if not hit:
            raise RiftError(f"no archive reference names {key!r} (riftstone find {key} --type arc finds resources)")
        pulls = sorted({t for h, t in refs if h in hit})
        pulled_by = sorted({h for h, t in refs if t in hit})
        return hit, pulls, pulled_by

    def nearest(self, stage: int, xyz, type_: str = "e"):
        """The placement of this stage (and layout type) nearest a point: (distance, row)."""
        best = None
        for p in self.placements:
            if p[5] is None:
                continue
            lay = self.layouts[p[0]]
            if lay["stage"] != stage or lay["type"] != type_:
                continue
            d = sum((a - b) ** 2 for a, b in zip(p[5], xyz)) ** 0.5
            if best is None or d < best[0]:
                best = (d, p)
        return best


def load(game: Game, idx, rebuild: bool = False, progress_factory=None, lang: str = "eng") -> World:
    """The world map, rebuilt when the game's archives changed (or when asked)."""
    path = cache_path(game)
    sig = _signature(idx)
    if not rebuild and path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            # a cache that is not the map's object (JSON null, a list: data.get raised AttributeError) is rebuilt
            if isinstance(data, dict) and data.get("schema") == SCHEMA and data.get("signature") == sig \
                    and data.get("lang") == lang:
                return World(data)
        except (OSError, ValueError, RecursionError):   # nesting deeper than the decoder goes
            pass
    bar = progress_factory() if progress_factory else None
    data = build(game, idx, bar, lang)
    if bar:
        bar.done(f"World map ready: {len(data['stages'])} stages, {len(data['groups']):,} groups, "
                 f"{len(data['layouts']):,} layouts, {len(data['placements']):,} placements")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)
    return World(data)


# -- the resource-type atlas -----------------------------------------------------------------------
def atlas(idx) -> list[dict]:
    """Every resource type in the game: class, extension, how many, where they live, and how Riftstone
    handles it (YAML, a dedicated converter, or kept as is)."""
    from . import params

    special = {"tex": "texture <-> .dds (riftstone tex)", "mrl": "materials: info + retex (riftstone mrl)",
               "ean": "byte-exact container (payload opaque)",
               "arc": "archive reference: what it pulls in (riftstone world deps)",
               "mod": "model: Blender + Albam", "lmt": "animation: Blender + Albam",
               "sbc": "collision mesh: Blender + Albam"}
    rows = []
    for t, count, distinct in idx.db.execute(
            "SELECT type, COUNT(*), COUNT(DISTINCT name) FROM res GROUP BY type ORDER BY COUNT(*) DESC"):
        ext = typemap.extension(t)
        folders = defaultdict(int)
        for (n,) in idx.db.execute("SELECT name FROM res WHERE type=? LIMIT 4000", (t,)):
            parts = n.decode("latin-1").split("\\")
            folders["\\".join(parts[:2]) if len(parts) > 2 else parts[0]] += 1
        if typemap.is_xfs(t):
            how = "YAML (XFS)"
        elif params.has_yaml_form(t):
            how = "YAML"
        else:
            how = special.get(ext, "kept byte-exact inside archives (not decoded)")
        rows.append({"ext": ext, "class": typemap.class_name(t), "count": count, "names": distinct,
                     "folders": [f for f, _ in sorted(folders.items(), key=lambda kv: -kv[1])[:3]], "riftstone": how})
    return rows
