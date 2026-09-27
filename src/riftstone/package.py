"""A mod package: what a player needs to rebuild the mods from their own game -- and no game data.

    riftstone package "mods\\DDO Chimeras" "mods\\Gran Soren Horde" [--plugin x.asi] --out dist\\x.zip
    riftstone package install x.zip [--into <mods folder>]
    riftstone package check x.zip

A package holds no file of either game.  For each resource a mod changes or adds it carries a delta
(delta.py): copies from resources the player already has -- the game's own resource of that name, the
game's other resources of that kind in the same archive, or what a recipe makes -- plus the bytes the
author wrote.  Content that came from the other game (a Dragon's Dogma Online chimera skin, a port) and
files a fixed rule makes from the game's own (a texture preset) travel as recipes (sources.py): the
player's Riftstone makes them again from the player's own copies of the games.  Every base, every recipe's
file and every rebuilt file is checked by SHA-256.

The zip:

    riftstone-package.json          what is inside, the games it needs, each member's SHA-256
    README - <name>.txt             what it is, how to install it, the notice
    mods/<folder>/riftstone-mod.json, skins.json    the mods' own descriptions
    mods/<folder>/recipes.json      the recipes the mod needs (sources.py)
    mods/<folder>/patch.json        per file: where it goes, its size and SHA-256, its bases, its delta
    mods/<folder>/deltas/<n>.rsd    the deltas
    plugins/<file>                  native plugins the maker added (and their .ini)
    LICENSE-Riftstone.txt           when it carries Riftstone's own plugins

``build`` refuses to carry the other game's content, so a file marked as the other game's must come out of
its recipe (almost) unchanged.  ``install`` writes new mod folders only (never over one that exists), and
``check`` reads a package without a game.

``build_plugins`` (``riftstone package --plugins-only``) makes the other kind of zip, for players without
Riftstone: the loader and plugins, no mods and no game data, unzipped into the game folder.  Writes one
local file; publishes nothing.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import textwrap
import time
import zipfile
import zlib
from pathlib import Path

from . import __version__, delta, ipaudit, loader, sources, typemap
from . import mod as modlib
from .errors import RiftError
from .runtime import ini_text

SCHEMA = "riftstone.package/2"
PATCH_SCHEMA = "riftstone.patch/1"
MANIFEST = "riftstone-package.json"
PLUGIN_SUFFIXES = (".asi", ".dll")
RESERVED = ("dinput8.dll", "riftstone_loader.dll")
# Riftstone's own plugins (native/plugins, MIT); any other plugin in a package keeps its author's terms.
OWN_PLUGINS = ("enemy_cap", "enemy_skins", "lod_tuner", "inclination_lock", "save_backup", "free_sprint",
               "draw_distance", "six_skill_warrior")
MAX_MEMBER = 256 * 1024 * 1024          # a package member, unpacked
MAX_TOTAL = 1024 * 1024 * 1024          # everything in a package, unpacked
MAX_MEMBERS = 50_000
NEIGHBOURS = 16                          # other resources of a kind tried as bases for a new file
NEIGHBOUR_BYTES = 48 * 1024 * 1024
FOLDER = re.compile(r"[\w .()'&+-]{1,64}")
WINDOWS_DEVICES = {"CON", "PRN", "AUX", "NUL"} | {f"{p}{i}" for p in ("COM", "LPT") for i in range(1, 10)}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _storable(name: str) -> bool:
    """A file name a zip keeps as it is: UTF-8, no control characters (zipfile cuts a name at NUL)."""
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return all(c >= " " for c in name)


def _file_title(title: str) -> str:
    """The title as part of a file name that works on Windows and in a zip."""
    t = "".join(c for c in title.encode("utf-8", "replace").decode("utf-8")
                if c >= " " and c not in '\\/:*?"<>|' and c != "�")
    return t.strip(" .")[:80].strip(" .") or "mods"


def _folder_ok(name) -> bool:
    return (isinstance(name, str) and bool(FOLDER.fullmatch(name)) and name == name.strip()
            and not name.endswith(".") and name.split(".")[0].upper() not in WINDOWS_DEVICES)


def _folder_for(m: modlib.Mod, taken: set[str]) -> str:
    base = m.root.name if _folder_ok(m.root.name) else re.sub(r"[^\w .()'&+-]", "_", m.name)[:60].strip(" .") or "mod"
    if not _folder_ok(base):
        base = "mod"
    name, n = base, 2
    while name.lower() in taken:
        name, n = f"{base[:58]} ({n})", n + 1
    taken.add(name.lower())
    return name


def _about(plugin: Path) -> str:
    """What a plugin does: the first comment line of its .ini ("; name -- what it does"), else the plugin
    catalog's line for one of Riftstone's own (most of their .ini files open with where to keep them)."""
    from .plugins import CATALOG
    ini = plugin.with_suffix(".ini")
    try:
        first = next((ln.strip() for ln in ini_text(ini.read_bytes()).splitlines() if ln.strip()), "")
    except OSError:
        first = ""
    if first.startswith(";") and " -- " in first:
        return first.split(" -- ", 1)[1].strip()[:160]
    return CATALOG.get(plugin.stem.lower(), {}).get("summary", "")[:160]


def _license() -> bytes:
    here = Path(__file__).resolve().parents[2]
    for p in (here / "LICENSE", here / "LICENSE.txt"):
        if p.is_file():
            return p.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    return b"MIT License\r\n\r\nCopyright (c) 2026 NryQ\r\n"


def _build_of(game) -> str | None:
    """The SHA-256 of this game's executable when it is the build Riftstone knows (the plugins' build)."""
    from .install import sha256_file
    try:
        digest = sha256_file(game.exe)
    except OSError:
        return None
    return digest if digest == game.known_build_sha256 else None


# -- the game's resources, as bases ---------------------------------------------------------------
class GameFiles:
    """Decoded resources of one game's archives (their original bytes), read once."""

    KEEP = 384 * 1024 * 1024                          # archives kept in memory at once (stage archives are ~95 MB)

    def __init__(self, game, idx):
        self.game, self.idx = game, idx
        self._archives: dict[str, tuple[object, int]] = {}

    def archive(self, name: str):
        from . import arc
        held = self._archives.pop(name, None)
        if held is None:
            path = self.game.vanilla_arc(name)
            held = (arc.Archive.read(path), path.stat().st_size)
        self._archives[name] = held                    # the newest last; the oldest go first
        while len(self._archives) > 1 and sum(size for _, size in self._archives.values()) > self.KEEP:
            self._archives.pop(next(iter(self._archives)))
        return held[0]

    def entry(self, archive: str, name: bytes, type_id: int) -> bytes | None:
        e = self.archive(archive).find(name, type_id)
        return e.data() if e is not None else None

    def holders(self, name: bytes, type_id: int) -> list[str]:
        return self.idx.archives_with(name, type_id)

    def neighbours(self, archive: str, name: bytes, type_id: int) -> list[tuple[bytes, bytes]]:
        """The archive's other resources of this kind, the likeliest sources first: the same file name, then
        the longest shared start of the path."""
        base = name.rsplit(b"\\", 1)[-1].lower()
        rows = [e for e in self.archive(archive).entries if e.type_id == type_id and e.name != name]

        def score(e):
            other = e.name.lower()
            shared = len(os.path.commonprefix([other, name.lower()]))
            return (other.rsplit(b"\\", 1)[-1] != base, -shared, other)

        out, total = [], 0
        for e in sorted(rows, key=score)[:NEIGHBOURS]:
            if total + e.size > NEIGHBOUR_BYTES:
                break
            out.append((e.name, e.data()))
            total += e.size
        return out


