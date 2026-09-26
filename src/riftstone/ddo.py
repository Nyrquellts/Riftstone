"""Dragon's Dogma Online's world for solo play: the local server's data, named by the client.

DDO decides what spawns where, what shops sell and what enemies drop on the *server*.  Riftstone
reads the local Arrowgene server's asset folder (``EnemySpawn.json`` ...) and names everything from
the client's own files, decoded here (both exact on the whole client):

  param/enemy_group.emg  rEnemyGroup: u32 version (1), u32 count, then per record u32 nameId, u32 sort,
                         u32 n, n x u32 enemy id.  nameId labels ENEMY_NAME_<nameId> in
                         ui/00_message/enemy/enemy_name.gmd.  395 records, 515 enemy ids, the file's
                         last byte; it names 375 of the 377 enemies the server spawns.
  scr/stage_list.slt     rStageList: "slt\\0", u32 0x22, u32 count, then 17-byte records: u32 stageNo,
                         u32 type, u8, u32 name message, u32 flags.  The message's label
                         STAGE_NAME_<StageId> in ui/00_message/common/stage_list.gmd is the server's
                         StageId (561 of 561 against Arrowgene's own stage table).

Where a spawn stands: a spawn row's (StageId, GroupId, PositionIndex) is record PositionIndex of the
client layout ``scr\\stNNNN\\etc\\stNNNN_XXmYYn_eGG`` (NNNN the stage number, GG the group): 400 of 400
sampled groups have that file and every index falls inside it.  The layout's records are spawn points
(their unit id is a placeholder); the server picks the enemy.

A DDO mod's ``server/`` folder holds asset files at their paths under the asset folder
(``server/EnemySpawn.json``); ``install`` writes them there and keeps the originals, like archives.
The server reads its assets when it starts, so a change needs a server restart.
"""

from __future__ import annotations

import json
import os
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

from . import arc, gmd, typemap
from .errors import RiftError
from .game import Game

SPAWN_FILE = "EnemySpawn.json"
_ENEMY_NAMES = b"ui\\00_message\\enemy\\enemy_name"
_STAGE_NAMES = b"ui\\00_message\\common\\stage_list"
_ENEMY_GROUP = b"param\\enemy_group"
_STAGE_LIST = b"scr\\stage_list"


