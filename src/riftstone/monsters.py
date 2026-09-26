"""Monsters between the games: which Dragon's Dogma Online enemy is which Dark Arisen enemy, measured, and
turning one into the other where the bodies match (``riftstone monster``; ``docs/monsters.md``).

What is measured (both games read through Riftstone's own readers; ``tools/monster_census.py`` writes the
whole report):

  body      an enemy's model.  DDDA keeps its enemies in ``rom/enemy/emNNNN.arc`` with models under
            ``model\\em\\eNN\\eNNMM\\``, DDO in ``rom/EM/EMxxxxxx.arc`` under ``obj\\em\\em01NNMM\\{model,
            model_org}\\``.  An archive's body is its model with the most joints; the body's folder is its
            family (``e5200``, ``em015200``), so DDO's variants (EM015200..EM015204) group under their base.
            Archive ids are not always model ids (DDDA's em2000.arc holds ``e0300``).
  joints    by joint id -- what motion tracks address -- with parent and offset (``port.skeleton``).
  skinned   the joints a model's meshes are bound to: every envelope (the 144-byte bone volumes after the
            mesh records, ``numEnvelopes`` per mesh) names one.  A skinned joint the destination's rig
            does not have is never moved by the destination's motions.
  motions   the joint ids a family's own motion lists (``<body>_<xx>.lmt``) drive.
  formats   each mesh's vertex format against the set the destination game uses (port's rule).
  textures  the maps the body's material binds, as sheets (``skin``/``NM``); the same sheet of the two games
            is compared at TEXTURE_SIDE px, against the mirrored picture as the baseline (texture_check).

A pair (a source body put in place of a destination body) gets one verdict, the first that holds:

  none           fewer than MIN_RIG skinned joints (a rigid prop), fewer than half of them in the destination
                 with the same parent, or no body model at all
  partial        some skinned joints are missing or re-parented there, or a mesh's vertex format or a
                 texture's format is one the destination game never uses (each listed)
  same skeleton  every skinned joint is there with the same parent; some offsets differ by more than
                 TOLERANCE: the model converts, its proportions differ from the rig the motions move
  same body      every skinned joint is there with the same parent and offset: the model converts with its
                 rebuilt materials and textures, and the destination's motions move it as their own

``convert`` puts a source's model, its rebuilt material and its converted textures over a destination
enemy's own resources (port.into_mod), so every placement of that enemy wears the source.  It is allowed
for "same body", and for "same skeleton" only when the texture check shows the sheets line up.  The
chimera family can instead become a per-placement skin (skins.py, the enemy_skins plugin).  A new enemy
class in Dark Arisen (its own archive and AI) needs an archive tag and more (docs/archive-tags.md); that
is out of reach here.  How any of it looks and plays in game stays UNKNOWN until played.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import struct
import time
import zlib
from pathlib import Path

from . import corpus, lmt, mrl, port, tex, texcodec, typemap
from .errors import FormatError, ParamError, RiftError
from .game import KINDS, Game

SCHEMA = 2
TOLERANCE = 0.5            # cm: joint offsets this close are the same joint (port.skeleton_diff's default)
TEXTURE_SIDE = 64          # sheets are compared at this width
MIN_RIG = 4                # a body bound to fewer joints is a rigid prop (crystals, boulders), not a rig
VERDICTS = ("same body", "same skeleton", "partial", "none")
MOD, MRL, TEX, LMT = (typemap.BY_EXT[e] for e in ("mod", "mrl", "tex", "lmt"))
ENEMY_ARCHIVES = {"ddda": "rom/enemy/", "ddo": "rom/EM/"}
_MODEL = {"ddda": re.compile(r"^model\\em\\e\d\d\\(e\d{4})\\([^\\]+)$", re.I),
          "ddo": re.compile(r"^obj\\em\\(em[0-9a-f]{6})\\(model|model_org)\\([^\\]+)$", re.I)}
_ARCHIVE_ID = {"ddda": re.compile(r"^(em\d{4})", re.I), "ddo": re.compile(r"^em([0-9a-f]{6})", re.I)}
_ENVELOPE = 144
_ROOT_TRACK = 255          # a motion's root / scene track, not a body joint
MAP_KINDS = ("BM", "NM", "MM", "CMM", "TM", "AM", "DM", "GM", "LM", "SM", "NUKI")
_SHEET_PREFIX = re.compile(r"^(?:d_)?(?:e\d{4}|em[0-9a-f]{6})_", re.I)
_DECISIVE = ("NM", "BM")   # normal maps first (geometry, never recoloured), then albedo


def other(kind: str) -> str:
    return "ddo" if kind == "ddda" else "ddda"


def title(kind: str) -> str:
    return KINDS[kind]["title"]


# -- one model --------------------------------------------------------------------------------------
def model_facts(data: bytes) -> dict:
    """One model's joints ([id, parent id or None, x, y, z]), the joints its meshes are bound to (the
    envelopes' bones), vertex formats (hex -> meshes) and material names; a RiftError for a model whose
    sections are not where the shared layout puts them (port.model_info)."""
    info = port.model_info(data)
    joints = []
    if info.bones:
        for j in sorted(port.skeleton(data).values(), key=lambda j: j.id):
            joints.append([j.id, j.parent, *(round(v, 4) for v in j.offset)])
    head = port.MOD_HEADER.unpack_from(data)
    nm, meshes = head[3], head[14]
    nenv = struct.unpack_from("<I", data, 0x80)[0]
    env0 = meshes + nm * port.MOD_MESH.size      # model_info checked the envelopes end at the vertex buffer
    skinned = set()
    for i in range(nenv):
        b = struct.unpack_from("<I", data, env0 + _ENVELOPE * i)[0]
        if b < info.bones:
            skinned.add(info.joints[b])
    formats: dict[str, int] = {}
    for f in info.vertex_formats:
        formats[f"0x{f:08x}"] = formats.get(f"0x{f:08x}", 0) + 1
    return {"joints": joints, "skinned": sorted(skinned), "meshes": len(info.vertex_formats), "formats": formats,
            "materials": [n.decode("latin-1") for n in info.material_names]}


def motion_joints(data: bytes) -> set[int]:
    """The joint ids a motion list's tracks address (the root track, 255, left out), read from the motion
    headers and track arrays only (lmt.py's layout; nothing is decoded)."""
    if len(data) < 8 or data[:4] != lmt.MAGIC:
        raise FormatError("lmt", "not a motion list")
    version, count = struct.unpack_from("<HH", data, 4)
    if version not in lmt.VERSIONS:
        raise FormatError("lmt", f"version {version}; Dragon's Dogma uses {lmt.VERSIONS}")
    if 8 + 4 * count > len(data):
        raise FormatError("lmt", "the motion table runs past the end")
    out: set[int] = set()
    seen: set[int] = set()
    budget = len(data) // lmt.TRACK_SIZE       # the game's track arrays never overlap, so they fit the file once
    for off in struct.unpack_from(f"<{count}I", data, 8):
        if not off:
            continue
        if off + lmt.MOTION_SIZE > len(data):
            raise FormatError("lmt", f"a motion at 0x{off:x} runs past the end")
        tracks, n = struct.unpack_from("<II", data, off)
        if tracks in seen or not n:
            continue
        seen.add(tracks)
        if tracks + n * lmt.TRACK_SIZE > len(data):
            raise FormatError("lmt", f"a track array at 0x{tracks:x} runs past the end")
        budget -= n
        if budget < 0:
            raise FormatError("lmt", "track arrays overlap more than the file can hold")
        out.update(data[tracks + k * lmt.TRACK_SIZE + 3] for k in range(n))
    out.discard(_ROOT_TRACK)
    return out


def sheet_of(texture: str) -> tuple[str, str] | None:
    """A texture's sheet and map kind: '...\\em015200_skin_NM' and '...\\e5200_skin_NM' are both
    ('skin', 'NM'); 'e0200_body_NM_HQ' is ('body', 'NM'), 'd_e5100_skin01_NM' ('skin01', 'NM') and
    'e8000_NM' ('', 'NM').  None when the name carries no map kind."""
    base = texture.rsplit("\\", 1)[-1]
    short = _SHEET_PREFIX.sub("", base)
    toks = short.split("_")
    for i, t in enumerate(toks):
        if t in MAP_KINDS and (i or short != base):
            return "_".join(toks[:i]).lower(), t
    return None


# -- reading a game -----------------------------------------------------------------------------------
def _payload(game: Game, arc_name: str, row, limit: int | None = None) -> bytes:
    """One entry of an archive (row from corpus.directory), or only its first `limit` bytes."""
    _, _, zsize, _, off = row
    path = game.vanilla_arc(arc_name)
    with open(path, "rb") as fh:
        encrypted = fh.read(4) == b"ARCC"
        fh.seek(off)
        stored = fh.read(zsize if limit is None else min(zsize, 4096 + 8 * limit))
    if limit is None:
        return corpus.payload(stored, encrypted)
    if encrypted:
        from .cipher import arc_cipher
        stored = arc_cipher().decrypt(stored[:len(stored) // 8 * 8])
    try:
        return zlib.decompressobj().decompress(stored, limit)
    except zlib.error as e:
        raise FormatError("arc", f"entry does not inflate ({e})") from None


def _names(game: Game, idx, lang: str) -> tuple[dict[str, str], list[str]]:
    """Archive id ('em5200', 'EM015202') -> the game's own name for that enemy, and warnings."""
    try:
        if game.kind == "ddda":
            from . import world
            return {k.lower(): v for k, v in world.enemy_names(game, idx, lang).items()}, []
        from . import ddo
        return {f"em{k:06x}": v for k, v in ddo.client_enemy_names(game, idx).items()}, []
    except RiftError as e:
        return {}, [f"{title(game.kind)}: enemy names are unavailable ({e})"]


def enemy_name(names: dict[str, str], kind: str, arc_id: str) -> str:
    m = _ARCHIVE_ID[kind].match(arc_id)
    key = (m.group(1) if kind == "ddda" else f"em{m.group(1)}").lower() if m else arc_id.lower()
    return names.get(arc_id.lower()) or names.get(key) or ""


def _body_rank(b: dict) -> tuple:
    return (len(b["joints"]), "\\model_org\\" not in b["name"].lower(), b["name"].lower())


def survey(game: Game, idx, lang: str = "eng", progress=None) -> dict:
    """Every enemy archive of one game: its body, family, name; every family's body, parts, motions and
    textures.  Reads directories, models, materials, motion lists and texture headers."""
    kind = game.kind
    names, warnings = _names(game, idx, lang)
    arcs = [a for (a,) in idx.db.execute("SELECT arc FROM arcs WHERE arc LIKE ? ORDER BY arc",
                                         (ENEMY_ARCHIVES[kind] + "%",))]
    if progress:
        progress.total = max(1, len(arcs))
    rows: dict[str, list] = {}
    bodies: dict[str, dict] = {}
    for a in arcs:
        rows[a] = corpus.directory(game.vanilla_arc(a))
        for row in rows[a]:
            n = row[0].decode("latin-1")
            m = _MODEL[kind].match(n) if row[1] == MOD else None
            if not m:
                continue
            b = bodies.get(n)
            if b is None:
                try:
                    facts = model_facts(_payload(game, a, row))
                except RiftError as e:
                    facts = {"joints": [], "skinned": [], "meshes": 0, "formats": {}, "materials": [],
                             "error": str(e)}
                b = bodies[n] = {"name": n, "family": m.group(1).lower(), "archives": [], **facts}
            b["archives"].append(a)
        if progress:
            progress.advance(1, a)
    enemies: dict[str, dict] = {}
    for a in arcs:
        arc_id = a.rsplit("/", 1)[-1]
        own = [bodies[n] for n in dict.fromkeys(r[0].decode("latin-1") for r in rows[a] if r[1] == MOD)
               if n in bodies and len(bodies[n]["joints"]) >= 2]
        body = max(own, key=_body_rank) if own else None
        e = {"id": arc_id, "name": enemy_name(names, kind, arc_id), "body": body["name"] if body else None,
             "family": body["family"] if body else None}
        if kind == "ddo":        # a variant's own material next to its base's model (the White Chimera's)
            key = f"obj\\em\\{arc_id.lower()}\\model\\{arc_id.lower()}".encode()
            if body is not None and any(r[0].lower() == key and r[1] == MRL for r in rows[a]) \
                    and body["name"].lower().encode() != key:
                e["material"] = key.decode()
        enemies[a] = e
    families: dict[str, dict] = {}
    for a, e in enemies.items():
        if e["family"] is None:
            continue
        f = families.setdefault(e["family"], {"key": e["family"], "game": kind, "enemies": [], "bodies": []})
        f["enemies"].append(a)
        if e["body"] not in f["bodies"]:
            f["bodies"].append(e["body"])
    for key, f in families.items():
        f["bodies"].sort(key=lambda n: _body_rank(bodies[n]), reverse=True)
        f["body"] = f["bodies"][0]
        base = f["body"].rsplit("\\", 1)[-1].lower()
        f["parts"] = sorted(n for n, b in bodies.items() if b["family"] == key and n not in f["bodies"]
                            and len(b["joints"]) >= 2 and "\\model_org\\" not in n.lower())
        own = names.get(("em" + key[1:]) if kind == "ddda" else key, "")
        for a in f["enemies"]:        # an archive whose id is not an enemy id (em5500C) is named by its family
            e = enemies[a]
            if not names.get(e["id"].lower()) and own:
                e["name"] = own
        f["name"] = own or next((enemies[a]["name"] for a in f["enemies"] if enemies[a]["name"]), "")
        f["names"] = sorted({enemies[a]["name"] for a in f["enemies"] if enemies[a]["name"]})
        # motion lists: <body>_<letters> in the family's archives
        motions, driven = {}, set()
        for a in f["enemies"]:
            for row in rows[a]:
                if row[1] != LMT:
                    continue
                n = row[0].decode("latin-1")
                tail = n.rsplit("\\", 1)[-1].lower()
                if n in motions or not tail.startswith(base + "_") or not tail[len(base) + 1:].isalpha():
                    continue
                try:
                    motions[n] = sorted(motion_joints(_payload(game, a, row)))
                except RiftError:
                    motions[n] = None
                driven.update(motions[n] or ())
        f["motions"] = sorted(motions)
        f["driven"] = sorted(driven)
        home = bodies[f["body"]]["archives"][0]
        f["alternates"] = _alternates(game, home, rows, bodies[f["body"]])
        f["textures"] = _textures(game, idx, [f["body"]] + f["alternates"], home, rows)
    return {"game": kind, "root": str(game.root), "families": families, "enemies": enemies, "bodies": bodies,
            "warnings": warnings}


def _alternates(game: Game, arc_name: str, rows: dict, body: dict) -> list[str]:
    """The other materials in the body's archive made for it (a material for each of its material names,
    port.covers): DDDA's full-detail ``<body>_a`` that the game puts on the unit by path, variants' own."""
    want = {zlib.crc32(n.encode("latin-1")) ^ 0xFFFFFFFF for n in body["materials"]}
    out = []
    for r in rows[arc_name]:
        n = r[0].decode("latin-1")
        if r[1] != MRL or n == body["name"] or not want:
            continue
        try:
            if want <= {x.material_hash for x in mrl.parse(_payload(game, arc_name, r)).materials}:
                out.append(n)
        except RiftError:
            continue
    return out


def _textures(game: Game, idx, materials: list[str], arc_name: str, rows: dict) -> list:
    """[texture, format, width, height] for every map the given materials of an archive bind (read from
    each texture's header only), each once."""
    names = []
    for mat in materials:
        mrow = next((r for r in rows[arc_name] if r[1] == MRL and r[0].decode("latin-1") == mat), None)
        try:
            names += port.used_textures(_payload(game, arc_name, mrow)) if mrow is not None else []
        except RiftError:
            continue
    out = []
    for n in dict.fromkeys(names):
        where = [arc_name] if any(r[1] == TEX and r[0].decode("latin-1") == n for r in rows[arc_name]) \
            else idx.archives_with(n.encode("latin-1"), TEX)[:1]
        if not where:
            out.append([n, None, 0, 0])
            continue
        rs = rows.get(where[0]) or corpus.directory(game.vanilla_arc(where[0]))
        row = next((r for r in rs if r[1] == TEX and r[0].decode("latin-1") == n), None)
        try:
            h = _payload(game, where[0], row, 16) if row is not None else b""
        except RiftError:
            h = b""
        if len(h) < 16 or h[:4] != b"TEX\0":
            out.append([n, None, 0, 0])
            continue
        _, w2, w3 = struct.unpack_from("<III", h, 4)          # tex.parse's header words
        out.append([n, (w3 >> 8) & 0xFF, (w2 >> 6) & 0x1FFF, (w2 >> 19) & 0x1FFF])
    return out


# -- two bodies ---------------------------------------------------------------------------------------
def _joint_map(body: dict) -> dict[int, tuple]:
    return {j[0]: (j[1], tuple(j[2:5])) for j in body["joints"]}


def compare(src: dict, dst: dict, dst_kind: str, tolerance: float = TOLERANCE, src_driven=(), dst_driven=(),
            dst_parts: dict | None = None, textures: list | None = None) -> dict:
    """Put the source body in place of the destination body: every joint the source's meshes are bound to,
    by what it is in the destination's rig, and the verdict (module docstring).  `dst_parts` {part model:
    body} lets missing joints be named by the destination part that has them; `textures` is the source's
    [name, format, w, h] list (formats the destination never uses make it partial)."""
    sj, dj = _joint_map(src), _joint_map(dst)
    skinned = [j for j in (src["skinned"] or sorted(sj)) if j in sj]
    same, moved, reparented, missing = [], [], [], []
    for j in skinned:
        a, b = sj[j], dj.get(j)
        if b is None:
            missing.append(j)
        elif a[0] != b[0]:
            reparented.append(j)
        else:
            d = max(abs(p - q) for p, q in zip(a[1], b[1]))
            if not all(math.isfinite(v) for v in (*a[1], *b[1])):
                moved.append([j, None])                  # an offset that is not a number is no known place
            elif d > tolerance:
                moved.append([j, round(d, 2)])
            else:
                same.append(j)
    foreign = sorted(f for f in src["formats"] if int(f, 16) not in port.VERTEX_FORMATS[dst_kind])
    tex_foreign = sorted({t[1] for t in (textures or ()) if t[1] is not None and t[1] not in port.TEX_FORMATS[dst_kind]})
    in_parts = {}
    for name, part in sorted((dst_parts or {}).items()):
        have = sorted(set(missing) & set(_joint_map(part)))
        if have:
            in_parts[name] = have
    n = len(skinned)
    parented = len(same) + len(moved)
    why = []
    if src.get("error"):
        why.append(f"the source model does not read: {src['error']}")
    if not n:
        verdict = "none"
        why.append("the source model binds its meshes to no joint")
    elif n < MIN_RIG:
        verdict = "none"
        why.append(f"its meshes are bound to {n} joint(s): a rigid object, not a creature's rig to compare")
    elif parented * 2 < n:
        verdict = "none"
        why.append(f"only {parented} of the {n} joints its meshes are bound to are in the destination's rig with "
                   "the same parent")
    elif missing or reparented or foreign or tex_foreign:
        verdict = "partial"
    elif moved:
        verdict = "same skeleton"
    else:
        verdict = "same body"
    if verdict != "none":
        if missing:
            why.append(f"{len(missing)} joint(s) its meshes are bound to are not in the destination's rig: "
                       f"{_ids(missing)}" + "".join(f"; {len(v)} of them are {k.rsplit(chr(92), 1)[-1]}'s, a separate "
                                                   "model there" for k, v in in_parts.items()))
        if reparented:
            why.append(f"{len(reparented)} joint(s) hang off another parent there: {_ids(reparented)}")
        if moved:
            known = [m for m in moved if m[1] is not None]
            worst = max(known, key=lambda m: m[1]) if known else None
            why.append(f"{len(moved)} joint(s) sit elsewhere from their parent"
                       + (f" (up to {worst[1]} cm, joint {worst[0]})" if worst else " (offsets that are not numbers)"))
        if foreign:
            why.append(f"vertex format(s) {', '.join(foreign)} are used by no {dst_kind.upper()} model")
        if tex_foreign:
            why.append(f"texture format(s) {', '.join(map(str, tex_foreign))} are used by no {dst_kind.upper()} texture")
    driven = set(dst_driven)
    src_motion = set(src_driven)
    fit = [j for j in src_motion if j in dj and j in sj and dj[j][0] == sj[j][0]]
    return {"verdict": verdict, "why": why, "score": round((len(same) + 0.5 * len(moved)) / n, 4) if n else 0.0,
            "skinned": n, "same": same, "moved": moved, "reparented": reparented, "missing": missing,
            "missing_in_parts": in_parts, "joints": [len(sj), len(dj), len(set(sj) & set(dj))],
            "moved_by_destination": len([j for j in skinned if j in driven]),
            "source_motions_fit": [len(fit), len(src_motion)],
            "formats_foreign": foreign, "texture_formats_foreign": tex_foreign,
            "formats_shared": sorted(set(src["formats"]) & set(dst["formats"]))}


def _ids(ids) -> str:
    """[1, 2, 3, 7, 9, 10] -> '1-3, 7, 9-10'."""
    ids = sorted(ids)
    out, i = [], 0
    while i < len(ids):
        j = i
        while j + 1 < len(ids) and ids[j + 1] == ids[j] + 1:
            j += 1
        out.append(str(ids[i]) if i == j else f"{ids[i]}-{ids[j]}")
        i = j + 1
    return ", ".join(out)


def names_match(a: list[str], b: list[str]) -> bool:
    """Two games' names for an enemy agree (case, accents, spacing and singular/plural aside:
    DDDA names its enemies in the plural, DDO in the singular)."""
    from .world import name_keys

    ka = set().union(*(name_keys(n) for n in a if n)) if a else set()
    kb = set().union(*(name_keys(n) for n in b if n)) if b else set()
    return bool(ka & kb)


def pair(sv: dict, src_family: str, dv: dict, dst_family: str, tolerance: float = TOLERANCE,
         src_body: str | None = None, dst_body: str | None = None) -> dict:
    """compare() for two families of two surveys (or two chosen bodies of them)."""
    sf, df = sv["families"][src_family], dv["families"][dst_family]
    sb, db = sv["bodies"][src_body or sf["body"]], dv["bodies"][dst_body or df["body"]]
    parts = {p: dv["bodies"][p] for p in df["parts"]}
    out = compare(sb, db, dv["game"], tolerance, sf["driven"], df["driven"], parts, sf.get("textures"))
    out.update(source=f"{sv['game']}:{src_family}", target=f"{dv['game']}:{dst_family}", source_body=sb["name"],
               target_body=db["name"])
    return out


def counterparts(sv: dict, dv: dict, tolerance: float = TOLERANCE) -> dict[str, dict]:
    """For each family of sv: its counterpart in dv -- the best verdict; among equal verdicts the family with
    the same name, then the higher score (skinned joints in place), then the closer joint set -- and the
    namesake, when a family with the same name is not the counterpart."""
    out = {}
    for key, f in sorted(sv["families"].items()):
        ranked = []
        for k2, g in dv["families"].items():
            p = pair(sv, key, dv, k2, tolerance)
            own = names_match([f["name"]], [g["name"]])
            anyn = own or names_match(f["names"], g["names"])
            sj, dj = _joint_map(sv["bodies"][f["body"]]), _joint_map(dv["bodies"][g["body"]])
            ranked.append(((-VERDICTS.index(p["verdict"]), 2 if own else 1 if anyn else 0, round(p["score"], 3),
                            -len(set(sj) ^ set(dj)), k2), k2, p, anyn))
        ranked.sort(key=lambda r: r[0], reverse=True)
        best = ranked[0] if ranked else None
        entry = {"counterpart": None, "pair": None, "namesake": None, "namesake_pair": None}
        if best is not None and best[2]["verdict"] != "none":
            entry["counterpart"], entry["pair"] = best[1], best[2]
        named = next((r for r in ranked if r[3]), None)
        if named is not None and named[1] != entry["counterpart"]:
            entry["namesake"], entry["namesake_pair"] = named[1], named[2]
        if entry["counterpart"] is None and best is not None:
            entry["pair"] = best[2]                   # the closest one, to say why nothing fits
            entry["closest"] = best[1]
        out[key] = entry
    return out


# -- do the textures line up? ------------------------------------------------------------------------
class _Textures:
    """Textures of one game by engine name, read from the first archive that holds each."""

    def __init__(self, game: Game, idx):
        self.game, self.idx, self.dirs = game, idx, {}

    def get(self, name: str) -> tex.Tex | None:
        arcs = self.idx.archives_with(name.encode("latin-1"), TEX)
        if not arcs:
            return None
        if arcs[0] not in self.dirs:
            self.dirs[arcs[0]] = {(r[0], r[1]): r for r in corpus.directory(self.game.vanilla_arc(arcs[0]))}
        row = self.dirs[arcs[0]].get((name.encode("latin-1"), TEX))
        return tex.parse(_payload(self.game, arcs[0], row)) if row is not None else None


def _mip(t: tex.Tex, side: int) -> tuple[int, int, bytes]:
    level = 0
    while level + 1 < t.mip_count and (t.width >> level) > side:
        level += 1
    return texcodec.decode(t, level)


def picture_difference(a: tuple[int, int, bytes], b: tuple[int, int, bytes], channels=(0, 1, 2),
                       mirror: bool = False) -> float:
    """Mean absolute difference (0..255) of two equally sized RGBA pictures over the given channels;
    `mirror` reads the second one flipped left to right (u -> 1 - u)."""
    w, h, pa = a
    w2, h2, pb = b
    if (w, h) != (w2, h2):
        raise ParamError("pictures of different sizes")
    total = 0
    for y in range(h):
        row = y * w
        for x in range(w):
            o = (row + x) * 4
            o2 = (row + (w - 1 - x if mirror else x)) * 4
            for c in channels:
                total += abs(pa[o + c] - pb[o2 + c])
    return total / (w * h * len(channels)) if w * h else 0.0


def sheet_result(difference: float, mirrored: float) -> str:
    """'same' when the pictures agree far better than the first agrees with the mirrored second (the UV
    layouts line up), 'different' when they do not, 'flat' when the picture has too little structure to
    tell (the mirrored picture is nearly as close)."""
    if mirrored < 12:
        return "flat"
    if difference <= 16 and difference * 3 <= mirrored:
        return "same"
    return "different" if difference * 2 > mirrored else "flat"


def texture_check(src: _Textures, src_textures: list, dst: _Textures, dst_textures: list,
                  side: int = TEXTURE_SIDE) -> dict:
    """Do two bodies' texture sheets line up?  Every normal map (else albedo map) of one that has a sheet of
    the same name and shape in the other is decoded at `side` px and compared (sheet_result)."""
    def sheets(lst):                 # per sheet the largest map (not an 8 px stand-in such as d_e5200_skin_BM)
        out = {}
        for t in lst:
            k = sheet_of(t[0])
            if k is not None and k[1] in _DECISIVE and (k not in out or t[2] * t[3] > out[k][2] * out[k][3]):
                out[k] = t
        return out

    a, b = sheets(src_textures), sheets(dst_textures)
    rows = []
    for key in sorted(set(a) & set(b), key=lambda k: (_DECISIVE.index(k[1]), k[0])):
        ta, tb = a[key], b[key]
        row = {"sheet": key[0], "kind": key[1], "source": ta[0], "target": tb[0]}
        rows.append(row)
        if not (ta[2] and tb[2] and ta[3] and tb[3]) or ta[2] * tb[3] != tb[2] * ta[3]:
            row["result"] = "other shape"
            continue
        try:
            xa, xb = src.get(ta[0]), dst.get(tb[0])
            if xa is None or xb is None:
                row["result"] = "missing"
                continue
            s = min(side, xa.width, xb.width)
            pa, pb = _mip(xa, s), _mip(xb, s)
            chans = (0, 1) if key[1] == "NM" else (0, 1, 2)
            d = picture_difference(pa, pb, chans)
            m = picture_difference(pa, pb, chans, mirror=True)
        except (RiftError, ValueError) as e:
            row["result"] = f"not decoded ({e})"
            continue
        row.update(size=[pa[0], pa[1]], difference=round(d, 1), mirrored=round(m, 1), result=sheet_result(d, m))
    measured = [r for r in rows if "difference" in r]
    decisive = [r for r in measured if r["kind"] == "NM"] or [r for r in measured if r["kind"] == "BM"]
    if not decisive:
        verdict = "not compared"
    elif any(r["result"] == "different" for r in decisive):
        verdict = "differ"
    elif any(r["result"] == "same" for r in decisive):
        verdict = "line up"
    else:
        verdict = "inconclusive"
    return {"verdict": verdict, "sheets": rows}


# -- the census ---------------------------------------------------------------------------------------
def signature(idxs: dict) -> str:
    from .world import _signature

    h = hashlib.sha1(f"{SCHEMA}".encode())
    for k in ("ddda", "ddo"):
        h.update(k.encode() + _signature(idxs[k]).encode())
    return h.hexdigest()


def census(games: dict, idxs: dict, lang: str = "eng", tolerance: float = TOLERANCE, textures: bool = True,
           progress=None) -> dict:
    """Both games surveyed, every family's counterpart and verdict both ways, and (textures=True) the
    texture check of every counterpart and namesake pair."""
    t0 = time.time()
    sv = {k: survey(games[k], idxs[k], lang, progress) for k in ("ddda", "ddo")}
    cp = {k: counterparts(sv[k], sv[other(k)], tolerance) for k in ("ddda", "ddo")}
    out = {"schema": f"riftstone.monsters/{SCHEMA}", "tolerance": tolerance, "lang": lang, "signature": signature(idxs),
           "textures": False, "ddda": sv["ddda"], "ddo": sv["ddo"], "counterparts": cp}
    if textures:
        add_textures(out, games, idxs)
    out["summary"] = summary(out)
    out["seconds"] = round(time.time() - t0, 1)
    return out


def add_textures(c: dict, games: dict, idxs: dict) -> None:
    """The texture check of every counterpart and namesake pair, in place."""
    readers = {k: _Textures(games[k], idxs[k]) for k in ("ddda", "ddo")}
    done: dict = {}
    for k in ("ddda", "ddo"):
        sv, dv = c[k], c[other(k)]
        for key, e in c["counterparts"][k].items():
            for slot in ("counterpart", "namesake"):
                target = e.get(slot)
                if target is None:
                    continue
                pk = (k, key, target)
                if pk not in done:
                    done[pk] = texture_check(readers[k], sv["families"][key]["textures"], readers[other(k)],
                                             dv["families"][target]["textures"])
                e[slot + "_textures"] = done[pk]
    c["textures"] = True


def summary(c: dict) -> dict:
    out = {}
    for k in ("ddda", "ddo"):
        fams = c[k]["families"]
        counts = {v: 0 for v in VERDICTS}
        for e in c["counterparts"][k].values():
            counts[e["pair"]["verdict"] if e["counterpart"] else "none"] += 1
        out[k] = {"families": len(fams), "enemies": len(c[k]["enemies"]),
                  "enemies_without_body": sum(1 for e in c[k]["enemies"].values() if e["family"] is None),
                  "verdicts": counts}
    return out


def cache_path(games: dict) -> Path:
    from .index import home

    key = hashlib.sha1("|".join(str(games[k].root.resolve()).lower() for k in ("ddda", "ddo")).encode()).hexdigest()
    return home() / f"monsters-{key[:12]}.json"


def load(games: dict, idxs: dict, rebuild: bool = False, textures: bool = False, lang: str = "eng",
         progress_factory=None) -> dict:
    """The census, from the cache when both games are unchanged (and it has what is asked), else computed
    and cached (%LOCALAPPDATA%\\Riftstone)."""
    path = cache_path(games)
    sig = signature(idxs)
    if not rebuild and path.is_file():
        try:
            c = normalise(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, AttributeError, RiftError):
            c = None
        if c is not None and c.get("signature") == sig and c.get("lang") == lang and c.get("tolerance") == TOLERANCE:
            if textures and not c.get("textures"):
                add_textures(c, games, idxs)
                save(c, path)
            return c
    bar = progress_factory() if progress_factory else None
    c = census(games, idxs, lang, TOLERANCE, textures, bar)
    if bar:
        bar.done(f"Monster census ready: {c['summary']['ddo']['families']} Online and "
                 f"{c['summary']['ddda']['families']} Dark Arisen families")
    save(c, path)
    return c


def save(c: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(c, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def normalise(c: dict) -> dict:
    """A census read back from JSON is the census that was written (JSON keeps lists and string keys)."""
    for k in ("ddda", "ddo"):
        if not isinstance(c.get(k), dict) or not isinstance(c[k].get("families"), dict):
            raise RiftError("not a monster census")
    return c


# -- what people type -----------------------------------------------------------------------------------
def resolve(c: dict, kind: str, query: str) -> tuple[str, str | None]:
    """(family key, enemy archive or None) of one game for what someone typed: an archive or enemy id
    (EM015202, 0x015202, em5200, e5200, 5200), a family key, or an enemy's name ('White Chimera', 'wolves').
    Several families answering one name are listed and refused."""
    from .world import match_names

    sv = c[kind]
    q = " ".join(str(query).strip().split())
    if not q:
        raise RiftError(f"which {title(kind)} enemy? an id or a name (riftstone monster list --game {kind})")
    low = q.lower()
    by_id = {e["id"].lower(): a for a, e in sv["enemies"].items()}
    m = re.fullmatch(r"(?:0x|em)?([0-9a-f]{6})", low) if kind == "ddo" else re.fullmatch(r"(?:em|e)?(\d{4})", low)
    eid = low if low in by_id else f"em{m.group(1)}" if m else None      # an archive id as it is (em5500C) too
    if eid in by_id:
        a = by_id[eid]
        if sv["enemies"][a]["family"] is None:
            raise RiftError(f"{sv['enemies'][a]['id']} ({sv['enemies'][a]['name'] or 'no name'}) has no enemy body "
                            "model in its archive (built from equipment or effects): nothing to convert")
        return sv["enemies"][a]["family"], a
    if m:
        fam = f"em{m.group(1)}" if kind == "ddo" else "e" + m.group(1)
        if fam in sv["families"]:
            return fam, None
    if low in sv["families"]:
        return low, None
    names = {a: e["name"] for a, e in sv["enemies"].items() if e["name"] and e["family"]}
    for found in match_names(q, names):
        if not found:
            continue
        fams = sorted({sv["enemies"][a]["family"] for a in found})
        if len(fams) > 1:
            listed = ", ".join(f"{f} ({sv['families'][f]['name'] or '?'})" for f in fams[:10])
            raise RiftError(f"{q!r} could be {listed}{' ...' if len(fams) > 10 else ''}; say which by id")
        own = [a for a in found if sv["enemies"][a]["id"].lower() == fams[0]
               or sv["enemies"][a]["id"].lower() == "em" + fams[0][1:]]
        return fams[0], sorted(own or found)[0]
    raise RiftError(f"no {title(kind)} enemy is called {q!r} (riftstone monster list --game {kind})")


# -- converting -----------------------------------------------------------------------------------------
SKIN_VARIANTS = {"em015202": "white", "em015203": "shadow", "em015204": "blaze"}   # tools/ddo_skins.py's


def skin_family() -> str:
    """The Dark Arisen family whose placements can wear a skin (the chimera's model folder, e5200)."""
    from . import skins

    return skins.FAMILIES["chimera"].folder.rsplit("\\", 1)[-1].lower()


def _enemy_label(sv: dict, archive: str | None, family: str) -> str:
    if archive is not None:
        e = sv["enemies"][archive]
        return f"{e['id']} {e['name']}".strip()
    f = sv["families"][family]
    return f"{family} {f['name']}".strip()


def plan(c: dict, games: dict, idxs: dict, src_kind: str, source: str, target: str, skin: int | None = None) -> dict:
    """What `riftstone monster convert` would do: both sides resolved, the pair compared (verdict, reasons,
    texture check) and whether it is allowed.  Nothing is written."""
    return plan_for(c, games, idxs, src_kind, resolve(c, src_kind, source), resolve(c, other(src_kind), target), skin)


def plan_for(c: dict, games: dict, idxs: dict, src_kind: str, source: tuple, target: tuple,
             skin: int | None = None) -> dict:
    """plan() for resolved sides: (family key, enemy archive or None) each."""
    dst_kind = other(src_kind)
    (sf, sa), (df, da) = source, target
    sv, dv = c[src_kind], c[dst_kind]
    sbody = sv["enemies"][sa]["body"] if sa else sv["families"][sf]["body"]
    dbody = dv["enemies"][da]["body"] if da else dv["families"][df]["body"]
    p = pair(sv, sf, dv, df, src_body=sbody, dst_body=dbody)
    readers = {k: _Textures(games[k], idxs[k]) for k in ("ddda", "ddo")}
    tx = texture_check(readers[src_kind], sv["families"][sf]["textures"], readers[dst_kind],
                       dv["families"][df]["textures"])
    out = {"source": {"game": src_kind, "family": sf, "archive": sa, "body": sbody, "enemy": _enemy_label(sv, sa, sf),
                      "material": sv["enemies"][sa].get("material") if sa else None},
           "target": {"game": dst_kind, "family": df, "archive": da, "body": dbody, "enemy": _enemy_label(dv, da, df)},
           "pair": p, "textures": tx, "skin": skin, "allowed": False, "refused": [], "notes": []}
    if skin is not None:
        return _skin_plan(out, c)
    v = p["verdict"]
    if v == "same body":
        out["allowed"] = True
    elif v == "same skeleton" and tx["verdict"] == "line up":
        out["allowed"] = True
        out["notes"].append("same skeleton, and the texture sheets line up: the model converts; its proportions "
                            "differ from the rig the destination's motions were made for")
    else:
        out["refused"] = [f"{out['source']['enemy']} -> {out['target']['enemy']}: {v}"] + p["why"]
        if v == "same skeleton":
            out["refused"].append(f"texture sheets: {tx['verdict']}; a same-skeleton conversion needs them to line up")
        if src_kind == "ddo" and SKIN_VARIANTS.get((sv["enemies"][sa]["id"] if sa else sf).lower()) \
                and df == skin_family() and tx["verdict"] == "line up":
            out["refused"].append("as a skin it works: the texture sheets line up, so add --as-skin <1..99> to give "
                                  "chosen chimera placements this look (the enemy_skins plugin)")
    parts = dv["families"][df]["parts"]
    if parts:
        out["notes"].append(f"the destination's separate part model(s) {', '.join(n.rsplit(chr(92), 1)[-1] for n in parts)} "
                            "stay its own")
    users = [a for a in dv["enemies"] if a in dv["bodies"][dbody]["archives"] and a != da]
    if users:
        out["notes"].append("every enemy wearing the same model changes too: " + ", ".join(
            f"{dv['enemies'][a]['id']} {dv['enemies'][a]['name']}".strip() for a in users[:8])
                            + (" ..." if len(users) > 8 else ""))
    alts = dv["families"][df].get("alternates") or []
    if alts:
        out["notes"].append(f"{', '.join(n.rsplit(chr(92), 1)[-1] for n in alts)}: material(s) made for the same model, "
                            "which the game may put on the unit by path as it does the chimera's e5200_a; a "
                            "conversion rebuilds them too")
    out["notes"].append("the destination keeps its motions, AI, collision and parameters; what moves and hits is "
                        "its rig's, and in game everything stays UNKNOWN until played")
    return out


def _skin_plan(out: dict, c: dict) -> dict:
    """--as-skin: a Dragon's Dogma Online chimera variant as a per-placement skin of Dark Arisen's chimera
    (skins.py, tools/ddo_skins.py, the enemy_skins plugin)."""
    from . import skins

    s, t = out["source"], out["target"]
    fam = skins.FAMILIES["chimera"]
    refuse = out["refused"]
    if t["game"] != "ddda":
        refuse.append("skins are Dark Arisen's (the enemy_skins plugin): convert into a Dark Arisen chimera")
    elif t["family"] != skin_family() or (t["archive"] and t["archive"] != fam.archive):
        refuse.append(f"skins exist for Dark Arisen's chimera ({fam.enemy}) only; {t['enemy']} is not it "
                      "(the plugin leaves the Gorechimera, a table variant, alone)")
    arc_id = (c[s["game"]]["enemies"][s["archive"]]["id"].lower() if s["archive"] else s["family"])
    variant = SKIN_VARIANTS.get(arc_id)
    if s["game"] != "ddo" or variant is None:
        refuse.append("a skin is made from Dragon's Dogma Online's White, Shadow or Blaze Chimera "
                      "(EM015202..EM015204, tools/ddo_skins.py); " + f"{s['enemy']} is not one of them")
    if out["textures"]["verdict"] != "line up":
        refuse.append(f"the chimera texture sheets {out['textures']['verdict']}: a skin needs them to line up")
    try:
        skins.check_number(out["skin"])
    except ParamError as e:
        refuse.append(str(e))
    out["variant"] = variant
    out["allowed"] = not refuse
    if out["allowed"]:
        out["notes"].append("the skin's four albedo maps go into rom/enemy/em5200.arc as s%02d; only placements that "
                            "wear it change (riftstone encounter ... --skin %d; needs the enemy_skins plugin)"
                            % (out["skin"], out["skin"]))
    return out


def convert(p: dict, mod_root: Path, games: dict, idxs: dict) -> dict:
    """Carry out an allowed plan into a mod of the destination game: the source model over the target's,
    its material rebuilt from the target's, its textures converted (port.into_mod) -- or, for a skin, the
    skin's files (skins.write).  Returns {written: [paths], notes: [...]}."""
    from . import fsmap

    if not p["allowed"]:
        raise RiftError("; ".join(p["refused"]) or "not allowed")
    s, t = p["source"], p["target"]
    if p["skin"] is not None:
        return _write_skin(p, mod_root, games["ddda"], idxs["ddda"])
    resource = fsmap.encode_name(s["body"].encode("latin-1"), MOD)
    target = fsmap.encode_name(t["body"].encode("latin-1"), MOD)
    from_arc = s["archive"] or idxs[s["game"]].archives_with(s["body"].encode("latin-1"), MOD)[0]
    material = fsmap.encode_name(s["material"].encode("latin-1"), MRL) if s.get("material") else None
    res = port.into_mod(mod_root, games[s["game"]], games[t["game"]], idxs[s["game"]], idxs[t["game"]], resource,
                        as_=target, from_arc=from_arc, material=material, alternates=True)
    holders = idxs[t["game"]].archives_with(t["body"].encode("latin-1"), MOD)
    notes = list(res.notes) + [f"the model is replaced in every archive that holds it ({len(holders)}): "
                               + ", ".join(holders[:8]) + (" ..." if len(holders) > 8 else "")]
    material_file = Path(mod_root) / "files" / fsmap.encode_name(t["body"].encode("latin-1"), MRL)
    mat = [material_file] if material_file.is_file() else []
    return {"written": list(res.written) + mat + list(res.textures), "notes": notes}


def _write_skin(p: dict, mod_root: Path, game: Game, idx) -> dict:
    from . import skins, studiofiles

    fam = skins.FAMILIES["chimera"]
    tool = studiofiles.ddo_tool()
    if tool is None or p["variant"] not in getattr(tool, "VARIANTS", {}):
        raise RiftError("a Dragon's Dogma Online skin needs tools/ddo_skins.py (the Riftstone checkout's)")
    skins.check_free(mod_root, fam, p["skin"])
    textures = tool.build(p["variant"])
    title_, source = tool.VARIANTS[p["variant"]][1].split(" (")[0], tool.VARIANTS[p["variant"]][1]
    res = skins.resources(game, idx, fam, p["skin"], textures)
    written = skins.write(mod_root, fam, p["skin"], res, title_, source)
    return {"written": written, "notes": [f"{fam.key} skin {p['skin']}: {source}",
                                          f"place it: riftstone encounter <stage> {fam.enemy} --count 1 --at x,y,z "
                                          f"--skin {p['skin']} --mod <this mod> (needs the enemy_skins plugin)"]}
