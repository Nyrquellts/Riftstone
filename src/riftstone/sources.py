"""Where a mod's files come from, so a mod package carries a recipe instead of the game's bytes.

A command that writes the other game's content into a mod (a Dragon's Dogma Online chimera skin, a port)
or makes a file from a game resource by a fixed rule (a texture preset) records how, in the mod's
``riftstone-sources.json``:

    {"schema": "riftstone.sources/1",
     "recipes": [{"kind": "ddo-skin", "args": {"family": "chimera", "skin": 1, "variant": "white"},
                  "files": ["archives/rom/enemy/em5200.arc/model/em/e52/e5200/s01/e5200_skin_BM.tex", ...]},
                 {"kind": "port", "args": {... port.into_mod's arguments ...}, "files": [...]},
                 {"kind": "texfx", "args": {"path": ..., "on": {...}, "preset": ..., "strength": ..., "seed": ...},
                  "files": [...]}],
     "foreign": {"<file>": "Dragon's Dogma Online"}}

``replay`` makes a recipe's files again from the games on *this* PC.  A package (package.py) replays the
recipes on the maker's PC and uses what they make as bases for its deltas, so an unchanged file costs
nothing and an edited one only its edits; the player's Riftstone replays them from the player's own
games.  ``foreign`` names files that hold the other game's content: a package never carries their bytes.

File names here are a mod's own paths with forward slashes and without ``.yaml`` (a YAML file and its
binary are the same file).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .errors import RiftError

FILE = "riftstone-sources.json"
SCHEMA = "riftstone.sources/1"
KINDS = ("ddo-skin", "port", "texfx")
PORT_ARGS = ("src", "dst", "resource", "as_", "like", "arc_name", "from_arc", "model_only", "rebake", "material",
             "alternates", "dye")


def norm(rel: str) -> str:
    """A mod path as this file names it: forward slashes, no .yaml."""
    rel = str(rel).replace("\\", "/")
    return rel[:-5] if rel.lower().endswith(".yaml") else rel


def empty() -> dict:
    return {"schema": SCHEMA, "recipes": [], "foreign": {}}


def load(root: Path) -> dict:
    """The mod's sources, checked; an empty record when it has none."""
    p = Path(root) / FILE
    if not p.is_file():
        return empty()
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise RiftError(f"{p}: not a sources file ({e})") from None
    return check(data, str(p))


def check(data, where: str = FILE) -> dict:
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise RiftError(f"{where}: expected \"schema\": \"{SCHEMA}\"")
    recipes, foreign = data.get("recipes", []), data.get("foreign", {})
    if not isinstance(recipes, list) or not isinstance(foreign, dict):
        raise RiftError(f"{where}: recipes is a list and foreign a map")
    for r in recipes:
        if not (isinstance(r, dict) and r.get("kind") in KINDS and isinstance(r.get("args"), dict)
                and isinstance(r.get("files", []), list) and all(isinstance(f, str) for f in r.get("files", []))):
            raise RiftError(f"{where}: a recipe is {{kind: one of {', '.join(KINDS)}, args: {{...}}, files: [...]}}")
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in foreign.items()):
        raise RiftError(f"{where}: foreign maps a file to the game it came from")
    return {"schema": SCHEMA, "recipes": recipes, "foreign": foreign}


def save(root: Path, data: dict) -> None:
    from .arcfolder import write_file
    write_file(Path(root) / FILE, (json.dumps(check(data), indent=1, ensure_ascii=False) + "\n").encode("utf-8"))


def _rels(root: Path, files) -> list[str]:
    return sorted({norm(Path(f).relative_to(root).as_posix() if Path(f).is_absolute() else f) for f in files})


def _drop(data: dict, files: list[str]) -> None:
    gone = set(files)
    for r in data["recipes"]:
        r["files"] = [f for f in r.get("files", []) if f not in gone]
    data["recipes"] = [r for r in data["recipes"] if r["files"]]
    for f in gone:
        data["foreign"].pop(f, None)


def record(root: Path, kind: str, args: dict, files, foreign: str | None = None, keep_earlier: bool = False) -> None:
    """Note that a recipe made these files of the mod, and, when it brought the other game's content, that
    they are that game's.  Whatever made them before no longer counts -- unless the recipe works on what
    the earlier ones made (``keep_earlier``: a texture preset on a ported texture)."""
    if kind not in KINDS:
        raise RiftError(f"unknown recipe {kind!r}")
    files = _rels(root, files)
    data = load(root)
    if not keep_earlier:
        _drop(data, files)
    data["recipes"] = [r for r in data["recipes"] if not (r["kind"] == kind and r["args"] == args)]
    data["recipes"].append({"kind": kind, "args": args, "files": files})
    if foreign:
        for f in files:
            data["foreign"][f] = foreign
    save(root, data)