def _game_ref(kind: str, archive: str, name: bytes, type_id: int, data: bytes) -> dict:
    return {"game": kind, "archive": archive, "name": name.decode("latin-1"), "type": type_id,
            "size": len(data), "sha256": _sha(data)}


def _delta(target: bytes, candidates: list[tuple[dict, bytes]]) -> tuple[list[tuple], list[dict]]:
    """The delta over the bases it uses (unused ones dropped and the copies renumbered)."""
    ops = delta.make(target, [b for _, b in candidates])
    used = sorted(delta.bases_used(ops))
    renumber = {old: new for new, old in enumerate(used)}
    ops = [(op[0], renumber[op[1]], op[2], op[3]) if op[0] == delta.COPY else op for op in ops]
    return ops, [candidates[i][0] for i in used]


def _allowance(size: int) -> int:
    """The new bytes a file marked as the other game's may still hold (an author's small edits)."""
    return min(4096, max(64, size // 100))


def _server_base(game, rel: str) -> tuple[dict, bytes] | None:
    """The local server's own copy of an asset file (the original, if Riftstone replaced it)."""
    from . import ddo, install
    assets = ddo.need_assets(game)
    for p in (install._server_target(game.state_dir / "server-vanilla", rel), install._server_target(assets, rel)):
        if p.is_file():
            data = p.read_bytes()
            return {"server": rel, "size": len(data), "sha256": _sha(data)}, data
    return None


class _Prints:
    """Texture fingerprints of both games (texprints.py), made when first needed; None for a game that is
    not on this PC."""

    def __init__(self, games: sources.Games, note=None):
        self.games, self.note, self.by_kind = games, note, {}

    def find(self, kind: str, data: bytes):
        from . import texprints
        if kind not in self.by_kind:
            try:
                self.by_kind[kind] = texprints.Prints(self.games.game(kind), self.games.index(kind))
            except RiftError:
                self.by_kind[kind] = None
        p = self.by_kind[kind]
        return p.find(data, self.note) if p is not None else None


def _patch_mod(m: modlib.Mod, games: sources.Games, files: GameFiles, canonical: dict[str, str],
               refusals: list[str], prints: _Prints, progress=None):
    """(patch entries, their delta blobs, the recipes used, new bytes) for one mod."""
    from .game import KINDS
    kind = m.game
    other = "ddo" if kind == "ddda" else "ddda"
    TEX = typemap.BY_EXT["tex"]
    changes = modlib.collect(m)
    for c in changes:
        if c.arc is not None:
            c.arc = canonical.get(c.arc.lower(), c.arc)      # one spelling per archive, as plan() uses
    present = {sources.norm(c.source) for c in changes}
    recipes = sources.recipes_for(m.root, present)
    try:
        made = sources.replay(recipes, games) if recipes else {}
    except RiftError as e:
        raise RiftError(f"{m.name}: a recipe could not be made again on this PC, so the package could not carry it: "
                        f"{e}") from None
    foreign = sources.foreign_files(m.root, recipes)
    entries, blobs, new_total = [], [], 0
    for c in changes:
        key = sources.norm(c.source)
        candidates: list[tuple[dict, bytes]] = []
        holders = files.holders(c.name, c.type_id)          # archives with the game's own resource of that name
        homes = [c.arc] if c.arc else holders                # where the file goes
        first = c.arc if c.arc in holders else (holders[0] if holders else None)
        data = files.entry(first, c.name, c.type_id) if first else None
        if data is not None:
            candidates.append((_game_ref(kind, first, c.name, c.type_id, data), data))
        if key in made:
            candidates.append(({"made": key, "size": len(made[key]), "sha256": _sha(made[key])}, made[key]))
        ops, refs = _delta(c.data, candidates)
        if delta.new_bytes(ops) > max(64, len(c.data) // 100) and homes:
            more = [(_game_ref(kind, homes[0], n, c.type_id, d), d)
                    for n, d in files.neighbours(homes[0], c.name, c.type_id)]
            ops2, refs2 = _delta(c.data, candidates + more)
            if delta.new_bytes(ops2) < delta.new_bytes(ops):
                ops, refs = ops2, refs2
        new = delta.new_bytes(ops)
        why = foreign.get(key)
        if c.type_id == TEX and new > max(16384, len(c.data) // 2) and key not in made:
            # a texture that is mostly new: one of the game's own under another name (a base), or the other
            # game's (never carried), whatever the mod's records say
            hit = prints.find(kind, c.data)
            if hit is not None:
                ops, refs = _delta(c.data, candidates + [(_game_ref(kind, hit[0], hit[1], TEX, hit[2]), hit[2])])
                new = delta.new_bytes(ops)
            else:
                hit = prints.find(other, c.data)
                if hit is not None:
                    why = f"{KINDS[other]['title']} (it is {hit[1].decode('latin-1')} of {hit[0]}.arc)"
        if why and new > _allowance(len(c.data)):
            refusals.append(f"{m.name}: {key} holds {why}'s content ({new:,} of its {len(c.data):,} bytes "
                            "would travel in the package). A package carries only a recipe for that: make the file "
                            "with 'riftstone monster convert', 'port' or a Dragon's Dogma Online skin import, and "
                            "leave it as they make it")
        n = len(blobs)
        blobs.append(delta.encode(ops, len(c.data)))
        entries.append({"path": key, "yaml": c.source.lower().endswith(".yaml"), "size": len(c.data),
                        "sha256": _sha(c.data), "bases": refs, "delta": f"deltas/{n}.rsd", "new_bytes": new})
        new_total += new
        if progress:
            progress.advance(1, key)
    game = games.game(kind)
    for rel, data in sorted(modlib.collect_server(m).items()):
        base = _server_base(game, rel)
        ops, refs = _delta(data, [base] if base else [])
        new = delta.new_bytes(ops)
        n = len(blobs)
        blobs.append(delta.encode(ops, len(data)))
        entries.append({"path": f"server/{rel}", "size": len(data), "sha256": _sha(data), "bases": refs,
                        "delta": f"deltas/{n}.rsd", "new_bytes": new})
        new_total += new
    return entries, blobs, recipes, new_total


def _out_path(out) -> Path:
    out = Path(out)
    if out.suffix.lower() != ".zip":
        raise RiftError("the package is a .zip file: give --out a name ending in .zip")
    if any(c < " " or c in '<>"|?*' for c in str(out)):
        raise RiftError(f"{str(out)!r} cannot be a file name on Windows; choose another --out")
    return out


def _plugin_files(plugins: list[Path]) -> tuple[list[Path], dict[str, Path]]:
    """The plugins, checked, and their settings: one .ini for x.asi and x.dll alike (one folder holds them)."""
    plugin_files = []
    for p in plugins:
        p = Path(p)
        if not _storable(p.name):
            raise RiftError(f"{p.name!r}: that file name cannot be stored in a zip; rename the plugin")
        if p.suffix.lower() not in PLUGIN_SUFFIXES or not p.is_file():
            raise RiftError(f"{p}: a plugin is an existing .asi or .dll file")
        if p.name.lower() in RESERVED:
            raise RiftError(f"{p.name} is the loader's own name; it cannot be a plugin")
        plugin_files.append(p)
    if len({p.name.lower() for p in plugin_files}) != len(plugin_files):
        raise RiftError("two plugins have the same file name")
    inis: dict[str, Path] = {}
    for p in plugin_files:
        ini = p.with_suffix(".ini")
        if not ini.is_file():
            continue
        seen = inis.setdefault(ini.name.lower(), ini)
        if seen != ini and seen.read_bytes() != ini.read_bytes():
            raise RiftError(f"{seen} and {ini} are different settings for one name; keep one of them")
    return plugin_files, inis


def _readme(title: str, kind: str, mods: list, plugins: list[tuple[str, str]], needs: list[str],
            per_mod: list[tuple[str, int, int]], build_ok: bool) -> str:
    from .game import KINDS
    game_title = KINDS[kind]["title"]
    lines = [title, "=" * len(title), ""]
    lines += [f"Mods for {game_title}, made with Riftstone. This package holds no game files: Riftstone",
              "rebuilds the mods on your PC from your own copy of the game"
              + (" and your own copy of " + KINDS["ddo" if kind == "ddda" else "ddda"]["title"] if len(needs) > 1 else "")
              + ",", "and checks every file it makes against the author's.", "", "What is inside"]
    for m in mods:
        by = f" by {m.author}" if m.author else ""
        lines.append(f"  * {m.name} {m.version}{by}" + (f" -- {m.description}" if m.description else ""))
    for p, about in plugins:
        lines.append(f"  * plugin {p}" + (f" -- {about}" if about else ""))
    lines += ["", "What you need",
              f"  * {game_title} ({KINDS[kind]['build']}" + (")" if build_ok else ", the build the author used)")]
    for k in needs:
        if k != kind:
            lines.append(f"  * {KINDS[k]['title']} ({KINDS[k]['build']}): some of these mods are made from it on your PC")
    lines += [f"  * Riftstone {__version__} or newer (free, MIT)", "",
              "Install",
              "  1. Close the game.",
              f"  2. Riftstone.cmd package install \"<this zip>\"   (or Studio: Mods, Install a package)",
              "     It makes each mod folder from your game files and checks each file by its SHA-256.",
              "  3. Turn the mods on in Studio, or: Riftstone.cmd install \"<mods folder>\\<mod>\" ...",
              "     Dark Arisen mods need the Riftstone loader (Riftstone.cmd loader install), which serves them",
              "     from riftstone\\overlay: the game's own files are never changed."]
    if plugins:
        lines += ["  4. The plugins are copied next to the mods (a _plugins folder); add each with",
                  "     Riftstone.cmd loader plugin add \"<file>\"."]
    lines += ["", "Remove", "  Riftstone.cmd restore (or turn the mods off in Studio), then delete the mod folders.", "",
              "What the author wrote"]
    for name, files, new in per_mod:
        lines.append(f"  * {name}: {files} file(s); {new:,} bytes of the author's own (numbers, names, pictures), "
                     "the rest made from your game")
    from . import legal
    wrap = lambda s: textwrap.wrap(s, 100, initial_indent="  ", subsequent_indent="  ")   # noqa: E731
    lines += ["", "Legal"] + wrap(legal.DISCLAIMER) + wrap(
        "This package and Riftstone are free: never sell them or put them behind a paywall.") + wrap(
        "If the game crashes with mods on, the report in riftstone\\logs is for the mods' authors and Riftstone, "
        "not for Capcom's support.")
    others = [p for p, _ in plugins if Path(p).stem.lower() not in OWN_PLUGINS]
    if any(Path(p).stem.lower() in OWN_PLUGINS for p, _ in plugins):
        lines += ["  Riftstone's own plugins are free software under the MIT License (LICENSE-Riftstone.txt)."]
    if others:
        lines += [f"  Not Riftstone's: {', '.join(others)}. Their authors' own terms apply to them."]
    lines += ["", f"Made with Riftstone {__version__} on {time.strftime('%Y-%m-%d')}."]
    return "\r\n".join(lines) + "\r\n"


def build(games: sources.Games, mod_roots: list[Path], plugins: list[Path], out: Path, name: str | None = None,
          progress=None, note=None) -> dict:
    """Write the package; returns what went in."""
    out = _out_path(out)
    if not mod_roots:
        raise RiftError("name at least one mod to package (a loader + plugins zip is build_plugins, --plugins-only)")
    mods = [modlib.Mod.load(Path(r)) for r in mod_roots]
    names = [m.root.resolve() for m in mods]
    if len(set(names)) != len(names):
        raise RiftError("a mod is listed twice")
    kinds = {m.game for m in mods}
    if len(kinds) != 1:
        raise RiftError("a package is for one game: package the Dark Arisen and the Online mods apart")
    kind = kinds.pop()
    for m in mods:
        loose = modlib.collect_loose(m)
        if any(rel.startswith(f"{modlib.PROGRAMS_DIR}/") and rel.endswith(".skills") for rel in loose):
            raise RiftError(f"{m.name} was made by 'riftstone compat pack' from this computer's own copy of Dragon's "
                            "Dogma Online: its files are converted from Capcom's, for this computer only. A player "
                            "makes their own with 'riftstone compat pack' (the compat plugin itself can be packaged)")
        if loose:
            raise RiftError(f"{m.name} has loose files (loose/), which a package does not carry yet")
    plugin_files, inis = _plugin_files(plugins)
    if plugin_files and kind != "ddda":
        raise RiftError("plugins are for Dark Arisen (the loader's plugins folder)")

    game, idx = games.game(kind), games.index(kind)
    p = modlib.plan(game, idx, mods)
    modlib.check_plan(p)
    files = GameFiles(game, idx)
    canonical = {game.arc_name(f).lower(): game.arc_name(f) for f in game.archives()}
    title = (name or " + ".join(m.name for m in mods)).encode("utf-8", "replace").decode("utf-8")
    taken: set[str] = set()
    parts, refusals, needs, per_mod = [], [], {kind}, []
    prints = _Prints(games, note)
    for m in mods:
        folder = _folder_for(m, taken)
        entries, blobs, recipes, new_total = _patch_mod(m, games, files, canonical, refusals, prints, progress)
        for r in recipes:
            needs.add("ddo" if r["kind"] == "ddo-skin" else r["args"].get("src", kind) if r["kind"] == "port"
                      else r["args"].get("on", {}).get("game", kind))
        parts.append((m, folder, entries, blobs, recipes, new_total))
        per_mod.append((m.name, len(entries), new_total))
    if refusals:
        raise RiftError("this package would carry the other game's content:\n" + "\n".join(refusals[:10])
                        + (f"\n... and {len(refusals) - 10} more" if len(refusals) > 10 else ""))
    if not any(entries for _, _, entries, _, _, _ in parts):
        raise RiftError("these mods change nothing in the game; there is nothing to package")

    members: dict[str, str] = {}
    tmp = out.with_name(out.name + ".riftstone-tmp")
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise RiftError(f"cannot make the folder for {out}: {e.strerror or e}") from None
    readme = f"README - {_file_title(title)}.txt"
    build_ok = _build_of(game) is not None
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            def put(path: str, data: bytes) -> None:
                found = ipaudit.scan_bytes(path, data)           # the last guard: no game file, key or notation
                if found:
                    raise RiftError("the package would carry what it must not: " + "; ".join(found))
                z.writestr(zipfile.ZipInfo(path, time.localtime()[:6]), data, zipfile.ZIP_DEFLATED)
                members[path] = _sha(data)

            def put_json(path: str, value) -> None:
                put(path, (json.dumps(value, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))

            for m, folder, entries, blobs, recipes, _ in parts:
                base = f"mods/{folder}/"
                put_json(base + modlib.MOD_FILE, {"schema": modlib.SCHEMA, "name": m.name, "version": m.version,
                                                  "author": m.author, "description": m.description,
                                                  "priority": m.priority, "game": m.game})
                if (m.root / "skins.json").is_file():
                    from . import skins
                    put_json(base + "skins.json", skins.read_manifest(m.root))
                put_json(base + "recipes.json", {"schema": sources.SCHEMA, "recipes": recipes, "foreign": {}})
                put_json(base + "patch.json", {"schema": PATCH_SCHEMA, "files": entries})
                for e, blob in zip(entries, blobs):
                    put(base + e["delta"], blob)
            own = False
            for f in plugin_files:
                put(f"plugins/{f.name}", f.read_bytes())
                own = own or f.stem.lower() in OWN_PLUGINS
            for ini in inis.values():                            # once for x.asi and x.dll alike
                put(f"plugins/{ini.name}", ini.read_bytes())
            if own:
                put("LICENSE-Riftstone.txt", _license())
            text = _readme(title, kind, mods, [(f.name, _about(f)) for f in plugin_files], sorted(needs), per_mod,
                           build_ok)
            put(readme, text.encode("utf-8", "replace"))
            manifest = {"schema": SCHEMA, "name": title, "made_with": f"Riftstone {__version__}",
                        "made": time.strftime("%Y-%m-%dT%H:%M:%S"), "game": kind,
                        "game_build": _build_of(game), "needs": sorted(needs),
                        "mods": [{"folder": folder, "name": m.name, "version": m.version, "author": m.author,
                                  "files": len(entries), "new_bytes": new}
                                 for m, folder, entries, _, _, new in parts],
                        "plugins": [f.name for f in plugin_files],
                        "new_bytes": sum(new for *_, new in parts), "game_files": 0,
                        "members": dict(sorted(members.items()))}
            put_json(MANIFEST, manifest)
        os.replace(tmp, out)
    finally:
        if tmp.exists():
            tmp.unlink()
    return {"out": str(out), "bytes": out.stat().st_size, "mods": [folder for _, folder, *_ in parts],
            "files": sum(len(e) for _, _, e, *_ in parts), "new_bytes": manifest["new_bytes"],
            "needs": sorted(needs), "plugins": [f.name for f in plugin_files], "readme": readme}


# -- the loader and plugins, for players without Riftstone -----------------------------------------
def _loader_ini() -> bytes:
    here = Path(__file__).resolve().parents[2]
    for t in (here / "loader" / "riftstone_loader.ini", here / "native" / "loader" / "riftstone_loader.ini"):
        if t.is_file():
            return t.read_bytes()
    return b"[loader]\r\noverlay = 1\r\ncrash_reports = 1\r\nplugins = 1\r\nchain =\r\n"


NINPUT_LICENSES = Path(__file__).resolve().parents[2] / "native" / "ninput" / "licenses"


def _ninput_lines() -> list[str]:
    return ["Optional: Ninput (EXPERIMENTAL, off until you copy it)",
            "  optional\\ninput\\xinput1_3.dll is Ninput, a host for native plugins written for its SDK: one shared",
            "  hook registry, one owner of Direct3D's device resets, and a controller layer (the Guide button can",
            "  be bound). It has not been run in the game yet. Nothing loads it until you copy xinput1_3.dll next",
            "  to DDDA.exe; delete it there to turn it off. It works beside the Riftstone loader (dinput8.dll).",
            "  Its plugins go in <game folder>\\ninput\\plugins; it writes ninput\\ninput.log. It links in",
            "  SafetyHook (Boost Software License 1.0), Zydis and Zycore (MIT): optional\\ninput\\licenses."]


def _plugins_readme(title: str, plugins: list[tuple[str, str]], ninput: bool = False) -> str:
    from . import legal
    lines = [title, "=" * len(title), ""]
    lines += ["Native plugins for Dragon's Dogma: Dark Arisen (Steam), made with Riftstone. You do not need",
              "Riftstone: the loader and the plugins are in this zip, and it holds no game files.", "",
              "What is inside",
              "  * dinput8.dll -- the Riftstone loader, which loads the plugins"]
    for p, about in plugins:
        lines.append(f"  * plugin {p}" + (f" -- {about}" if about else ""))
    lines += ["", "Install (Windows)",
              "  1. Close the game.",
              "  2. Find the game folder: in Steam, right-click Dragon's Dogma: Dark Arisen > Manage > Browse local files",
              "     (the folder with DDDA.exe in it).",
              "  3. Unzip everything here into that folder, keeping the folders. dinput8.dll and",
              "     riftstone_loader.ini go next to DDDA.exe; the riftstone folder goes there too.",
              "  4. Play. Nothing in the game's own files is changed. Each plugin's settings are the .ini beside it",
              "     in riftstone\\plugins.",
              "",
              "  Already have a dinput8.dll there (for example DDDA Tweak)? Before step 3, rename yours to",
              "  dinput8_chain.dll, then open riftstone_loader.ini and change the last line to",
              "  chain = dinput8_chain.dll  -- both keep working.",
              "",
              "Steam Deck / Linux (Proton)",
              "  Do the same, then in Steam: the game's Properties > General > Launch Options, enter",
              "      WINEDLLOVERRIDES=\"dinput8=n,b\" %command%",
              "  so Proton uses the dinput8.dll from the game folder.",
              "",
              "Remove",
              "  Delete dinput8.dll, riftstone_loader.ini and the riftstone folder from the game folder",
              "  (if you renamed your own dinput8.dll to dinput8_chain.dll, rename it back). The game is as it was.",
              "",
              "Good to know",
              "  * Made for the Steam version of the game (build 2364871). The plugins check the game's",
              "    code when it starts and do nothing on any other version (their reason is in riftstone\\logs).",
              "  * If the game crashes, riftstone\\logs has a crash report (crash-*.txt) to send to the plugins' authors",
              "    or to Riftstone, not to Capcom's support.",
              "  * The loader (dinput8.dll) and Riftstone's own plugins are free software under the MIT License",
              "    (riftstone\\LICENSE-Riftstone.txt)."]
    others = [p for p, _ in plugins if Path(p).stem.lower() not in OWN_PLUGINS]
    if others:
        lines += [f"  * Not Riftstone's: {', '.join(others)}. Their authors' own terms apply to them."]
    if ninput:
        lines += [""] + _ninput_lines()
    wrap = lambda s: textwrap.wrap(s, 100, initial_indent="  ", subsequent_indent="  ")   # noqa: E731
    lines += ["", "Legal"] + wrap(legal.DISCLAIMER) + wrap(legal.FREE)
    lines += ["", f"Made with Riftstone {__version__} on {time.strftime('%Y-%m-%d')}."]
    return "\r\n".join(lines) + "\r\n"


def _check_ninput(path: Path) -> bytes:
    """Ninput's xinput1_3 personality: a 32-bit DLL exporting XInputGetState, as the game imports it."""
    from . import pe
    try:
        data = Path(path).read_bytes()
        h = pe.header(data)
        ok = h.is_dll and not h.pe32plus and "XInputGetState" in pe.exports(data)
    except (OSError, ValueError, RiftError):
        ok = False
    if not ok:
        raise RiftError(f"{path} is not Ninput's xinput1_3.dll (a 32-bit DLL exporting XInputGetState); build it with "
                        "native\\ninput\\build_msvc.cmd")
    if not NINPUT_LICENSES.is_dir():
        raise RiftError(f"Ninput's licences are not in {NINPUT_LICENSES}; a Ninput build must carry them")
    return data


def build_plugins(plugins: list[Path], out: Path, name: str | None = None, loader_dir: Path | None = None,
                  ninput: Path | None = None) -> dict:
    """A zip for players without Riftstone: the loader (dinput8.dll + riftstone_loader.ini) and the plugins at
    their game-folder paths, a README and riftstone/package.json -- no mods and no game data.  ``ninput`` (its
    xinput1_3.dll) adds Ninput under optional\\ninput with its licences: shipped, but off until a player copies it."""
    out = _out_path(out)
    plugin_files, inis = _plugin_files(plugins)
    if not plugin_files:
        raise RiftError("no plugins to package: build them (native\\plugins\\<name>\\build.cmd) or pass --plugin")
    ninput_dll = _check_ninput(ninput) if ninput is not None else None
    ldir = Path(loader_dir) if loader_dir else loader.built_loader()
    dll = ldir / "dinput8.dll"
    if not dll.is_file() or not loader.is_ours(dll):
        raise RiftError(f"{dll} is not the Riftstone loader; build it with native\\loader\\build.cmd")
    title = (name or "Riftstone plugins").encode("utf-8", "replace").decode("utf-8")
    readme = f"README - {_file_title(title)}.txt"
    entries: dict[str, str] = {}
    tmp = out.with_name(out.name + ".riftstone-tmp")
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise RiftError(f"cannot make the folder for {out}: {e.strerror or e}") from None
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            def put(path: str, data: bytes) -> None:
                found = ipaudit.scan_bytes(path, data)           # the last guard: no game file, key or notation
                if found:
                    raise RiftError("the package would carry what it must not: " + "; ".join(found))
                z.writestr(zipfile.ZipInfo(path, time.localtime()[:6]), data, zipfile.ZIP_DEFLATED)
                entries[path] = _sha(data)

            put("dinput8.dll", dll.read_bytes())
            put("riftstone_loader.ini", _loader_ini())
            put("riftstone/LICENSE-Riftstone.txt", _license())
            for f in plugin_files:
                put(f"riftstone/plugins/{f.name}", f.read_bytes())
            for ini in inis.values():
                put(f"riftstone/plugins/{ini.name}", ini.read_bytes())
            if ninput_dll is not None:
                put("optional/ninput/xinput1_3.dll", ninput_dll)
                put("optional/ninput/README - Ninput.txt", ("\r\n".join(_ninput_lines()) + "\r\n").encode("utf-8"))
                for lic in sorted(NINPUT_LICENSES.glob("*.txt")):
                    put(f"optional/ninput/licenses/{lic.name}", lic.read_bytes())
            put(readme, _plugins_readme(title, [(f.name, _about(f)) for f in plugin_files],
                                        ninput_dll is not None).encode("utf-8", "replace"))
            manifest = {"name": title, "made_with": f"Riftstone {__version__}", "game_build": "2364871",
                        "made": time.strftime("%Y-%m-%dT%H:%M:%S"), "mods": [],
                        "plugins": [f.name for f in plugin_files], "archives": [],
                        "optional": ["optional/ninput/xinput1_3.dll"] if ninput_dll is not None else [],
                        "files": dict(sorted(entries.items()))}
            put("riftstone/package.json", json.dumps(manifest, indent=1).encode("utf-8"))
        os.replace(tmp, out)
    finally:
        if tmp.exists():
            tmp.unlink()
    return {"out": str(out), "bytes": out.stat().st_size, "plugins": [f.name for f in plugin_files],
            "files": len(entries), "readme": readme, "ninput": ninput_dll is not None}


# -- reading a package ---------------------------------------------------------------------------
def _safe_member(name: str) -> bool:
    parts = name.split("/")
    return (bool(name) and _storable(name) and "\\" not in name and not name.startswith("/")
            and all(p not in ("", ".", "..") and ":" not in p for p in parts))


# What reading a damaged or odd zip raises: a bad deflate stream (zlib.error), an unsupported compression
# method (NotImplementedError), an encrypted member (RuntimeError), a CRC or header error (BadZipFile) ...
_ZIP_ERRORS = (KeyError, zipfile.BadZipFile, zlib.error, NotImplementedError, RuntimeError, OSError, EOFError,
               ValueError)


class Package:
    """A package opened for reading, every member checked against the manifest."""

    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            self.z = zipfile.ZipFile(self.path)
        except _ZIP_ERRORS as e:
            raise RiftError(f"{self.path.name} is not a zip file ({e})") from None
        try:
            self._check()
        except BaseException:
            self.z.close()
            raise

    def close(self) -> None:
        self.z.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def read(self, name: str) -> bytes:
        want = self.members.get(name)
        if want is None:
            raise RiftError(f"the package's manifest does not list {name}")
        try:
            data = self.z.read(name)
        except _ZIP_ERRORS as e:                                         # a damaged member
            raise RiftError(f"{name} cannot be read from the package ({e})") from None
        if _sha(data) != want:
            raise RiftError(f"{name} in the package is damaged (its SHA-256 is not the manifest's)")
        return data

    def json(self, name: str):
        try:
            return json.loads(self.read(name).decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as e:
            raise RiftError(f"{name} in the package is not JSON ({e})") from None

    def _check(self) -> None:
        infos = self.z.infolist()
        if len(infos) > MAX_MEMBERS:
            raise RiftError("the package has too many files")
        names = [i.filename for i in infos]
        if len(set(names)) != len(names):
            raise RiftError("a file is in the package twice")
        total = 0
        for i in infos:
            if not _safe_member(i.filename):
                raise RiftError(f"the package holds a file at {i.filename!r}, outside where packages put files")
            if i.file_size > MAX_MEMBER:
                raise RiftError(f"{i.filename} is too large for a package")
            total += i.file_size
        if total > MAX_TOTAL:
            raise RiftError("the package unpacks to more than a package may")
        if MANIFEST not in names:
            raise RiftError(f"{self.path.name} is not a Riftstone package (no {MANIFEST}); a package made by an older "
                            "Riftstone holds game archives, and this one does not install those")
        try:
            man = json.loads(self.z.read(MANIFEST).decode("utf-8"))
        except _ZIP_ERRORS + (UnicodeDecodeError,) as e:
            raise RiftError(f"the package's manifest is damaged ({e})") from None
        if not isinstance(man, dict) or man.get("schema") != SCHEMA:
            raise RiftError(f"not a {SCHEMA} package")
        from .game import KINDS
        members = man.get("members")
        if not (isinstance(members, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in members.items())):
            raise RiftError("the package's manifest does not list its files")
        if set(members) != set(names) - {MANIFEST}:
            raise RiftError("the package's files are not the ones its manifest lists")
        if man.get("game") not in KINDS or not isinstance(man.get("mods"), list) or not man["mods"]:
            raise RiftError("the package's manifest names no game or no mods")
        needs = man.get("needs")
        if not (isinstance(needs, list) and all(k in KINDS for k in needs)):
            raise RiftError("the package's manifest names a game Riftstone does not know")
        folders = []
        for m in man["mods"]:
            if not isinstance(m, dict) or not _folder_ok(m.get("folder")):
                raise RiftError("a mod in the package has a folder name Riftstone would not make")
            folders.append(m["folder"])
        if len({f.lower() for f in folders}) != len(folders):
            raise RiftError("two mods in the package share a folder")
        for n in names:
            top = n.split("/")[0]
            if n == MANIFEST or n == "LICENSE-Riftstone.txt" or (top == n and n.startswith("README - ")):
                continue
            if top == "mods" and n.split("/")[1] in folders:
                continue
            if top == "plugins" and n.count("/") == 1 and n.lower().endswith((".asi", ".dll", ".ini")):
                continue
            raise RiftError(f"the package holds {n}, which is not part of a package")
        self.manifest, self.members, self.folders = man, members, folders


def _check_ref(ref) -> str:
    if not isinstance(ref, dict) or not isinstance(ref.get("sha256"), str) or not isinstance(ref.get("size"), int):
        raise RiftError("a base in the package is not {..., size, sha256}")
    if "made" in ref:
        return "made"
    if "server" in ref:
        return "server"
    if not (isinstance(ref.get("game"), str) and isinstance(ref.get("archive"), str) and isinstance(ref.get("name"), str)
            and isinstance(ref.get("type"), int)):
        raise RiftError("a base in the package names no game resource")
    try:
        ref["name"].encode("latin-1")
    except UnicodeEncodeError:
        raise RiftError("a base in the package names a resource no game has") from None
    return "game"


def _patch(pkg: Package, folder: str) -> list[dict]:
    patch = pkg.json(f"mods/{folder}/patch.json")
    if not isinstance(patch, dict) or patch.get("schema") != PATCH_SCHEMA or not isinstance(patch.get("files"), list):
        raise RiftError(f"{folder}: the package's patch is damaged")
    seen = set()
    for e in patch["files"]:
        if not (isinstance(e, dict) and isinstance(e.get("path"), str) and isinstance(e.get("sha256"), str)
                and isinstance(e.get("size"), int) and isinstance(e.get("bases"), list) and isinstance(e.get("delta"), str)):
            raise RiftError(f"{folder}: an entry of the package's patch is damaged")
        path = e["path"]
        top = path.split("/")[0]
        if not _safe_member(path) or top not in ("files", "archives", "server") or path.lower() in seen \
                or path.lower().endswith(".yaml") or not 0 <= e["size"] <= delta.MAX_SIZE:
            raise RiftError(f"{folder}: the package puts a file at {path!r}")
        seen.add(path.lower())
        if top != "server":
            from . import fsmap
            rest = path.split("/", 1)[1] if top == "files" else path.split(".arc/", 1)[-1]
            if top == "archives" and ".arc/" not in path:
                raise RiftError(f"{folder}: {path} names no archive")
            fsmap.decode_path(rest)                 # the same checks as a mod's own files
        for ref in e["bases"]:
            _check_ref(ref)
    return patch["files"]


def check(path: Path) -> dict:
    """What a package holds, read without a game: its mods, files, the author's own bytes and what it needs;
    every member, delta and name checked."""
    with Package(path) as pkg:
        man = pkg.manifest
        mods = []
        for folder in pkg.folders:
            files = _patch(pkg, folder)
            sources.check(pkg.json(f"mods/{folder}/recipes.json"), f"mods/{folder}/recipes.json")
            new = 0
            for e in files:
                ops, size = delta.decode(pkg.read(f"mods/{folder}/{e['delta']}"))
                if size != e["size"]:
                    raise RiftError(f"{folder}: {e['path']}: its delta is for another size")
                for op in ops:
                    if op[0] == delta.COPY and op[1] >= len(e["bases"]):
                        raise RiftError(f"{folder}: {e['path']}: its delta copies from a base it does not name")
                new += delta.new_bytes(ops)
            mods.append({"folder": folder, "files": len(files), "new_bytes": new})
        found = [f for n in pkg.members for f in ipaudit.scan_bytes(n, pkg.read(n))]
        if found:
            raise RiftError("the package carries what a package must not: " + "; ".join(found[:5]))
        return {"name": man.get("name"), "game": man["game"], "needs": man["needs"], "mods": mods,
                "plugins": [n.split("/", 1)[1] for n in pkg.members if n.startswith("plugins/")],
                "new_bytes": sum(m["new_bytes"] for m in mods), "made_with": man.get("made_with")}


def install(path: Path, into: Path, games: sources.Games, progress=None) -> dict:
    """Make the package's mods in the folder ``into`` from this PC's games; returns what was made."""
    into = Path(into)
    with Package(path) as pkg:
        man = pkg.manifest
        kind = man["game"]
        for folder in pkg.folders:
            if (into / folder).exists():
                raise RiftError(f"{into / folder} exists already; move it away (or install into another folder)")
        for k in man["needs"]:
            games.game(k)                               # says which game is missing, before anything is written
        game = games.game(kind)
        files = GameFiles(game, games.index(kind))
        made_folders = []
        into.mkdir(parents=True, exist_ok=True)
        try:
            for folder in pkg.folders:
                _install_mod(pkg, folder, into, games, files, progress)
                made_folders.append(folder)
        except BaseException:
            for folder in made_folders:                # all or nothing: this call's own new folders go again
                shutil.rmtree(into / folder, ignore_errors=True)
            raise
        plugins = []
        for n in pkg.members:
            if n.startswith("plugins/"):
                target = into / "_plugins" / n.split("/", 1)[1]
                if target.exists() and target.read_bytes() != pkg.read(n):
                    raise RiftError(f"{target} exists and is a different file")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(pkg.read(n))
                plugins.append(str(target))
        return {"mods": [str(into / f) for f in made_folders], "plugins": plugins, "needs": man["needs"],
                "name": man.get("name")}


def _install_mod(pkg: Package, folder: str, into: Path, games: sources.Games, files: GameFiles, progress) -> None:
    from . import arcfolder, params
    base = f"mods/{folder}/"
    meta = pkg.json(base + modlib.MOD_FILE)
    recipes = sources.check(pkg.json(base + "recipes.json"), base + "recipes.json")["recipes"]
    entries = _patch(pkg, folder)
    tmp = into / f".{folder}.riftstone-tmp"
    if tmp.exists():
        shutil.rmtree(tmp)                              # a leftover of an interrupted install of this package
    tmp.mkdir()
    try:
        arcfolder.write_file(tmp / modlib.MOD_FILE, (json.dumps(meta, indent=2) + "\n").encode("utf-8"))
        m = modlib.Mod.load(tmp)                         # the same checks as any mod
        if m.game != pkg.manifest["game"]:
            raise RiftError(f"{folder} is not a {pkg.manifest['game']} mod")
        made = sources.replay(recipes, games) if recipes else {}
        for e in entries:
            bases = [_base(ref, m.game, games, files, made, folder, e["path"]) for ref in e["bases"]]
            ops, size = delta.decode(pkg.read(base + e["delta"]))
            data = delta.apply(ops, bases, size)
            if size != e["size"] or _sha(data) != e["sha256"]:
                raise RiftError(f"{folder}: {e['path']} did not come out as the author's file")
            target = arcfolder.safe_member(tmp, e["path"])
            if e.get("yaml") and not e["path"].startswith("server/"):
                from . import fsmap
                rest = e["path"].split("/", 1)[1] if e["path"].startswith("files/") else e["path"].split(".arc/", 1)[1]
                name, tid = fsmap.decode_path(rest)
                text = params.resource_to_yaml(data, name.decode("latin-1"), tid)
                if text is not None and params.yaml_to_resource(text, source=e["path"]) == data:
                    target, data = target.with_name(target.name + ".yaml"), text.encode("utf-8")
            arcfolder.write_file(target, data)
            if progress:
                progress.advance(1, e["path"])
        if pkg.members.get(base + "skins.json"):
            from . import skins
            arcfolder.write_file(tmp / skins.MANIFEST, pkg.read(base + "skins.json"))
            skins.read_manifest(tmp)
        if recipes:                                      # so this mod can be packaged again
            sources.save(tmp, {"schema": sources.SCHEMA, "recipes": recipes, "foreign": {}})
        modlib.collect(modlib.Mod.load(tmp))              # every file reads as the build will read it
        os.replace(tmp, into / folder)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)


def _base(ref: dict, kind: str, games, files: GameFiles, made: dict, folder: str, path: str) -> bytes:
    which = _check_ref(ref)
    if which == "made":
        data = made.get(ref["made"])
        what = f"what its recipe makes for {ref['made']}"
    elif which == "server":
        from . import ddo, install as installlib
        game = games.game(kind)
        data = None
        for p in (installlib._server_target(game.state_dir / "server-vanilla", ref["server"]),
                  installlib._server_target(ddo.need_assets(game), ref["server"])):
            if p.is_file():
                data = p.read_bytes()
                break
        what = f"the local server's {ref['server']}"
    else:
        if ref["game"] != kind:
            raise RiftError(f"{folder}: {path}: a base from another game's archive")
        data = files.entry(ref["archive"], ref["name"].encode("latin-1"), ref["type"]) \
            if files.game.arc_path(ref["archive"]).is_file() else None
        what = f"{ref['archive']}.arc's {ref['name']}"
    if data is None:
        raise RiftError(f"{folder}: {path} is made from {what}, which is not on this PC")
    if len(data) != ref["size"] or _sha(data) != ref["sha256"]:
        raise RiftError(f"{folder}: {path} is made from {what}, and yours is not the author's (another game build, "
                        "or a changed file: verify the game files)")
    return data
