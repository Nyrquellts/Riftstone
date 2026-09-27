"""riftstone: command line.  Every command explains what it changed and where."""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from pathlib import Path

from . import __version__, arc, arcfolder, fsmap, params, typemap, ui, xfs
from .errors import RiftError
from .game import KEYWORDS, KINDS, Game, detect_kind, find_game

QUICK = """\
Start here
  riftstone studio                     the app: your plugins and mods, Launch, every tool
  riftstone doctor                     is everything set up? (game, build, loader, mods, last session)
  riftstone live                       what the running game is doing (F10 shows it in game)
  riftstone crash                      how the last session ended, and any report explained

Archives and parameter files
  riftstone unpack em0100.arc          archive -> folder (parameters become .yaml)
  riftstone pack em0100                folder  -> archive, verified
  riftstone param enemy.statusparam    parameter file <-> .yaml

Loose-file mods (no manual repacking)
  riftstone find goblin                the goblins' stats, attacks and AI files, then any file named like it
  riftstone new "My Mod"               create a mod in the mods folder (the one Studio shows)
  riftstone extract charparam/em/em0100_cmn.prp --mod "My Mod"
                                       copy a game file into it, as YAML, ready to edit
  riftstone install "My Mod"           build + put into the game (originals backed up)
  riftstone mods                       your mods: where they are, which are installed
  riftstone watch "My Mod"             rebuild and reinstall every time you save
  riftstone restore                    every archive back to the original

AI state machines
  riftstone fsm quest/q0005_b00.fsm    states, actions and conditions as pseudo-code

Text and dialogue
  riftstone text find "west gate"      which file and line id hold some text
  riftstone text add id/npc_wind/stage/st100_eng.gmd "A new line." --mod "My Mod"
                                       add a line to every language version at once

The world, mapped
  riftstone world stages               every stage, its rooms and what it places
  riftstone world enemy goblin         where an enemy spawns
  riftstone encounter 424 goblin --count 100 --at group:5 --mod "My Mod"
                                       100 goblins as a new group (the game's own horde setting)

Enemies and objects
  riftstone spawns list scr/st100/etc/st100_45m55n_e143.lot
  riftstone spawns copy scr/st100/etc/st100_45m55n_e143.lot 0 --at 58600,42716,-45360 --mod "My Mod"

Monsters between Dark Arisen and Online
  riftstone monster list               every enemy family, its counterpart in the other game, the verdict
  riftstone monster convert Wolf --into wolves --mod "My Mod"   an Online wolf over Dark Arisen's (same body)

Gransys terrain
  riftstone terrain where 58600,42716,-45360          the cell a world position lies in
  riftstone terrain localize st100_45m55n.world.mod   a cell edited in world coordinates, back in its cell

Items
  riftstone items list sword           id, name, weight and prices (--free: unused slots)
  riftstone items new "Rift Tonic" --like Greenwarish --buy 500 --shop n007ShopList --mod "My Mod"

Your save (copies made by the loader and the save_backup plugin)
  riftstone saves list                 every copy of your save, newest first
  riftstone saves restore 3 --yes      put copy 3 back (close the game first)

Dragon's Dogma Online, played alone
  riftstone ddo solo --mod "Solo Balance"   enemies and pacing for one player with pawns; then install it

Also: open, find, info, status, uninstall, build, index, doctor, loader,
live, crash, saves, studio.  Drag files onto Riftstone.cmd to unpack, pack or convert them.
riftstone <command> -h for help."""


# -- helpers --------------------------------------------------------------------

def _mod_game(folder) -> str | None:
    """The game a mod folder is for ('ddda'/'ddo'), or None when it is not a mod."""
    try:
        meta = json.loads((Path(folder) / "riftstone-mod.json").read_text(encoding="utf-8-sig"))
        return str(meta.get("game", "ddda"))
    except (OSError, ValueError, AttributeError, RecursionError):
        return None


def _game(args) -> Game:
    """--game, else the game the given mod(s) are for, else $RIFTSTONE_GAME / DDDA through Steam."""
    explicit = getattr(args, "game", None)
    if explicit is None:
        folders = list(getattr(args, "mods", None) or [])
        if getattr(args, "mod", None):
            folders.append(args.mod)
        kinds = {k for k in map(_mod_game, folders) if k}
        if len(kinds) == 1 and not getattr(args, "_cross", False):
            explicit = kinds.pop()
    return find_game(explicit)


def _game_kind(args) -> str:
    """'ddda' or 'ddo' for work that needs to know the game but not its install (writing a texture):
    --game (a keyword or a game folder), else $RIFTSTONE_GAME, else DDDA."""
    want = str(getattr(args, "game", None) or os.environ.get("RIFTSTONE_GAME", "") or "ddda")
    if want.lower() in ("ddo", "online"):
        return "ddo"
    if want.lower() in ("ddda", "dd", "da"):
        return "ddda"
    kind = detect_kind(Path(want))
    if kind is None:
        raise RiftError(f"--game {want}: not ddda, ddo or a game folder")
    return kind


# Every resource index a command opens; main() closes them when the command ends, whatever it returns or
# raises (a command may also close its own first: closing twice is harmless).
_OPENED: list = []


def _index(game: Game, quiet: bool = False):
    from .index import Index

    idx = Index(game)
    _OPENED.append(idx)
    pending = idx.pending()
    if pending:
        if not quiet:
            ui.step(f"Indexing {pending} archive{'s' if pending != 1 else ''} (only needed when the game files change)")
        bar = ui.Progress(pending, "reading directories") if not quiet else None
        stats = idx.refresh(bar)
        if bar:
            bar.done(f"Index ready: {stats['resources']:,} resources in {stats['archives']:,} archives")
    return idx


def _inside_game(path: Path) -> Game | None:
    try:
        for parent in [path, *path.parents]:
            kind = detect_kind(parent)
            if kind is not None:
                return Game(parent, kind)
    except OSError:
        pass
    return None


def _default_unpack_dir(arc_path: Path) -> tuple[Path, str | None]:
    """An archive of a game -- in nativePC, or the originals and the overlay Riftstone keeps in the same
    layout -- unpacks by its archive name under Documents\\Riftstone\\unpacked, not into the game folder;
    anything else next to itself."""
    p = arc_path.resolve()
    g = _inside_game(p)
    if g is not None:
        for base in (g.native, g.vanilla_dir, g.overlay_dir):
            if base.resolve() in p.parents:
                name = p.relative_to(base.resolve()).with_suffix("").as_posix()
                return Path.home() / "Documents" / "Riftstone" / "unpacked" / name, name
    return arc_path.with_suffix(""), None


def _os_error(e: OSError) -> str:
    """An OSError as one plain line: the file(s), then what the system said."""
    if e.filename is not None and e.strerror:
        return f"{e.filename}" + (f" -> {e.filename2}" if e.filename2 is not None else "") + f": {e.strerror}"
    return str(e) or type(e).__name__


def _keep_edits(out: Path, body: bytes, force: bool, instead: str) -> None:
    """Before an editable form (a parameter file's YAML, a texture's DDS, a cell model in world space) is
    written over `out`: an `out` that differs may hold edits, so it is kept as <out>.bak, as the other
    direction keeps the binary it replaces; with that .bak taken too it is refused (`instead` says what else
    to do).  force: overwritten, no .bak kept."""
    if force or not out.is_file() or out.read_bytes() == body:
        return
    bak = out.with_name(out.name + ".bak")
    if bak.exists():
        raise RiftError(f"{out} differs from what would be written (edited?) and {bak.name} is taken; move one of "
                        f"them aside, or {instead}")
    os.replace(out, bak)
    ui.info(f"previous {out.name} kept as {bak.name}")


def _outputs(out: str | None, inputs: list[Path], default) -> list[Path]:
    """Where each input's result goes: its default place, or -o; with several inputs -o is a folder that
    gets each result under its default name (as unpack does), since one file cannot hold them all."""
    if not out:
        return [default(p) for p in inputs]
    if len(inputs) == 1:
        return [Path(out)]
    if Path(out).is_file():
        raise RiftError(f"-o {out}: with several inputs -o is the folder that gets each result, and {out} is a file")
    targets = [Path(out) / default(p).name for p in inputs]
    names = [t.name.lower() for t in targets]
    twice = next((n for n in names if names.count(n) > 1), None)
    if twice:
        raise RiftError(f"two inputs would both become {Path(out) / twice}; convert them one at a time")
    return targets


def _resource_type_from_filename(path: Path) -> int | None:
    name = path.name
    if name.lower().endswith(".yaml"):
        name = name[:-5]
    if "." not in name:
        return None
    return typemap.type_for_extension(name.rsplit(".", 1)[1])


# commands that make the mod they are given when it is not there yet, and the game each is for
_MAKES_MOD = {"import": None, "ddo": "ddo", "playtest": "ddda"}


def _mod_names(args) -> None:
    """A mod given by name -- --mod "Harder Goblins", install "Harder Goblins" -- is the mod of that name in
    the mods folder (the one Studio shows; mod.locate); a path, or a mod folder right here, stays as given."""
    from . import mod as modlib

    cmd = getattr(args, "cmd", None)
    one, many = getattr(args, "mod", None), getattr(args, "mods", None)
    named = [m for m in ([one] if isinstance(one, str) else []) + (many if isinstance(many, list) else [])
             if isinstance(m, str) and modlib.is_bare_name(m)]
    if cmd == "new" or not named:
        return
    want = getattr(args, "game", None) or _MAKES_MOD.get(cmd)
    folders = modlib.mods_folders(want)
    if isinstance(one, str):
        args.mod = str(modlib.locate(one, folders, _mods_folder_for(want) if cmd in _MAKES_MOD else None))
    if isinstance(many, list):
        out = []
        for m in many:
            try:
                out.append(str(modlib.locate(m, folders)))
            except RiftError:
                if cmd != "uninstall":
                    raise
                out.append(m)           # a mod whose folder is gone still uninstalls by its path, as before
        args.mods = out


# -- commands -------------------------------------------------------------------

def cmd_unpack(args) -> int:
    for src in args.archives:
        src = Path(src)
        if not src.is_file():
            raise RiftError(f"{src} does not exist")
        out, arc_name = (Path(args.out), None) if args.out else _default_unpack_dir(src)
        if args.out and len(args.archives) > 1:
            out = Path(args.out) / src.stem
        count = len(arc.Archive.parse(src.read_bytes()).entries)
        bar = ui.Progress(count, "unpacking")
        r = arcfolder.unpack(src, out, yaml=not args.raw, archive_name=arc_name, progress=bar)
        bar.done(f"Unpacked {src.name}: {r.resources} resources, {ui.human(r.bytes)}"
                 + (f", {r.as_yaml} as editable YAML" if r.as_yaml else ""))
        ui.info(f"-> {r.folder}")
    return 0


def cmd_pack(args) -> int:
    folders = [Path(f).resolve() for f in args.folders]     # 'pack .' inside the folder: its real name
    for folder, out in zip(folders, _outputs(args.out, folders, lambda f: f.with_suffix(".arc"))):
        r = arcfolder.pack(folder)
        g = _inside_game(out.resolve())
        if g is not None and g.native.resolve() in out.resolve().parents:
            raise RiftError("Riftstone does not overwrite the game's archives by hand. Put the folder's changes "
                            "in a mod and run 'riftstone install', which keeps a verified original to restore.")
        if out.exists():
            bak = out.with_name(out.name + ".bak")
            if not bak.exists():
                os.replace(out, bak)
                ui.info(f"previous {out.name} kept as {bak.name}")
        arcfolder.write_file(out, r.data)
        what = []
        if r.changed:
            what.append(f"{len(r.changed)} changed")
        if r.added:
            what.append(f"{len(r.added)} added")
        what.append(f"{r.unchanged} unchanged")
        ui.ok(f"Packed {out.name}: {', '.join(what)}; every resource verified")
        if r.identical_to_source:
            ui.info("identical to the original archive, byte for byte")
        for rel in (r.changed + r.added)[:12]:
            ui.info(("changed " if rel in r.changed else "added   ") + rel)
        ui.info(f"-> {out}")
    return 0


def _param_default(f: Path) -> Path:
    return f.with_name(f.name[:-5]) if f.name.lower().endswith(".yaml") else f.with_name(f.name + ".yaml")


def cmd_param(args) -> int:
    files = [Path(f) for f in args.files]
    for f in files:
        if not f.is_file():
            raise RiftError(f"{f} is a folder, not a parameter file" if f.is_dir() else f"{f} does not exist")
    for f, out in zip(files, _outputs(args.out, files, _param_default)):
        if f.name.lower().endswith(".yaml"):
            data = params.yaml_to_resource(params.decode_text(f.read_bytes(), f.name), source=f.name)
            if out.exists() and not args.force:
                bak = out.with_name(out.name + ".bak")
                if not bak.exists():
                    os.replace(out, bak)
            arcfolder.write_file(out, data)
            ui.ok(f"{f.name} -> {out.name} ({ui.human(len(data))})")
        else:
            raw = f.read_bytes()
            tid = _resource_type_from_filename(f)
            if not params.is_editable_resource(raw, tid):
                raise RiftError(f"{f.name} is not an editable resource (XFS parameters, collision, text, tables "
                                "or a flat parameter format)")
            text = params.resource_to_yaml(raw, f.stem if tid else f.name, tid)
            if text is None or params.yaml_to_resource(text) != raw:
                raise RiftError(f"{f.name}: YAML would not rebuild it exactly; not written")
            body = text.encode("utf-8")
            _keep_edits(out, body, args.force, f"add --force to overwrite {out.name}")   # was: overwritten, no .bak
            arcfolder.write_file(out, body)
            ui.ok(f"{f.name} -> {out.name} ({text.count(chr(10))} lines, checked to rebuild exactly)")
    return 0


def _find_rows(game: Game, args, idx=None) -> list[dict]:
    tid = typemap.type_for_extension(args.type) if args.type else None
    if args.type and tid is None:                   # was: an unknown type searched every type
        raise RiftError(f"no resource type {args.type!r} (riftstone world types lists the game's types)")
    return (idx or _index(game, args.json)).search(args.text, tid, args.limit)   # closed when the command ends


def _rows_json(rows: list[dict]) -> list[dict]:
    return [{**r, "name": r["name"].decode("latin-1")} for r in rows]


def cmd_find(args) -> int:
    if (args.game or "").lower() in ("both", "all"):
        games = []
        for kind in KINDS:
            try:
                games.append(find_game(kind))
            except RiftError:
                continue
        if not games:
            raise RiftError("--game both: neither game was found (set RIFTSTONE_GAME to a game folder, or "
                            "RIFTSTONE_DDO to Online's)")
        if args.json:                               # one document, {game: rows}; no headings in it
            print(json.dumps({g.kind: _rows_json(_find_rows(g, args)) for g in games}, indent=1))
            return 0
        code = 1
        for g in games:
            ui.step(g.title)
            code = min(code, cmd_find(argparse.Namespace(**{**vars(args), "game": str(g.root)})))
        return code
    game = _game(args)
    idx = _index(game, args.json)
    rows = _find_rows(game, args, idx)
    if args.json:
        print(json.dumps(_rows_json(rows), indent=1))
        return 0
    nm = _names(game, idx)
    meant = False if args.type else _find_by_name(nm, idx, args.text)
    if not rows:
        if meant:
            return 0
        ui.warn(f"nothing matches {args.text!r}")
        return 1
    if meant:
        ui.step(f"Files named like {args.text!r}")
    for r in rows:
        where = r["first_arc"] + (f" (+{r['archives'] - 1} more)" if r["archives"] > 1 else "")
        path = fsmap.encode_name(r["name"], r["type"])
        d = nm.describe(path)
        label = f"  {d['title']}" if d["enemy"] or d["stage"] is not None else ""
        print(f"  {ui.gold(path):<60} {ui.mist(ui.human(r['size'])):>10}  {ui.mist(where + label)}")
    if len(rows) == args.limit:
        ui.info(f"first {args.limit} shown; narrow the search or use --limit")
    return 0


def _arc_named(game: Game, text: str) -> str:
    """--arc as the name of an archive the game has: 'rom\\enemy\\em0100.arc' -> rom/enemy/em0100, the name a
    mod keeps it under (archives/<name>.arc; with the suffix kept it became em0100.arc.arc, which build
    refuses).  One the game lacks is refused here, not by a FileNotFoundError."""
    name = game.arc_path(text).relative_to(game.native).with_suffix("").as_posix()
    if not game.vanilla_arc(name).is_file():
        raise RiftError(f"{game.title} has no archive {name} (riftstone find <name> says which archives hold a "
                        "resource)")
    return name


def _names(game: Game, idx):
    """Dark Arisen's plain names for files and search words (names.py); empty when they cannot be read."""
    from . import names

    try:
        return names.Names.load(game, idx)
    except (RiftError, OSError, ValueError, KeyError):
        return names.Names()        # a convenience: the file-name search works without them


def _find_by_name(nm, idx, text: str) -> bool:
    """The enemies and places a search names ('goblin', 'urban quarter', 'em0100', 'st220'), each enemy
    with the files that shape it and each stage with its group lists; False when it names none."""
    from . import names

    meant = nm.match(text)
    if meant["enemies"]:
        ui.step(f"Enemies named like {text!r}")
    for e in meant["enemies"]:
        files = names.enemy_files(idx, e["id"])
        ui.ok(f"{e['name']} ({e['id']})" + ("" if files else ui.mist("  no file carries its id")))
        by_kind: dict[str, list[str]] = {}
        for f in files:
            by_kind.setdefault(f["kind"], []).append(f["path"])
        for kind, paths in by_kind.items():
            for p in paths[:3]:
                print(f"    {kind:<24} {ui.gold(p)}")
            if len(paths) > 3:
                print(f"    {'':<24} {ui.mist(f'+{len(paths) - 3} more')}")
        ui.info(f"where it spawns: riftstone world enemy {e['id']}; every file named after it: riftstone find {e['id']}")
    if meant["stages"]:
        ui.step(f"Places named like {text!r}")
    for s in meant["stages"]:
        shown = (s["matched"] or s["rooms"])[:4]
        more = len(s["rooms"]) - len(shown)
        ui.ok(f"Stage {s['stage']}" + (f": {', '.join(shown)}" if shown else "")
              + (f" (+{more} more rooms)" if more > 0 else ""))
        for f in names.stage_files(idx, s["stage"]):
            kind = f["kind"] + (f" ({f['detail']})" if f["detail"] else "")
            print(f"    {kind:<24} {ui.gold(f['path'])}")
        ui.info(f"its groups and placements: riftstone world stage {s['stage']}")
    return bool(meant["enemies"] or meant["stages"])


def _locate(idx, game: Game, resource: str, arc_hint: str | None):
    rel = resource.replace("\\", "/")
    if rel.lower().endswith(".yaml"):
        rel = rel[:-5]
    name, tid = fsmap.decode_path(rel)
    arcs = [_arc_named(game, arc_hint)] if arc_hint else idx.archives_with(name, tid)
    if not arcs:
        near = idx.search(name.decode("latin-1").rsplit("\\", 1)[-1], tid, 5)
        hint = "; similar: " + ", ".join(fsmap.encode_name(r["name"], r["type"]) for r in near) if near else ""
        raise RiftError(f"no archive contains {rel}{hint}")
    a = arc.Archive.read(game.vanilla_arc(arcs[0]))
    e = a.find(name, tid)
    if e is None:
        raise RiftError(f"{arcs[0]} does not contain {rel}")
    return name, tid, e.data(), arcs


def cmd_extract(args) -> int:
    game = _game(args)
    idx = _index(game)
    for resource in args.resources:
        name, tid, data, arcs = _locate(idx, game, resource, args.arc)
        rel = fsmap.encode_name(name, tid)
        if args.mod:
            from .mod import Mod

            m = Mod.load(Path(args.mod))
            base = (m.root / "archives" / (arcs[0] + ".arc")) if args.arc else (m.root / "files")
        else:
            base = Path(args.out or ".")
        as_yaml = not args.raw and params.is_editable_resource(data, tid)
        target = base / (rel + (".yaml" if as_yaml else ""))
        if target.exists() and not args.force:
            raise RiftError(f"{target} already exists (use --force to overwrite)")
        payload = params.resource_to_yaml(data, name.decode("latin-1"), tid).encode("utf-8") if as_yaml else data
        arcfolder.write_file(target, payload)
        ui.ok(f"{rel}{' as YAML' if as_yaml else ''} -> {target}")
        ui.info(f"found in {len(arcs)} archive{'s' if len(arcs) != 1 else ''}: {', '.join(arcs[:4])}"
                + (" ..." if len(arcs) > 4 else ""))
    return 0


def _other_kind(kind: str) -> str:
    return "ddo" if kind == "ddda" else "ddda"


def cmd_port(args) -> int:
    """Bring a resource from one game into a mod for the other (converted and checked)."""
    from . import port
    from .mod import Mod

    m = Mod.load(Path(args.mod))
    dst_kind = m.game
    src_kind = args.source or _other_kind(dst_kind)
    if src_kind == dst_kind:
        raise RiftError(f"'{m.name}' is already a {KINDS[dst_kind]['title']} mod; use 'riftstone extract'")
    src_game, dst_game = find_game(src_kind), find_game(args.game or dst_kind)
    if dst_game.kind != dst_kind:
        raise RiftError(f"--game points at {dst_game.title}, but '{m.name}' is a {KINDS[dst_kind]['title']} mod")
    into = _arc_named(dst_game, args.arc) if args.arc else None
    from_arc = _arc_named(src_game, args.from_arc) if args.from_arc else None
    src_idx, dst_idx = _index(src_game), _index(dst_game)
    ui.step(f"{args.resource}  {KINDS[src_kind]['title']} -> {KINDS[dst_kind]['title']}")
    rebake = getattr(args, "retarget", None)
    if rebake is None:                                  # player motion lists rebake by default, like Studio's Port
        rebake = port.is_player_motion(args.resource)
    # --dye: Online's equipment colours baked in (ddodye.py); the port's recipe records the colour
    res = port.into_mod(m.root, src_game, dst_game, src_idx, dst_idx, args.resource, args.as_, args.like,
                        into, from_arc, args.model_only, rebake=rebake, dye=getattr(args, "dye", None))
    for w in res.written:
        ui.ok(f"-> {w}")
    for n in res.notes:
        ui.info(n)
    if res.dyed is not None:
        for w in res.dyed.written:
            ui.ok(f"-> {w} (dyed: {res.dye_label})")
        for w in res.dyed.removed:
            ui.info(f"removed the undyed copy {w} (no material uses it now)")
        for n in res.dyed.notes:
            ui.info(n)
    ui.info("structure checked; how it looks in game is UNKNOWN until you play it")
    return 0


def _describe_for_compare(data: bytes, tid: int) -> str:
    """One line: the format revision and the shape that matters for porting."""
    import struct as _st

    from . import port

    ext = typemap.extension(tid)
    try:
        if ext == "tex":
            from . import tex
            t = tex.parse(data)
            return f"tex rev 0x{t.version:x}, {t.width}x{t.height}, format {t.fmt}, {t.mip_count} mips"
        if ext == "gmd":
            from . import gmd
            g = gmd.parse(data)
            return f"gmd {gmd.VERSION_NAMES.get(g.version, hex(g.version))}, {len(g.messages)} messages, {g.language_name}"
        if ext == "mod":
            i = port.model_info(data)
            return f"model rev {i.version}, {len(i.vertex_formats)} meshes, {len(i.material_names)} materials, {i.bones} bones"
        if ext == "mrl":
            from . import mrl
            m = mrl.parse(data)
            classes = sorted({port.MATERIAL_CLASSES.get(x.shader, f"0x{x.shader:08x}") for x in m.materials})
            return f"mrl rev 0x{m.version:x}, {len(m.materials)} materials ({', '.join(classes)}), {len(m.textures)} textures"
        if data[:4] == b"XFS\0":
            from . import xfs
            x = xfs.parse(data)
            return f"xfs 0x{x.version:04x}, root {x.root_class.name}"
        if len(data) >= 8:
            return f"magic {bytes(data[:4])!r}, word 0x{_st.unpack_from('<I', data, 4)[0]:x}"
    except Exception as e:  # noqa: BLE001 - a description, never fatal
        return f"does not parse ({e})"
    return f"{len(data)} bytes"


def cmd_compare(args) -> int:
    """The same resource in both games, side by side (and whether it ports)."""
    from . import port

    games = []
    for kind in KINDS:
        try:
            games.append(find_game(kind))
        except RiftError:
            pass
    if len(games) < 2:
        raise RiftError("compare needs both games on this PC")
    found = {}
    for g in games:
        idx = _index(g, quiet=True)
        try:
            try:   # the path as given, else the --as path (each game has one of them)
                name, tid, data, arcs = port._locate(idx, g, args.resource)
            except RiftError:
                if not args.as_:
                    raise
                name, tid, data, arcs = port._locate(idx, g, args.as_)
            found[g.kind] = (name, tid, data, arcs)
            ui.ok(f"{g.title}: {fsmap.encode_name(name, tid)}  {ui.human(len(data))} in {len(arcs)} archive(s)")
            ui.info("  " + _describe_for_compare(data, tid))
        except RiftError as e:
            ui.warn(f"{g.title}: {e}")
        finally:
            idx.close()
    if len(found) == 2:
        (ka, (na, ta, da, _)), (kb, (nb, tb, db, _)) = found.items()
        if da == db:
            ui.ok("identical in both games")
        elif typemap.extension(ta) == "gmd":
            from . import gmd
            a, b = gmd.parse(da), gmd.parse(db)
            la = {m.label: m.text for m in a.messages if m.label}
            lb = {m.label: m.text for m in b.messages if m.label}
            both = [k for k in la if k in lb]
            diff = [k for k in both if la[k] != lb[k]]
            ui.info(f"{len(both)} labels in both, {len(diff)} with different text; "
                    f"{len(set(la) - set(lb))} only in {KINDS[ka]['title']}, {len(set(lb) - set(la))} only in {KINDS[kb]['title']}")
            for k in diff[:args.limit]:
                print(f"  {k}\n    {ka}: {la[k][:100]}\n    {kb}: {lb[k][:100]}")
    for src, dst in ((games[0].kind, games[1].kind), (games[1].kind, games[0].kind)):
        if src in found and port.portable(found[src][1]):
            name, tid, data, _ = found[src]
            try:
                if typemap.extension(tid) != "mrl":
                    port.convert(data, tid, src, dst)
                ui.info(f"ports {KINDS[src]['title']} -> {KINDS[dst]['title']}: riftstone port "
                        f"{fsmap.encode_name(name, tid)} --mod <{dst} mod>" + (" --like <material.mrl>" if typemap.extension(tid) == "mrl" else ""))
            except RiftError as e:
                ui.info(f"does not port {src} -> {dst}: {e}")
    return 0 if found else 1