def forget(root: Path, files) -> None:
    """These files are something else now (replaced by hand): no recipe made them, and they are not known to
    be the other game's."""
    if not (Path(root) / FILE).is_file():
        return
    data = load(root)
    _drop(data, _rels(root, files))
    save(root, data)


def mark_foreign(root: Path, files, game_title: str) -> None:
    """These files hold the other game's content, and no recipe makes them."""
    files = _rels(root, files)
    if not files:
        return
    data = load(root)
    _drop(data, files)
    for f in files:
        data["foreign"][f] = game_title
    save(root, data)


def _skins_from_online(root: Path):
    """(family, number, variant or None, the skin's files) for each skin whose skins.json source says it came
    from Dragon's Dogma Online."""
    from . import ddoskins, fsmap, skins, typemap
    try:
        man = skins.read_manifest(root)
    except RiftError:
        return
    for key, entries in man.items():
        fam = skins.FAMILIES.get(key)
        if fam is None or not isinstance(entries, dict):
            continue
        for n, e in entries.items():
            source = e.get("source", "") if isinstance(e, dict) else ""
            if not ddoskins.names_online(source) or not str(n).isdigit() or not 1 <= int(n) <= skins.MAX_SKIN:
                continue
            files = sorted(f"archives/{fam.archive}.arc/" + fsmap.encode_name(
                skins.skin_name(fam, int(n), base).encode("latin-1"), typemap.BY_EXT[ext])
                for ext, bases in (("tex", fam.textures), ("mrl", fam.materials)) for base in bases)
            yield fam, int(n), ddoskins.variant_of(source), files


def implied(root: Path) -> list[dict]:
    """Recipes a mod made before recipes were recorded implies: a chimera skin whose skins.json source names
    one of Online's variants (ddoskins.variant_of) came from ddo-skin."""
    return [{"kind": "ddo-skin", "args": {"family": fam.key, "skin": n, "variant": variant}, "files": files}
            for fam, n, variant, files in _skins_from_online(root) if variant is not None]


def recipes_for(root: Path, present: set[str]) -> list[dict]:
    """The mod's recipes (recorded, then implied ones not recorded) that made a file it still has."""
    data = load(root)
    out = [r for r in data["recipes"] if present & set(r.get("files", []))]
    kinds_args = [(r["kind"], r["args"]) for r in data["recipes"]]
    out += [r for r in implied(root) if (r["kind"], r["args"]) not in kinds_args and present & set(r["files"])]
    return out


def foreign_files(root: Path, recipes: list[dict]) -> dict[str, str]:
    """The mod's files that hold the other game's content: marked ones, the textures of skins whose source says
    Dragon's Dogma Online, and every ddo-skin/port output."""
    out = dict(load(root)["foreign"])
    for _fam, _n, _variant, files in _skins_from_online(root):
        for f in files:
            if f.endswith(".tex"):
                out.setdefault(f, "Dragon's Dogma Online")
    for r in recipes:
        if r["kind"] in ("ddo-skin", "port"):
            title = "Dragon's Dogma Online" if r["kind"] == "ddo-skin" or r["args"].get("src") == "ddo" \
                else "Dragon's Dogma: Dark Arisen"
            for f in r.get("files", []):
                out.setdefault(f, title)
    return out


# -- replay ----------------------------------------------------------------------------------------
def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _mod_files(root: Path) -> dict[str, bytes]:
    """A mod folder's resources as the build compiles them: path (no .yaml) -> resource bytes."""
    from . import params
    out = {}
    for top in ("files", "archives"):
        base = Path(root) / top
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*")):
            if not f.is_file() or f.name.lower() == "readme.txt":
                continue
            rel = f.relative_to(root).as_posix()
            data = f.read_bytes()
            if rel.lower().endswith(".yaml"):
                data = params.yaml_to_resource(params.decode_text(data, rel), source=rel)
            out[norm(rel)] = data
    return out


def replay(recipes: list[dict], ctx) -> dict[str, bytes]:
    """The files the recipes make, in order (a later recipe's file replaces an earlier one's), from the games
    ``ctx`` gives: ctx.game(kind) -> Game, ctx.index(kind) -> Index (both raise RiftError when that game is
    not on this PC)."""
    made: dict[str, bytes] = {}
    for r in recipes:
        made.update(_replay_one(r, ctx, made))
    return made