# -- where the server's data lives ----------------------------------------------------------------
def server_assets(game: Game) -> Path | None:
    """The asset folder the local DDO server reads: $RIFTSTONE_DDO_ASSETS, else the instance the DDO
    toolkit (C:\\Dev\\DDO) runs (server.json -> server-<build>\\Arrowgene.Ddon.config.json AssetPath),
    else the server bundled with the client (nativePC\\Server\\Files\\Assets)."""
    if game.kind != "ddo":
        return None
    env = os.environ.get("RIFTSTONE_DDO_ASSETS")
    if env:
        return Path(env)
    home = game.root.parent
    build = None
    try:
        build = json.loads((home / "server.json").read_text(encoding="utf-8"))["build"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    instances = ([home / f"server-{build}"] if build and build != "bundled" else []) + [home / "server"]
    for inst in instances:
        try:
            cfg = json.loads((inst / "Arrowgene.Ddon.config.json").read_text(encoding="utf-8-sig"))
            p = Path(cfg["AssetPath"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if p.is_dir():
            return p
    bundled = game.native / "Server" / "Files" / "Assets"
    return bundled if bundled.is_dir() else None


def need_assets(game: Game) -> Path:
    p = server_assets(game)
    if p is None or not (p / SPAWN_FILE).is_file():
        raise RiftError("the local DDO server's asset folder was not found (set RIFTSTONE_DDO_ASSETS to its "
                        "Files\\Assets folder, or set the server up with C:\\Dev\\DDO\\ddon.cmd server setup)")
    return p


# -- client tables ----------------------------------------------------------------------------------
def parse_enemy_groups(data: bytes) -> list[tuple[int, int, tuple[int, ...]]]:
    """rEnemyGroup -> [(nameId, sort, enemy ids)]; strict: the records must end at the last byte."""
    if len(data) < 8:
        raise RiftError("enemy_group.emg is too short")
    ver, count = struct.unpack_from("<II", data)
    if ver != 1:
        raise RiftError(f"enemy_group.emg version {ver}, expected 1")
    p, out = 8, []
    for _ in range(count):
        if p + 12 > len(data):
            raise RiftError("enemy_group.emg is truncated")
        name_id, sort, n = struct.unpack_from("<3I", data, p)
        p += 12
        if p + 4 * n > len(data):
            raise RiftError("enemy_group.emg is truncated")
        out.append((name_id, sort, struct.unpack_from(f"<{n}I", data, p)))
        p += 4 * n
    if p != len(data):
        raise RiftError(f"enemy_group.emg has {len(data) - p} bytes after its records")
    return out


def parse_stage_list(data: bytes) -> list[tuple[int, int, int, int, int]]:
    """rStageList -> [(stageNo, type, byte, name message, flags)]."""
    if len(data) < 12 or data[:4] != b"slt\0":
        raise RiftError("not a stage list (slt)")
    ver, count = struct.unpack_from("<II", data, 4)
    if 12 + 17 * count != len(data):
        raise RiftError(f"stage list: {count} records do not fill {len(data)} bytes")
    return [struct.unpack_from("<IIBII", data, 12 + 17 * i) for i in range(count)]


def _resource(game: Game, idx, name: bytes, ext: str) -> bytes:
    tid = typemap.type_for_extension(ext)
    arcs = idx.archives_with(name, tid)
    if not arcs:
        raise RiftError(f"{name.decode('latin-1')}.{ext} is not in this game")
    e = arc.Archive.read(game.vanilla_arc(arcs[0])).find(name, tid)
    if e is None:
        raise RiftError(f"{arcs[0]} does not hold {name.decode('latin-1')}.{ext}")
    return e.data()


# -- the world -------------------------------------------------------------------------------------
@dataclass
class DdoWorld:
    assets: Path
    schema: list[str]
    doc: dict                                     # EnemySpawn.json as loaded (rows stay lists)
    enemies: dict[int, str] = field(default_factory=dict)             # enemy id -> name
    stages: dict[int, tuple[int, str]] = field(default_factory=dict)  # StageId -> (stageNo, name; '' = none)
    warnings: list[str] = field(default_factory=list)                 # what could not be named, and why

    @property
    def rows(self) -> list[list]:
        return self.doc["enemies"]

    def col(self, name: str) -> int:
        return self.schema.index(name)

    def enemy_name(self, eid: int) -> str:
        return self.enemies.get(eid, f"enemy 0x{eid:06x}")

    def stage_name(self, sid: int) -> str:
        """The client's name for a StageId, else 'stage N' (no stage-list entry, or an empty name)."""
        return self.stages.get(sid, (None, ""))[1] or f"stage {sid}"


_STAGE_LABEL = re.compile(r"STAGE_NAME_(\d+)", re.ASCII)


def client_enemy_names(game: Game, idx) -> dict[int, str]:
    """Enemy id -> the client's name for it (param/enemy_group.emg + enemy_name.gmd; no server needed).
    A RiftError when either table is missing or does not read."""
    enemy_text = gmd.parse(_resource(game, idx, _ENEMY_NAMES, "gmd")).messages
    groups = parse_enemy_groups(_resource(game, idx, _ENEMY_GROUP, "emg"))
    names = [m.text for m in enemy_text]
    labels = {m.label: m.text for m in enemy_text if m.label}
    out: dict[int, str] = {}
    for name_id, _, ids in groups:
        text = labels.get(f"ENEMY_NAME_{name_id}") or (names[name_id - 1] if 0 < name_id <= len(names) else None)
        for eid in ids:
            if text:
                out[eid] = text
    return out


def load(game: Game, idx, assets: Path | None = None, spawn_file: Path | None = None) -> DdoWorld:
    """The server's spawns (from `spawn_file`, else the asset folder's) with client names.  A name table
    the client lacks or that does not read only costs the names: w.warnings says so, and enemies and
    stages show as 'enemy 0x...' / 'stage N'."""
    assets = assets or need_assets(game)
    src = spawn_file or assets / SPAWN_FILE
    try:
        doc = json.loads(src.read_text(encoding="utf-8"))
        schema = list(doc["schemas"]["enemies"])
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise RiftError(f"{src}: not the server's enemy spawn table ({e})") from None
    w = DdoWorld(assets, schema, doc)
    try:
        w.enemies.update(client_enemy_names(game, idx))
    except RiftError as e:
        w.warnings.append(f"enemy names are unavailable ({e}); enemies show as their ids")
    try:
        msgs = gmd.parse(_resource(game, idx, _STAGE_NAMES, "gmd")).messages
        stage_list = parse_stage_list(_resource(game, idx, _STAGE_LIST, "slt"))
    except RiftError as e:
        w.warnings.append(f"stage names are unavailable ({e}); stages show as 'stage <StageId>'")
    else:
        unlabelled = 0
        for stage_no, _, _, msg, _ in stage_list:
            m = _STAGE_LABEL.fullmatch((msgs[msg].label or "") if msg < len(msgs) else "")
            if not m:                 # the StageId is the label's number; without one the record names nothing
                unlabelled += 1
                continue
            w.stages.setdefault(int(m.group(1)), (stage_no, (msgs[msg].text or "").strip()))
        if unlabelled:
            w.warnings.append(f"{unlabelled} stage-list record(s) point at no STAGE_NAME_<StageId> message; "
                              "those stages show as 'stage <StageId>'")
    return w


def spawn_points(game: Game, idx, w: DdoWorld, stage: int, group: int) -> tuple[str | None, list]:
    """(layout path, [(x, y, z)] per record) of a stage's enemy group, from the client."""
    import re as _re

    from . import lot_ddo

    no = w.stages.get(stage, (None,))[0]
    if no is None:
        return None, []
    cache = getattr(w, "_points", None)
    if cache is None:
        cache = w._points = {}
        tid = typemap.type_for_extension("lot")
        pat = _re.compile(r"^scr\\st(\d{4})\\etc\\st\d{4}_\d\dm\d\dn_e(\d+)$", _re.I)
        for n, a in idx.db.execute("SELECT name, arc FROM res WHERE type=?", (tid,)):
            m = pat.match(n.decode("latin-1"))
            if m:
                cache.setdefault((int(m.group(1)), int(m.group(2))), (n, a))
    hit = cache.get((no, group))
    if hit is None:
        return None, []
    n, a = hit
    tid = typemap.type_for_extension("lot")
    e = arc.Archive.read(game.vanilla_arc(a)).find(n, tid)
    if e is None:
        return None, []
    return n.decode("latin-1"), layout_points(lot_ddo.parse(e.data()))


def layout_points(layout) -> list:
    """[(x, y, z)] per record of a DDO layout (lot_ddo.LotDdo), None for a record without a position."""
    from . import lot_ddo

    return [tuple(round(lot_ddo.f32(b), 1) for b in pos) if (pos := r.get("mPosition")) else None
            for r in layout.records]


def find_stage(w: DdoWorld, query: str) -> int:
    """A StageId from what someone typed: the id ('5', or 'stage 5' as a stage without a name is shown),
    a stage number ('st0200'), or part of the client's name for it.  An id must exist: named by the
    client or used by a spawn row."""
    q = " ".join(query.strip().lower().split())
    if not q:
        raise RiftError("which stage? its StageId, st<number> or part of its name (riftstone world stages --game ddo)")
    used = {r[w.col("StageId")] for r in w.rows}
    m = re.fullmatch(r"(?:stage ?)?(\d{1,9})", q, re.ASCII)   # bounded: int() refuses over 4,300 digits
    if m:
        sid = int(m.group(1))
        if sid not in w.stages and sid not in used:
            raise RiftError(f"no stage has StageId {sid} (riftstone world stages --game ddo lists them)")
        return sid
    m = re.fullmatch(r"st(\d{1,9})", q, re.ASCII)      # a stage number (st0200) -> its StageId
    if m:
        no = int(m.group(1))
        for sid, (sno, _) in sorted(w.stages.items()):
            if sno == no:
                return sid
        raise RiftError(f"no stage number {no}")
    hits = sorted(sid for sid, (_, name) in w.stages.items() if name and q in " ".join(name.lower().split()))
    hits = [s for s in hits if s in used] or hits
    if not hits:
        raise RiftError(f"no stage is called {query!r} (riftstone world stages --game ddo)")
    return hits[0]


def find_enemies(w: DdoWorld, query: str) -> list[int]:
    """Enemy ids for a query: an id (0x010100, em010100); else every enemy whose name is the query, with
    case, accents, spaces, hyphens and singular/plural set aside as for Dark Arisen (world.match_names:
    wolves -> Wolf, cyclopes -> Cyclops, oxen -> Ox); else every enemy whose name contains it."""
    from .world import match_names

    m = re.fullmatch(r"(?:0x|em)([0-9a-f]{1,8})", query.strip().lower())
    if m:
        return [int(m.group(1), 16)]
    exact, part = match_names(query, w.enemies)
    return sorted(exact or part)


def pick_enemy(w: DdoWorld, query: str) -> int:
    """The one enemy a query names.  Several ids can share a name (8 are 'Cyclops'): then they are listed,
    with how many spawn rows the server has for each, and an id is asked for instead of a guess."""
    ids = find_enemies(w, query)
    if not ids:
        raise RiftError(f"no enemy is called {query!r} (riftstone world enemies --game ddo)")
    if len(ids) == 1:
        return ids[0]
    from collections import Counter

    rows = Counter(enemy_id(r[w.col("EnemyId")]) for r in w.rows)
    listed = ", ".join(f"0x{e:06X} {w.enemy_name(e)} ({rows[e]} spawn row{'s' if rows[e] > 1 else ''})"
                       if rows[e] else f"0x{e:06X} {w.enemy_name(e)} (not spawned)" for e in ids[:12])
    raise RiftError(f"{query!r} matches {len(ids)} enemies: {listed}{' ...' if len(ids) > 12 else ''}; "
                    f"say which by id, e.g. 0x{ids[0]:06X} (riftstone world enemy <id> --game ddo shows where it spawns)")


def rows_where(w: DdoWorld, **match) -> list[list]:
    cols = {k: w.col(k) for k in match}
    return [r for r in w.rows if all(r[c] == match[k] for k, c in cols.items())]


def enemy_id(row_value) -> int:
    return int(row_value, 16) if isinstance(row_value, str) else int(row_value)


# -- encounters ------------------------------------------------------------------------------------
def encounter(w: DdoWorld, stage: int, enemy: int, count: int, group: int | None = None,
              layer: int | None = None, level: int | None = None,
              points: int | None = None) -> tuple[list[list], list[str]]:
    """Add `count` of `enemy` to a stage's spawn group (server data).  Each new row copies the group's
    first row (level, experience, drops ...) with the enemy swapped.  Positions: when the group's
    client layout has `points` spawn points, the ones the server does not use yet come first, then
    they cycle; without it, they cycle over the points the group uses.  Returns (new rows, notes);
    w.doc is changed in place."""
    if count < 1 or count > 500:
        raise RiftError("--count must be between 1 and 500")
    C = w.col
    in_stage = [r for r in w.rows if r[C("StageId")] == stage]
    if not in_stage:
        raise RiftError(f"the server spawns nothing in {w.stage_name(stage)} (StageId {stage}); pick a stage "
                        "from 'riftstone world stages --game ddo'")
    if group is None:
        same = [r for r in in_stage if enemy_id(r[C("EnemyId")]) == enemy]
        pick = (same or in_stage)[0]
        layer, group = pick[C("LayerNo")], pick[C("GroupId")]
    if layer is None:
        layers = sorted({r[C("LayerNo")] for r in in_stage if r[C("GroupId")] == group})
        if not layers:
            raise RiftError(f"stage {stage} has no group {group}")
        layer = layers[0]
    members = [r for r in in_stage if r[C("LayerNo")] == layer and r[C("GroupId")] == group]
    if not members:
        raise RiftError(f"stage {stage} has no group {group} on layer {layer}")
    base = members[0]
    used = sorted({r[C("PositionIndex")] for r in members})
    positions = ([i for i in range(points) if i not in used] + used) if points else used
    ref = next((r for r in w.rows if enemy_id(r[C("EnemyId")]) == enemy), None)
    new = []
    for i in range(count):
        row = list(base)
        row[C("PositionIndex")] = positions[i % len(positions)]
        row[C("EnemyId")] = f"0x{enemy:06X}"
        if ref is not None:  # the enemy's own drops and orbs where the server already spawns it
            for k in ("NamedEnemyParamsId", "DropsTableId", "Experience", "BloodOrbs", "HighOrbs", "PPDrop",
                      "HmPresetNo", "EnemyTargetTypesId", "MontageFixNo"):
                if k in w.schema:
                    row[C(k)] = ref[C(k)]
        if level is not None:
            row[C("Lv")] = level
        new.append(row)
    at = w.rows.index(members[-1]) + 1
    w.rows[at:at] = new
    notes = [f"{count} x {w.enemy_name(enemy)} added to {w.stage_name(stage)} (StageId {stage}), layer {layer}, "
             f"group {group}, which spawns {len(members)} already",
             (f"the group's layout has {points} spawn points ({len(used)} used by the server): new ones fill "
              f"{min(count, len(positions) - len(used))} unused point(s) first, then share"
              if points else f"positions cycle over the group's {len(positions)} spawn point(s): {positions[:8]}"
              + (" ..." if len(positions) > 8 else "")),
             "level " + (str(level) if level is not None else f"{base[C('Lv')]} (the group's)")
             + ("" if ref is not None else "; the server spawns this enemy nowhere else, so drops/exp are the group's")]
    return new, notes


def dumps(doc: dict) -> bytes:
    """The server's own formatting (indent 2, no trailing newline): an unchanged table is byte-identical."""
    return json.dumps(doc, indent=2, ensure_ascii=False).encode("utf-8")


# -- server JSON, written back in the file's own style ---------------------------------------------
# "jackson" is the Java pretty printer DDON-Tools writes its extracts with (named_param.ndp.json):
# '"key" : value', two spaces per object level, arrays inline as "[ a, b ]", empty ones "[ ]" / "{ }".
STYLES = ((2, ""), (4, "\n"), (2, "\n"), (4, ""), (2, "\r\n"), (4, "\r\n"), ("jackson", ""), ("jackson", "\n"))
_JACKSON_ESC = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _jackson_str(s: str) -> str:
    return '"' + "".join(_JACKSON_ESC.get(c) or (f"\\u{ord(c):04X}" if ord(c) < 0x20 else c) for c in s) + '"'


def _jackson(v, level: int = 0) -> str:
    """JSON the way Jackson's DefaultPrettyPrinter writes it (objects indent, arrays stay on the line)."""
    if isinstance(v, dict):
        if not v:
            return "{ }"
        pad = "\n" + "  " * (level + 1)
        return ("{" + pad + ("," + pad).join(f"{_jackson_str(str(k))} : {_jackson(x, level + 1)}" for k, x in v.items())
                + "\n" + "  " * level + "}")
    if isinstance(v, (list, tuple)):
        return "[ " + ", ".join(_jackson(x, level) for x in v) + " ]" if v else "[ ]"
    if isinstance(v, str):
        return _jackson_str(v)
    return json.dumps(v)


def _dumps(doc, indent) -> str:
    return _jackson(doc) if indent == "jackson" else json.dumps(doc, indent=indent, ensure_ascii=False)


def read_json(path: Path) -> tuple[object, tuple[int | str, str]]:
    """(document, style): the style is the indent and ending that reproduce the file exactly
    (EnemySpawn.json: 2 and none; Shop.json: 4 and a newline; named_param.ndp.json: Jackson's, none),
    so an edit only changes what it edits."""
    return parse_json(path.read_bytes(), str(path))


def parse_json(raw: bytes, what: str = "JSON") -> tuple[object, tuple[int | str, str]]:
    """read_json for bytes already in memory."""
    try:
        doc = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, RecursionError) as e:
        raise RiftError(f"{what}: not valid JSON ({type(e).__name__}: {str(e)[:120]})") from None
    for indent, tail in STYLES:
        try:
            if (_dumps(doc, indent) + tail).encode("utf-8") == raw:
                return doc, (indent, tail)
        except (ValueError, RecursionError):      # NaN/Infinity, lone surrogates, nesting deeper than dumps goes
            break
    return doc, (2, "\n")


def dumps_style(doc, style: tuple[int | str, str]) -> bytes:
    try:
        return (_dumps(doc, style[0]) + style[1]).encode("utf-8")
    except (ValueError, RecursionError) as e:     # a lone surrogate from a \\ud800 escape, absurd nesting
        raise RiftError(f"the JSON cannot be written back ({type(e).__name__}: {str(e)[:120]})") from None


def server_file(game: Game, mod_root: Path | None, rel: str) -> tuple[Path, Path]:
    """(the file to read, the mod's copy to write): a mod's own copy wins, so edits accumulate."""
    assets = need_assets(game)
    mine = mod_root / "server" / rel if mod_root is not None else None
    return (mine if mine is not None and mine.is_file() else assets / rel), mine


# -- items, shops and drops (server data) -----------------------------------------------------------
ITEM_FILE, SHOP_FILE = "itemlist.csv", "Shop.json"


def load_items(assets: Path) -> dict[int, dict]:
    """The server's item list (itemlist.csv): id -> {Name, Category, Price, ...}."""
    import csv

    path = assets / ITEM_FILE
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as e:
        raise RiftError(f"the server's item list is missing ({e})") from None
    head = lines[0].lstrip("#").split(",")
    out = {}
    for row in csv.reader(lines[1:]):
        if row and row[0].strip().isdigit():
            out[int(row[0])] = dict(zip(head, row))
    return out


def find_items(items: dict[int, dict], query: str) -> list[int]:
    q = query.strip().lower()
    if re.fullmatch(r"[0-9]{1,9}", q):          # an id; str.isdigit() also takes '²', which int() refuses
        return [int(q)] if int(q) in items else []
    exact = [i for i, r in items.items() if r.get("Name", "").lower() == q]
    return sorted(exact or [i for i, r in items.items() if q in r.get("Name", "").lower()])


def shop_add(shops: list, shop_id: int, item: int, price: int, stock: int) -> str:
    shop = next((s for s in shops if s.get("ShopId") == shop_id), None)
    if shop is None:
        raise RiftError(f"no shop {shop_id} (riftstone items shop --game ddo lists them)")
    goods = shop["Data"]["GoodsParamList"]
    if any(g.get("ItemId") == item for g in goods):
        raise RiftError(f"shop {shop_id} already sells item {item}")
    new = dict(goods[-1]) if goods else {"Unk4": False, "Unk5": 0, "Unk6": 0, "Unk7": []}
    new.update({"Index": len(goods), "ItemId": item, "Price": price, "Stock": stock})
    new["Unk7"] = list(new.get("Unk7", []))
    goods.append(new)
    return f"shop {shop_id} now sells {len(goods)} goods"


def drop_tables_with(spawn: dict, item: int) -> list[tuple[int, str, float]]:
    out = []
    for t in spawn.get("dropsTables", []):
        for it in t.get("items", []):
            if it[0] == item:
                out.append((t["id"], t.get("name", ""), it[5]))
    return out


def drop_add(spawn: dict, table_id: int, item: int, chance: float, num: int = 1) -> str:
    t = next((t for t in spawn.get("dropsTables", []) if t.get("id") == table_id), None)
    if t is None:
        raise RiftError(f"no drop table {table_id} (riftstone items sets <item> --game ddo finds some)")
    if not 0 < chance <= 1:
        raise RiftError("--percent must be between 1 and 100")
    if any(it[0] == item for it in t["items"]):
        raise RiftError(f"drop table {table_id} already has item {item}")
    t["items"].append([item, num, num, 0, False, round(chance, 4)])
    users = sum(1 for r in spawn["enemies"] if r[spawn["schemas"]["enemies"].index("DropsTableId")] == table_id)
    return f"drop table {table_id} ({t.get('name', '')}) now has {len(t['items'])} items; {users} spawn row(s) use it"