def cmd_import(args) -> int:
    """Changed archives -> a mod holding only what changed (merges with other mods)."""
    from . import importer
    from .mod import MOD_FILE, Mod

    root = Path(args.mod)
    game = _game(args)
    paths = [Path(x) for x in args.paths]
    files = importer._inputs(paths)             # a missing input is refused before a new mod is made
    if (root / MOD_FILE).is_file():
        m = Mod.load(root)
        if m.game != game.kind:
            raise RiftError(f"{m.name} is a {KINDS[m.game]['title']} mod; import {game.title} archives into one "
                            f"made with --game {game.kind}")
    else:
        m = Mod.create(root, None, "", game.kind)
        ui.ok(f"Created {game.title} mod '{m.name}' at {m.root}")
    idx = _index(game)
    try:
        bar = ui.Progress(len(files), "comparing with the originals")
        rep = importer.import_archives(game, idx, paths, m.root, not args.binary, args.per_archive, bar)
        bar.done(f"{rep.archives} archive(s) compared")
    finally:
        idx.close()
    ui.ok(f"{rep.changed} changed and {rep.added} new resource(s) -> {m.root}  "
          f"({rep.to_files} in files/, {rep.to_archives} in archives/, {rep.as_yaml} as YAML)")
    if rep.unchanged:
        ui.info(f"{rep.unchanged} resource(s) were identical to the originals and are left out")
    if rep.removed:
        ui.warn(f"{rep.removed} resource(s) missing from the inputs stay as they are in the game "
                "(a mod cannot delete resources)")
    for u in rep.unmatched[:10]:
        ui.warn(f"no archive of the game matches {u}")
    ui.info(f'riftstone build "{m.root}" checks it; riftstone install "{m.root}" plays it')
    return 0 if not rep.unmatched else 1


def cmd_new(args) -> int:
    from .mod import Mod, check_name, find_mod, is_bare_name, mods_folders

    # --game, else $RIFTSTONE_GAME's game: a keyword, or a game folder (an Online folder made a DDDA mod)
    env = os.environ.get("RIFTSTONE_GAME", "")
    kind = args.game or KEYWORDS.get(env.lower()) or (env and detect_kind(Path(env))) or "ddda"
    folder = Path(args.folder)
    shared = is_bare_name(args.folder) and not args.here     # a plain name goes to the mods folder, where Studio
    if shared:                                               # and every --mod "<name>" find it
        name = args.folder.strip()
        check_name(name)
        taken = find_mod(name, mods_folders(kind))
        if taken is not None:
            raise RiftError(f'a mod called "{name}" is already at {taken}; pick another name')
        folder = _mods_folder_for(kind) / name
    m = Mod.create(folder, args.name, args.author or "", kind)
    ui.ok(f"Created {KINDS[m.game]['title']} mod '{m.name}' at {m.root}")
    ui.info(f'add a game file to it: riftstone extract <file> --mod "{folder.name if shared else folder}"   '
            "(riftstone find <name> finds files)")
    return 0


def _mods_folder_for(kind: str | None) -> Path:
    """The mods folder of the installed game of this kind (--game's default for None), else the default one."""
    from .mod import mods_folder

    try:
        return mods_folder(find_game(kind))
    except RiftError:
        return mods_folder(None)


def cmd_mods(args) -> int:
    """Your mods: each mods folder and the mods in it (their game, whether installed), then installed mods
    kept anywhere else."""
    from . import install
    from .mod import list_mods, mods_folder

    games: dict[str, Game] = {}
    for want in ([args.game] if args.game else list(KINDS)):
        try:
            g = find_game(want)
        except RiftError:
            continue
        games.setdefault(g.kind, g)
    installed: set[Path] = set()
    for g in games.values():
        try:
            installed |= {Path(m["path"]).resolve() for m in install.load_state(g).get("mods", []) if m.get("path")}
        except (RiftError, OSError):
            pass
    folders: dict[Path, list[str]] = {}
    for g in games.values():
        folders.setdefault(mods_folder(g), []).append(KINDS[g.kind]["title"])
    listed: set[Path] = set()
    for folder, titles in (folders or {mods_folder(None): []}).items():
        ui.ok(f"Mods folder: {folder}" + (f"  ({' and '.join(titles)})" if titles else ""))
        roots = list_mods(folder)
        if not roots:
            ui.info('none yet: riftstone new "My Mod" makes one here')
        for r in roots:
            listed.add(r.resolve())
            print("  " + _mod_line(r, installed))
    elsewhere = sorted(installed - listed)
    if elsewhere:
        ui.ok("Installed mods kept elsewhere")
        for r in elsewhere:
            print("  " + _mod_line(r, installed, at=True))
    if not games:
        ui.info("no game was found, so which mods are installed is UNKNOWN")
    return 0


def _mod_line(root: Path, installed: set[Path], at: bool = False) -> str:
    from .mod import Mod

    try:
        m = Mod.load(root)
        n = sum(1 for f in root.rglob("*") if f.is_file() and f.relative_to(root).parts[0] in ("files", "archives", "server")
                and f.name.lower() != "readme.txt")
        title = KINDS[m.game]["title"]
        text = f"{m.name}  " + ui.mist(f"{m.version}, {title}")
    except RiftError as e:
        return f"{root.name}  {ui.mist('(cannot be read: ' + str(e) + ')')}" + (ui.mist(f"  {root}") if at else "")
    state = ui.gold("installed") if root.resolve() in installed else ui.mist("not installed")
    return f"{text}  {n} file{'s' if n != 1 else ''}  [{state}]" + (ui.mist(f"  {root}") if at else "")


def _print_plan_conflicts(conflicts) -> None:
    for c in conflicts[:10]:
        what = f" ({c['detail']})" if c.get("detail") else ""
        ui.warn(f"{c['resource']} in {c['archive']}{what}: '{c['winner']}' overrides '{c['loser']}'")
    if len(conflicts) > 10:
        ui.warn(f"... and {len(conflicts) - 10} more")


def _print_merges(p) -> None:
    """Files merged from several mods (a Plan or an install report), and the groups and records they renumbered."""
    from .merging import kept

    for m in p.merged:
        ui.ok(f"{m['resource']} in {m['archive']}: merged from " + " and ".join(f"'{n}'" for n in m["mods"])
              + f", every mod's {kept(m)} kept")
    for r in p.renumbered:
        if "record" in r:
            ui.info(f"'{r['mod']}' record {r['record']} of {r['layout']} is record {r['as']} in the game (another mod "
                    f"adds a record {r['record']} there)")
        else:
            ui.info(f"'{r['mod']}' group {r['group']} of stage {r['stage']} ({r['type']}) is group {r['as']} in the "
                    f"game (another mod adds a group {r['group']} there); its layouts are renamed with it")
    for r in p.unmoved:
        what = (f"record {r['record']} of {r['layout']}" if "record" in r
                else f"group {r['group']} of stage {r['stage']} ({r['type']})")
        ui.warn(f"'{r['mod']}' {what} keeps its number: {r['why']}"
                + (f"; '{r['winner']}' has that number too" if r.get("winner") else ""))


def cmd_build(args) -> int:
    from . import mod as modlib

    game = _game(args)
    idx = _index(game)
    mods = [modlib.Mod.load(Path(m)) for m in args.mods]
    p = modlib.plan(game, idx, mods)
    modlib.check_plan(p)
    _print_merges(p)
    _print_plan_conflicts(p.conflicts)
    out = Path(args.out) if args.out else mods[0].root / "build"
    bar = ui.Progress(len(p.archives), "building")
    total = 0
    for a, changes in sorted(p.archives.items()):
        b = modlib.build_archive(game, a, changes)
        arcfolder.write_file(out / (a + ".arc"), b.data)
        total += len(b.data)
        bar.advance(1, a)
    bar.done(f"Built {len(p.archives)} archive{'s' if len(p.archives) != 1 else ''} from {p.changes} change"
             f"{'s' if p.changes != 1 else ''} ({ui.human(total)}), all verified")
    ui.info(f"-> {out}  (the game is untouched; use 'riftstone install' to play them)")
    return 0


def _legal_lines() -> str:
    from . import legal
    return legal.DISCLAIMER + "\n" + legal.FREE


def _package_plugins(args, items: list[str]) -> int:
    """riftstone package --plugins-only: the loader and plugins, to unzip into the game folder."""
    from . import package
    from . import plugins as pluginmod

    if items:
        raise RiftError("--plugins-only makes a loader + plugins zip (no mods, no game data); do not also name mods")
    if not args.out:
        raise RiftError("give the zip's file: --out dist\\Riftstone-Player.zip")
    out = Path(args.out)
    if out.exists() and not args.force:
        raise RiftError(f"{out} already exists; pick another name or add --force to replace it")
    # Riftstone's stable plugins by default; an experimental one (compat) only when --plugin names it
    plugin_paths = [Path(x) for x in args.plugin or []] or [p for stem, p in sorted(pluginmod.built().items())
                                                             if stem in package.OWN_PLUGINS]
    if not plugin_paths:
        raise RiftError("no plugins are built; run native\\plugins\\<name>\\build.cmd, or pass --plugin")
    ninput = None
    if args.ninput:
        ninput = (Path(__file__).resolve().parents[2] / "native" / "ninput" / "out-msvc" / "RelWithDebInfo" /
                  "xinput1_3.dll") if args.ninput == "built" else Path(args.ninput)
    r = package.build_plugins(plugin_paths, out, args.name or "Riftstone plugins", ninput=ninput)
    n = len(r["plugins"])
    ui.ok(f"Packaged the loader and {n} plugin{'s' if n != 1 else ''} (no game data)"
          + (", with Ninput under optional\\ninput (off until a player copies it)" if r["ninput"] else "")
          + f": {ui.human(r['bytes'])}")
    ui.info(f"-> {r['out']}")
    ui.info(f"a player unzips it into the game folder (the one with DDDA.exe); \"{r['readme']}\" inside says how")
    return 0


def cmd_package(args) -> int:
    """make (the default): a package of mods, with no game data; install: make its mods here from this PC's
    games; check: what a package holds, read without a game; --plugins-only: the loader and plugins in a zip
    for players without Riftstone (no mods, no game data)."""
    from . import mod as modlib
    from . import package, sources

    items = list(args.items)
    if args.plugins_only:
        return _package_plugins(args, items)
    action = items.pop(0) if items and items[0] in ("make", "install", "check") else "make"
    if action in ("install", "check"):
        if len(items) != 1:
            raise RiftError(f"riftstone package {action} <package.zip>")
        zp = Path(items[0])
        if not zp.is_file():
            raise RiftError(f"{zp} is not a file")
        if action == "check":
            r = package.check(zp)
            if args.json:
                print(json.dumps(r, indent=1))
                return 0
            ui.ok(f"{zp.name}: \"{r['name']}\", a {KINDS[r['game']]['title']} package ({r['made_with']}) with no "
                  "game files")
            for m in r["mods"]:
                ui.info(f"{m['folder']}: {m['files']} file(s), {m['new_bytes']:,} bytes of the author's own; the rest "
                        "is made from the player's game")
            if r["plugins"]:
                ui.info("plugins: " + ", ".join(r["plugins"]))
            ui.info("needs: " + ", ".join(KINDS[k]["title"] for k in r["needs"]))
            return 0
        games = sources.Games()
        try:
            from .studio import default_workspace
            into = Path(args.into) if args.into else default_workspace(None)
            ui.step(f"Making the mods of {zp.name} in {into} from this PC's games")
            r = package.install(zp, into, games)
        finally:
            games.close()
        for m in r["mods"]:
            ui.ok(f"-> {m}")
        for p in r["plugins"]:
            ui.info(f"plugin {Path(p).name}: add it with  Riftstone.cmd loader plugin add \"{p}\"")
        ui.info("every file was checked against the author's; turn the mods on in Studio, or: riftstone install "
                + " ".join(f'"{m}"' for m in r["mods"]))
        return 0
    if not items:
        raise RiftError("riftstone package <mod folder> ... --out <file.zip>")
    if not args.out:
        raise RiftError("give the package's file: --out dist\\my-mods.zip")
    out = Path(args.out)
    if out.exists() and not args.force:
        raise RiftError(f"{out} already exists; pick another name or add --force to replace it")
    mods = [modlib.Mod.load(Path(m)) for m in items]
    kinds = {m.game for m in mods}
    if len(kinds) != 1:
        raise RiftError("a package is for one game: package the Dark Arisen and the Online mods apart")
    game = find_game(args.game or kinds.pop())
    idx = _index(game)
    games = sources.Games({game.kind: game}, {game.kind: idx})
    try:
        p = modlib.plan(game, idx, mods)
        _print_merges(p)
        _print_plan_conflicts(p.conflicts)
        bar = ui.Progress(sum(len(modlib.collect(m)) for m in mods), "making deltas")
        r = package.build(games, [m.root for m in mods], [Path(x) for x in args.plugin or []], out, args.name,
                          progress=bar, note=ui.info)
    finally:
        games.close()
        idx.close()
    bar.done(f"Packaged {r['files']} file{'s' if r['files'] != 1 else ''} of {len(r['mods'])} mod(s)"
             + (f" and {len(r['plugins'])} plugin{'s' if len(r['plugins']) != 1 else ''}" if r["plugins"] else "")
             + f": {ui.human(r['bytes'])}, of which {r['new_bytes']:,} bytes are the authors' own and no game data")
    ui.info(f"-> {r['out']}")
    ui.info("a player makes the mods from their own game with: Riftstone.cmd package install \"<zip>\" "
            f"(\"{r['readme']}\" inside says how)"
            + ("; it needs " + " and ".join(KINDS[k]["title"] for k in r["needs"]) if len(r["needs"]) > 1 else ""))
    return 0


def _known_vanilla(game: Game) -> dict[str, str] | None:
    from . import install

    return install.known_vanilla(game)


def _apply(game: Game, roots: list[Path], dry_run: bool) -> int:
    from . import install

    idx = _index(game)
    ui.step(f"{'Planning' if dry_run else 'Installing'} {len(roots)} mod(s)")
    rep = install.apply(game, idx, roots, dry_run=dry_run, known_vanilla=_known_vanilla(game))
    _print_merges(rep)
    _print_plan_conflicts(rep.conflicts)
    verb = "Would write" if dry_run else "Wrote"
    for w in rep.written:
        ui.ok(f"{verb} {w['archive']}.arc ({len(w['replaced'])} replaced, {len(w['added'])} added) "
              f"{ui.mist('[' + ', '.join(w['mods']) + ']')}")
    for a in rep.restored:
        ui.ok(f"{'Would restore' if dry_run else 'Restored'} {a}.arc to the original")
    same_arcs = [u for u in rep.unchanged if not u.startswith(("loose/", "server/"))]
    same_loose = sum(u.startswith("loose/") for u in rep.unchanged)
    if same_arcs or same_loose:
        ui.info(f"{len(same_arcs)} archive(s)" + (f" and {same_loose} loose file(s)" if same_loose else "")
                + " already up to date")
    if rep.same_as_vanilla:
        ui.info(f"{len(rep.same_as_vanilla)} archive(s) left alone: your files there match the originals "
                f"({', '.join(rep.same_as_vanilla[:3])}{' ...' if len(rep.same_as_vanilla) > 3 else ''})")
    for f in rep.server_written:
        ui.ok(f"{'Would write' if dry_run else 'Wrote'} server file {f}")
    for f in rep.server_restored:
        ui.ok(f"{'Would restore' if dry_run else 'Restored'} server file {f} to the original")
    if (rep.server_written or rep.server_restored) and not dry_run:
        ui.info(f"server assets: {rep.server_assets}; restart the local server to load them")
    if rep.loose_written:
        ui.ok(f"{'Would write' if dry_run else 'Wrote'} {len(rep.loose_written)} loose file(s) into the overlay "
              f"({', '.join(rep.loose_written[:2])}{' ...' if len(rep.loose_written) > 2 else ''})")
    if rep.loose_removed:
        ui.ok(f"{'Would remove' if dry_run else 'Removed'} {len(rep.loose_removed)} loose file(s) no mod has any more")
    if not (rep.written or rep.restored or rep.server_written or rep.server_restored or rep.loose_written
            or rep.loose_removed):
        ui.ok("Nothing to do: the game already matches your mods")
    ui.info(f"mode: {rep.mode}" + ("  (originals kept in <game>/riftstone/vanilla)" if rep.mode == "direct" else
                                   "  (loader serves <game>/riftstone/overlay; nativePC untouched)"))
    return 0


def _enabled(game: Game) -> list[Path]:
    from . import install

    return [Path(m["path"]) for m in install.load_state(game).get("mods", [])]


def cmd_compat(args) -> int:
    from . import compat_pack
    from .game import find_game
    from .index import Index

    if args.compat_action == "list":
        for s in compat_pack.ALCHEMIST:
            ui.info(f"{s.key:12} {s.title} (Online Alchemist custom skill {s.number}, shell group {s.group})")
        return 0
    skills = [k.strip() for k in (args.skill or ",".join(compat_pack.SKILLS)).split(",") if k.strip()]
    ddo, ddda = find_game("ddo"), find_game(args.game or "ddda")
    if ddda.is_ddo:
        raise RiftError("compat packs are for Dark Arisen: --game names the Dark Arisen install")
    ui.warn("EXPERIMENTAL: the Online motions look wrong on Dark Arisen's body"
            + (", and converted effects stopped the game when tried" if args.effects else "")
            + ". The files made here are converted from Capcom's game data, from this computer's own copies of "
              "both games: for this computer only, never shared (docs/compat-layer.md)")
    ui.step(f"Converting {', '.join(skills)} from {ddo.title} for {ddda.title}"
            + ("" if args.effects else " (without effects)"))
    pack = compat_pack.build(compat_pack.Source(ddo, Index(ddo)), compat_pack.Source(ddda, Index(ddda)), skills,
                             effects=args.effects)
    from .studio import default_workspace

    root = Path(args.mod) if args.mod else default_workspace(ddda) / "DDO Alchemist"
    written = compat_pack.write_mod(pack, root, root.name, skills)
    ui.ok(f"{len(pack.files)} converted file(s) and the programs in {root / 'loose'} "
          f"({sum(len(d) for d in pack.files.values()) // 1024} KB)")
    for k, v in sorted(pack.counts.items()):
        ui.info(f"{v:6}  {k}")
    for n in pack.notes:
        ui.warn(n)
    ui.info(f"{len(written)} file(s) written; install with: riftstone install \"{root}\" (needs the loader and the "
            "compat plugin: riftstone loader plugin add native\\plugins\\compat\\out\\compat.asi)")
    return 0


def cmd_install(args) -> int:
    game = _game(args)
    roots = _enabled(game)
    for m in args.mods:
        p = Path(m).resolve()
        if all(p != r.resolve() for r in roots):
            roots.append(p)
    return _apply(game, roots, args.dry_run)


def cmd_uninstall(args) -> int:
    game = _game(args)
    enabled = _enabled(game)
    drop = {Path(m).resolve() for m in args.mods}
    known = {r.resolve() for r in enabled}
    for m in args.mods:
        if Path(m).resolve() not in known:          # was: "Nothing to do", as if it had been taken out
            ui.warn(f"{m} is not installed in {game.title} (riftstone status lists the installed mods)")
    if not drop & known:
        return 1
    roots = [r for r in enabled if r.resolve() not in drop]
    return _apply(game, roots, args.dry_run)


def cmd_restore(args) -> int:
    from . import install

    game = _game(args)
    done = install.restore_all(game)
    srv = sum(d.startswith("server/") for d in done)
    what = f"{len(done) - srv} archive(s)" + (f" and {srv} server file(s)" if srv else "")
    ui.ok(f"Restored {what}; the game is vanilla again" if done else "Nothing to restore")
    return 0


def cmd_status(args) -> int:
    from . import install

    game = _game(args)
    s = install.status(game)
    if args.json:
        print(json.dumps(s, indent=1))
        return 0
    ui.ok(f"Game: {game.root}")
    ui.info(f"mode: {s['mode']}   loader: {'installed' if s['loader'] else 'not installed'}")
    if not s["mods"]:
        ui.info("no mods installed by Riftstone")
    for m in s["mods"]:
        ui.info(f"mod  {m['name']} {m['version']}  (priority {m['priority']})  {m['path']}")
    ui.info(f"{len(s['archives'])} archive(s) changed"
            + (f", {len(s['server'])} server file(s) in {s['server_assets']}" if s.get("server") else ""))
    for a in s["drift"]:
        ui.warn(f"{a}{'' if a.startswith('server/') else '.arc'} no longer matches what Riftstone installed; "
                "run 'riftstone install' or 'restore'")
    return 1 if s["drift"] else 0


def cmd_watch(args) -> int:
    from . import install

    game = _game(args)
    roots = _enabled(game)
    for m in args.mods:
        p = Path(m).resolve()
        if all(p != r.resolve() for r in roots):
            roots.append(p)

    def snapshot():
        stamp = {}
        for r in roots:
            for f in r.rglob("*"):
                if f.is_file() and "build" not in f.relative_to(r).parts[:1]:
                    st = f.stat()
                    stamp[str(f)] = (st.st_size, st.st_mtime_ns)
        return stamp

    ui.step(f"Watching {len(roots)} mod(s); save a file to rebuild. Ctrl+C stops.")
    last = None          # files as last seen
    pending = True       # changes not yet installed (the first pass installs everything)
    said = ""            # last warning/error shown, so a waiting state is reported once
    retry_at = 0.0
    while True:
        try:
            cur = snapshot()
            if cur != last:
                if last is not None:
                    changed = [k for k in cur if last.get(k) != cur[k]] + [k for k in last if k not in cur]
                    ui.step(f"{time.strftime('%H:%M:%S')} change: {Path(changed[0]).name}"
                            + (f" (+{len(changed) - 1})" if len(changed) > 1 else ""))
                last = cur
                pending, retry_at, said = True, 0.0, ""
            if pending and time.time() >= retry_at:
                if install.mode_for(game) == "direct" and install.game_running(game):
                    msg = ("game is running; direct mode installs when it closes "
                           "(install the loader to update mods while playing)")
                    if said != msg:
                        ui.warn(msg)
                        said = msg
                    retry_at = time.time() + 3
                else:
                    try:
                        t0 = time.time()
                        _apply(game, roots, False)
                        ui.info(f"done in {time.time() - t0:.1f}s")
                        pending, said = False, ""
                    except RiftError as e:
                        if str(e) != said:
                            ui.fail(str(e))
                            said = str(e)
                        # an archive the game holds open is retried; anything else waits for the next edit
                        pending = "in use" in str(e)
                        retry_at = time.time() + 3
            time.sleep(0.75)
        except KeyboardInterrupt:
            ui.info("stopped watching")
            return 0


def cmd_index(args) -> int:
    from .index import Index

    game = _game(args)
    if args.rebuild:
        idx = Index(game)
        idx.db.execute("DELETE FROM arcs")
        idx.db.execute("DELETE FROM res")
        idx.db.commit()
        idx.close()
    idx = _index(game)
    s = idx.stats()
    ui.ok(f"{s['resources']:,} resources in {s['archives']:,} archives  ({idx.path})")
    for t in s["types"][:args.top]:
        print(f"  {ui.gold(t['ext']):<22} {t['count']:>8,}  {ui.mist(t['class'])}")
    return 0