def _replay_one(r: dict, ctx, made: dict[str, bytes]) -> dict[str, bytes]:
    kind, a = r["kind"], r["args"]
    if kind == "ddo-skin":
        from . import ddoskins, fsmap, skins
        fam = skins.family(str(a.get("family", "")))
        n = skins.check_number(a.get("skin"))
        variant = a.get("variant")
        if variant not in ddoskins.VARIANTS:
            raise RiftError(f"no Dragon's Dogma Online chimera variant {variant!r}")
        ctx.game("ddo")                                  # says clearly when Online is not on this PC
        textures = ddoskins.build(variant)
        res = skins.resources(ctx.game("ddda"), ctx.index("ddda"), fam, n, textures)
        return {f"archives/{fam.archive}.arc/" + fsmap.encode_name(name.encode("latin-1"), tid): data
                for (name, tid), data in res.items()}
    if kind == "port":
        import tempfile

        from . import port
        from .mod import Mod
        missing = [k for k in ("src", "dst", "resource") if not isinstance(a.get(k), str)]
        if missing or set(a) - set(PORT_ARGS):
            raise RiftError(f"a port recipe names {', '.join(PORT_ARGS)}")
        with tempfile.TemporaryDirectory(prefix="riftstone-replay-") as tmp:
            root = Path(tmp) / "mod"
            Mod.create(root, "replay", game=a["dst"])
            kw = {k: a.get(k) for k in PORT_ARGS if k not in ("src", "dst", "resource")}
            kw["model_only"], kw["rebake"], kw["alternates"] = (bool(kw.get(k)) for k in ("model_only", "rebake",
                                                                                         "alternates"))
            port.into_mod(root, ctx.game(a["src"]), ctx.game(a["dst"]), ctx.index(a["src"]), ctx.index(a["dst"]),
                          a["resource"], record=False, **kw)
            return _mod_files(root)
    if kind == "texfx":
        from . import arc as arclib, texfx
        path = a.get("path")
        on = a.get("on")
        if not isinstance(path, str) or not isinstance(on, dict):
            raise RiftError("a texfx recipe names the path it makes and what it was made on")
        if "path" in on:
            data = made.get(norm(on["path"]))
            if data is None:
                raise RiftError(f"the texture preset on {path} was made on {on['path']}, which no earlier recipe makes")
        else:
            name, tid = on.get("name"), on.get("type")
            archive, kind_ = on.get("archive"), on.get("game", "ddda")
            if not (isinstance(name, str) and isinstance(tid, int) and isinstance(archive, str)
                    and isinstance(kind_, str)):
                raise RiftError("a texfx recipe's source is a game resource (game, archive, name, type)")
            try:
                bname = name.encode("latin-1")
            except UnicodeEncodeError:
                raise RiftError(f"a texfx recipe names a resource no game has ({name!r})") from None
            source = ctx.game(kind_).vanilla_arc(archive)          # arc_path refuses a name that is not one
            e = arclib.Archive.read(source).find(bname, tid) if source.is_file() else None
            if e is None:
                raise RiftError(f"{archive} has no {name}")
            data = e.data()
        if _sha(data) != on.get("sha256"):
            raise RiftError(f"the texture the preset on {path} was made on is not this one (a different game build?)")
        strength, seed = a.get("strength", 1.0), a.get("seed", 1)
        if isinstance(strength, bool) or not isinstance(strength, (int, float)) or isinstance(seed, bool) \
                or not isinstance(seed, int):
            raise RiftError("a texfx recipe's strength is a number and its seed an integer")
        return {norm(path): texfx.apply(data, str(a.get("preset", "")), float(strength), seed)}
    raise RiftError(f"unknown recipe {kind!r}")


class Games:
    """The games and resource indexes on this PC, found when first asked for, closed together."""

    def __init__(self, games: dict | None = None, indexes: dict | None = None):
        self.games = dict(games or {})
        self.indexes = dict(indexes or {})
        self._opened: list = []

    def game(self, kind: str):
        from .game import KINDS as GAME_KINDS, find_game
        if not isinstance(kind, str) or kind not in GAME_KINDS:
            raise RiftError(f"unknown game {kind!r}")
        if kind not in self.games:
            try:
                self.games[kind] = find_game(kind)
            except RiftError as e:
                raise RiftError(f"this needs {GAME_KINDS[kind]['title']} on this PC: {e}") from None
        return self.games[kind]

    def index(self, kind: str):
        if kind not in self.indexes:
            from .index import Index
            idx = Index(self.game(kind))
            if idx.pending():
                idx.refresh()
            self.indexes[kind] = idx
            self._opened.append(idx)
        return self.indexes[kind]

    def close(self) -> None:
        for idx in self._opened:
            idx.close()
        self._opened.clear()