def cmd_info(args) -> int:
    for f in args.files:
        f = Path(f)
        if f.is_dir():
            m = f / arcfolder.MANIFEST
            if m.is_file():
                try:        # read as pack reads it: a damaged manifest is refused, not a traceback
                    man = json.loads(m.read_text(encoding="utf-8"))
                except (ValueError, UnicodeDecodeError, RecursionError) as e:
                    raise RiftError(f"{m} is damaged: {e}") from None
                if not (isinstance(man, dict) and isinstance(man.get("archive"), str)
                        and isinstance(man.get("entries"), list)):
                    raise RiftError(f"{m} is not a Riftstone archive manifest (no archive name and entry list)")
                ui.ok(f"{f.name}: unpacked archive '{man['archive']}' with {len(man['entries'])} resources")
            else:
                from .mod import MOD_FILE, Mod, collect

                if (f / MOD_FILE).is_file():
                    mod = Mod.load(f)
                    ui.ok(f"{f.name}: mod '{mod.name}' {mod.version}, {len(collect(mod))} resource change(s)")
                else:
                    ui.warn(f"{f}: not a Riftstone folder")
            continue
        if not f.exists():
            raise RiftError(f"{f} does not exist")
        data = f.read_bytes()
        if data[:4] == b"ARC\0":
            a = arc.Archive.parse(data)
            ui.ok(f"{f.name}: ARC v{a.version}, {len(a.entries)} resources, {ui.human(len(data))}")
            counts: dict[str, int] = {}
            for e in a.entries:
                counts[typemap.extension(e.type_id)] = counts.get(typemap.extension(e.type_id), 0) + 1
            ui.info(", ".join(f"{k} x{v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])))
        elif data[:4] == b"XFS\0":
            x = xfs.parse(data)
            n = sum(1 for _ in xfs.walk(x.root))
            ui.ok(f"{f.name}: XFS {x.root_class.name}, {n} objects, {len(x.classes)} classes")
            ui.info("classes: " + ", ".join(c.name for c in x.classes[:8]) + (" ..." if len(x.classes) > 8 else ""))
        else:
            tid = _resource_type_from_filename(f)
            ui.ok(f"{f.name}: {typemap.class_name(tid) if tid else 'unknown type'}, {ui.human(len(data))}, "
                  f"magic {data[:4]!r}")
    return 0


def _doctor_address_space(game: Game) -> int:
    """The exe's large-address-aware flag (4 GB of address space, not 2 GB).  1 when it is missing."""
    from . import pe

    try:
        info = pe.describe(game.exe)
    except (OSError, RiftError) as e:
        ui.warn(f"{game.exe.name} could not be read for its address-space flag ({e})")
        return 0
    if info["bits"] != 32:
        return 0
    if info["large_address_aware"]:
        ui.ok(f"{game.exe.name} is large-address aware: 4 GB of address space on 64-bit Windows")
        return 0
    ui.warn(f"{game.exe.name} is NOT large-address aware: it gets 2 GB of address space instead of 4 GB. "
            "'riftstone laa --copy <file>' writes a copy with the flag set (the game's own file is never changed)")
    return 1


def _doctor_d3d9(game: Game) -> int:
    """[d3d9] chain (DXVK) and any d3d9.dll put straight into the game folder."""
    from . import loader as loader_mod

    st = loader_mod.d3d9_status(game)
    if st["game_folder_d3d9"]:
        ui.info(f"the game folder has its own d3d9.dll ({'DXVK' if st['game_folder_dxvk'] else 'not DXVK'}): the game "
                "uses it instead of Windows' Direct3D 9")
    if not st["setting"]:
        return 0
    if st["problem"]:
        ui.warn(f"[d3d9] chain = {st['setting']} {st['problem']}; the game gets its usual Direct3D 9")
        return 1
    ui.ok(f"[d3d9] chain: {st['setting']} ({'DXVK' if st['dxvk'] else 'a Direct3D 9 runtime'}, sha256 "
          f"{st['sha256'][:16]}...){' with ' + st['conf'].name if st['conf'] else ''}")
    return 0


def _doctor_runtime(game: Game, loader_installed: bool, running: bool = False) -> int:
    """The runtime's part of doctor: loader version, safe mode, quarantine, how the last session ended,
    the last reports, saves."""
    from . import loader as loader_mod
    from . import runtime

    problems = 0
    installed = runtime.loader_version(game.root / "dinput8.dll") if loader_installed else None
    try:
        built = runtime.loader_version(loader_mod.built_loader() / "dinput8.dll")
    except RiftError:
        built = None
    if installed and built and installed != built:
        ui.warn(f"the installed loader is {installed}; this Riftstone has {built}: 'riftstone loader install' updates it "
                "(your settings are kept)")
    elif installed:
        ui.ok(f"loader {installed} installed (F10 in the game shows its diagnostics panel)")
    if loader_installed:
        problems += _doctor_d3d9(game)
        from . import plugins as plugins_mod
        rows = [r for r in plugins_mod.describe(game) if r["installed"]]
        on = [r["name"] for r in rows if r["enabled"]]
        off = [r["name"] for r in rows if not r["enabled"]]
        if rows:
            ui.ok(f"plugins on: {', '.join(on) or 'none'}" + (f"; off: {', '.join(off)}" if off else ""))
    st = runtime.runtime_state(game.root)
    if st["safe_mode"]:
        problems += 1
        ui.warn("SAFE MODE: the game crashed twice in a row while starting, so it now starts without plugins and mods. "
                "It ends when a mod or plugin changes, or with 'riftstone loader safe-mode off'. 'riftstone crash' "
                "explains the crashes.")
    for name in st["quarantine"]:
        problems += 1
        ui.warn(f"plugin {name} is quarantined (it crashed the game's start-up twice); a new version of the file is "
                f"loaded again, or 'riftstone loader plugin release {name}'")
    _show_session_end(runtime.session_end(game.root, running=running))
    reports = runtime.list_reports(game.state_dir / "logs")
    if reports:
        last = reports[0]
        rep = runtime.parse_report(last["path"].read_text(encoding="utf-8", errors="replace"))
        lines = runtime.explain(rep, game.root)
        ui.info(f"newest report: {last['name']} -- {lines[0] if lines else ''} ('riftstone crash' explains it)")
    from . import saves

    copies = saves.backups(saves.backup_root(game))
    if copies:
        newest = max((b.time for b in copies if b.time), default=None)
        ui.ok(f"{len(copies)} cop{'y' if len(copies) == 1 else 'ies'} of your save"
              + (f", the newest from {newest:%Y-%m-%d %H:%M}" if newest else "") + " ('riftstone saves list')")
    elif loader_installed:
        ui.info("no save backups yet: the loader makes one the next time the game starts ([saves] backup = 1)")
    live = runtime.read_live()
    if live and not live["exited"]:
        for line in runtime.describe_live(live)[:3]:
            ui.info("live: " + line)
    return problems


def cmd_doctor(args) -> int:
    problems = 0
    ui.ok(f"Riftstone {__version__} on Python {sys.version.split()[0]}")
    try:
        game = _game(args)
        ui.ok(f"Game found: {game.title} at {game.root}")
    except RiftError as e:
        ui.fail(str(e))
        return 1
    from . import install
    from .game import KINDS

    digest = install.sha256_file(game.exe)
    if digest == game.known_build_sha256:
        ui.ok(f"{game.exe.name} is {KINDS[game.kind]['build']} (the build Riftstone is tested against)")
    else:
        ui.warn(f"{game.exe.name} differs from the tested build (sha256 {digest[:16]}...); formats are likely the "
                "same, but crash offsets and any engine patches are build-specific")
    problems += _doctor_address_space(game)
    other = game.other()
    if other:
        ui.ok(f"also found {other.title} at {other.root} (use --game {other.kind})")
    running = install.game_running(game)
    if running:
        ui.warn("the game is running; install/restore wait until it closes")
    state = install.status(game)
    ui.ok(f"mode {state['mode']}, loader {'installed' if state['loader'] else 'not installed'}, "
          f"{len(state['mods'])} mod(s), {len(state['archives'])} archive(s) changed")
    for a in state["drift"]:
        problems += 1
        ui.warn(f"{a}.arc was changed by something other than Riftstone since install")
    other = game.root / "dinput8.dll"
    if other.is_file() and not state["loader"] and not game.is_ddo:
        ui.warn("a dinput8.dll that is not Riftstone's loader is installed (e.g. DDDA Tweak); "
                "the loader can chain-load it -- see docs/loader.md")
    problems += _doctor_runtime(game, state["loader"], running)
    known = _known_vanilla(game)
    if known and args.verify:
        bar = ui.Progress(len(known), "hashing archives")
        bad = []
        for a in sorted(known):
            p = game.vanilla_arc(a)
            if not p.is_file() or not install.matches_vanilla(p, known[a]):
                bad.append(a)
            bar.advance(1, a)
        bar.done(f"{len(known) - len(bad):,} of {len(known):,} original archives verified")
        for a in bad[:20]:
            ui.warn(f"{a}.arc is not the original file")
        problems += len(bad)
    elif known:
        ui.info("run 'riftstone doctor --verify' to check every original archive against its known hash")
    return 1 if problems else 0


def cmd_open(args) -> int:
    from . import inspect as inspector

    for target in args.targets:
        p = Path(target)
        if p.is_file():
            data = p.read_bytes()
            tid = _resource_type_from_filename(p) or 0
            name = p.name
        else:
            game = _game(args)
            idx = _index(game)
            name_b, tid, data, arcs = _locate(idx, game, target, args.arc)
            name = f"{target}  (in {arcs[0]}{', +%d more' % (len(arcs) - 1) if len(arcs) > 1 else ''})"
        rep = inspector.describe(data, tid)
        print(rep.text(name))
        print()
    return 0


def cmd_fsm(args) -> int:
    from . import fsm, fsmcheck

    if args.model and len(args.targets) != 1:
        raise RiftError("--model writes one machine: give one target")
    out, problems = [], 0
    for target in args.targets:
        p = Path(target)
        if p.is_file():
            data, name = p.read_bytes(), p.name
            if p.suffix.lower() == ".yaml":
                data = params.yaml_to_resource(params.decode_text(data, p.name), p.name)
        else:
            game = _game(args)
            name_b, tid, data, arcs = _locate(_index(game), game, target, args.arc)
            name = target
        if data[:4] != b"XFS\0":
            raise RiftError(f"{name} is not an AI state machine (.fsm)")
        try:
            x = xfs.parse(data)
            if not (args.check or args.model):
                out.append(fsm.decompile(x, name))
                continue
            m = fsmcheck.read(x)
            if args.model:
                try:
                    doc = fsmcheck.model(m, args.level)
                except ValueError as e:
                    raise RiftError(f"{name}: {e}; its machines: " + "; ".join(fsmcheck.levels(m))) from None
                arcfolder.write_file(Path(args.model),
                                     (json.dumps(doc, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
                ui.ok(f"wrote {args.model}: machine {args.level} of {name}, {len(m.levels[args.level].states)} "
                      f"states, {len(doc['transitions']) - 1} transitions; check it with "
                      f"<path> formal fsm {args.model}")
                return 0
            found = fsmcheck.check(m)
            problems += sum(1 for f in found if f.severity == fsmcheck.PROBLEM)
            machines = fsmcheck.levels(m)
            more = f"; ... ({len(machines) - 20} more)" if len(machines) > 20 else ""
            out.append("\n".join([f"state machine {name}"] + fsmcheck.report(m, found)
                                 + ([f"  machines (--model --level N): " + "; ".join(machines[:20]) + more]
                                    if len(machines) > 1 else [])) + "\n")
        except ValueError as e:
            raise RiftError(f"{name}: {e}") from None
    text = "\n".join(out)
    if args.out:
        arcfolder.write_file(Path(args.out), text.encode("utf-8"))
        ui.ok(f"wrote {args.out}")
    else:
        print(text, end="")
    return 1 if problems else 0


def cmd_tex(args) -> int:
    from . import tex

    def load(target):
        p = Path(target)
        if p.is_file():
            return p.read_bytes(), p.name, p
        game = _game(args)
        name_b, tid, data, arcs = _locate(_index(game), game, target, args.arc)
        return data, target, None

    if args.action == "info":
        for target in args.targets:
            data, name, _ = load(target)
            print(f"  {name}: {tex.info(tex.parse(data))}")
        return 0

    if args.action == "to-dds":
        if len(args.targets) != 1:
            raise RiftError("tex to-dds takes one .tex")
        data, name, src = load(args.targets[0])
        t = tex.parse(data)
        out = Path(args.out) if args.out else (src.with_suffix(".dds") if src
                                               else Path(Path(name).name).with_suffix(".dds"))
        dds = tex.to_dds(t)
        _keep_edits(out, dds, False, "write this one elsewhere with -o")     # the .dds is the one people edit
        arcfolder.write_file(out, dds)
        ui.ok(f"wrote {out}  ({tex.info(t)})")
        return 0

    if args.action == "preset":
        from . import texfx
        if len(args.targets) != 1:
            raise RiftError("tex preset takes one .tex")
        if not args.preset:
            raise RiftError(f"tex preset needs --preset ({', '.join(texfx.PRESETS)})")
        data, name, src = load(args.targets[0])
        out_bytes = texfx.apply(data, args.preset, args.strength, args.seed)
        out = Path(args.out) if args.out else (src.with_name(f"{src.stem}_{args.preset}.tex") if src
                                               else Path(Path(name).stem + f"_{args.preset}.tex"))
        arcfolder.write_file(out, out_bytes)
        ui.ok(f"wrote {out}  ({args.preset}, strength {args.strength}; {tex.info(tex.parse(out_bytes))})")
        return 0

    if args.action == "from-dds":
        if len(args.targets) != 1:
            raise RiftError("tex from-dds takes one .dds")
        src = Path(args.targets[0])
        if not src.is_file():
            raise RiftError(f"{src} is not a file")
        dds = src.read_bytes()
        template = None
        like = Path(args.like) if args.like else src.with_suffix(".tex")
        if like.is_file():
            template = tex.parse(like.read_bytes())
        elif args.like:
            raise RiftError(f"{args.like} does not exist")
        # the original decides the game, unless --game says otherwise; a new texture is the game's own
        kind = _game_kind(args) if (args.game or template is None) else None
        version = tex.GAME_VERSION[kind] if kind else tex.VERSION
        t = tex.dds_to_tex(dds, template, version=version)
        note = "kept the original texture's format" if template else f"new {KINDS[kind]['title']} texture"
        if template is not None and kind and t.version != version:
            t.version, t.attr1 = version, tex.attr1_for(t.attr1, version)
            note += f", as a {KINDS[kind]['title']} texture"
        out = Path(args.out) if args.out else src.with_suffix(".tex")
        arcfolder.write_file(out, tex.build(t))
        ui.ok(f"wrote {out}  ({tex.info(t)}; {note})")
        return 0
    raise RiftError(f"unknown tex action {args.action}")


def cmd_terrain(args) -> int:
    from . import terrain

    if _game_kind(args) == "ddo":
        if args.action in ("localize", "worldize", "check", "cells"):
            raise RiftError("Dragon's Dogma Online's field has no terrain cells: its terrain (scr\\fd\\model) is "
                            "stored in world space, so nothing needs moving. 'terrain where <name>' says more")
    if args.action != "cells" and not args.target:
        raise RiftError(f"terrain {args.action} needs a target (see riftstone terrain -h)")
    if args.action == "cells":
        cs = terrain.cells()
        if args.json:
            print(json.dumps([{"cell": c.name, "m": c.m, "n": c.n, "model": c.model, "collisions": list(c.collisions),
                               "archive": c.archive, "offset": list(c.offset)} for c in cs]))
            return 0
        for c in cs:
            dx, _, dz = c.offset
            print(f"  {c.name}  adds x {dx:>9.0f}  z {dz:>9.0f}   {c.archive}")
        ui.info(f"{len(cs)} cells; world = local + (10000*n - 500000, 0, 10000*m - 500000) cm "
                "(docs/terrain.md)")
        return 0
    if args.action == "where":
        text = args.target
        parts = text.replace(" ", "").split(",")
        if len(parts) in (2, 3):
            try:
                nums = [float(p) for p in parts]
            except ValueError:
                nums = None
            if nums:
                if not all(math.isfinite(v) for v in nums):
                    raise RiftError(f"({text}): a world position is two or three finite numbers, x,z or x,y,z")
                if _game_kind(args) == "ddo":
                    raise RiftError("Dragon's Dogma Online's field has no terrain cells: a position there is a "
                                    "world position as it is (its terrain, scr\\fd\\model, is world space)")
                x, z = nums[0], nums[-1]
                c = terrain.cell_at(x, z)
                if c not in set(terrain.cells()):
                    raise RiftError(f"({text}) lies outside Gransys' terrain: it would be cell {c.name}, which the "
                                    "game does not have ('riftstone terrain cells' lists the ones it has)")
                dx, _, dz = c.offset
                print(f"  ({text}) lies in cell {c.name}: local x {x - dx:.1f}, z {z - dz:.1f}")
                return 0
        kind = _game_kind(args)
        try:
            name = terrain.parse_cell(text).model if kind == "ddda" else text
        except RiftError:
            name = text
            if Path(text).suffix:
                try:
                    name = fsmap.decode_path(text)[0].decode("latin-1")
                except RiftError:
                    name = Path(text).stem
        f = terrain.frame(name, kind)
        print(f"  {name}: {f.kind} -- {f.why}")
        return 0
    if args.action in ("localize", "worldize", "check"):
        src = Path(args.target)
        if not src.is_file():
            raise RiftError(f"{src} is not a file")
        cell = terrain.parse_cell(args.cell if args.cell else str(src))
        data = src.read_bytes()
        if args.action == "check":
            errors, notes = terrain.check(data, cell)
            for e in errors:
                ui.fail(f"{src.name}: {e}")
            for n in notes:
                ui.warn(f"{src.name}: {n}")
            if not errors and not notes:
                ui.ok(f"{src.name} fits cell {cell.name} (local frame, bounds hold every vertex)")
            return 1 if errors else 0
        if args.action == "localize":
            errors, _ = terrain.check(data, cell)
            if not any("world coordinates" in e for e in errors) and not args.force:
                raise RiftError(f"{src.name} is not in cell {cell.name}'s world position; taking the corner off "
                                "again would move it away (pass --force to move it anyway)")
            out_data = terrain.localize(data, cell)
        else:
            out_data = terrain.worldize(data, cell)
        dst = Path(args.out) if args.out else src.with_name(f"{src.stem}.{args.action[:5]}{src.suffix}")
        if args.action == "worldize":           # the world-space copy is the one edited next to its neighbours
            _keep_edits(dst, out_data, False, "write this one elsewhere with -o")
        arcfolder.write_file(dst, out_data)
        dx, _, dz = cell.offset
        sign = "-" if args.action == "localize" else "+"
        ui.ok(f"wrote {dst}  (cell {cell.name}, positions {sign}({dx:.0f}, 0, {dz:.0f}) cm)")
        errors, notes = terrain.check(out_data, cell)
        if args.action == "localize":
            for e in errors:
                ui.fail(e)
            for n in notes:
                ui.warn(n)
        return 0
    raise RiftError(f"unknown terrain action {args.action}")


def cmd_mrl(args) -> int:
    from . import mrl

    def load(target):
        p = Path(target)
        if p.is_file():
            return p.read_bytes(), p.name, p
        game = _game(args)
        name_b, tid, data, arcs = _locate(_index(game), game, target, args.arc)
        return data, target, None

    data, name, src = load(args.target)
    m = mrl.parse(data)
    if args.action == "info":
        print(f"  {name}:")
        print("  " + mrl.info(m).replace("\n", "\n  "))
        return 0
    if args.action == "retex":
        if not args.old or not args.new:
            raise RiftError("mrl retex needs --old <name> --new <name>")
        hit = [t for t in m.textures if t.name == args.old]
        if not hit:
            raise RiftError(f"no texture named {args.old!r}; use 'mrl info' to list them")
        for t in hit:
            t.set_name(args.new)
        out = Path(args.out) if args.out else (src if src else Path(Path(name).name))
        arcfolder.write_file(out, mrl.build(m))
        ui.ok(f"repointed {len(hit)} texture ref(s) {args.old} -> {args.new} in {out}")
        return 0
    raise RiftError(f"unknown mrl action {args.action}")


def _load_resource(args, target, game_kind=None):
    """(bytes, display name, path on disk or None) for a file on disk or an engine path in a game."""
    p = Path(target)
    if p.is_file():
        return p.read_bytes(), p.name, p
    game = find_game(game_kind) if game_kind else _game(args)
    _name, _tid, data, _arcs = _locate(_index(game), game, target, getattr(args, "arc", None))
    return data, target, None


def _body_model(kind: str, given: str | None) -> bytes:
    """A skeleton for --retarget: a .mod file on disk, else an engine path in that game (default: its
    player body). Looked up by name alone -- a motion list's --arc does not apply to it."""
    from . import port as portmod

    if given and Path(given).is_file():
        return Path(given).read_bytes()
    game = find_game(kind)
    return portmod._locate(_index(game), game, given or portmod.PLAYER_BODY[kind])[2]


def cmd_lmt(args) -> int:
    import struct

    from . import lmt, lmtcodec, port as portmod

    if args.action == "info":
        for target in args.targets:
            data, name, _ = _load_resource(args, target)
            m = lmt.parse(data)
            print(f"  {name}: {lmt.info(m)}")
            for line in lmt.table(m, args.limit):
                print("    " + line)
        return 0
    if len(args.targets) != 1:
        raise RiftError(f"lmt {args.action} takes one motion list")
    data, name, src = _load_resource(args, args.targets[0])
    m = lmt.parse(data)
    if args.action == "bones":
        print(f"  {name}: {lmt.info(m)}")
        for bone, n in sorted(lmt.bones(m).items()):
            print(f"    bone {bone:>3}  {n:>5} track(s)")
        return 0
    if args.action == "keys":
        live = [i for i, mo in enumerate(m.motions) if mo is not None]
        slot = args.motion if args.motion is not None else (live[0] if live else None)
        if slot is None or not 0 <= slot < len(m.motions) or m.motions[slot] is None:
            raise RiftError(f"no motion in slot {slot}; lmt info lists them")
        mo = m.motions[slot]
        print(f"  {name} motion {slot}: {mo.frames} frames, loop {mo.loop}, {len(mo.tracks.tracks)} tracks")
        for k, t in enumerate(mo.tracks.tracks):
            codec = lmt.CODECS.get(t.codec, (f"codec{t.codec}",))[0]
            usage = lmt.USAGES.get(t.usage, f"usage{t.usage}")
            if t.buffer is None:
                ref = struct.unpack("<4f", t.reference)
                print(f"    track {k:>3} bone {t.bone:>3} {usage:18} constant {tuple(round(x, 5) for x in ref)}")
                continue
            vals = lmtcodec.values(t.codec, t.buffer.data, t.extremes.data if t.extremes else None, t.reference)
            print(f"    track {k:>3} bone {t.bone:>3} {usage:18} {codec} ({t.codec}), {len(vals)} keys")
            for f, v in vals[:args.limit]:
                print(f"        frame {f:>5}  {tuple(round(x, 5) for x in v)}")
        return 0
    if args.action == "convert":
        if not args.to:
            raise RiftError("lmt convert needs --to ddda|ddo")
        src_kind = "ddda" if m.version == portmod.LMT_VERSION["ddda"] else "ddo"
        rebake = None
        if args.retarget:
            if src_kind == args.to:
                raise RiftError("--retarget moves a motion list between the two games; --to names the same game")
            sb = _body_model(src_kind, args.src_body)
            db = _body_model(args.to, args.dst_body)
            joints = portmod.parse_bones(args.retarget_joints) if args.retarget_joints else None
            rebake = (sb, db, joints) if joints else (sb, db)
        elif args.src_body or args.dst_body or args.retarget_joints:
            raise RiftError("--src-body, --dst-body and --retarget-joints go with --retarget")
        done = portmod.convert_lmt(data, src_kind, args.to,
                                   drop_bones=portmod.parse_bones(args.drop_bones) if args.drop_bones else None,
                                   rebake=rebake)
        out = Path(args.out) if args.out else (src.with_name(src.stem + f".{args.to}.lmt") if src
                                               else Path(Path(name).name).with_suffix(f".{args.to}.lmt"))
        arcfolder.write_file(out, done.data)
        ui.ok(f"wrote {out}  ({src_kind} -> {args.to})")
        for n in done.notes:
            print(f"    {n}")
        return 0
    raise RiftError(f"unknown lmt action {args.action}")


def cmd_learn(args) -> int:
    """Learn more about a Studio tab, a format or a field, in English or Japanese."""
    from . import help as helpmod

    if not args.topic:
        print("Topics: " + ", ".join(sorted(helpmod.TOPICS)))
        return 0
    topic = next((t for t in (args.topic, f"tab:{args.topic.lower()}") if t in helpmod.TOPICS), None) \
        or helpmod.topic_for(args.topic)             # a Studio tab by its name too: learn world
    if topic is None:
        raise RiftError(f"no help for {args.topic!r}; `riftstone learn` lists the topics")
    print(helpmod.text(topic, args.lang, args.field))
    return 0


def cmd_skeleton(args) -> int:
    """A model's joints (what motions address), or two models' skeletons compared by joint id."""
    from . import port as portmod

    if len(args.targets) > 2:                           # was: the third and later were left out without a word
        raise RiftError("skeleton takes one model, or two to compare")
    a, an, _ = _load_resource(args, args.targets[0])
    if len(args.targets) == 1:
        sk = portmod.skeleton(a)
        print(f"  {an}: {len(sk)} joints")
        for j in sorted(sk.values(), key=lambda j: j.id):
            print(f"    joint {j.id:>3}  parent {'-' if j.parent is None else j.parent:>3}  "
                  f"offset {tuple(round(x, 2) for x in j.offset)}")
        return 0
    b, bn, _ = _load_resource(args, args.targets[1], args.vs_game)
    d = portmod.skeleton_diff(a, b, args.tolerance)
    print(f"  {an}  vs  {bn}: {d['common']} joint ids in common")
    print(f"    only in the first:  {d['only_first'] or 'none'}")
    print(f"    only in the second: {d['only_second'] or 'none'}")
    print(f"    parent differs:     {d['parent_differs'] or 'none'}")
    print(f"    offset differs by more than {args.tolerance}: {d['offset_differs'] or 'none'}")
    return 0


def _ddo_items(args, game: Game) -> int:
    """items for Dragon's Dogma Online: the local server's item list, shops and enemy drop tables."""
    from . import ddo
    from .mod import SERVER_DIR, Mod

    assets = ddo.need_assets(game)
    items = ddo.load_items(assets)
    words = " ".join(args.rest)
    m = Mod.load(Path(args.mod)) if args.mod else None
    if m is not None and m.game != "ddo":
        raise RiftError(f"{m.name} is a Dark Arisen mod; make one with: riftstone new <folder> --game ddo")
    root = m.root if m else None

    def name(i: int) -> str:               # a row shorter than the header has no Name
        return items[i].get("Name", "")

    def one_item() -> int:
        ids = ddo.find_items(items, words)
        if not ids:
            raise RiftError(f"no item is called {words!r} (riftstone items list <words> --game ddo)")
        if len(ids) > 1 and not words.isdigit():
            exact = [i for i in ids if name(i).lower() == words.lower()]
            if len(exact) != 1:
                raise RiftError(f"{len(ids)} items match {words!r}: "
                                + ", ".join(f"{i} {name(i)}" for i in ids[:6]) + "; give the id")
            ids = exact
        return ids[0]

    def save(rel: str, doc, style) -> None:
        if root is None:
            raise RiftError('say which mod gets it: --mod "My Mod" (riftstone new "My Mod" --game ddo)')
        target = root / SERVER_DIR / rel
        arcfolder.write_file(target, ddo.dumps_style(doc, style))
        ui.ok(f"-> {target}")
        ui.info(f"riftstone install \"{root}\" puts it into the server ({assets}); restart the server to load it")

    if args.action == "list":
        hits = ddo.find_items(items, words) if words else sorted(items)
        for i in hits[:args.limit]:
            r = items[i]
            print(f"  {i:>6}  {name(i):<40} {ui.mist('price ' + (r.get('Price') or '-'))}")
        ui.info(f"{len(hits)} item(s)" + (f"; showing {args.limit}" if len(hits) > args.limit else ""))
        return 0 if hits else 1
    if args.action == "shop":
        src, _ = ddo.server_file(game, root, ddo.SHOP_FILE)
        shops, style = ddo.read_json(src)
        ddo.check_shops(shops, src)
        if not words:
            for sh in shops:
                goods = sh["Data"]["GoodsParamList"]
                names = ", ".join(items.get(g["ItemId"], {}).get("Name", str(g["ItemId"])) for g in goods[:4])
                print(f"  shop {sh['ShopId']:>4}  {len(goods):>3} goods  {ui.mist(names + (' ...' if len(goods) > 4 else ''))}")
            ui.info("put an item on sale: riftstone items shop <item> --shop <id> --mod <mod> --game ddo "
                    "(--buy price, --stock)")
            return 0
        if not args.shop or not re.fullmatch(r"[0-9]{1,9}", str(args.shop)):     # '²' passed isdigit()
            raise RiftError("--shop <shop id> (riftstone items shop --game ddo lists the shops)")
        item = one_item()
        listed = (items[item].get("Price") or "").strip()
        if args.buy is None and listed and not re.fullmatch(r"[0-9]{1,9}", listed):
            raise RiftError(f"{name(item)} ({item}) costs {listed!r} in the server's item list, which is no price: "
                            "give the shop's price with --buy")
        price = args.buy if args.buy is not None else int(listed or 1)
        note = ddo.shop_add(shops, int(args.shop), item, price, args.stock)
        ui.ok(f"{name(item)} ({item}) for {price}: {note}")
        save(ddo.SHOP_FILE, shops, style)
        return 0
    if args.action in ("sets", "drop"):
        src, _ = ddo.server_file(game, root, ddo.SPAWN_FILE)
        spawn, style = ddo.read_json(src)
        ddo.check_drop_tables(spawn, src)
        item = one_item()
        if args.action == "sets":
            hits = ddo.drop_tables_with(spawn, item)
            for tid, table, chance in hits:
                print(f"  table {tid:>4}  {table:<40} {chance * 100:.0f}%")
            ui.info(f"{name(item)} ({item}) drops from {len(hits)} table(s); add it to another: "
                    "riftstone items drop <item> --set <table> --percent N --mod <mod> --game ddo")
            return 0
        if args.set is None or args.weight_pct is None:
            raise RiftError("--set <drop table id> and --percent <chance> (items sets <item> --game ddo finds tables)")
        ddo.check_spawns(spawn, src)                  # drop counts the rows that use the table, and writes it back
        note = ddo.drop_add(spawn, args.set, item, args.weight_pct / 100)
        ui.ok(f"{name(item)} ({item}) at {args.weight_pct}%: {note}")
        save(ddo.SPAWN_FILE, spawn, style)
        return 0
    raise RiftError(f"'items {args.action}' is Dark Arisen only for now; on Online: list, shop, sets, drop")


def cmd_items(args) -> int:
    from . import items as itemsmod
    from .mod import Mod

    game = _game(args)
    if game.is_ddo:
        return _ddo_items(args, game)
    idx = _index(game)
    try:
        root = Mod.load(Path(args.mod)).root if args.mod else None
        if args.action == "list":
            words = " ".join(args.rest).casefold()
            rows = [it for it in itemsmod.listing(game, idx, root)
                    if (it.free if args.free else not it.free)
                    and (not words or words in it.name.casefold() or words == str(it.id))]
            for it in rows[: args.limit]:
                print(f"  {it.id:>5}  {it.name[:40]:<40} {ui.mist(f'{it.weight:>6.2f} kg')}  "
                      f"{ui.mist(f'buy {it.buy:>7}  sell {it.sell:>7}')}")
            if not rows:
                ui.warn("no item matches" if not args.free else "no unused slot is left")
                return 1
            if len(rows) > args.limit:
                ui.info(f"first {args.limit} of {len(rows)} shown; use more words or --limit")
            if args.free:
                ui.info(f"{len(rows)} unused slots: 'riftstone items new' fills the first one (or --id)")
            return 0
        if args.action == "sets":
            if len(args.rest) != 1:
                raise RiftError("items sets takes one item: items sets \"Wolf Pelt\" [--table enemy|reward]")
            items = itemsmod.listing(game, idx, root)
            it = itemsmod.find(items, args.rest[0])
            rows = itemsmod.sets_with(game, idx, root, it.id, args.table)
            for r in rows[: args.limit]:
                slots = ", ".join(("nothing" if i == 0xFFFF else (items[i].name if i < len(items) else str(i)))
                                  + f" {w}%" for i, w in zip(r[4:12], r[12:20]) if w or i != 0xFFFF)
                print(f"  set {r[0]:>5}  {slots}")
            if not rows:
                ui.warn(f"no {args.table} set holds {it.name}")
                return 1
            ui.info(f"{len(rows)} {args.table} set(s) hold {it.name} ({it.id}); add an item to one with "
                    "items drop <item> --set <id> --percent <n> --mod <mod>")
            return 0
        if args.action == "stats":
            if len(args.rest) != 1:
                raise RiftError('items stats takes one item: items stats "Iron Sword" [--mod <mod>]')
            return _item_stats(game, idx, root, args.rest[0])
        if not root:
            raise RiftError(f"say which mod gets the change: --mod <mod folder>")
        if args.action == "set":
            from . import itemstats
            if len(args.rest) < 2 or any("=" not in w for w in args.rest[1:]):
                raise RiftError('items set takes an item and field=value pairs: items set "Iron Sword" mAttack=80 '
                                'mShrink=120 --mod <mod>  (riftstone learn itl lists the fields)')
            values = dict(w.split("=", 1) for w in args.rest[1:])
            iid, name, changed, out = itemstats.set_fields(game, idx, root, args.rest[0], values)
            for k, (old, new) in changed.items():
                ui.ok(f"{name} ({iid}): {k} {old} -> {new}")
            ui.info(out.relative_to(root).as_posix())
            return 0
        if args.action == "recipe":
            if len(args.rest) != 2 or not args.makes:
                raise RiftError('items recipe takes two ingredients and --makes: items recipe Crimplecap "Pine Branch" '
                                '--makes "Poison Arrow" --count 30 --mod <mod>')
            items = itemsmod.listing(game, idx, root)
            a, b, c = (itemsmod.find(items, k) for k in (args.rest[0], args.rest[1], args.makes))
            out = itemsmod.recipe(game, idx, root, a.id, b.id, c.id, args.count)
            ui.ok(f"{a.name} + {b.name} makes {c.name} x{args.count}")
            ui.info(out.relative_to(root).as_posix())
            return 0
        if args.action == "drop":
            if len(args.rest) != 1 or args.set is None or args.weight_pct is None:
                raise RiftError("items drop takes an item, --set and --percent: items drop \"Rift Tonic\" --set 7 "
                                "--percent 10 --mod <mod>")
            it = itemsmod.find(itemsmod.listing(game, idx, root), args.rest[0])
            out, total = itemsmod.drop(game, idx, root, it.id, args.set, args.weight_pct, args.table)
            ui.ok(f"{it.name} now drops from {args.table} set {args.set} ({args.weight_pct}%); the set adds up to {total}%")
            ui.info(out.relative_to(root).as_posix())
            return 0
        if args.action == "shop":
            if len(args.rest) != 1 or not args.shop:
                raise RiftError('items shop takes one item and a shop: items shop "Riftstone Draught" '
                                "--shop n007ShopList --mod <mod>")
            it = itemsmod.find(itemsmod.listing(game, idx, root), args.rest[0])
            out = itemsmod.shop(game, idx, root, args.shop, it.id, args.stock, args.restock)
            ui.ok(f"{it.name} ({it.id}) is on sale in {out.name[:-len('.shp.yaml')] if out.name.endswith('.yaml') else out.name}"
                  f", {args.stock} in stock")
            ui.info(out.relative_to(root).as_posix())
            return 0
        if len(args.rest) != 1:
            raise RiftError('items new takes the new item\'s name: items new "Riftblade" --like "Iron Sword" --mod <mod>')
        if not args.like:
            raise RiftError("say what the new item is based on: --like <id or name>, e.g. --like Greenwarish")
        made = itemsmod.new(game, idx, root, args.rest[0], args.like, args.description or "", args.id,
                            args.buy, args.sell, args.weight)
        ui.ok(f"Added item {made.id} \"{args.rest[0]}\", a copy of {made.like.name} ({made.like.id}) "
              "with its own name, description, price and weight")
        for f in made.files:
            ui.info(f.relative_to(root).as_posix())
        if args.shop:
            out = itemsmod.shop(game, idx, root, args.shop, made.id, args.stock, args.restock)
            ui.ok(f"On sale in {out.relative_to(root).as_posix()}, {args.stock} in stock")
        else:
            ui.info(f"Make it obtainable: riftstone items shop {made.id} --shop <shop> --mod <mod> "
                    "(riftstone find ShopList --type shp lists the shops), then install the mod.")
        from . import itl as itlmod
        rec = itemsmod.load_list(game, idx, root)[0].records[made.id]    # the template's record, copied
        table = itlmod.level_table(itlmod.BY_NAME["mKind"].get(rec))
        if table:
            ui.info(f"It enhances like {made.like.name}: row {itlmod.BY_NAME['mLevelUpType'].get(rec)} of {table} "
                    f"(riftstone items stats {made.id} --mod <mod> shows every level)")
        return 0
    finally:
        idx.close()


def _item_stats(game, idx, root, key: str) -> int:
    from . import itemstats

    s, name = itemstats.item_stats(game, idx, root, key)
    where = (f"row {s.row} of {s.table}" if s.row is not None else s.note)
    kind = itemstats.KIND_NAMES.get(s.kind)
    ui.ok(f"{name} ({s.item}): {kind + ' ' if kind else ''}(mKind {s.kind}); {where}")
    if kind and s.jobs:
        ui.info("equipped by " + (", ".join(s.jobs) if len(s.jobs) < len(itemstats.JOBS) else "every vocation"))
    shown = [n for n in s.base if any(lv[n] for lv in s.levels)]
    if not shown:
        ui.info("none of the stats an enhancement can change is set on this item")
        return 0
    cols = ["base"] + [f"Lv{i}" for i in range(1, itemstats.LEVELS + 1)] if s.row is not None else ["base"]
    print(f"  {'field':<26} {'':<24}" + "".join(f"{c:>8}" for c in cols))
    for n in shown:
        vals = [s.levels[i][n] for i in range(len(cols))]
        text = "".join(f"{(int(v) if float(v).is_integer() else round(v, 2))!s:>8}" for v in vals)
        print(f"  {n:<26} {ui.mist(itemstats.GLOSS.get(n, '')[:24]):<24}" + text)
    if s.row is not None:
        ui.info("levels 1-3 are the stars; the value at a level is the base changed by that level's entry, as the "
                "game computes it (DDDA.exe 0x0045B620); in game UNKNOWN for an edited table")
    return 0


def cmd_spawns(args) -> int:
    from . import lot, modfiles
    from .mod import Mod

    game = _game(args)
    idx = _index(game)
    try:
        rel = args.layout.replace("\\", "/")
        if rel.lower().endswith(".yaml"):
            rel = rel[:-5]
        if not rel.lower().endswith(".lot"):
            rel += ".lot"
        name, tid = fsmap.decode_path(rel)
        root = Mod.load(Path(args.mod)).root if args.mod else None
        data, out = modfiles.load(game, idx, root, name, tid)
        if game.is_ddo:
            return _ddo_spawns(args, data, out, root, name, tid)
        lt = lot.parse(data)
        recs = lot.records(lt)
        if args.action == "list":
            ln = lot.parse_name(name.decode("latin-1"))
            if ln is not None:
                ui.info(f"stage {ln.stage}, cell {ln.x:02d}m{ln.z:02d}n, "
                        + ("static models" if ln.type == "s" else f"group {ln.number} of {lot.TYPES[ln.type]}"))
            for i, r in enumerate(recs):
                p = r.vec()
                at = f"at [{p[0]:.1f}, {p[1]:.1f}, {p[2]:.1f}]" if p else "(no position)"
                print(f"  {i:>4}  id {r.id:<4} {r.name or '':<22} {at:<34} {r.cls}")
            ui.info(f"{len(recs)} records; copy or remove them by number (--mod)")
            return 0
        if root is None:
            raise RiftError("say which mod gets the change: --mod <mod folder>")
        if args.number is None:
            raise RiftError(f"say which record: spawns {args.action} <layout> <number> (spawns list shows them)")
        if args.action == "copy":
            at = None
            if args.at:
                try:
                    at = tuple(float(v) for v in args.at.split(","))
                except ValueError:
                    at = ()
                if len(at) != 3:
                    raise RiftError("--at is x,y,z, e.g. --at 58600,42716,-45360")
            # the id: clear of the group's other layouts and of the game's own ids, under 32 for enemies
            reserved, below = modfiles.group_ids(game, idx, root, name)
            new = lot.copy(lt, args.number, at, reserved, below)
            rid = new.records[-1].id
            what = f"Copied record {args.number} ({recs[args.number].name}) as record {lt.count}, id {rid}"
            if below is not None and rid >= below:
                ui.warn(f"every id under {below} is taken in this group: the game keeps one kill-record bit per id, "
                        f"so id {rid} shares id {rid % below}'s (killing one counts for both)")
        else:
            new = lot.remove(lt, args.number)
            what = f"Removed record {args.number} ({recs[args.number].name}); the ones after it moved up one"
        modfiles.save(out, lot.build(new), name, tid)
        ui.ok(what)
        ui.info(out.relative_to(root).as_posix())
        return 0
    finally:
        idx.close()


def _ddo_spawns(args, data, out, root, name, tid) -> int:
    from . import lot_ddo, modfiles

    lt = lot_ddo.parse(data)
    if args.action == "list":
        for i, r in enumerate(lt.records):
            pos = r.get("mPosition")
            at = f"at [{', '.join(f'{lot_ddo.f32(b):.1f}' for b in pos)}]" if pos else "(no position)"
            unit = r.get("mUnitID")
            print(f"  {i:>4}  id {r.id:<4} {lot_ddo.shown(r.get('mName')):<16} {at:<34} {r.cls}"
                  + (f"  unit 0x{unit:06X}" if unit else ""))
        ui.info(f"{len(lt.records)} records; copy or remove them by number (--mod)")
        return 0
    if root is None:
        raise RiftError("say which mod gets the change: --mod <mod folder>")
    if args.number is None:
        raise RiftError(f"say which record: spawns {args.action} <layout> <number> (spawns list shows them)")
    if args.action == "copy":
        at = None
        if args.at:
            try:
                at = tuple(float(v) for v in args.at.split(","))
            except ValueError:
                at = ()
            if len(at) != 3:
                raise RiftError("--at is x,y,z")
        new = lot_ddo.copy(lt, args.number, at)
        what = f"Copied record {args.number} as id {new.records[-1].id}"
    else:
        new = lot_ddo.remove(lt, args.number)
        what = f"Removed record {args.number}"
    modfiles.save(out, lot_ddo.build(new), name, tid)
    ui.ok(what)
    ui.info(out.relative_to(root).as_posix())
    return 0


def _world(game: Game, idx, rebuild: bool = False, lang: str = "eng", quiet: bool = False):
    from . import world

    return world.load(game, idx, rebuild, None if quiet else (lambda: ui.Progress(1, "mapping the world")), lang)


def _story(g: dict) -> str:
    b, e = g["appear"]
    if (b, e) == (0, 0):
        return "always"
    return f"scenario {b}..{e if e else 'on'}"


def _units(w, units) -> str:
    return ", ".join(f"{u} {w.enemies.get(u, {}).get('name', '')}".strip() for u in units) or "(none)"


def _ddo_world(args, game: Game, idx) -> int:
    """world for Dragon's Dogma Online: the local server's spawns, named by the client."""
    from collections import Counter, defaultdict

    from . import ddo

    w = ddo.load(game, idx)
    for note in w.warnings:           # names the client could not give; kept off stdout when it is JSON
        if args.json:
            print(f"warning: {note}", file=sys.stderr)
        else:
            ui.warn(note)
    C = w.col
    rows = w.rows
    eid = lambda r: ddo.enemy_id(r[C("EnemyId")])  # noqa: E731

    def emit(doc) -> int:           # --json: the rows the action lists (it printed every row, whatever was asked)
        print(json.dumps(doc, indent=1, ensure_ascii=False))
        return 0

    def as_json(some) -> list[dict]:
        return [dict(zip(w.schema, r), EnemyName=w.enemy_name(eid(r)), StageName=w.stage_name(r[C("StageId")]))
                for r in some]

    if args.action in ("overview", "build"):
        if args.json:
            return emit(as_json(rows))
        per_enemy = Counter(eid(r) for r in rows)
        ui.ok(f"{len({r[C('StageId')] for r in rows})} stages, {len(rows):,} spawn rows, {len(per_enemy)} enemies, "
              f"{len(w.doc.get('dropsTables', []))} drop tables  (server data: {w.assets})")
        for e, n in per_enemy.most_common(min(args.limit, 12)):
            print(f"  {ui.gold(w.enemy_name(e)):<34} {n:>5,} placements  {ui.mist(f'0x{e:06X}')}")
        ui.info("riftstone world stages|stage <id or name>|enemies|enemy <name> --game ddo; "
                "riftstone encounter <stage> <enemy> --count N --mod <mod> --game ddo")
        return 0
    if args.action == "stages":
        per = Counter(r[C("StageId")] for r in rows)
        if args.json:
            return emit([{"StageId": sid, "StageNo": w.stages.get(sid, (None, ""))[0], "StageName": w.stage_name(sid),
                          "Spawns": per[sid]} for sid in sorted(per)[:args.limit]])
        for sid in sorted(per)[:args.limit]:
            no = w.stages.get(sid, (None, ""))[0]
            print(f"  {sid:>5}  {ui.mist(f'st{no:04d}' if no is not None else '      ')}  {w.stage_name(sid):<40} {per[sid]:>5} spawns")
        ui.info(f"{len(per)} stages with spawns" + (f"; showing {args.limit}" if len(per) > args.limit else ""))
        return 0
    if args.action == "stage":
        if not args.rest:
            raise RiftError("which stage? e.g. riftstone world stage 'Hidell Plains' --game ddo")
        sid = ddo.find_stage(w, " ".join(args.rest))
        groups = defaultdict(list)
        for r in rows:
            if r[C("StageId")] == sid:
                groups[(r[C("LayerNo")], r[C("GroupId")])].append(r)
        if args.json:
            return emit(as_json([r for _, rs in sorted(groups.items())[:args.limit] for r in rs]))
        ui.ok(f"{w.stage_name(sid)} (StageId {sid}): {sum(map(len, groups.values()))} spawns in {len(groups)} groups")
        for (layer, g), rs in sorted(groups.items())[:args.limit]:
            kinds = Counter(w.enemy_name(eid(r)) for r in rs)
            lv = sorted({r[C("Lv")] for r in rs})
            _, pts = ddo.spawn_points(game, idx, w, sid, g)
            first = next((p for p in pts if p), None)
            at = f"  at {first[0]:.0f}, {first[1]:.0f}, {first[2]:.0f} ({len(pts)} points)" if first else ""
            print(f"  layer {layer} group {g:<4} Lv {lv[0]}{'-' + str(lv[-1]) if len(lv) > 1 else '':<5} "
                  + ", ".join(f"{n} x{c}" if c > 1 else n for n, c in kinds.most_common()) + ui.mist(at))
        return 0
    if args.action == "enemies":
        per = Counter(eid(r) for r in rows)
        where = defaultdict(set)
        for r in rows:
            where[eid(r)].add(r[C("StageId")])
        if args.json:
            return emit([{"EnemyId": f"0x{e:06X}", "EnemyName": w.enemy_name(e), "Spawns": per[e],
                          "Stages": sorted(where[e])}
                         for e in sorted(per, key=lambda e: w.enemy_name(e).lower())[:args.limit]])
        for e in sorted(per, key=lambda e: w.enemy_name(e).lower())[:args.limit]:
            print(f"  {w.enemy_name(e):<34} {ui.mist(f'0x{e:06X}')}  {per[e]:>5} placements in {len(where[e])} stage(s)")
        ui.info(f"{len(per)} enemies spawn" + (f"; showing {args.limit}" if len(per) > args.limit else ""))
        return 0
    if args.action == "enemy":
        if not args.rest:
            raise RiftError("which enemy? e.g. riftstone world enemy goblin --game ddo")
        ids = set(ddo.find_enemies(w, " ".join(args.rest)))
        hits = [r for r in rows if eid(r) in ids]
        if not hits:
            raise RiftError(f"the server spawns no {' '.join(args.rest)!r}")
        if args.json:
            return emit(as_json(hits[:args.limit]))
        per = Counter((r[C("StageId")], r[C("LayerNo")], r[C("GroupId")], w.enemy_name(eid(r)), r[C("Lv")]) for r in hits)
        ui.ok(f"{len(hits)} placements of {', '.join(sorted({w.enemy_name(e) for e in ids}))}")
        for (sid, layer, g, name, lv), n in sorted(per.items())[:args.limit]:
            print(f"  {w.stage_name(sid):<34} {ui.mist(f'StageId {sid} layer {layer} group {g}')}  {name} Lv {lv}"
                  + (f" x{n}" if n > 1 else ""))
        return 0
    raise RiftError(f"'world {args.action}' is for Dark Arisen; on Online use overview, stages, stage, enemies, enemy")


def _ddo_encounter(args, game: Game, idx) -> int:
    """encounter for Dragon's Dogma Online: N more of an enemy in a stage's spawn group (server data)."""
    from . import ddo
    from .mod import SERVER_DIR, Mod

    if not args.mod and not args.dry_run:
        raise RiftError('say which mod gets the encounter: --mod "My Mod" (riftstone new "My Mod" --game ddo)')
    # checked as Dark Arisen's encounter.plan checks them: NaN or 1e39 made spawn points no float holds
    if not (math.isfinite(args.spread) and 0 < args.spread <= 5000):
        raise RiftError("--spread is the distance between spawn points, more than 0 and at most 5000")
    if args.at_once is not None and not 1 <= args.at_once <= 500:
        raise RiftError("on Online --at-once is how many spawn points the group's layout gets, 1 to 500")
    if args.level is not None and not 1 <= args.level <= 0xFFFF:
        raise RiftError("--level is 1 to 65535 (the server reads a spawn's level as a 16-bit number)")
    m = Mod.load(Path(args.mod)) if args.mod else None
    if m is not None and m.game != "ddo":
        raise RiftError(f"{m.name} is a Dark Arisen mod; make one with: riftstone new <folder> --game ddo")
    mine = m.root / SERVER_DIR / ddo.SPAWN_FILE if m else None
    w = ddo.load(game, idx, spawn_file=mine if mine is not None and mine.is_file() else None)
    for note in w.warnings:
        ui.warn(note)
    sid = ddo.find_stage(w, args.stage)
    enemy = ddo.pick_enemy(w, " ".join(args.enemy))      # several enemies of that name: listed, not guessed
    group = layer = None
    if args.at:
        spec = args.at.lower().removeprefix("group:").split(":")
        try:
            nums = [int(x) for x in spec]
        except ValueError:
            raise RiftError("on Online --at is group:N or group:LAYER:N (riftstone world stage <stage> --game ddo)") from None
        if len(nums) > 2:
            raise RiftError("on Online --at is group:N or group:LAYER:N (riftstone world stage <stage> --game ddo)")
        layer, group = (nums[0], nums[1]) if len(nums) == 2 else (None, nums[0])
    if group is None:   # the group encounter() will pick, to read its layout's points
        C = w.col
        rows = [r for r in w.rows if r[C("StageId")] == sid]
        same = [r for r in rows if ddo.enemy_id(r[C("EnemyId")]) == enemy]
        pick = (same or rows)[0] if rows else None
        if pick is not None:
            layer, group = pick[C("LayerNo")], pick[C("GroupId")]
    lay, pts = ddo.spawn_points(game, idx, w, sid, group) if group is not None else (None, [])
    grown = 0
    if lay and m is not None:
        # the group's layout as the mod installs it: an earlier encounter may have added points to its copy
        from . import lot_ddo, modfiles

        lname, ltid = lay.encode("latin-1"), typemap.type_for_extension("lot")
        data, out = modfiles.load(game, idx, m.root, lname, ltid)
        lt = lot_ddo.parse(data)
        pts = ddo.layout_points(lt)
    if args.at_once and group is not None and not lay:              # was: passed over without a word
        raise RiftError(f"--at-once adds spawn points to the group's client layout, and group {group} of StageId "
                        f"{sid} has none (riftstone world stage {sid} --game ddo)")
    if args.at_once and lay and args.at_once > len(pts):
        # more points at once: add them to the group's client layout (the mod's copy), in rings
        # around the group's points, --spread apart
        if m is None:
            raise RiftError("adding spawn points writes the group's layout into a mod: give --mod")
        have = [p for p in pts if p]
        if not have:                    # the mod's copy can have lost them all (riftstone spawns remove)
            raise RiftError(f"{lay} has no placed record to add spawn points around")
        cx = sum(p[0] for p in have) / len(have)
        cy = sum(p[1] for p in have) / len(have)
        cz = sum(p[2] for p in have) / len(have)
        grown = args.at_once - len(lt.records)
        spots = []
        for i in range(grown):
            ring, k = divmod(i, 8)
            r = args.spread * (ring + 1)
            ang = 2 * math.pi * k / 8 + ring * 0.39
            spots.append((cx + r * math.cos(ang), cy, cz + r * math.sin(ang)))
        lt = lot_ddo.copies(lt, 0, spots)            # one copy of the layout (a copy per point was quadratic)
        grown_data = lot_ddo.build(lt)
        pts = pts + [None] * grown
    # the rows first: a refusal here (--count, a group the stage lacks) used to come after the grown layout
    # was already saved into the mod
    new, notes = ddo.encounter(w, sid, enemy, args.count, group, layer, args.level, len(pts) or None)
    if lay:
        notes.append(f"spawn points from {lay}" + (f"; {grown} {'would be ' if args.dry_run else ''}added to the "
                                                     f"mod's copy in rings {args.spread:g} apart around the group "
                                                     f"(their height is the group's average; in game UNKNOWN)"
                                                     if grown else ""))
    for n in notes:
        ui.info(n)
    if args.dry_run:
        ui.ok(f"would add {len(new)} spawn row(s); nothing written")
        return 0
    if grown:
        modfiles.save(out, grown_data, lname, ltid)
    arcfolder.write_file(mine, ddo.dumps(w.doc))
    ui.ok(f"-> {mine}")
    ui.info(f"riftstone install \"{m.root}\" writes it into the server's assets ({w.assets}); restart the server "
            "(Stop Server.cmd, then Play Solo.cmd) to load it. In game: UNKNOWN until played")
    return 0


def cmd_world(args) -> int:
    from . import lot, world

    def emit(doc) -> int:   # --json: the rows the action lists (it printed the whole map, cut at 2,000,000
        print(json.dumps(doc, ensure_ascii=False))     # characters: no JSON at all on the real 9 MB map)
        return 0

    game = _game(args)
    idx = _index(game, args.json)
    try:
        if game.is_ddo and args.action != "types":
            return _ddo_world(args, game, idx)
        if args.action == "types":
            rows = world.atlas(idx)
            if args.json:
                return emit(rows[:args.limit])
            for r in rows[:args.limit]:
                print(f"  {r['ext']:<12} {r['class']:<26} {r['count']:>7,} ({r['names']:,} names)  {r['riftstone']}")
            ui.info(f"{len(rows)} resource types in the game" + (f"; showing {args.limit}" if len(rows) > args.limit else ""))
            return 0
        w = _world(game, idx, args.rebuild or args.action == "build", args.lang, args.json)
        rest = args.rest
        if args.json and args.action in ("overview", "build", "stages"):
            return emit({"stages": w.data["stages"]})
        if args.action in ("overview", "build"):
            ui.ok(f"{len(w.stages)} stages, {len(w.groups):,} groups, {len(w.layouts):,} layouts, "
                  f"{len(w.placements):,} placements, {len(w.enemies)} enemies")
            for line in ("riftstone world stages            every stage, its rooms and what it places",
                         "riftstone world stage 424         one stage: its enemy groups, where they stand, free numbers",
                         "riftstone world enemy goblin      where an enemy spawns",
                         "riftstone world enemies           every enemy with its name and how often it is placed",
                         "riftstone world group 424 e 5     one group: settings, layout files and placements",
                         "riftstone world deps em0200       which archives an archive pulls in, and who pulls it in",
                         "riftstone world types             every resource type and how Riftstone handles it",
                         "riftstone encounter 424 goblin --count 100 --at group:5 --mod \"My Mod\""):
                ui.info(line)
            return 0
        if args.action == "stages":
            for s, st in sorted(w.stages.items()):
                gl = st["group_lists"]
                counts = " ".join(f"{t} {gl[t]['groups']}" for t in world.GROUP_TYPES if t in gl)
                rooms = ", ".join(st["rooms"][:4]) + (f" (+{len(st['rooms']) - 4})" if len(st["rooms"]) > 4 else "")
                print(f"  st{s:03d}  groups {counts or '-':<22} placements {st['placements']:>6,}  {rooms}")
            return 0
        if args.action == "stage":
            if not rest:
                raise RiftError("say which stage: riftstone world stage 424")
            from .encounter import parse_stage
            s = parse_stage(rest[0])
            st = w.stage(s)
            groups = w.groups_of(s, "e")
            free = w.free_groups(s, "e")
            if args.json:
                return emit({"stage": s, **st, "free_groups": free, "groups": [
                    {**g, "placements": [p[5] for name in w.layouts_of(s, "e", g["number"])
                                         for p in w.placements_in(name) if p[5]]}
                    for g in sorted(groups, key=lambda g: (g["dlc"], g["number"]))]})
            ui.ok(f"Stage {s}" + (f" -- {', '.join(st['rooms'])}" if st["rooms"] else ""))
            ui.info(f"{len(groups)} enemy groups; {len(free)} free group numbers of {world.GROUP_SLOTS}"
                    + (f" (first: {free[0]})" if free else ""))
            for g in sorted(groups, key=lambda g: (g["dlc"], g["number"])):
                pts = [p[5] for name in w.layouts_of(s, "e", g["number"]) for p in w.placements_in(name) if p[5]]
                mid = [sum(c) / len(pts) for c in zip(*pts)] if pts else None
                cells = " ".join(f"{x:02d}m{z:02d}n" for x, z in g["cells"][:3]) + (" ..." if len(g["cells"]) > 3 else "")
                where = f"at [{mid[0]:.0f}, {mid[1]:.0f}, {mid[2]:.0f}]" if mid else "no placements"
                cap = "all" if g["count_max"] < 0 else f"{g['count_max']} total"
                print(f"  group {g['number']:>3}{' (DLC)' if g['dlc'] else ''}  {_units(w, g['units'])}")
                print(f"             {len(pts):>2} placed {where}; spawns {cap}, respawn type {g['respawn']}, "
                      f"{_story(g)}, cells {cells}")
            other = {t: len(w.groups_of(s, t)) for t in ("n", "p", "t")}
            ui.info(f"also {other['n']} NPC/human groups, {other['p']} object groups, {other['t']} sensor-target groups "
                    f"(riftstone world group {s} p <number>)")
            return 0
        if args.action == "enemies":
            listed = [(em, e) for em, e in sorted(w.enemies.items(), key=lambda kv: -kv[1]["placements"])
                      if e["placements"] or args.all]
            if args.json:
                return emit([{"id": em, **e} for em, e in listed])
            for em, e in listed:
                print(f"  {em:<9} {e['name'] or '?':<26} {e['placements']:>5} placements in {len(e['stages']):>3} "
                      f"stage(s), {e['groups']:>3} groups  {', '.join(e['classes'])}")
            return 0
        if args.action == "enemy":
            if not rest:
                raise RiftError("say which enemy: riftstone world enemy goblin (or em0100)")
            em = w.find_enemy(" ".join(rest))
            e = w.enemies[em]
            if args.json:
                return emit({"id": em, **e, "spawns": w.spawns_of(em)[:args.limit]})
            ui.ok(f"{em} {e['name']}: {e['placements']} placements in {len(e['stages'])} stage(s)"
                  + (f"; archive {e['archive']}" if e["archive"] else ""))
            for sp in w.spawns_of(em)[:args.limit]:
                g = sp["group"]
                extra = f"group {sp['number']}: {_units(w, g['units'])}; {_story(g)}" if g else "no group"
                print(f"  st{sp['stage']:03d} cell {sp['x']:02d}m{sp['z']:02d}n  {sp['count']:>2}x   {extra}")
            return 0
        if args.action == "deps":
            if not rest:
                raise RiftError("say which archive: riftstone world deps rom/enemy/em0200 (or shellhellhound)")
            hit, pulls, pulled_by = w.deps(" ".join(rest))
            if args.json:
                return emit({"archives": hit, "pulls": pulls[:args.limit], "pulled_by": pulled_by[:args.limit]})
            ui.ok(", ".join(hit[:6]) + (f" (+{len(hit) - 6})" if len(hit) > 6 else ""))
            ui.info(f"pulls in {len(pulls)} archive(s)" + (":" if pulls else ""))
            for a in pulls[:args.limit]:
                print(f"    {a}")
            ui.info(f"pulled in by {len(pulled_by)} archive(s)" + (":" if pulled_by else ""))
            for a in pulled_by[:args.limit]:
                print(f"    {a}")
            return 0
        if args.action == "group":
            if len(rest) != 3 or rest[1] not in world.GROUP_TYPES:
                raise RiftError("riftstone world group <stage> <e|n|p|t> <number>, e.g. world group 424 e 5")
            from .encounter import parse_stage
            s, t = parse_stage(rest[0]), rest[1]
            try:
                n = int(rest[2])
            except ValueError:
                raise RiftError("the group number is a whole number") from None
            g = w.group(s, t, n)
            if g is None:
                raise RiftError(f"stage {s} has no {lot.TYPES[t]} group {n}")
            if args.json:
                return emit({**g, "layouts": [{"name": name, **w.layouts[name],
                                               "placements": w.placements_in(name)[:args.limit]}
                                              for name in w.layouts_of(s, t, n)]})
            ui.ok(f"Stage {s}, {lot.TYPES[t]} group {n} ({g['list']})")
            ui.info(f"units {_units(w, g['units'])}; spawns {'all' if g['count_max'] < 0 else g['count_max']}, "
                    f"respawn type {g['respawn']}, {_story(g)}, hours {g['hours'][0]}..{g['hours'][1]}"
                    + (f", appears after enemy group {g['link']} is cleared" if g["link"] is not None else ""))
            for name in w.layouts_of(s, t, n):
                lay = w.layouts[name]
                print(f"  {name}  ({lay['records']} records)  in {', '.join(lay['archives'])}")
                for p in w.placements_in(name)[:args.limit]:
                    at = f"at [{p[5][0]:.0f}, {p[5][1]:.0f}, {p[5][2]:.0f}]" if p[5] else ""
                    extra = p[6] if len(p) > 6 else ""
                    print(f"     id {p[2]:<4} {p[4]:<20} {at:<30} {lot.KINDS[p[3]][0]} {extra or ''}")
            return 0
        raise RiftError(f"unknown world action {args.action!r}")
    finally:
        idx.close()


def cmd_ddo(args) -> int:
    """Dragon's Dogma Online on the local server: 'solo' writes the Solo Balance mod; 'access' opens
    party-gated missions for a lone player."""
    args.game = args.game or "ddo"
    game = _game(args)
    if not game.is_ddo:
        raise RiftError("riftstone ddo works on Dragon's Dogma Online (--game ddo)")
    if args.action == "access":
        return _ddo_access(args, game)
    if args.action == "dye":
        return _ddo_dye(args, game)
    if not args.mod:
        raise RiftError("riftstone ddo solo needs --mod (the mod to write)")
    return _ddo_solo(args, game)


def _ddo_dye(args, game: Game) -> int:
    """An Online weapon or armour's colours (its own, every colour number, the dyes) and, with --out, its colour
    maps with a colour baked in, as Dark Arisen textures (ddodye.py, docs/ddo-dye.md)."""
    from . import ddodye

    if not args.what:
        raise RiftError('which item? e.g. riftstone ddo dye "Bronze Plate" (an item name, an item id or a model path)')
    spec = ddodye.parse_spec(args.colour) if args.out else None
    idx = _index(game)
    try:
        target = ddodye.resolve(game, idx, args.what, args.sex)
        look = ddodye.load_look(game, idx, target)
        for line in ddodye.describe(look):
            ui.info(line)
        if spec is None:
            ui.info("bake a colour: --colour <default | red | ... | a colour number | #rrggbb> --out <folder>; into "
                    "a port: riftstone port <model> --dye <colour> --mod <Dark Arisen mod>")
            return 0
        ui.step(f"Baking {spec.label} into {len(look.masked)} material(s)' colour maps")
        baked, notes = ddodye.bake_look(game, idx, look, spec)
    finally:
        idx.close()
    for n in notes:
        ui.info(n)
    for p in ddodye.write_folder(Path(args.out), baked, look, args.as_ or "tex"):
        ui.ok(f"-> {p}")
    ui.info("the maps are Dark Arisen textures (revision 0x99) of the same format, size and mips; how they look in "
            "game is UNKNOWN until you play it")
    return 0


def _ddo_access(args, game: Game) -> int:
    from . import ddo, ddo_access

    assets = ddo.need_assets(game)
    ui.step(f"Reading the local server's mission quests ({assets})")
    # the quests as the server shipped them: while Solo Access is installed the live files are its own copies
    p = ddo_access.plan(assets, fill_pawns=args.fill_pawns, game=game)
    for line in ddo_access.report_lines(p.report):
        ui.info(line)
    for n in p.notes:
        ui.info(n)
    if not args.mod:
        ui.info('write the fix as a mod with --mod "Solo Access" (nothing is written without it)')
        return 0
    root = Path(args.mod)
    # copies an earlier run wrote that this one does not: kept, install would put them back over the server's own
    stale = ddo_access.stale(root, p)
    if not p.files and not stale:
        ui.ok("Nothing to write: every mission already starts for one player")
        return 0
    if args.dry_run:
        for rel in stale:
            ui.info(f"would remove {rel}: no longer needed")
        ui.ok(f"Dry run: {len(p.files)} quest file(s) would go into {root}; nothing written")
        return 0
    written = ddo_access.write(root, p)
    root = root.resolve()
    for rel in written:
        ui.ok(f"-> {rel}  ({ui.human(len(p.files[rel]))})")
    for rel in stale:
        ui.ok(f"removed {rel}: no longer needed")
    ui.info(f'next: riftstone install "{root}" --game ddo, then restart the local server '
            "(<path> server stop, then start it or Play Solo.cmd)")
    ui.info(f'remove it: riftstone uninstall "{root}" --game ddo (restart the server after)')
    ui.info("the files are checked; how a solo party then plays the content is UNKNOWN until you play it")
    return 0


def _ddo_solo(args, game: Game) -> int:
    from . import ddo_solo

    tiers = ddo_solo.tiers_from_options(args.tiers, args.offsets,
                                        **{f"{n}_{w}": getattr(args, f"{n}_{w}") for n in ddo_solo.TIER_NAMES
                                           for w in ("hp", "attack")})
    if args.no_settings and args.set:
        raise RiftError("--set changes a setting; leave out --no-settings")
    idx = _index(game)
    try:
        ui.step("Reading the client's named parameters and the local server's files")
        src = ddo_solo.sources(game, idx)
    finally:
        idx.close()
    settings = () if args.no_settings else ddo_solo.with_overrides(src, ddo_solo.SETTINGS, args.set or [])
    p = ddo_solo.plan(src, tiers, not args.keep_part_hp, settings)
    for n in p.notes:
        ui.info(n)
    for t in (t for t in tiers if t.active):
        att = f", attack x{t.attack:g}" if t.attack != 1 else ""
        ui.info(f"{t.name}: HP x{t.hp:g}{' (body parts too)' if not args.keep_part_hp else ''}{att}; twin id = id + {t.offset}")
    for name, s in p.record["settings"].items():
        ui.info(f"{name}: " + ", ".join(f"{k} = {str(v).lower() if isinstance(v, bool) else v}"
                                        for k, v in s["values"].items() if not isinstance(v, list))
                + "".join(f", {k} ({len(v)} rows)" for k, v in s["values"].items() if isinstance(v, list)))
    if args.dry_run:
        ui.ok(f"Dry run: {len(p.files)} file(s) would go into {Path(args.mod)}; nothing written")
        return 0
    written = ddo_solo.write(Path(args.mod), p)
    root = Path(args.mod).resolve()
    for rel in written:
        ui.ok(f"-> {rel}  ({ui.human(len(p.files[rel]))})")
    ui.info(f'next: riftstone install "{root}" --game ddo, then restart the local server '
            "(<path> server stop, then start it or Play Solo.cmd)")
    ui.info(f'remove it: riftstone uninstall "{root}" --game ddo (restart the server after)')
    ui.info("the files are checked; how it plays is UNKNOWN until you play it")
    return 0


_DARK_ARISEN_ONLY = ("group", "story", "like", "skin", "hours")    # a new enemy group and its settings
_ONLINE_ONLY = ("level",)                                          # the level of the server's new spawn rows


def _encounter_options(args, ddo: bool) -> None:
    """Refuse the options this game's encounter would not use, rather than drop them silently."""
    unused = [f"--{k}" for k in (_DARK_ARISEN_ONLY if ddo else _ONLINE_ONLY) if getattr(args, k, None) is not None]
    if unused:
        raise RiftError(f"{', '.join(unused)}: only for " + (
            "Dark Arisen, whose encounter is a new enemy group; on Online an encounter adds spawn rows to a "
            "group (--at group:N)" if ddo else "Online (--game ddo), whose server gives each spawn row a level"))


def cmd_encounter(args) -> int:
    from . import encounter
    from .mod import Mod

    game = _game(args)
    _encounter_options(args, game.is_ddo)
    idx = _index(game)
    try:
        if game.is_ddo:
            return _ddo_encounter(args, game, idx)
        if not args.at:
            raise RiftError("say where: --at x,y,z or --at group:N")
        if not args.mod:
            raise RiftError('say which mod gets the encounter: --mod "My Mod" (riftstone new "My Mod" makes one)')
        root = Mod.load(Path(args.mod)).root
        w = _world(game, idx)
        hours = encounter.parse_hours(args.hours) if args.hours else None
        enc = encounter.plan(game, idx, w, root, args.stage, " ".join(args.enemy), args.count, args.at,
                             args.at_once, args.spread, args.group, args.story, args.like, args.skin, hours,
                             ground=False if args.no_ground else None, always=args.always)
        _report_encounter(enc)
        if args.dry_run:
            ui.info("dry run: nothing written")
            return 0
        for f in encounter.write(enc, root):
            ui.info(f.relative_to(root).as_posix())
        ui.ok(f"Written into {root.name}. riftstone install \"{root.name}\" puts it into the game.")
        return 0
    finally:
        idx.close()


def _report_encounter(enc) -> None:
    ui.ok(f"Stage {enc.stage}: {enc.total} x {enc.enemy} {enc.enemy_name} as new enemy group {enc.group}, "
          f"{enc.points} spawn point(s) around [{enc.at[0]:.0f}, {enc.at[1]:.0f}, {enc.at[2]:.0f}]")
    ui.info(f"a copy of group {enc.template_group} (its enemies stand {enc.template_distance:.0f} units away: "
            f"its areas, story window and load flags); placements copy {enc.source_record[0]} #{enc.source_record[1]}")
    ui.info(f"layout {enc.layout_name} (cell {enc.cell[0]:02d}m{enc.cell[1]:02d}n), added to "
            f"{len(enc.layout_archives)} archive(s): {', '.join(enc.layout_archives)}")
    for n in enc.notes:
        ui.warn(n)


def cmd_encounters(args) -> int:
    """Every encounter an encounter plan lists (NYR-Lang's riftstone target writes them), in order."""
    from . import encounter_plan
    from .mod import Mod

    entries = encounter_plan.load(Path(args.plan))
    if not args.mod:
        raise RiftError('say which mod gets the encounters: --mod "My Mod" (riftstone new "My Mod" makes one)')
    game = _game(args)
    if game.is_ddo:
        raise RiftError("encounter plans are for Dragon's Dogma: Dark Arisen (Online: riftstone encounter --game ddo)")
    root = Mod.load(Path(args.mod)).root
    idx = _index(game)
    try:
        w = _world(game, idx)
        ui.step(f"{len(entries)} encounter(s) from {Path(args.plan).name}")
        done = encounter_plan.apply(game, idx, w, root, entries, dry_run=args.dry_run)
        for entry, enc, files in done:
            if entry.source:
                ui.info(f"-- {entry.source} of {Path(args.plan).name}")
            _report_encounter(enc)
            for f in files:
                ui.info(f.relative_to(root).as_posix())
        if args.dry_run:
            ui.info("dry run: nothing written (the encounters above stack as a real run writes them: the same group "
                    "numbers, the same refusals)")
            return 0
        ui.ok(f"{len(done)} encounter(s) written into {root.name}. riftstone install \"{root.name}\" puts them into "
              "the game.")
        return 0
    finally:
        idx.close()


def _parse_point(text: str) -> tuple:
    import math

    try:
        xyz = tuple(float(v) for v in str(text).split(","))
    except ValueError:
        xyz = ()
    if len(xyz) != 3 or not all(math.isfinite(v) and abs(v) < 1e6 for v in xyz):
        raise RiftError("a point is x,y,z in centimetres, e.g. 1200,-1340,-3700")
    return xyz


def cmd_nav(args) -> int:
    """A navigation mesh: a .nav file (read, rebuilt byte for byte) or the one a stage loads, its doors, and
    where a point stands on it."""
    import math

    from . import encounter, nav

    target = Path(args.target)
    if target.suffix.lower() == ".nav" or target.is_file():
        if not target.is_file():
            raise RiftError(f"{target} is not a file")
        data = target.read_bytes()
        n = nav.parse(data)
        ui.ok(nav.info(n))
        if nav.build(n) != data:
            ui.fail("it does not rebuild byte for byte")
            return 1
        ui.info("rebuilds byte for byte")
        return 0
    game = _game(args)
    if game.is_ddo:
        raise RiftError("riftstone nav <stage> is for Dark Arisen's stages; an Online mesh: riftstone nav <file.nav>")
    idx = _index(game)
    try:
        s = encounter.parse_stage(args.target)
        mesh = nav.stage_mesh(game, idx, s)
        if mesh is None:
            raise RiftError(f"stage {s} has no navigation mesh (the open field, 100, and stages 501 and 703 have none)")
        ns = nav.nav_stage(s)
        ui.ok(f"stage {s}: {nav.resource_name(s)}" + (f" (stage {s} loads stage {ns}'s)" if ns != s else ""))
        ui.info(nav.info(mesh.nav))
        doors = []
        for p in nav.start_positions(game, idx, s):
            spot = mesh.locate(p) or mesh.nearest(p, 600.0)
            doors.append((p, spot))
            where = "not on the mesh" if spot is None else (
                f"on the mesh ({spot.gap:+.0f} cm), region of {mesh.component_sizes()[mesh.component(spot.triangle)]} "
                "triangles")
            ui.info(f"door [{p[0]:.0f}, {p[1]:.0f}, {p[2]:.0f}]: {where}")
        if args.at:
            p = _parse_point(args.at)
            spot = mesh.locate(p) or mesh.nearest(p, encounter.GROUND_SNAP, above=encounter.GROUND_RISE,
                                                  below=encounter.GROUND_RISE)
            if spot is None:
                ui.warn(f"[{p[0]:.0f}, {p[1]:.0f}, {p[2]:.0f}] has no walkable ground within "
                        f"{encounter.GROUND_SNAP / 100:.0f} m")
                return 1
            reach = mesh.distances([sp.triangle for _, sp in doors if sp is not None])
            depth = reach.get(spot.triangle)
            ui.ok(f"walkable ground at [{spot.point[0]:.0f}, {spot.point[1]:.0f}, {spot.point[2]:.0f}]"
                  + (f", {math.dist(p, spot.point) / 100:.1f} m from the point" if math.dist(p, spot.point) > 50 else "")
                  + f"; {mesh.clearance(spot.point) / 100:.1f} m of room; "
                  + (f"{depth:.0f} m on foot from the nearest door" if depth is not None else
                     "not reached on foot from any door"))
        return 0
    finally:
        idx.close()


def cmd_dungeon(args) -> int:
    """A whole dungeon for a stage: a mission, its places on the stage's walkable ground, its enemies; checked,
    then written into a mod as encounters (or saved as a plan)."""
    from . import bestiary, dungeon, encounter, encounter_plan, lot, mission
    from .mod import Mod

    game = _game(args)
    if game.is_ddo:
        raise RiftError("the dungeon director works on Dark Arisen's stages (Online's spawns are the server's)")
    grammar = None
    if args.mission:
        try:
            grammar = mission.parse(Path(args.mission).read_text(encoding="utf-8"))
        except OSError as e:
            raise RiftError(f"cannot read {args.mission}: {e.strerror or e}") from None
        except UnicodeDecodeError:
            raise RiftError(f"{args.mission} is not UTF-8 text") from None
    hours = encounter.parse_hours(args.hours) if args.hours else None
    if args.plan and Path(args.plan).exists() and not args.force:
        raise RiftError(f"{args.plan} exists; pass --force to replace it")
    # the mod first: a --mod that is not one is refused before anything is planned or a --plan saved, and its own
    # navigation mesh (when it has one) is the ground the encounters and the check stand on
    root = Mod.load(Path(args.mod)).root if args.mod else None
    idx = _index(game)
    try:
        w = _world(game, idx)
        b = bestiary.load(game, idx, w, rebuild=args.rebuild)
        only = {w.find_enemy(e) for e in args.enemies.split(",") if e.strip()} if args.enemies else None
        exclude = {w.find_enemy(e) for e in args.exclude.split(",") if e.strip()} if args.exclude else set()
        s = encounter.parse_stage(args.stage)
        d = dungeon.direct(game, idx, w, s, seed=args.seed, grammar=grammar, which=args.pool, only=only,
                           exclude=exclude, spacing=args.spacing, max_points=args.at_once, b=b,
                           keep_lots=args.keep_lot_flags, mod_root=root)
        text = dungeon.plan_text(d, args.story, hours)
        if args.json:
            print(text)
        else:
            lines = dungeon.describe(d)
            ui.ok(lines[0])
            for line in lines[1:]:
                ui.info(line)
            for n in d.notes:
                ui.warn(n)
        if args.plan:
            Path(args.plan).write_text(text + "\n", encoding="utf-8")
            ui.ok(f"plan saved: {args.plan} (riftstone encounters \"{args.plan}\" --mod <mod> writes it)")
        if root is None:
            if not args.plan:
                ui.info("nothing written: --mod <mod> writes these encounters into a mod, --plan <file> saves the plan")
            return 0
        entries = encounter_plan.parse(text)
        # checked before anything is written: every encounter planned in a scratch copy of the mod first (the group
        # numbers and refusals a real run gets), every spawn point found on the mesh in the doors' region
        done = encounter_plan.apply(game, idx, w, root, entries, dry_run=True)
        proof = dungeon.check(d.space, [enc for _, enc, _ in done])
        for _, enc, files in done:
            for n in enc.notes:
                if "spawn point" in n and ("fit" in n or "from walkable" in n) or "open ground" in n:
                    ui.warn(f"{enc.enemy}: {n}")
        ui.ok(f"checked: {proof.on_mesh} of {proof.points} spawn points on stage {d.space.nav_stage}'s navigation mesh, "
              f"{proof.in_region} reached on foot from the doors (the farthest {proof.farthest:.0f} m in)")
        for problem in proof.problems[:8]:
            ui.warn(problem)
        if args.dry_run:
            ui.info("dry run: nothing written")
            return 0 if not proof.problems else 1
        if proof.problems:
            raise RiftError(f"{len(proof.problems)} spawn point(s) are not on ground reached from the doors; nothing "
                            "written")
        done = encounter_plan.apply(game, idx, w, root, entries)
        layouts = []                          # the files as written, read back
        for _, enc, files in done:
            for f in files:
                if f.name.endswith(".lot.yaml"):
                    layouts.append(lot.yaml_to_bytes(f.read_text(encoding="utf-8"), str(f)))
        written = dungeon.check_layouts(d.space, layouts)
        if written.points != proof.points or written.problems:
            raise RiftError(f"the written layouts differ from the plan: {written.problems[:3]}")
        ui.ok(f"{len(done)} encounter(s) written into {root.name}. riftstone install \"{root.name}\" puts them into the "
              "game; how it plays is UNKNOWN until played")
        return 0
    finally:
        idx.close()


def cmd_waves(args) -> int:
    """Enemy waves: new groups that appear one after another, each once the one before it is dead."""
    from . import encounter, waves
    from .mod import Mod

    game = _game(args)
    if game.is_ddo:
        raise RiftError("enemy waves are for Dark Arisen; Online's spawns are its server's (riftstone encounter --game ddo)")
    idx = _index(game)
    try:
        w = _world(game, idx)
        root = Mod.load(Path(args.mod)).root if args.mod else None
        if not waves.census_ready(game, idx):
            ui.step("Reading which lot flags the game uses (once; kept with the world map)")
        if args.flags:
            stage = encounter.parse_stage(args.stage)
            w.stage(stage)
            print("\n".join(waves.flags_report(waves.flags_in_use(game, idx, w, root, stage), stage)))
            return 0
        if args.after is None or not args.wave:
            raise RiftError("say which group comes first and what follows it: --after N --wave enemy:count "
                            "(repeat --wave for each wave, in order)")
        if root is None:
            raise RiftError('say which mod gets the waves: --mod "My Mod" (riftstone new "My Mod" makes one)')
        chain = waves.plan(game, idx, w, root, args.stage, args.after, [waves.parse_wave(x) for x in args.wave],
                           args.at, args.spread, args.like)
        for kind, text in waves.report(chain):
            {"ok": ui.ok, "info": ui.info, "warn": ui.warn}[kind](text)
        if args.dry_run:
            ui.info("dry run: nothing written")
            return 0
        for f in waves.write(chain, root):
            ui.info(f.relative_to(root).as_posix())
        ui.ok(f"Written into {root.name}. riftstone install \"{root.name}\" puts it into the game.")
        return 0
    finally:
        idx.close()


def cmd_skin(args) -> int:
    from . import skins
    from .mod import Mod

    if args.action == "list":
        if not args.mod:
            ui.info("skin families: " + ", ".join(f"{f.key} ({f.enemy}: {', '.join(f.textures)})"
                                                  for f in skins.FAMILIES.values()))
            return 0
        root = Mod.load(Path(args.mod)).root
        man = skins.read_manifest(root)
        if not man:
            ui.info(f"{root.name} has no skins (riftstone skin make ...)")
        for fam, entries in sorted(man.items()):
            if not isinstance(entries, dict):                        # a hand edit the manifest cannot mean
                continue
            for n, e in sorted(entries.items(),
                               key=lambda kv: int(kv[0]) if re.fullmatch(r"[0-9]{1,9}", kv[0]) else 0):
                e = e if isinstance(e, dict) else {}
                ui.info(f"{fam} skin {n}: {e.get('title', '')}" + (f"  (from {e['source']})" if e.get("source") else ""))
        return 0
    if args.action == "export":
        if not (args.family and args.number is not None and args.mod and args.out):
            raise RiftError("riftstone skin export <family> <number> --mod <mod> --out <folder> [--as png|dds]")
        fam = skins.family(args.family)
        skins.check_number(args.number)
        root = Mod.load(Path(args.mod)).root
        files = skins.export(root, fam, args.number, Path(args.out), args.as_ or "png")
        ui.ok(f"{fam.key} skin {args.number}: {len(files)} picture(s) in {args.out}")
        for f in files:
            ui.info(f.name)
        ui.info(f"edit them, then: riftstone skin make {fam.key} {args.number} --textures \"{args.out}\" --mod \"{root.name}\"")
        return 0
    # make
    if not (args.family and args.number is not None and args.textures and args.mod):
        raise RiftError("riftstone skin make <family> <number> --textures <folder> --mod <mod>")
    game = _game(args)
    idx = _index(game)
    try:
        fam = skins.family(args.family)
        skins.check_number(args.number)
        root = Mod.load(Path(args.mod)).root
        skins.check_free(root, fam, args.number)             # skin numbers are shared by every mod
        tex_in = skins.textures_from_folder(Path(args.textures), fam)
        res = skins.resources(game, idx, fam, args.number, tex_in)
        ui.ok(f"{fam.key} skin {args.number}: {len(tex_in)} of {len(fam.textures)} albedo maps from {args.textures} "
              f"(the rest vanilla), {len(fam.materials)} materials pointed at model\\...\\s{args.number:02d}")
        for f in skins.write(root, fam, args.number, res, args.title or "", args.source or ""):
            ui.info(f.relative_to(root).as_posix())
        variant = skins.ddo_variant_of_folder(Path(args.textures))
        if variant is not None:          # tools/ddo_skins.py's folder: the package carries this recipe instead
            skins.record_ddo(root, fam, args.number, variant, res)
            ui.info(f"made from Dragon's Dogma Online's {variant} chimera: a package of this mod carries the recipe, "
                    "and each player's Riftstone makes the maps from their own Online client")
        elif skins.mark_other_game(root, fam, args.number, tex_in):
            ui.warn("some maps are Dragon's Dogma Online textures: they stay in this mod on this PC, and a package "
                    "refuses to carry them (make the skin with 'riftstone monster convert ... --as-skin' instead)")
        ui.info(f"place it: riftstone encounter <stage> {fam.enemy} --count 1 --at x,y,z --skin {args.number} "
                f"--mod \"{root.name}\" (needs the enemy_skins plugin)")
        return 0
    finally:
        idx.close()


def _monster_line(c: dict, kind: str, key: str) -> str:
    from . import monsters

    f = c[kind]["families"][key]
    e = c["counterparts"][kind][key]
    others = [n for n in f["names"] if n != f["name"]]
    who = (f["name"] or "(no name)") + (f" +{len(others)}" if others else "")
    if e["counterpart"] is None:
        tail = f"{ui.mist('no counterpart')}  {ui.mist((e['pair']['why'] or [''])[0][:70]) if e.get('pair') else ''}"
        return f"  {key:<10} {who[:30]:<30} {'':<32} {'none':<14} {tail}"
    g = c[monsters.other(kind)]["families"][e["counterpart"]]
    tex = e.get("counterpart_textures", {}).get("verdict")
    v = e["pair"]["verdict"]
    shade = ui.cyan if v == "same body" else ui.bold if v == "same skeleton" else (lambda s: s)
    return (f"  {key:<10} {who[:30]:<30} {e['counterpart']:<10} {(g['name'] or '')[:21]:<21} {shade(f'{v:<14}')}"
            + (f" textures {tex}" if tex else ""))


def cmd_monster(args) -> int:
    """Monsters between the games: every enemy family's counterpart in the other game, measured, and
    converting one into the other where the bodies match (docs/monsters.md)."""
    from . import monsters
    from .mod import Mod

    games = {}
    for k in ("ddda", "ddo"):
        try:
            games[k] = find_game(k)
        except RiftError:
            raise RiftError(f"riftstone monster compares both games, and {KINDS[k]['title']} was not found (--game "
                            "cannot stand in: set RIFTSTONE_GAME to its folder, or RIFTSTONE_DDO for Online)") from None
    quiet = bool(getattr(args, "json", False))
    idxs = {k: _index(g, quiet) for k, g in games.items()}
    try:
        c = monsters.load(games, idxs, args.rebuild, textures=args.action == "list" and args.textures,
                          progress_factory=None if quiet else (lambda: ui.Progress(1, "reading both games' enemies")))
        for w in c["ddda"]["warnings"] + c["ddo"]["warnings"]:      # kept off stdout when it is JSON
            if quiet:
                print(f"warning: {w}", file=sys.stderr)
            else:
                ui.warn(w)
        if args.action == "list":
            kinds = [_game_kind(args)] if args.game else ["ddo", "ddda"]
            if args.json:
                print(json.dumps({k: {key: {"name": f["name"], "names": f["names"], "enemies": [c[k]["enemies"][a]["id"]
                                                                                          for a in f["enemies"]],
                                            "body": f["body"], **{n: c["counterparts"][k][key].get(n) for n in (
                                                "counterpart", "pair", "namesake", "counterpart_textures")}}
                                      for key, f in sorted(c[k]["families"].items())} for k in kinds},
                                 ensure_ascii=False, indent=1))
                return 0
            for k in kinds:
                s = c["summary"][k]
                ui.step(f"{monsters.title(k)} -> {monsters.title(monsters.other(k))}: {s['families']} families; "
                        f"{s['enemies_without_body']} of {s['enemies']} enemy archives hold no enemy body")
                for key in sorted(c[k]["families"]):
                    e = c["counterparts"][k][key]
                    v = e["pair"]["verdict"] if e["counterpart"] else "none"
                    if args.verdict and v != args.verdict:
                        continue
                    print(_monster_line(c, k, key))
                ui.info(", ".join(f"{n} {v}" for v, n in s["verdicts"].items()))
            if not c.get("textures"):
                ui.info("texture sheets not compared yet: riftstone monster list --textures (or tools/monster_census.py)")
            ui.info("details: riftstone monster show <enemy>; convert: riftstone monster convert <enemy> --into <enemy> "
                    "--mod <mod>")
            return 0
        words = " ".join(args.source)
        if not words:
            raise RiftError(f"riftstone monster {args.action} <enemy> (a name or id, e.g. \"White Chimera\" or em5200)")
        if args.action == "show":
            found = []
            for k in (["ddo", "ddda"] if not args.game else [_game_kind(args)]):
                try:
                    found.append((k, *monsters.resolve(c, k, words)))
                except RiftError as e:
                    last = e
            if not found:
                raise last
            for k, key, arc in found:
                _monster_show(c, games, idxs, k, key, arc, args.into)
            return 0
        # convert
        if not args.into:
            raise RiftError("say which enemy of the other game it replaces: --into <enemy>")
        if not args.mod:
            raise RiftError('say which mod gets it: --mod "My Mod" (its game is the destination)')
        m = Mod.load(Path(args.mod))
        src = monsters.other(m.game)
        p = monsters.plan(c, games, idxs, src, words, args.into, args.as_skin)
        _monster_plan(p)
        if not p["allowed"]:
            for r in p["refused"]:
                ui.fail(r)
            return 2
        if args.dry_run:
            ui.info("dry run: nothing written")
            return 0
        done = monsters.convert(p, m.root, games, idxs)
        for w in done["written"]:
            ui.info(Path(w).relative_to(m.root).as_posix() if Path(w).is_relative_to(m.root) else str(w))
        for n in done["notes"]:
            ui.info(n)
        ui.ok(f"Written into {m.root.name}. riftstone build \"{m.root}\" checks it; how it looks and moves in game is "
              "UNKNOWN until played.")
        return 0
    finally:
        for i in idxs.values():
            i.close()


def _monster_plan(p: dict) -> None:
    s, t, pr, tx = p["source"], p["target"], p["pair"], p["textures"]
    ui.step(f"{s['enemy']} ({s['body'].rsplit(chr(92), 1)[-1]}) -> {t['enemy']} ({t['body'].rsplit(chr(92), 1)[-1]})"
            + (f" as chimera skin {p['skin']}" if p["skin"] is not None else ""))
    ui.info(f"verdict: {pr['verdict']}; {pr['skinned']} joint(s) its meshes are bound to: {len(pr['same'])} same, "
            f"{len(pr['moved'])} moved, {len(pr['reparented'])} re-parented, {len(pr['missing'])} missing")
    ui.info(f"textures: {tx['verdict']}" + "".join(
        f"; {r['sheet']} {r['kind']} {r['difference']} vs {r['mirrored']} mirrored" for r in tx["sheets"] if "difference" in r))
    for n in p["notes"]:
        ui.info(n)


def _monster_show(c: dict, games: dict, idxs: dict, kind: str, key: str, arc: str | None, into: str | None) -> None:
    from . import monsters

    f = c[kind]["families"][key]
    b = c[kind]["bodies"][f["body"]]
    ui.ok(f"{monsters.title(kind)}: family {key} {f['name']}")
    ui.info("enemies: " + ", ".join(f"{c[kind]['enemies'][a]['id']} {c[kind]['enemies'][a]['name']}".strip()
                                    for a in f["enemies"]))
    ui.info(f"body {f['body']}: {len(b['joints'])} joints, meshes bound to {len(b['skinned'])}; {b['meshes']} meshes in "
            f"{len(b['formats'])} vertex format(s); {len(b['materials'])} material(s)")
    if f["parts"]:
        ui.info("parts: " + ", ".join(f"{p.rsplit(chr(92), 1)[-1]} ({len(c[kind]['bodies'][p]['joints'])} joints)"
                                     for p in f["parts"]))
    ui.info(f"motion lists: {len(f['motions'])}, driving {len(f['driven'])} joint(s); textures: {len(f['textures'])}")
    e = c["counterparts"][kind][key]
    targets = []
    if into:
        targets.append(monsters.resolve(c, monsters.other(kind), into))
    else:
        for slot in ("counterpart", "namesake", "closest"):
            if e.get(slot):
                targets.append((e[slot], None))
    for tkey, tarc in dict.fromkeys(targets):
        p = monsters.plan_for(c, games, idxs, kind, (key, arc), (tkey, tarc))
        _monster_plan(p)
        for w in p["pair"]["why"]:
            print(f"      {w}")
        print(f"      convert: {'allowed' if p['allowed'] else 'refused'}")


def _language(value: str) -> int | None:
    """english / eng / 1 -> 1; 'all' -> None."""
    from . import gmd

    v = value.strip().lower()
    if v == "all":
        return None
    for lang, name in gmd.LANGUAGES.items():
        if v in (name, gmd.SUFFIXES[lang], str(lang)):
            return lang
    raise RiftError(f"unknown language {value!r}: use one of {', '.join(gmd.LANGUAGES.values())}, or all")


def cmd_text(args) -> int:
    from . import text as textmod

    game = _game(args)
    idx = _index(game)
    try:
        if args.action == "find":
            words = " ".join(args.rest)
            lang = args.lang or ("all" if game.is_ddo else "english")  # DDO ships one language slot
            hits = textmod.find(game, words, _language(lang), args.limit)
            if not hits:
                ui.warn(f"no {lang} text contains {words!r}")
                return 1
            for h in hits:
                line = h.text.replace("\r\n", " / ").replace("\n", " / ").replace("\r", " / ")
                label = f" [{h.label}]" if h.label is not None else ""
                print(f"  {ui.gold(h.path)}  {ui.mist('id')} {h.message}{label}  {line[:120]}")
            if len(hits) == args.limit:
                ui.info(f"first {args.limit} shown; use more words or --limit")
            ui.info("add a line next to these with:  riftstone text add <file> \"your line\" --mod <mod>")
            return 0
        if len(args.rest) != 2:
            raise RiftError('text add takes a text file and one line: text add <file> "the line" --mod <mod>')
        if not args.mod:
            raise RiftError("say which mod gets the line: --mod <mod folder>")
        from .mod import Mod

        m = Mod.load(Path(args.mod))
        added = textmod.add(game, idx, m.root, args.rest[0], args.rest[1], args.label, not args.one_language)
        for a in added:
            ui.info(f"{a.language:<9} id {a.message:<5} {a.path.relative_to(m.root).as_posix()}")
        ids = sorted({a.message for a in added})
        if len(ids) == 1:
            ui.ok(f"Added line {ids[0]} to {len(added)} language version{'s' if len(added) != 1 else ''}. "
                  "Translate it in each file if you like, then install the mod.")
        else:
            ui.warn(f"the language versions gave the new line different ids ({', '.join(map(str, ids))}): they "
                    "already had different line counts in this mod. Other data finds a line by id, so line them up.")
        return 0
    finally:
        idx.close()


def cmd_loader(args) -> int:
    from . import install, loader, runtime

    game = _game(args)
    if args.action in ("status", "install", "remove") and (args.plugin_action or args.file):
        # a plugin's words after these went unread: 'loader remove remove x.asi' removed the loader itself
        raise RiftError(f"'loader {args.action}' takes no more words; plugins: riftstone loader plugin list|add|"
                        "remove|release, safe mode: riftstone loader safe-mode status|off")
    if args.action == "status":
        s = loader.status(game)
        version = runtime.loader_version(game.root / "dinput8.dll") if s["installed"] else None
        ui.ok(f"loader {'installed' if s['installed'] else 'not installed'}"
              + (f" ({version})" if version else "") + (" (chaining another dinput8)" if s["chained"] else ""))
        if s["other_dinput8"]:
            ui.warn("another dinput8.dll is installed; 'riftstone loader install' keeps it working by chaining it")
        st = runtime.runtime_state(game.root)
        if st["safe_mode"]:
            ui.warn("SAFE MODE is on: the game starts without plugins and mods ('riftstone loader safe-mode off')")
        _show_session_end(runtime.session_end(game.root, running=install.game_running(game)))
        quarantined = {q.lower() for q in st["quarantine"]}       # the loader keeps unusual names in lower case
        for p in s["plugins"]:
            ui.info(f"plugin: {p}" + ("  (QUARANTINED)" if p.lower() in quarantined else ""))
        for r in runtime.list_reports(game.state_dir / "logs")[:5]:
            ui.info(f"{r['kind']} report: {r['path']}")
        if s["log"]:
            ui.info(f"log: {s['log']}")
        return 0
    if args.action == "d3d9":
        return _loader_d3d9(game, args)
    if args.action == "safe-mode":
        if args.plugin_action not in (None, "status", "off"):
            raise RiftError(f"'loader safe-mode {args.plugin_action}': safe-mode takes status or off")
        if args.plugin_action == "off":
            was = runtime.safe_mode_off(game.root)
            ui.ok("safe mode is off: the next start loads plugins and mods again" if was else
                  "safe mode was not on; the start-up crash count is reset")
        else:
            st = runtime.runtime_state(game.root)
            ui.ok("safe mode is " + ("ON: the game starts without plugins and mods" if st["safe_mode"] else "off"))
        return 0
    if args.action == "plugin":
        # each action by name: 'off' and 'status' (safe-mode's words) fell through to remove and deleted the plugin
        if args.plugin_action not in (None, "list", "add", "remove", "release"):
            raise RiftError(f"'loader plugin {args.plugin_action}': plugin takes list, add <file>, remove <name> or "
                            f"release <name> ('{args.plugin_action}' goes with safe-mode)")
        if args.plugin_action in ("add", "remove", "release") and not args.file:
            raise RiftError(f"name the plugin: riftstone loader plugin {args.plugin_action} "
                            + ("<.asi or .dll file>" if args.plugin_action == "add" else "<name>"))
        if args.plugin_action == "release":
            if runtime.release_plugin(game.root, args.file):
                ui.ok(f"{args.file} is out of quarantine; the next start loads it")
            else:
                ui.info(f"{args.file} was not quarantined")
            return 0
        if args.plugin_action in (None, "list"):
            names = loader.list_plugins(game)
            ui.ok(f"{len(names)} plugin(s) in {loader.plugins_dir(game)}")
            for n in names:
                ui.info(n)
        elif args.plugin_action == "add":
            if not args.file:
                raise RiftError("loader plugin add needs a .asi/.dll file, or the name of a plugin this Riftstone "
                                "built (e.g. free_sprint)")
            src = Path(args.file)
            if not src.is_file() and src.suffix.lower() not in (".asi", ".dll"):
                from . import plugins as plugins_mod       # a plugin built here, by name (as Studio adds it)
                name = plugins_mod.add(game, args.file)["file"]
            else:
                name = loader.add_plugin(game, src)
            ui.ok(f"Added plugin {name}")
            ini = loader.plugins_dir(game) / Path(name).with_suffix(".ini")
            if ini.is_file():
                ui.info(f"settings: {ini}")
            if not game.loader_installed():
                ui.warn("the loader is not installed yet; run 'riftstone loader install' so plugins load")
        elif args.plugin_action == "remove":
            loader.remove_plugin(game, args.file)
            ui.ok(f"Removed plugin {args.file}")
        return 0
    idx = _index(game)
    if args.action == "install":
        r = loader.install_loader(game, idx)
        if r.get("updated"):
            ui.ok(f"Loader updated to {runtime.loader_version(game.root / 'dinput8.dll')}: your settings and installed "
                  "mods are unchanged; new settings are in riftstone_loader.ini with their defaults")
        else:
            ui.ok("Loader installed: archives are now served from <game>\\riftstone\\overlay, nativePC stays original")
        if r["chained"]:
            ui.info(f"your previous dinput8.dll now loads through Riftstone as {r['chained']}")
        if r["mods_moved_to_overlay"]:
            ui.info(f"served from the overlay: {', '.join(r['mods_moved_to_overlay'])}")
    else:
        r = loader.remove_loader(game, idx)
        ui.ok("Loader removed: the game runs as Steam installed it")
        if r["restored_other_dinput8"]:
            ui.info("your previous dinput8.dll is back in place")
        if r["mods_waiting"]:
            ui.info(f"these mods are off until the loader is back (Riftstone.cmd loader install): "
                    f"{', '.join(r['mods_waiting'])}")
    return 0


def _loader_d3d9(game: Game, args) -> int:
    """riftstone loader d3d9 [status | add <DXVK release, folder or d3d9.dll> | off]"""
    from . import loader

    what = args.plugin_action if args.plugin_action in ("add", "off") else "status"
    if what == "add":
        if not args.file:
            raise RiftError("name DXVK's release (.tar.gz or .zip), its folder, or its x32 d3d9.dll: "
                            "riftstone loader d3d9 add <path>")
        r = loader.d3d9_add(game, Path(args.file))
        ui.ok(f"{r['origin']} -> {r['path']} ({'DXVK' if r['dxvk'] else 'a Direct3D 9 runtime'}, sha256 "
              f"{r['sha256'][:16]}...); [d3d9] chain = {loader.D3D9_CHAIN}")
        if r["wrote_conf"]:
            ui.info(f"DXVK's settings: {r['conf']} (all commented out: DXVK's defaults)")
        if r["game_folder_d3d9"]:
            ui.warn(f"{r['game_folder_d3d9']} is in the game folder too, and stays in charge: take one of the two out")
        if not r["loader_installed"]:
            ui.warn("the loader is not installed yet; 'riftstone loader install' puts it in place, and the chain works "
                    "from its next start")
        ui.info("the next start of the game uses it; loader.log says 'd3d9     chained ...' (and why not, if it could "
                "not). 'riftstone loader d3d9 off' goes back to Windows' own Direct3D 9")
        return 0
    if what == "off":
        was = loader.d3d9_off(game)
        ui.ok("[d3d9] chain is off: the next start gets Windows' own Direct3D 9 (the DLL stays in riftstone\\dxvk)"
              if was else "[d3d9] chain was not set")
        return 0
    st = loader.d3d9_status(game)
    if st["game_folder_d3d9"]:
        ui.info(f"{st['game_folder_d3d9']}: {'DXVK' if st['game_folder_dxvk'] else 'a d3d9.dll'} in the game folder, "
                "which the game loads instead of Windows' own")
    if not st["setting"]:
        ui.ok("[d3d9] chain is not set: the game gets " + ("the game folder's d3d9.dll" if st["game_folder_d3d9"]
                                                           else "Windows' own Direct3D 9"))
        return 0
    if st["problem"]:
        ui.warn(f"[d3d9] chain = {st['setting']} {st['problem']}")
        return 1
    ui.ok(f"[d3d9] chain = {st['setting']}: {'DXVK' if st['dxvk'] else 'a Direct3D 9 runtime'}, sha256 {st['sha256']}")
    if st["conf"]:
        ui.info(f"DXVK's settings: {st['conf']}")
    return 0


def cmd_laa(args) -> int:
    """Whether an exe is large-address aware; --copy writes a copy with the flag set."""
    from . import pe

    exe = Path(args.exe) if args.exe else _game(args).exe
    info = pe.describe(exe)
    if info["bits"] != 32:
        ui.ok(f"{exe}: a {info['bits']}-bit {info['machine']} {'DLL' if info['dll'] else 'program'}; the flag only matters "
              "for 32-bit programs")
    elif info["large_address_aware"]:
        ui.ok(f"{exe}: large-address aware, so it gets 4 GB of address space on 64-bit Windows")
    else:
        ui.warn(f"{exe}: NOT large-address aware, so it gets 2 GB of address space instead of 4 GB")
    if args.copy:
        r = pe.write_large_address_aware_copy(exe, Path(args.copy))
        ui.ok(f"wrote {args.copy}: " + ("the flag set" if r["changed"] else "the flag was already set; an exact copy")
              + f", header checksum 0x{r['checksum']:08x}. {exe.name} itself is unchanged")
    return 0


def cmd_live(args) -> int:
    from . import runtime

    while True:
        live = runtime.read_live()
        if args.json:
            print(json.dumps(live, indent=1, default=str) if live else "null")
            return 0 if live else 1
        if not live:
            ui.info("no game with the Riftstone loader is running (start the game; 'riftstone loader install' puts "
                    "the loader in place)")
            if not args.watch:
                return 1
        else:
            if args.watch:
                print("\x1b[2J\x1b[H", end="")
            for i, line in enumerate(runtime.describe_live(live)):
                (ui.ok if i == 0 else ui.info)(line)
        if not args.watch:
            return 0
        try:
            time.sleep(1.0)
        except KeyboardInterrupt:
            return 0


def _show_session_end(end: dict | None) -> None:
    """How the last session ended, as the loader recorded it (crash, doctor, loader status)."""
    from . import runtime

    if not end:
        return
    (ui.ok if end["reason"] in runtime.NORMAL_ENDS else ui.warn)(runtime.describe_end(end))
    if end.get("detail"):
        ui.info(f"what the loader saw: {end['detail']}")


def cmd_crash(args) -> int:
    from . import install, legal, runtime

    game = _game(args)
    logs = game.state_dir / "logs"
    found = runtime.list_reports(logs)
    if args.list:
        if not found:
            ui.ok("no crash, fatal-error or hang reports")
        for r in found:
            ui.info(f"{r['name']}  ({r['kind']}{', with a minidump' if r['dump'] else ''})")
        return 0
    end = runtime.session_end(game.root, running=install.game_running(game))
    if args.report:
        match = [r for r in found if r["name"] == args.report or r["stamp"] == args.report]
        if not match:
            raise RiftError(f"no report named {args.report!r} in {logs} ('riftstone crash --list' shows them)")
        chosen = match[0]
    elif found:
        chosen = found[0]
    else:
        if args.json:
            print(json.dumps({"report": None, "session_end": end}, indent=1, default=str))
            return 0
        _show_session_end(end)
        ui.ok("no crash, fatal-error or hang reports: nothing has gone wrong since the loader was installed")
        return 0
    rep = runtime.parse_report(chosen["path"].read_text(encoding="utf-8", errors="replace"))
    lines = runtime.explain(rep, game.root)
    if args.json:
        print(json.dumps({"report": str(chosen["path"]), "facts": rep, "explanation": lines, "session_end": end,
                          "support": legal.SUPPORT}, indent=1, default=str))
        return 0
    if not args.report:
        _show_session_end(end)
    earlier = bool(end and end.get("started") and chosen["stamp"] < end["started"])
    ui.ok(f"{chosen['name']} ({rep.get('time') or chosen['stamp']})"
          + ("; from an earlier session than the last one" if earlier else ""))
    for line in lines:
        ui.info(line)
    ui.info(f"the whole report: {chosen['path']}")
    ui.warn(legal.SUPPORT)
    return 0


def cmd_playtest(args) -> int:
    """The last play session checked item by item from its logs; or the texture-guard test mod."""
    from . import install, playtest

    game = _game(args)
    if args.action == "guard-mod":
        if not args.mod:
            raise RiftError("playtest guard-mod needs --mod <folder> (a new mod is made there)")
        idx = _index(game)
        try:
            written = playtest.guard_test_mod(game, idx, Path(args.mod))
        finally:
            idx.close()
        ui.ok(f"wrote {len(written)} file(s) into {args.mod}: the goblins' skin now points at a texture that does "
              "not exist")
        ui.info(f"install it ('riftstone install \"{args.mod}\"'), find goblins in the game: with the loader's "
                "guard on they draw grey there and loader.log names the file; then 'riftstone uninstall' it")
        return 0
    if game.is_ddo:
        raise RiftError("playtest reads the Riftstone loader's logs, and the loader runs in Dark Arisen only")
    items = playtest.check_session(game.root, previous=args.previous)
    if args.json:
        print(json.dumps([i.as_dict() for i in items], indent=1))
        return 0
    if install.game_running(game) and not args.previous:
        ui.info("the game is running: this is the session in progress")
    show = {playtest.OK: ui.ok, playtest.FAIL: ui.fail, playtest.UNTESTED: ui.warn, playtest.INFO: ui.info}
    for it in items:
        word = {"ok": "OK", "fail": "FAILED", "untested": "not exercised", "info": "note"}[it.status]
        show[it.status](f"{it.title}: {word}")
        for line in it.lines:
            ui.info(f"  {line}")
        if it.todo:
            ui.info(f"  to exercise it: {it.todo}")
    counts = {s: sum(1 for i in items if i.status == s) for s in (playtest.OK, playtest.FAIL, playtest.UNTESTED)}
    ui.info(f"{counts['ok']} OK, {counts['fail']} failed, {counts['untested']} not exercised "
            "(docs/playtest.md is the checklist)")
    return 1 if counts["fail"] else 0


def cmd_saves(args) -> int:
    from . import install, runtime, saves

    try:
        game = _game(args)
    except RiftError:
        game = None  # no install found: the default folders and Steam's saves still work
    plugin_root = Path(args.folder) if args.folder else saves.backup_root(game)
    if args.save:
        found = [(args.account or "other", Path(args.save))]
    else:
        found = [(a, p) for a, p in saves.steam_saves() if args.account in (None, a)]

    if args.action == "backup":
        if not found:
            raise RiftError("no DDDA.sav found under Steam's userdata; pass --save <file>")
        for a, p in found:
            # a folder given as --save: its parent folder was copied as if it were the save's
            if not p.is_file():
                raise RiftError(f"--save {p}: " + ("a folder; name the DDDA.sav in it" if p.is_dir() else
                                                   "no such file"))
        for a, p in found:
            try:
                saves.check(p.read_bytes())
            except (OSError, RiftError) as e:
                ui.warn(f"account {a}: the save is not complete ({e}); it is copied as it is")
        made = runtime.backup_saves([p.parent for _, p in found])
        for d in made:
            ui.ok(f"copied the save folder to {d}")
        if not made:
            ui.ok("the save has not changed since the newest copy of its folder")
        return 0

    if args.action == "knowledge":
        return _saves_knowledge(args, game, found, plugin_root)

    listed = saves.backups(plugin_root, account=args.account)

    def complete(b) -> bool:
        try:
            saves.check(b.save.read_bytes())
            return True
        except (OSError, RiftError):
            return False

    if args.action == "list":
        numbered, count = [], {}
        for b in listed:
            count[b.account] = count.get(b.account, 0) + 1
            numbered.append((count[b.account], b))
        if args.json:
            print(json.dumps({"saves": [{"account": a, "path": str(p)} for a, p in found],
                              "folders": {"while_playing": str(plugin_root), "save_folder": str(saves.loader_root())},
                              "copies": [{"account": b.account, "number": n, "kind": b.kind, "name": b.name,
                                          "path": str(b.path), "time": b.time.isoformat(sep=" ") if b.time else None,
                                          "session_start": b.session_start, "complete": complete(b)}
                                         for n, b in numbered]}, indent=1))
            return 0
        if not found:
            ui.warn("no DDDA.sav found under Steam's userdata" + (f" for account {args.account}" if args.account else ""))
        for a, p in found:
            try:
                data = p.read_bytes()
                saves.check(data)
                state = "complete"
                # The game rewrites unchanged saves, and neither kind of copy repeats a save: say which holds it.
                held = next((n for n, b in numbered if b.account == a and b.save.is_file()
                             and b.save.stat().st_size == len(data) and b.save.read_bytes() == data), None)
                state += f", the same bytes as copy {held}" if held else ", no copy holds it yet"
            except (OSError, RiftError) as e:
                state = f"NOT a complete save: {e}"
            when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime)) if p.is_file() else "missing"
            ui.ok(f"save of account {a}: {p} ({when}, {state})")
        if not listed:
            ui.info(f"no copies yet. The loader copies the save folder when the game starts "
                    f"({saves.loader_root()}); the save_backup plugin copies each save while you play ({plugin_root}); "
                    "'riftstone saves backup' copies it now")
            return 0
        ui.ok(f"{len(listed)} cop{'y' if len(listed) == 1 else 'ies'}, newest first")
        account = None
        for n, b in numbered:
            if b.account != account:
                account = b.account
                ui.info(f"account {account}")
            when = b.time.strftime("%Y-%m-%d %H:%M:%S") if b.time else "?"
            notes = [b.kind] + (["a game session started from it"] if b.session_start else []) \
                + ([] if complete(b) else ["NOT complete: cannot be put back"])
            ui.info(f"  {n:3}  {when}  {b.name}   {', '.join(notes)}")
        ui.info("put one back with:  riftstone saves restore <number> --yes   (close the game first)")
        return 0

    if not args.which:
        raise RiftError("say which copy: riftstone saves restore <number> --yes  ('riftstone saves list' numbers them)")
    b = saves.pick(listed, args.which, args.account)
    target = Path(args.save) if args.save else next((p for a, p in found if a == b.account), None)
    if target is None:
        raise RiftError(f"no DDDA.sav for account {b.account} under Steam's userdata; pass --save <file>")
    if not args.yes:
        what = "every file of the save folder" if b.kind != saves.IN_PLAY else "the save"
        ui.warn(f"this puts {b.name} ({b.kind}) back as {what} in {target.parent}; what it replaces is kept first. "
                "Steam Cloud may then ask which save to keep: choose this PC's. Run again with --yes to do it.")
        return 1
    # no game folder: the game is looked for by its exe's name (game_running(None) raised AttributeError)
    running = install.game_running(game) if game else install.exe_running("DDDA.exe")
    kept = saves.restore_backup(b, target, plugin_root, game_running=lambda: running)
    ui.ok(f"Restored {b.name} ({b.kind}) to {target.parent}")
    ui.info(f"what it replaced is kept as {kept}" if kept else "the save already held these bytes; nothing changed")
    ui.info("If Steam reports a cloud conflict when the game starts, keep the files on this PC.")
    return 0


def _saves_knowledge(args, game, found, plugin_root) -> int:
    """riftstone saves knowledge [--grant --yes]: the main pawn's enemy knowledge by the game's counters."""
    from . import install, saves

    if not found:
        raise RiftError("no DDDA.sav found under Steam's userdata; pass --save <file>")
    if len(found) > 1:
        raise RiftError(f"saves of several accounts ({', '.join(a for a, _ in found)}); choose one with --account")
    if game is None or game.is_ddo:
        raise RiftError("the knowledge thresholds are read from DDDA.exe, and Dark Arisen was not found "
                        "(pass --game \"C:\\path\\to\\DDDA\")")
    tables = saves.knowledge_tables(game.exe)
    account, save = found[0]
    k = saves.knowledge(save.read_bytes(), tables)
    if args.json and not args.grant:
        print(json.dumps({"account": account, "save": str(save), "complete": k["complete"],
                          "groups": len(k["levels"]), "levels": {str(g): v for g, v in k["levels"].items()},
                          "feats": {"reached": k["feats"][0], "of": k["feats"][1]}}, indent=1))
        return 0
    by = {lv: sum(1 for v in k["levels"].values() if v == lv) for lv in range(saves.LEVELS + 1)}
    ui.ok(f"main pawn, account {account}: {k['complete']} of {len(k['levels'])} enemy groups at the top level by "
          f"its counters, {k['feats'][0]} of {k['feats'][1]} special feats")
    ui.info("groups per level: " + ", ".join(f"{lv}: {by[lv]}" for lv in range(saves.LEVELS, -1, -1)))
    if not args.grant:
        missing = [g for g, v in k["levels"].items() if v < saves.LEVELS]
        if missing:
            ui.info("below the top: groups " + ", ".join(str(g) for g in missing))
            ui.info("raise every counter to the top with:  riftstone saves knowledge --grant --yes   (close the game first)")
        return 0
    if not args.yes:
        ui.warn(f"this raises the main pawn's encounter, kill and feat counters in {save} to the game's own top "
                "thresholds, in both copies of the player's data the save holds; the game turns counters into "
                "knowledge while the pawn is with you. The save as it is now is kept as a copy first. "
                "Run again with --yes to do it.")
        return 1
    running = install.game_running(game)
    kept, n = saves.grant_knowledge_file(save, plugin_root, account, tables, game_running=lambda: running)
    if not n:
        ui.ok("nothing to raise: the counters already reach every level")
        return 0
    ui.ok(f"raised {n} counters of the main pawn in {save}")
    ui.info(f"the save as it was is kept as {kept} ('riftstone saves restore' puts it back)")
    ui.info("The game should give the knowledge the next time your main pawn is with you (it turns counters "
            "into knowledge as it runs; not yet seen in game). If Steam reports a cloud conflict when the game "
            "starts, keep the files on this PC.")
    return 0


def cmd_auto(paths: list[str]) -> int:
    """Drag-and-drop: do the obvious thing for each path."""
    code = 0
    for raw in paths:
        p = Path(raw)
        ns = argparse.Namespace(out=None, raw=False, force=False)
        try:
            if p.is_dir():
                if (p / arcfolder.MANIFEST).is_file():
                    ns.folders = [p]
                    cmd_pack(ns)
                else:
                    from .mod import MOD_FILE

                    if (p / MOD_FILE).is_file():
                        ns.mods, ns.game = [p], None
                        cmd_build(ns)
                    else:
                        ui.warn(f"{p.name}: drop an unpacked archive folder or a mod folder")
                        code = 1
            elif p.suffix.lower() == ".arc":
                ns.archives = [p]
                cmd_unpack(ns)
            elif p.is_file():
                head = p.read_bytes()[:4]
                if p.name.lower().endswith(".yaml") or head == b"XFS\0":
                    ns.files = [p]
                    cmd_param(ns)
                else:
                    ns.files = [p]
                    cmd_info(ns)
            else:
                ui.fail(f"{raw} does not exist")
                code = 1
        except (RiftError, OSError) as e:           # the next dropped path still gets its turn
            ui.fail(_os_error(e) if isinstance(e, OSError) else str(e))
            code = 1
    return code


def cmd_register(args) -> int:
    from .registration import run
    report = run(Path(args.plan), Path(args.source), Path(args.out) if args.out else None)
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0


def cmd_vfs(args) -> int:
    """Prepare a read-only in-process snapshot; never deploy or alter originals."""
    from .vfs import mount_manifest
    view, protected = mount_manifest(Path(args.mount))
    def write_new(path, data):
        target = Path(path).resolve()
        if any(target.is_relative_to(root) for root in protected):
            raise RiftError("vfs output must be outside every base/mod source folder")
        try:
            with target.open("xb") as stream: stream.write(data)
        except FileExistsError:
            raise RiftError(f"{target} already exists; vfs writes only a new file") from None
    if args.out and not args.read:
        raise RiftError("vfs --out requires --read PATH")
    if args.read:
        try:
            data = view.open(args.read).read()
        except FileNotFoundError:
            raise RiftError(f"--read {args.read}: the snapshot has no such file") from None
        if args.out:
            write_new(args.out, data)
        view.report["read"] = {"path": args.read, "bytes": len(data), "output": args.out}
    if args.bundle:
        from .vfs import digest
        data = view.bundle()
        write_new(args.bundle, data)
        view.report["bundle"] = {"path": args.bundle, "bytes": len(data), "sha256": digest(data)}
    print(json.dumps(view.report, indent=2, ensure_ascii=True, sort_keys=True))
    return 0


def _count(text: str) -> int:
    """--limit, --top: a whole number of at least 1 (0 showed 'nothing matches', a negative one everything,
    or every type but the last)."""
    try:
        n = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from None
    if n < 1:
        raise argparse.ArgumentTypeError(f"is at least 1, not {n}")
    return n


def _port(text: str) -> int:
    """studio --port: 0 (any free port) to 65535 (outside it the server's bind raised OverflowError)."""
    try:
        n = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from None
    if not 0 <= n <= 65535:
        raise argparse.ArgumentTypeError(f"is 0 (any free port) to 65535, not {n}")
    return n


# A value may start with a minus: positions (--at -100,-350,-8800; half of Gransys has negative x), which
# argparse takes for an unknown option unless it is a plain number like -5.
_NEGATIVE = re.compile(r"^-\.?\d")


def _takes_negatives(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
    p._negative_number_matcher = _NEGATIVE          # argparse's own test for "a number, not an option"
    return p


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="riftstone",
                                 description="Modding toolchain for Dragon's Dogma: Dark Arisen and Dragon's Dogma Online.",
                                 epilog=QUICK, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"riftstone {__version__}\n" + _legal_lines())
    sub = ap.add_subparsers(dest="cmd", metavar="<command>")

    def add(name, fn, help_text, game=True, aliases=()):
        p = sub.add_parser(name, aliases=list(aliases), help=help_text, description=help_text)
        p.set_defaults(fn=fn)
        if game:
            p.add_argument("--game", help="ddda, ddo, or a game folder (default: the mod's game, "
                                          "$RIFTSTONE_GAME, else DDDA through Steam)")
        return p

    p = add("register", cmd_register, "build offline DDDA item/parameter archive copies; never install or grow native ID tables", game=False)
    p.add_argument("--plan", required=True, help="pinned registration JSON request")
    p.add_argument("--source", required=True, help="input root containing the request's rom/...arc files")
    p.add_argument("--out", help="new output directory outside source/game; omit to validate only")
    p = add("vfs", cmd_vfs, "prepare a read-only in-memory overlay snapshot and conflict report (no live-game mount)", game=False)
    p.add_argument("--mount", required=True, help="folder containing vfs.json (schema riftstone.vfs/1)")
    p.add_argument("--read", help="read this relative virtual file")
    p.add_argument("--out", help="save --read bytes to a new file outside all source folders")
    p.add_argument("--bundle", help="write a new RSV1 snapshot for native runtime initialization, outside source folders")
    p = add("unpack", cmd_unpack, "archive(s) -> editable folder(s); parameters become YAML", game=False)
    p.add_argument("archives", nargs="+")
    p.add_argument("-o", "--out")
    p.add_argument("--raw", action="store_true", help="keep parameter files binary (no YAML)")
    p = add("pack", cmd_pack, "unpacked folder(s) -> archive(s), verified", game=False)
    p.add_argument("folders", nargs="+")
    p.add_argument("-o", "--out")
    p = add("param", cmd_param, "parameter file <-> YAML", game=False)
    p.add_argument("files", nargs="+")
    p.add_argument("-o", "--out")
    p.add_argument("--force", action="store_true", help="overwrite without keeping a .bak")
    p = add("find", cmd_find, "search every resource in the game by name (--game both: both games)")
    p.add_argument("text")
    p.add_argument("--type", help="only this extension, e.g. statusparam, mod, tex")
    p.add_argument("--limit", type=_count, default=60)
    p.add_argument("--json", action="store_true")
    p = add("extract", cmd_extract, "copy original resource(s) out of the game (parameters as YAML)")
    p.add_argument("resources", nargs="+", help="engine path with extension, e.g. param/status/enemy.statusparam")
    p.add_argument("--mod", help="put it into this mod project, ready to edit")
    p.add_argument("--arc", help="take it from this archive (and target only it inside --mod)")
    p.add_argument("-o", "--out")
    p.add_argument("--raw", action="store_true")
    p.add_argument("--force", action="store_true")
    p = add("port", cmd_port, "bring a resource from the other game into a mod: textures, text, models "
                               "(with their material and textures), converted and checked")
    p.add_argument("resource", help="engine path in the source game, e.g. equip/.../model.mod")
    p.add_argument("--mod", required=True, help="the mod to put it in (its game is the destination)")
    p.add_argument("--from", dest="source", choices=["ddda", "ddo"], help="source game (default: the other one)")
    p.add_argument("--as", dest="as_", help="destination path: replace this resource instead of the same path")
    p.add_argument("--like", help="destination material (.mrl) to rebuild the material from")
    p.add_argument("--arc", help="add into this destination archive instead of replacing")
    p.add_argument("--from-arc", help="take it from this source archive")
    p.add_argument("--model-only", action="store_true", help="a model without its material and textures")
    p.add_argument("--retarget", action=argparse.BooleanOptionalAction, default=None,
                   help="a motion list: rebake the weapon joints the two games' player bodies parent differently, so "
                        "the weapons and their hit shapes keep their place (default: on for player motion lists; "
                        "docs/animation.md)")
    p.add_argument("--dye", metavar="COLOUR",
                   help="an Online weapon or armour model into a Dark Arisen mod: bake Online's colours into its "
                        "colour maps -- default (the item's own), material, red, green, blue, yellow, pink, black, a "
                        "colour number or #rrggbb (docs/ddo-dye.md)")
    p = add("compare", cmd_compare, "the same resource in both games side by side (text: the lines that differ), "
                                     "and whether it ports", game=False)
    p.add_argument("resource", help="engine path, e.g. ui/00_message/enemy/enemy_name.gmd")
    p.add_argument("--as", dest="as_", help="the path in the game that lacks the first one")
    p.add_argument("--limit", type=_count, default=12)
    p = add("import", cmd_import, "turn changed archives (another tool's output, an old archive mod) into a mod "
                                   "of only what changed, so it merges with other mods")
    p.add_argument("paths", nargs="+", help="archives, or folders of archives (e.g. a copy of nativePC)")
    p.add_argument("--mod", required=True, help="the mod to write (created when missing)")
    p.add_argument("--binary", action="store_true", help="keep editable formats binary (no YAML)")
    p.add_argument("--per-archive", action="store_true", help="never merge a change into files/")
    p = add("new", cmd_new, "create a mod (in the mods folder, where Studio and --mod \"<name>\" find it)", game=False)
    p.add_argument("folder", metavar="name", help='the mod\'s name ("Harder Goblins"), or a folder path')
    p.add_argument("--game", choices=["ddda", "ddo"], help="the game the mod is for (default: $RIFTSTONE_GAME's "
                                                           "game, else ddda)")
    p.add_argument("--name", help="a name to show that differs from the folder's")
    p.add_argument("--author")
    p.add_argument("--here", action="store_true", help="make it in the current folder, not the mods folder")
    p = add("mods", cmd_mods, "your mods: each mods folder, the mods in it and which are installed", game=False)
    p.add_argument("--game", help="only this game's mods folder: ddda, ddo or a game folder (default: every game "
                                  "on this PC)")
    p = add("build", cmd_build, "build a mod's archives into its build/ folder (the game is not touched)")
    p.add_argument("mods", nargs="+")
    p.add_argument("-o", "--out")
    p = add("install", cmd_install, "enable mod(s) and update the game (originals backed up first)")
    p.add_argument("mods", nargs="*")
    p.add_argument("--dry-run", action="store_true", help="show what would change, change nothing")
    p = add("uninstall", cmd_uninstall, "disable mod(s) and update the game")
    p.add_argument("mods", nargs="+")
    p.add_argument("--dry-run", action="store_true")
    add("restore", cmd_restore, "return every archive Riftstone changed to the original")
    p = add("status", cmd_status, "installed mods and whether the game still matches them")
    p.add_argument("--json", action="store_true")
    p = add("watch", cmd_watch, "rebuild and reinstall whenever a mod file changes")
    p.add_argument("mods", nargs="*")
    p = add("index", cmd_index, "(re)build the resource index and show resource types")
    p.add_argument("--rebuild", action="store_true")
    p.add_argument("--top", type=_count, default=20, help="how many resource types to list (default 20)")
    p = add("open", cmd_open, "open any resource to the best view: YAML if editable, else a structured read-out")
    p.add_argument("targets", nargs="+", help="a file on disk, or an engine path like scr/st443/etc/st443_00m00n_e20.lot")
    p.add_argument("--arc", help="take it from this archive")
    p = add("fsm", cmd_fsm, "show an AI state machine (.fsm) as readable states, actions and conditions, "
                            "and what the game's own transition rules make of it")
    p.add_argument("targets", nargs="+", help="a .fsm or .fsm.yaml file, or an engine path like quest/q0005_b00.fsm")
    p.add_argument("--arc", help="take it from this archive")
    p.add_argument("-o", "--out", help="write the listing to this file")
    p.add_argument("--check", action="store_true",
                   help="only the checks: links never taken or leading nowhere, states never entered or never "
                        "left (exit 1 when a problem is found)")
    p.add_argument("--model", metavar="FILE",
                   help="write one machine as a NYR-Lang formal FSM model (nyrc formal fsm FILE)")
    p.add_argument("--level", type=int, default=0, help="--model: which machine (0 = the root; --check lists them)")
    p = add("tex", cmd_tex, "textures: show info, convert .tex <-> .dds (edit in any image tool), or apply a "
                            "preset (frost, lava, abyssal, weathered)")
    p.add_argument("action", choices=["info", "to-dds", "from-dds", "preset"])
    p.add_argument("--preset", choices=["frost", "lava", "abyssal", "weathered"], help="preset: the look")
    p.add_argument("--strength", type=float, default=1.0, help="preset: 0..1 (default 1)")
    p.add_argument("--seed", type=int, default=1, help="preset: the noise pattern (default 1)")
    p.add_argument("targets", nargs="+", help="a .tex/.dds file, or an engine path like "
                                              "skn/f/em/em0000/00/tex/em0000_00_BM.tex")
    p.add_argument("--arc", help="take it from this archive")
    p.add_argument("--like", help="from-dds: the original .tex whose format to keep "
                                  "(default: a .tex next to the .dds)")
    p.add_argument("-o", "--out", help="write here instead of next to the input")
    p = _takes_negatives(add("terrain", cmd_terrain, "Gransys terrain cells: where a stage model sits, and moving a "
                                                     "cell model or its collision between the cell's frame and the "
                                                     "world (the 'floating terrain' fix)"))
    p.add_argument("action", choices=["cells", "where", "check", "localize", "worldize"])
    p.add_argument("target", nargs="?", default="", help="where: an engine name, a file or a world position x,z; "
                                                           "check/localize/worldize: a cell's .mod or .sbc file")
    p.add_argument("--cell", help="the cell (47m35n) when the file name does not say it")
    p.add_argument("--force", action="store_true", help="localize a file that is not in world coordinates")
    p.add_argument("--json", action="store_true", help="cells: machine-readable")
    p.add_argument("-o", "--out", help="localize/worldize: where to write (default: next to the input)")
    p = add("mrl", cmd_mrl, "materials: show shaders/textures a model uses, or repoint a texture")
    p.add_argument("action", choices=["info", "retex"])
    p.add_argument("target", help="a .mrl file, or an engine path")
    p.add_argument("--old", help="retex: the texture name to replace (mrl info lists them)")
    p.add_argument("--new", help="retex: the new texture name")
    p.add_argument("--arc", help="take it from this archive")
    p.add_argument("-o", "--out")
    p = add("lmt", cmd_lmt, "animations (.lmt): motions, keyframes, driven bones; convert between DDDA and DDO")
    p.add_argument("action", choices=["info", "keys", "bones", "convert"])
    p.add_argument("targets", nargs="+", help="a .lmt file, or an engine path like motion/pl/m/m00/m0004_at/m0004_at.lmt")
    p.add_argument("--motion", type=int, help="keys: the motion slot (lmt info lists them)")
    p.add_argument("--limit", type=_count, default=12, help="info: motions shown; keys: keys shown per track")
    p.add_argument("--to", choices=["ddda", "ddo"], help="convert: the game to convert for")
    p.add_argument("--drop-bones", help="convert: remove the tracks of these joint ids, e.g. 55,150-154 (the "
                                        "player weapon joints the two games rig differently)")
    p.add_argument("--retarget", action="store_true",
                   help="convert: rebake the joints the two player bodies parent differently (the weapon joints "
                        "150-154), so weapons and their hit shapes keep the source game's place")
    p.add_argument("--src-body", help="convert --retarget: the source skeleton (.mod file or engine path; "
                                      "default the source game's player body)")
    p.add_argument("--dst-body", help="convert --retarget: the destination skeleton (default its player body)")
    p.add_argument("--retarget-joints", help="convert --retarget: only these joint ids, e.g. 150-154 (naming 55 "
                                             "forces it too)")
    p.add_argument("--arc", help="take it from this archive")
    p.add_argument("-o", "--out")
    p = add("learn", cmd_learn, "learn more about a Studio tab, a format or a field (English or Japanese)",
            game=False)
    p.add_argument("topic", nargs="?", help="a tab (tab:world), a format (gpl, lmt, csd), or a file path")
    p.add_argument("--field", help="one field of the format, e.g. mSetCountMax")
    p.add_argument("--lang", choices=["en", "ja"], default="en")
    p = add("skeleton", cmd_skeleton, "a model's joints, or two models' skeletons compared by joint id "
                                      "(what animations address)")
    p.add_argument("targets", nargs="+", help="one or two .mod files or engine paths")
    p.add_argument("--vs-game", choices=["ddda", "ddo"], help="the game the second model is in (default --game)")
    p.add_argument("--tolerance", type=float, default=0.5, help="offset difference that counts, model units")
    p.add_argument("--arc", help="take the first one from this archive")
    p = _takes_negatives(add("spawns", cmd_spawns, "list a layout's records; copy (add) or remove one inside a mod"))
    p.add_argument("action", choices=["list", "copy", "remove"])
    p.add_argument("layout", help="a layout, e.g. scr/st100/etc/st100_45m55n_e143.lot")
    p.add_argument("number", nargs="?", type=int, help="copy/remove: the record's number (spawns list)")
    p.add_argument("--mod", help="the mod that gets the change (list: show the mod's version)")
    p.add_argument("--at", help="copy: put the copy here, x,y,z")
    p = add("world", cmd_world, "the game's world, mapped: stages, rooms, groups, layouts, enemies, resource types")
    p.add_argument("action", nargs="?", default="overview",
                   choices=["overview", "stages", "stage", "enemies", "enemy", "group", "deps", "types", "build"])
    p.add_argument("rest", nargs="*", metavar="stage | enemy | stage type number | archive",
                   help="stage: 424; enemy: goblin or em0100; group: 424 e 5; deps: rom/enemy/em0200")
    p.add_argument("--rebuild", action="store_true", help="re-read the game (the map is cached)")
    p.add_argument("--lang", default="eng", choices=["eng", "fre", "ger", "ita", "spa", "jpn", "zht"],
                   help="language of the enemy and room names (default eng)")
    p.add_argument("--limit", type=_count, default=60)
    p.add_argument("--all", action="store_true", help="enemies: also those only named in group lists")
    p.add_argument("--json", action="store_true")
    p = _takes_negatives(add("encounter", cmd_encounter, "add N of an enemy to a stage as a new enemy group, e.g. "
                                                         "100 goblins"))
    p.add_argument("stage", help="the stage, e.g. 424 or st424 (riftstone world stages)")
    p.add_argument("enemy", nargs="+", help="the enemy: em0100, goblin, 'greater goblin' ...")
    p.add_argument("--count", type=int, required=True, help="how many in total")
    p.add_argument("--at", help="where: x,y,z, or group:N (the middle of enemy group N); on Online group:N or "
                                "group:LAYER:N of the stage's spawn groups (default: a group that has the enemy)")
    p.add_argument("--level", type=int, help="Online: the new enemies' level (default: the group's)")
    p.add_argument("--at-once", type=int, help="spawn points = how many at once (default: the count, at most 10, "
                                               "the enemy pool's size); more than this come as the horde refills. "
                                               "Online: grow the group's layout to this many points")
    p.add_argument("--spread", type=float, default=250.0, help="distance between spawn points (default 250)")
    p.add_argument("--group", type=int, help="the new group's number (default: the first free one, 0..294)")
    p.add_argument("--story", choices=["any", "pre", "post"],
                   help="when it exists: any time, before the Dragon, after it (default: like the nearby group)")
    p.add_argument("--like", type=int, help="copy this enemy group's conditions (e.g. its lot flag, so the new "
                                            "group appears exactly when that one does); default: the nearest group")
    p.add_argument("--skin", help="the placements wear this enemy skin, or these in turn (1,2); riftstone skin list; "
                                  "needs the enemy_skins plugin")
    p.add_argument("--hours", help="the first and the last whole hour the group exists, 0..23, e.g. 4,19 or 20,3 "
                                   "(past midnight); default: like the copied group")
    p.add_argument("--mod", help="the mod that gets it")
    p.add_argument("--no-ground", action="store_true",
                   help="keep the spawn points on flat rings at the spot's height (by default a walker's go onto the "
                        "stage's walkable ground, its navigation mesh, where the stage has one)")
    p.add_argument("--always", action="store_true",
                   help="the new group loads whenever the stage does: clear the copied group's lot-flag condition "
                        "(every group of stages 420-447 loads only under one)")
    p.add_argument("--dry-run", action="store_true", help="show the plan, write nothing")
    p = add("compat", cmd_compat, "Dragon's Dogma Online skills for Dark Arisen: convert what the compat plugin "
                                   "runs them with into a mod (pack), or list the skills it knows (list)", game=False)
    p.add_argument("compat_action", choices=["pack", "list"])
    p.add_argument("--skill", help="comma-separated skill programs (default: every one it knows)")
    p.add_argument("--mod", help="the mod folder to write (default: 'DDO Alchemist' in the installed mods' folder)")
    p.add_argument("--game", help="the Dark Arisen install (default: through Steam)")
    p.add_argument("--effects", action="store_true",
                   help="also convert the skills' effects (experimental: in game on 2026-09-26 they stopped "
                        "the game; without them a skill is its motions, shells and hits)")
    p = add("encounters", cmd_encounters, "every encounter a plan file lists (NYR-Lang's riftstone target writes "
                                          "them), in order")
    p.add_argument("plan", help="the plan (JSON, format riftstone-encounters/1)")
    p.add_argument("--mod", help="the mod that gets them")
    p.add_argument("--dry-run", action="store_true", help="show the plans, write nothing")
    p = add("nav", cmd_nav, "a navigation mesh: a stage's (where its AI walks, its doors, where a point stands) or a "
                            ".nav file (read and rebuilt byte for byte)")
    p.add_argument("target", help="a stage (424, st424) or a .nav file")
    p.add_argument("--at", help="x,y,z: is it on walkable ground, how much room, how far on foot from a door")
    p = add("dungeon", cmd_dungeon, "a whole dungeon for a stage: a mission of fights, a horde, a guardian and a boss, "
                                    "placed on the stage's walkable ground and checked; into a mod as encounters")
    p.add_argument("stage", help="the stage, e.g. 424 (any stage with a navigation mesh: riftstone nav <stage>)")
    p.add_argument("--seed", type=int, default=0, help="which dungeon (the same seed, the same dungeon; default 0)")
    p.add_argument("--pool", choices=["stage", "game"], default="stage",
                   help="choose from the enemies of this stage and those sharing its mesh (default), or the whole game")
    p.add_argument("--enemies", help="only these enemies, e.g. goblin,hobgoblin,cyclops")
    p.add_argument("--exclude", help="never these enemies, e.g. death")
    p.add_argument("--mission", help="a mission grammar (JSON, riftstone-mission/1; default: the built-in one)")
    p.add_argument("--spacing", type=float, default=15.0, help="metres between places (default 15)")
    p.add_argument("--at-once", type=int, default=10, help="at most this many spawn points per encounter (default 10, "
                                                           "the game's enemy pool without enemy_cap)")
    p.add_argument("--story", choices=["any", "pre", "post"], help="when the encounters exist (default: like the "
                                                                   "groups they copy)")
    p.add_argument("--hours", help="the first and the last whole hour the groups exist, e.g. 20,3")
    p.add_argument("--plan", help="also save the encounter plan (JSON) to this file")
    p.add_argument("--force", action="store_true", help="replace an existing --plan file")
    p.add_argument("--mod", help="write the encounters into this mod")
    p.add_argument("--dry-run", action="store_true", help="plan and check, write nothing")
    p.add_argument("--json", action="store_true", help="print the plan as JSON")
    p.add_argument("--rebuild", action="store_true", help="re-measure the enemies (bestiary) from the game")
    p.add_argument("--keep-lot-flags", action="store_true",
                   help="encounters copying a group that loads only under a lot flag keep it (by default their "
                        "copies load whenever the stage does)")
    p = add("waves", cmd_waves, "enemy waves: new groups that appear one after another, each once the group before "
                                "it is dead (docs/enemy-waves.md)")
    p.add_argument("stage", help="the stage, e.g. 320 or st320 (riftstone world stages)")
    p.add_argument("--after", type=int, help="the enemy group whose death brings the first wave (riftstone world stage)")
    p.add_argument("--wave", action="append", metavar="ENEMY:COUNT",
                   help="a wave, e.g. goblin:8 or \"skeleton mage:4\"; once for each wave, in order")
    p.add_argument("--at", help="where the waves stand: x,y,z or group:N (default: where the --after group stands)")
    p.add_argument("--spread", type=float, default=250.0, help="distance between spawn points (default 250)")
    p.add_argument("--like", type=int, help="copy this enemy group's areas and conditions (default: the --after group)")
    p.add_argument("--flags", action="store_true", help="only list the stage's lot flags: who uses each, which are free")
    p.add_argument("--mod", help="the mod that gets them")
    p.add_argument("--dry-run", action="store_true", help="show the plan, write nothing")
    from .ddo_solo import TIERS as _solo_tiers

    p = add("ddo", cmd_ddo, "Dragon's Dogma Online on the local server: solo = the Solo Balance mod (enemies and "
                            "pacing for one player with pawns); access = check missions for party-size gates and "
                            "open them for a lone player; dye = an item's colours, and its colour maps with a colour "
                            "baked in for Dark Arisen")
    p.add_argument("action", choices=["solo", "access", "dye"])
    p.add_argument("what", nargs="?", help="dye: the weapon or armour (item name, item id or model path)")
    p.add_argument("--colour", "--color", dest="colour",
                   help="dye: default (the item's own), material, red, green, blue, yellow, pink, black, a colour "
                        "number, #rrggbb, or #rrggbb,#rrggbb,#rrggbb (one per mask)")
    p.add_argument("--sex", choices=["male", "female"], help="dye: the body an armour for either sex is made for "
                                                             "(default male)")
    p.add_argument("--out", help="dye: the folder that gets the baked colour maps")
    p.add_argument("--as", dest="as_", choices=["tex", "png", "dds"], help="dye: file kind (default tex)")
    p.add_argument("--mod", help='the mod to write (created when missing), e.g. "Solo Balance"; required for solo, '
                                 "optional for access (left out, access only reports)")
    p.add_argument("--fill-pawns", action="store_true", help="access: also raise max_pawns so pawns fill the party a "
                                                             "mission was balanced for (never lowers it)")
    for _t in _solo_tiers:
        p.add_argument(f"--{_t.name}-hp", type=float, help=f"{_t.name} enemies' HP factor for one player "
                                                           f"(default {_t.hp:g})")
        p.add_argument(f"--{_t.name}-attack", type=float, help=f"{_t.name} enemies' attack factor "
                                                               f"(default {_t.attack:g})")
    p.add_argument("--tiers", help="which enemies get solo twins: some of field,boss,exm (default all three)")
    p.add_argument("--offsets", help="twin id = id + offset, for field,boss,exm (default "
                                     + ",".join(str(_t.offset) for _t in _solo_tiers) + ")")
    p.add_argument("--keep-part-hp", action="store_true", help="leave the body parts' HP (mHpSub) as it is")
    p.add_argument("--no-settings", action="store_true", help="leave the server's settings alone (no EXP/gold changes)")
    p.add_argument("--set", action="append", metavar="NAME=VALUE",
                   help="also change a server setting, e.g. EnemyExpModifier=2 (repeat for more)")
    p.add_argument("--dry-run", action="store_true", help="show what it would write, write nothing")
    p = add("skin", cmd_skin, "enemy skins: chosen placements wear another texture set (e.g. DDO's White Chimera)")
    p.add_argument("action", choices=["make", "list", "export"])
    p.add_argument("family", nargs="?", help="make/export: the enemy family (chimera)")
    p.add_argument("number", nargs="?", type=int, help="make/export: the skin number, 1..99")
    p.add_argument("--textures", help="make: a folder of albedo maps named like the vanilla ones (.png, .dds or .tex; "
                                      "Dragon's Dogma Online textures are converted)")
    p.add_argument("--out", help="export: the folder that gets the skin's maps as pictures to edit")
    p.add_argument("--as", dest="as_", choices=["png", "dds"], help="export: picture format (default png)")
    p.add_argument("--title", help="make: a name for the skin")
    p.add_argument("--source", help="make: where the textures come from")
    p.add_argument("--mod", help="the mod that holds the skin")
    p = add("monster", cmd_monster, "monsters between Dark Arisen and Online: each enemy's counterpart in the other "
                                    "game, measured (skeleton, meshes, textures), and converting one into the other "
                                    "where the bodies match (docs/monsters.md)")
    p.add_argument("action", choices=["list", "show", "convert"])
    p.add_argument("source", nargs="*", metavar="enemy",
                   help="show/convert: an enemy by name or id, e.g. \"White Chimera\", EM015202, em5200, wolves")
    p.add_argument("--into", help="convert: the other game's enemy whose model it replaces (show: compare with it)")
    p.add_argument("--mod", help="convert: the mod that gets it; its game is the destination")
    p.add_argument("--as-skin", type=int, help="convert: a Dragon's Dogma Online chimera variant as Dark Arisen chimera "
                                               "skin N (1..99) instead: only the placements that wear it change")
    p.add_argument("--verdict", choices=["same body", "same skeleton", "partial", "none"], help="list: only these")
    p.add_argument("--textures", action="store_true", help="list: also compare the texture sheets of every pair "
                                                           "(slower; cached)")
    p.add_argument("--rebuild", action="store_true", help="read both games again (the census is cached)")
    p.add_argument("--dry-run", action="store_true", help="convert: show the plan, write nothing")
    p.add_argument("--json", action="store_true", help="list: machine-readable")
    p = add("package", cmd_package, "share mods with no game data: a zip of deltas and recipes that each player's "
                                    "Riftstone turns into the mods from their own game (package install), or "
                                    "check one; --plugins-only: the loader and plugins for players without Riftstone")
    p.add_argument("items", nargs="*", metavar="mod | install ZIP | check ZIP",
                   help="the mod folders, in priority order like install; or: install <zip>, check <zip>")
    p.add_argument("--out", help="make, --plugins-only: the .zip to write, e.g. dist\\my-mods.zip")
    p.add_argument("--plugins-only", action="store_true", dest="plugins_only",
                   help="a loader + plugins zip with no mods and no game data, to unzip into the game folder "
                        "(Riftstone's stable built plugins unless --plugin names them; experimental ones only by name)")
    p.add_argument("--ninput", nargs="?", const="built", metavar="XINPUT1_3.DLL",
                   help="--plugins-only: add Ninput (EXPERIMENTAL) under optional\\ninput, off until a player copies it "
                        "(default: native\\ninput's own build)")
    p.add_argument("--plugin", action="append", help="make: a native plugin to include (.asi; its .ini comes along); "
                                                     "repeat for more")
    p.add_argument("--name", help="make: the package's title (default: the mods' names)")
    p.add_argument("--force", action="store_true", help="make: replace the .zip if it exists")
    p.add_argument("--into", help="install: the folder of mods to make them in (default: Studio's mods folder)")
    p.add_argument("--json", action="store_true", help="check: machine-readable")
    p = add("items", cmd_items, "items: list, add (new), stats and set (an item's fields), sell (shop), craft (recipe), "
                                "drop (sets, drop)")
    p.add_argument("action", choices=["list", "new", "stats", "set", "shop", "recipe", "sets", "drop"])
    p.add_argument("rest", nargs="*", metavar="words | name | item",
                   help="list: words to look for; new: the new item's name; stats/shop/sets/drop: the item (id or name); "
                        "set: the item then field=value pairs (mAttack=80); recipe: the two ingredients")
    p.add_argument("--makes", help="recipe: what the two ingredients make")
    p.add_argument("--count", type=int, default=1, help="recipe: how many it makes (default 1)")
    p.add_argument("--set", type=int, help="drop: the set id (items sets <item> finds sets)")
    p.add_argument("--percent", dest="weight_pct", type=int, help="drop: the chance in percent, taken from the set's "
                                                                    "chance of nothing when it has one")
    p.add_argument("--table", default="enemy", choices=["enemy", "reward"],
                   help="sets/drop: enemy drops (ItemEmListSetTbl) or rewards and gathering (itemSetTbl)")
    p.add_argument("--mod", help="the mod (new: gets the item; list: shows the mod's items)")
    p.add_argument("--like", help="new: the item the new one copies (id or name), e.g. Greenwarish")
    p.add_argument("--description", help="new: the item's description")
    p.add_argument("--id", type=int, help="new: which unused slot to use (default: the first)")
    p.add_argument("--buy", type=int, help="new: buy price in gold (sell defaults to 40%%)")
    p.add_argument("--sell", type=int, help="new: sell price in gold")
    p.add_argument("--weight", type=float, help="new: weight in kg")
    p.add_argument("--shop", help="new/shop: the shop that sells it, e.g. n007ShopList")
    p.add_argument("--stock", type=int, default=3, help="new/shop: how many the shop has (default 3)")
    p.add_argument("--restock", type=int, default=5, help="new/shop: the shop's restock setting (default 5)")
    p.add_argument("--free", action="store_true", help="list: show the unused slots instead")
    p.add_argument("--limit", type=_count, default=60)
    p = add("text", cmd_text, "find a line of game text, or add a line to a text file in every language")
    p.add_argument("action", choices=["find", "add"])
    p.add_argument("rest", nargs="+", metavar="words | file line",
                   help='find: the words to look for; add: a text file and the line, e.g. '
                        'id/npc_wind/stage/st100_eng.gmd "A new line."')
    p.add_argument("--lang", help="find: which language to search, or 'all' (default english; all for DDO)")
    p.add_argument("--limit", type=_count, default=50)
    p.add_argument("--mod", help="add: the mod that gets the line")
    p.add_argument("--label", help="add: give the new line a label")
    p.add_argument("--one-language", action="store_true",
                   help="add: only this file, not its other language versions")
    p = add("info", cmd_info, "describe an archive, parameter file, folder or mod", game=False)
    p.add_argument("files", nargs="+")
    p = add("doctor", cmd_doctor, "check the setup: game, build, loader, installed mods")
    p.add_argument("--verify", action="store_true", help="hash every original archive (takes a minute)")
    p = add("loader", cmd_loader, "the loader (overlay, crash reports, safe mode, plugins): status, install, remove, "
            "plugin, safe-mode")
    p.add_argument("action", choices=["status", "install", "remove", "plugin", "safe-mode", "d3d9"])
    p.add_argument("plugin_action", nargs="?", choices=["list", "add", "remove", "release", "status", "off"],
                   help="with 'plugin': list (the default), add <file>, remove <name>, release <name> (from "
                   "quarantine); with 'safe-mode': status (the default) or off; with 'd3d9': status (the default), add <DXVK release, folder "
                   "or x32 d3d9.dll> (into riftstone\\dxvk, named in [d3d9] chain) or off")
    p.add_argument("file", nargs="?", help="the .asi/.dll to add, or the plugin name to remove or release; with "
                   "'d3d9 add': DXVK's release")
    p = add("laa", cmd_laa, "is the game's exe (or any exe) large-address aware (4 GB, not 2 GB of address space); "
            "--copy writes a copy with the flag set")
    p.add_argument("exe", nargs="?", help="an exe or DLL (default: the game's)")
    p.add_argument("--copy", help="write a copy with the flag set here (never the file itself)")
    p = add("live", cmd_live, "what the running game is doing: memory headroom, frame times, enemies, the loader")
    p.add_argument("--watch", action="store_true", help="refresh every second until Ctrl+C")
    p.add_argument("--json", action="store_true", help="everything as JSON")
    p = add("crash", cmd_crash, "explain the newest crash, fatal-error or hang report (or a named one)")
    p.add_argument("report", nargs="?", help="a report name from riftstone\\logs (default: the newest)")
    p.add_argument("--list", action="store_true", help="list the reports instead")
    p.add_argument("--json", action="store_true", help="the parsed report and the explanation as JSON")
    p = add("playtest", cmd_playtest, "the last play session checked item by item from its logs (the loader, "
            "plugins, the F10 panel, the texture guard, how it ended); guard-mod: a test mod for the texture guard")
    p.add_argument("action", nargs="?", choices=["check", "guard-mod"], default="check")
    p.add_argument("--previous", action="store_true", help="check the session before the last one")
    p.add_argument("--mod", help="guard-mod: the mod folder to write (made if it is not a mod yet)")
    p.add_argument("--json", action="store_true", help="every item as JSON")
    p = add("saves", cmd_saves, "your save: list every copy of it (the loader's and the save_backup plugin's), "
                                "copy it now, put a copy back, or see and raise your main pawn's enemy knowledge", aliases=("save",))
    p.add_argument("action", nargs="?", choices=["list", "backup", "restore", "knowledge"], default="list")
    p.add_argument("which", nargs="?", help="restore: a copy's number from 'saves list' (1 = the account's newest) "
                                            "or its name")
    p.add_argument("--yes", action="store_true", help="with 'restore' or 'knowledge --grant': really do it")
    p.add_argument("--grant", action="store_true", help="with 'knowledge': raise the main pawn's knowledge "
                                                        "counters to the game's top thresholds")
    p.add_argument("--account", help="the Steam account (its folder under Steam\\userdata) when there are several")
    p.add_argument("--save", help="the save file (default: each account's Steam\\userdata\\...\\remote\\DDDA.sav)")
    p.add_argument("--folder", help="where the save_backup plugin's copies are (default: save_backup.ini's Folder, "
                                    "else %%LOCALAPPDATA%%\\Riftstone\\saves)")
    p.add_argument("--json", action="store_true")
    p = add("studio", cmd_studio, "open Riftstone Studio in your browser")
    p.add_argument("--port", type=_port, default=0, help="0 (the default): any free port")
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--workspace", help="folder that holds your mods (default: the mods folder, as riftstone mods "
                                       "shows it)")
    ap.commands = frozenset(sub.choices)
    return ap


def cmd_studio(args) -> int:
    from . import studio

    return studio.serve(args)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        ap = build_parser()
        # Anything that is not a command is a file or folder dropped on Riftstone.cmd.
        if argv and argv[0] not in ap.commands and not argv[0].startswith("-"):
            return cmd_auto(argv)
        args = ap.parse_args(argv)
        if not getattr(args, "fn", None):
            ui.banner(f"v{__version__}", mark=True)
            print()
            print(QUICK)
            return 0
        _mod_names(args)
        return args.fn(args)
    except RiftError as e:
        ui.fail(str(e))
        return 2
    except OSError as e:        # a file named that is missing or a folder, one in use, a folder that is a file
        ui.fail(_os_error(e))
        return 2
    except KeyboardInterrupt:
        ui.fail("stopped")
        return 130
    finally:
        while _OPENED:
            _OPENED.pop().close()
