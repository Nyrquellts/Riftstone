"""Mod projects: loose files in, verified archives out.

    MyMod/
      riftstone-mod.json        name, version, author, description, priority
      files/                    a resource at its engine path replaces that
                                resource in EVERY archive that holds it
        param/status/enemy.statusparam.yaml
        model/em/e01/e0100/e0100.mod
      archives/                 changes for one archive only (replace or add)
        rom/enemy/em0100.arc/model/em/e01/e0100/e0100_BM.tex
      loose/                    resources no archive holds, which the game opens
        compat/job09/job09.shl  by path from nativePC when something asks for
        compat/alchemist.skills them (the loader serves them from its overlay);
                                compat/*.skills are the compat plugin's programs

``*.yaml`` files are compiled to the XFS resource named by the rest of the
path.  Nothing here writes to the game; install.py does that.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import arc, fsmap, params, typemap
from .errors import BuildError, RiftError, UnsafePathError
from .game import KINDS, Game

MOD_FILE = "riftstone-mod.json"
SCHEMA = "riftstone.mod/1"
IGNORED_NAMES = {"desktop.ini", "thumbs.db", ".ds_store", "readme.txt", "readme.md"}
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"{p}{i}" for p in ("COM", "LPT") for i in range(1, 10)}


def check_name(name: str) -> None:
    """A mod's name, which is also its folder's name in the mods folder: refused unless Windows keeps it as typed."""
    if (not re.fullmatch(r"[\w .()'&+-]{1,64}", name) or name != name.strip() or name.endswith(".")
            or name.split(".")[0].upper() in _RESERVED):
        raise RiftError("mod names use letters, digits, spaces and . ( ) ' & + - (at most 64), "
                        "cannot end with a dot or space, and cannot be a Windows device name like CON")


# -- the mods folder: where your mods live, the folder Studio shows ------------------------------------------
MODS_ENV = ("RIFTSTONE_MODS", "RIFTSTONE_WORKSPACE")     # the second is the name Studio read first; both work


def mods_env() -> Path | None:
    """The mods folder the environment names, if it names one."""
    for var in MODS_ENV:
        if os.environ.get(var):
            return Path(os.environ[var])
    return None


def mods_folder(game: Game | None = None) -> Path:
    """Where your mods live: the folder Studio shows, where ``riftstone new`` makes a mod and where
    ``--mod "<name>"`` looks.  $RIFTSTONE_MODS, else the folder that holds the mods installed in the game
    (so it shows what is installed), else Riftstone's own mods folder when it has mods, else
    Documents\\Riftstone\\mods."""
    env = mods_env()
    if env is not None:
        return env
    if game is not None:
        from . import install
        try:
            parents = {Path(m["path"]).parent for m in install.load_state(game).get("mods", []) if m.get("path")}
        except (RiftError, OSError, KeyError, TypeError):
            parents = set()
        if len(parents) == 1:
            p = parents.pop()
            if p.is_dir():
                return p
    own = Path(__file__).resolve().parents[2] / "mods"
    if own.is_dir() and any((d / MOD_FILE).is_file() for d in own.iterdir() if d.is_dir()):
        return own
    return Path.home() / "Documents" / "Riftstone" / "mods"


def mods_folders(first: str | os.PathLike | None = None) -> list[Path]:
    """Every folder a mod's name is looked up in: the environment's folder alone when it names one, else the
    mods folder of each game on this PC (``first`` -- a game keyword or folder, as --game takes -- first,
    then the others), then the one used without a game."""
    env = mods_env()
    if env is not None:
        return [env]
    from .game import find_game

    games: list[Game] = []
    for want in (first, *KINDS):
        try:
            g = find_game(want)
        except (RiftError, OSError):
            continue
        if all(g.root != h.root for h in games):
            games.append(g)
    out: list[Path] = []
    for p in [mods_folder(g) for g in games] + [mods_folder(None)]:
        if all(p != q for q in out):
            out.append(p)
    return out


def is_bare_name(text: str) -> bool:
    """'Harder Goblins' is a name; 'mods/Harder Goblins', '.\\x', 'C:x' and '..' are paths."""
    text = str(text).strip()
    return bool(text) and not any(c in text for c in "\\/:") and text not in (".", "..")


def list_mods(folder: Path) -> list[Path]:
    """The mods in one folder (each a folder with riftstone-mod.json), by name."""
    try:
        return sorted((d for d in Path(folder).iterdir() if (d / MOD_FILE).is_file()), key=lambda d: d.name.lower())
    except OSError:
        return []


def _display_name(root: Path) -> str:
    try:
        return str(json.loads((root / MOD_FILE).read_text(encoding="utf-8-sig")).get("name") or "").strip()
    except (OSError, ValueError, AttributeError):
        return ""


def find_mod(name: str, folders: list[Path]) -> Path | None:
    """The mod a name means in these folders: a mod folder of that name, else a mod that calls itself that
    (the name in its riftstone-mod.json); case does not matter.  None when no mod is called that."""
    want = " ".join(str(name).split()).casefold()
    if not want:
        return None
    mods = [d for f in folders for d in list_mods(f)]
    for d in mods:
        if " ".join(d.name.split()).casefold() == want:
            return d
    for d in mods:
        if " ".join(_display_name(d).split()).casefold() == want:
            return d
    return None


def locate(arg: str, folders: list[Path], create_in: Path | None = None) -> Path:
    """A mod given on the command line.  A path (or a mod folder right here) stays as given, as it always
    did; a plain name means the mod of that name in the mods folders.  With ``create_in``, a name no mod has
    becomes a new folder there (for commands that make their mod); otherwise the error names the mods that
    exist."""
    text = str(arg).strip()
    if not is_bare_name(text):
        return Path(arg)
    try:
        if (Path(text) / MOD_FILE).is_file():
            return Path(text)
    except (OSError, ValueError):
        pass
    found = find_mod(text, folders)
    if found is not None:
        return found
    if create_in is not None:
        check_name(text)
        return Path(create_in) / text
    have = [d.name for f in folders for d in list_mods(f)]
    listed = ", ".join(f'"{n}"' for n in have[:12]) + (" ..." if len(have) > 12 else "") if have else "none yet"
    where = " or ".join(str(f) for f in folders) or "the mods folder"
    raise RiftError(f'no mod called "{text}" in {where} (mods there: {listed}). Make it with: riftstone new "{text}"')


def _ignored(p: Path) -> bool:
    n = p.name.lower()
    return (n in IGNORED_NAMES or n.startswith((".", "~$")) or n.endswith((".bak", "~", ".tmp", ".riftstone-tmp"))
            or any(part.startswith(".") for part in p.parts))


@dataclass
class Mod:
    root: Path
    name: str
    version: str = "0.1.0"
    author: str = ""
    description: str = ""
    priority: int = 0
    game: str = "ddda"  # the game this mod is for: 'ddda' or 'ddo'

    @classmethod
    def load(cls, root: Path) -> "Mod":
        root = Path(root)
        f = root / MOD_FILE
        if not f.is_file():
            raise RiftError(f"{root} is not a Riftstone mod (no {MOD_FILE}). Create one with: riftstone new \"{root.name}\"")
        try:
            meta = json.loads(f.read_text(encoding="utf-8-sig"))
        except ValueError as e:
            raise RiftError(f"{f}: {e}") from None
        if meta.get("schema") != SCHEMA:
            raise RiftError(f"{f}: expected \"schema\": \"{SCHEMA}\"")
        game = str(meta.get("game", "ddda"))
        if game not in KINDS:
            raise RiftError(f"{f}: \"game\" must be one of {', '.join(KINDS)}")
        return cls(root, str(meta.get("name") or root.name), str(meta.get("version", "0.1.0")),
                   str(meta.get("author", "")), str(meta.get("description", "")), int(meta.get("priority", 0)), game)

    @classmethod
    def create(cls, root: Path, name: str | None = None, author: str = "", game: str = "ddda") -> "Mod":
        root = Path(root)
        if game not in KINDS:
            raise RiftError(f"unknown game {game!r}; use one of {', '.join(KINDS)}")
        if (root / MOD_FILE).exists():
            raise RiftError(f"{root} is already a Riftstone mod")
        name = name or root.name
        check_name(name)
        (root / "files").mkdir(parents=True, exist_ok=True)
        (root / "archives").mkdir(exist_ok=True)
        m = cls(root, name, author=author, game=game)
        m.save()
        (root / "files" / "README.txt").write_text(
            "Put resources here at their engine paths, e.g.\n"
            "  param/status/enemy.statusparam.yaml\n"
            "  model/em/e01/e0100/e0100.mod\n"
            "Each one replaces that resource in every archive of the game that contains it.\n"
            "Get the original with:  riftstone extract param/status/enemy.statusparam --mod <this mod>\n",
            encoding="utf-8")
        if game == "ddo":
            (root / SERVER_DIR).mkdir(exist_ok=True)
            (root / SERVER_DIR / "README.txt").write_text(
                "Dragon's Dogma Online decides spawns, shops and drops on its server. Files here replace\n"
                "the local server's asset files at the same path (Files\\Assets), e.g.\n"
                "  server/EnemySpawn.json    (riftstone encounter ... --game ddo writes it)\n"
                "install writes them there and keeps the originals; restart the server to load them.\n",
                encoding="utf-8")
        (root / "archives" / "README.txt").write_text(
            "Changes for a single archive: archives/<archive path>.arc/<resource path>\n"
            "  archives/rom/enemy/em0100.arc/model/em/e01/e0100/e0100_BM.tex\n"
            "A resource the archive does not have yet is added to it.\n", encoding="utf-8")
        return m

    def save(self) -> None:
        meta = {"schema": SCHEMA, "name": self.name, "version": self.version, "author": self.author,
                "description": self.description, "priority": self.priority, "game": self.game}
        (self.root / MOD_FILE).write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


@dataclass
class Change:
    mod: str
    source: str            # path inside the mod, for messages
    arc: str | None        # None: every archive that holds the resource
    name: bytes
    type_id: int
    data: bytes

    @property
    def label(self) -> str:
        return f"{self.name.decode('latin-1')}.{typemap.extension(self.type_id)}"

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


def _load_resource(f: Path, rel: str, mod: Mod) -> tuple[bytes, int, bytes]:
    target = rel[:-5] if rel.lower().endswith(".yaml") else rel
    try:
        name, type_id = fsmap.decode_path(target)
    except UnsafePathError as e:
        raise BuildError(f"{mod.name}: {rel}: {e}") from None
    if rel.lower().endswith(".yaml"):
        if not params.has_yaml_form(type_id):
            raise BuildError(f"{mod.name}: {rel}: .{typemap.extension(type_id)} resources are not YAML-editable")
        data = params.yaml_to_resource(params.decode_text(f.read_bytes(), f"{mod.name}/{rel}"),
                                       source=f"{mod.name}/{rel}")
    else:
        data = f.read_bytes()
        if len(data) > arc.MAX_DECODED:
            raise BuildError(f"{mod.name}: {rel} is larger than {arc.MAX_DECODED} bytes")
    if mod.game == "ddda" and type_id in (typemap.BY_EXT["mod"], typemap.BY_EXT["sbc"]):
        from . import terrain
        cell = terrain.cell_of_model(name) if type_id == typemap.BY_EXT["mod"] else terrain.cell_of_collision(name)
        if cell is not None:        # a Gransys terrain cell saved in world space would stand a corner away
            errors, _notes = terrain.check(data, cell)
            if errors:
                raise BuildError(f"{mod.name}: {rel}: " + "; ".join(errors))
    return name, type_id, data


SERVER_DIR = "server"
MAX_SERVER_FILE = 64 * 1024 * 1024


def collect_server(mod: Mod) -> dict[str, bytes]:
    """A Dragon's Dogma Online mod's server/ files: asset path (forward slashes) -> bytes.  They go
    into the local server's asset folder on install (EnemySpawn.json, Shop.json, quests/...)."""
    base = mod.root / SERVER_DIR
    out: dict[str, bytes] = {}
    if not base.is_dir():
        return out
    for f in sorted(base.rglob("*")):
        rel_path = f.relative_to(base)
        if not f.is_file() or _ignored(rel_path) or f.name.lower() == "readme.txt":
            continue
        rel = rel_path.as_posix()
        if any(part in ("", ".", "..") or ":" in part for part in rel.split("/")):
            raise BuildError(f"{mod.name}: server/{rel}: not a plain path")
        data = f.read_bytes()
        if len(data) > MAX_SERVER_FILE:
            raise BuildError(f"{mod.name}: server/{rel} is larger than {MAX_SERVER_FILE} bytes")
        if f.suffix.lower() == ".json":
            try:
                json.loads(data.decode("utf-8-sig"))
            except (UnicodeDecodeError, ValueError) as e:
                raise BuildError(f"{mod.name}: server/{rel} is not valid JSON: {e}") from None
        out[rel] = data
    return out


LOOSE_DIR = "loose"
PROGRAMS_DIR = "compat"       # loose/compat/*.skills: the compat plugin's programs (text)
MAX_PROGRAMS_FILE = 1 << 20


def collect_loose(mod: Mod) -> dict[str, bytes]:
    """A mod's loose/ files: nativePC path (forward slashes, with its extension) -> bytes.

    A loose resource is one the game opens by path when no loaded archive holds it (the loader serves
    <game>/riftstone/overlay/<path> in place of <game>/nativePC/<path>); ``*.yaml`` compiles as under
    files/.  loose/compat/*.skills are the compat plugin's skill programs.  Archives cannot be loose."""
    base = mod.root / LOOSE_DIR
    out: dict[str, bytes] = {}
    if not base.is_dir():
        return out
    for f in sorted(base.rglob("*")):
        rel_path = f.relative_to(base)
        if not f.is_file() or _ignored(rel_path):
            continue
        rel = rel_path.as_posix()
        if rel.lower().endswith(".skills"):
            parts = rel.split("/")
            if len(parts) != 2 or parts[0].lower() != PROGRAMS_DIR or not re.fullmatch(r"[\w.-]{1,64}", parts[1]):
                raise BuildError(f"{mod.name}: loose/{rel}: skill programs go in loose/{PROGRAMS_DIR}/<name>.skills")
            data = f.read_bytes()
            if len(data) > MAX_PROGRAMS_FILE:
                raise BuildError(f"{mod.name}: loose/{rel} is larger than {MAX_PROGRAMS_FILE} bytes")
            try:
                data.decode("ascii")
            except UnicodeDecodeError:
                raise BuildError(f"{mod.name}: loose/{rel}: programs are plain ASCII text") from None
            out[f"{PROGRAMS_DIR}/{parts[1]}"] = data
            continue
        name, type_id, data = _load_resource(f, rel, mod)
        if typemap.extension(type_id) == "arc":
            raise BuildError(f"{mod.name}: loose/{rel}: an archive cannot be a loose file")
        out[fsmap.encode_name(name, type_id)] = data
    return out


def collect(mod: Mod) -> list[Change]:
    out: list[Change] = []
    seen: dict[tuple, str] = {}
    files = mod.root / "files"
    if files.is_dir():
        for f in sorted(files.rglob("*")):
            if f.is_file() and not _ignored(f.relative_to(files)):
                rel = f.relative_to(files).as_posix()
                name, type_id, data = _load_resource(f, rel, mod)
                key = (None, name, type_id)
                if key in seen:
                    raise BuildError(f"{mod.name}: {rel} and {seen[key]} are the same resource")
                seen[key] = rel
                out.append(Change(mod.name, f"files/{rel}", None, name, type_id, data))
    archives = mod.root / "archives"
    if archives.is_dir():
        for f in sorted(archives.rglob("*")):
            if not f.is_file() or _ignored(f.relative_to(archives)):
                continue
            parts = f.relative_to(archives).parts
            cut = next((i for i, p in enumerate(parts) if p.lower().endswith(".arc")), None)
            if cut is None or cut == len(parts) - 1:
                raise BuildError(f"{mod.name}: archives/{'/'.join(parts)}: expected archives/<archive>.arc/<resource path>")
            arc_name = "/".join(parts[:cut] + (parts[cut][:-4],))
            rel = "/".join(parts[cut + 1:])
            name, type_id, data = _load_resource(f, rel, mod)
            key = (arc_name.lower(), name, type_id)
            if key in seen:
                raise BuildError(f"{mod.name}: {rel} appears twice for {arc_name}")
            seen[key] = rel
            out.append(Change(mod.name, f"archives/{'/'.join(parts)}", arc_name, name, type_id, data))
    return out


@dataclass
class Plan:
    archives: dict[str, list[Change]] = field(default_factory=dict)
    unresolved: list[Change] = field(default_factory=list)
    missing_archives: list[Change] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    changes: int = 0


def plan(game: Game, index, mods: list[Mod]) -> Plan:
    """Where every change lands.  Later (higher priority) mods win a conflict."""
    p = Plan()
    per_arc: dict[str, list[Change]] = defaultdict(list)
    # One spelling per archive: Windows ignores case, so "Rom/EM0100" and "rom/em0100" are one file.
    canonical = {game.arc_name(f).lower(): game.arc_name(f) for f in game.archives()}
    for m in mods:
        if m.game != game.kind:
            raise BuildError(f"{m.name} is a {KINDS[m.game]['title']} mod, not a {game.title} one: use --game "
                             f"{m.game}, or bring its files across with 'riftstone port'")
        if m.game != "ddo" and (m.root / SERVER_DIR).is_dir() and collect_server(m):
            raise BuildError(f"{m.name}: server/ files are for Dragon's Dogma Online mods (its local server)")
    for m in sorted(mods, key=lambda m: (m.priority, m.name.lower())):
        for c in collect(m):
            p.changes += 1
            if c.arc is None:
                arcs = index.archives_with(c.name, c.type_id)
                if not arcs:
                    p.unresolved.append(c)
                for a in arcs:
                    per_arc[canonical.get(a.lower(), a)].append(c)
            else:
                arc_name = canonical.get(c.arc.lower())
                if arc_name is None:
                    p.missing_archives.append(c)
                    continue
                c.arc = arc_name
                per_arc[arc_name].append(c)
    for a, changes in sorted(per_arc.items()):
        by_key: dict[tuple, Change] = {}
        for c in changes:
            prev = by_key.get((c.name, c.type_id))
            if prev is not None and prev.mod != c.mod and prev.sha256 != c.sha256:
                p.conflicts.append({"archive": a, "resource": c.label, "loser": prev.mod, "winner": c.mod})
            by_key[(c.name, c.type_id)] = c
        p.archives[a] = list(by_key.values())
    return p


def check_plan(p: Plan) -> None:
    problems = []
    for c in p.unresolved[:10]:
        problems.append(f"{c.mod}: {c.source}: no vanilla archive contains {c.label}. To add a new resource, "
                        f"put it under archives/<archive>.arc/ instead of files/.")
    for c in p.missing_archives[:10]:
        problems.append(f"{c.mod}: {c.source}: archive {c.arc} does not exist in this game")
    if problems:
        more = len(p.unresolved) + len(p.missing_archives) - len(problems)
        raise BuildError("\n".join(problems) + (f"\n... and {more} more" if more > 0 else ""))


@dataclass
class BuiltArchive:
    arc: str
    data: bytes
    replaced: list[str]
    added: list[str]
    unchanged: list[str]


def _needs(entry: arc.Entry) -> set[tuple[bytes, int]]:
    """What a resource looks up while it loads: a material its textures, a model its material."""
    from . import mrl

    TEX, MRL, MOD = typemap.BY_EXT["tex"], typemap.BY_EXT["mrl"], typemap.BY_EXT["mod"]
    if entry.type_id == MRL:
        try:
            return {(t.name.encode("latin-1"), TEX) for t in mrl.parse(entry.data()).textures if t.name}
        except (RiftError, UnicodeEncodeError):
            return set()
    if entry.type_id == MOD:
        return {(entry.name, MRL)}
    return set()


def load_order(base: arc.Archive, added: set[tuple[bytes, int]], changed: set[tuple[bytes, int]]) -> None:
    """Place the resources a mod adds where the game can load them.  The game loads an archive in
    order, and a material or model looks up what it references as it loads; a texture that is not
    loaded yet is then opened as a loose file from nativePC, which does not exist ("Fatal error:
    Failed open file").  The game's own archives list textures before the materials that use them and
    materials before models.  So the added resources (which put() appended) go textures first, then
    others, then materials, then models; and an added resource that a changed resource earlier in the
    archive needs moves up to just before it.  The game's own resources keep their order."""
    TEX, MRL, MOD = typemap.BY_EXT["tex"], typemap.BY_EXT["mrl"], typemap.BY_EXT["mod"]
    rank = {TEX: 0, MRL: 2, MOD: 3}
    n = len(base.entries) - sum(1 for e in base.entries if e.key in added)
    tail = sorted(base.entries[n:], key=lambda e: rank.get(e.type_id, 1))      # stable: name order kept
    entries = base.entries[:n] + tail
    needs = {e.key: _needs(e) for e in entries if e.key in changed}
    # textures need nothing, materials need textures, models need materials: no cycles, so this ends
    for _ in range(4 * (len(added) + 1) ** 2):
        pos = {e.key: i for i, e in enumerate(entries)}
        move = None
        for i, e in enumerate(entries):
            late = [pos[ref] for ref in needs.get(e.key, ()) if ref in added and pos.get(ref, -1) > i]
            if late:
                move = (min(late), i)
                break
        if move is None:
            break
        j, i = move
        entries.insert(i, entries.pop(j))                                        # just before its first user
    base.entries[:] = entries


def check_fsm(label: str, data: bytes, vanilla: bytes | None) -> None:
    """A state machine a mod changes or adds may not bring a problem the game's own copy does not
    have: a machine with states but no start state, or a link the game can take to a state that is
    not there (fsmcheck.py, both read in the executables).  Dead links and unreached states are
    reported by ``riftstone fsm --check`` but do not stop a build."""
    from collections import Counter

    from . import fsmcheck
    try:
        found = fsmcheck.check_bytes(data)
    except (RiftError, ValueError) as e:
        raise BuildError(f"{label} is not a readable state machine: {e}") from None
    new = Counter(fsmcheck.problem_keys(found))
    if vanilla is not None:
        try:
            new -= Counter(fsmcheck.problem_keys(fsmcheck.check_bytes(vanilla)))
        except (RiftError, ValueError):
            pass
    fresh = []
    for f in found:
        if f.severity == fsmcheck.PROBLEM and new[f.key] > 0:
            new[f.key] -= 1
            fresh.append(f.text)
    if fresh:
        more = f" (+{len(fresh) - 3} more)" if len(fresh) > 3 else ""
        raise BuildError(f"{label}: " + "; ".join(fresh[:3]) + more + " (riftstone fsm --check shows every finding)")


def build_archive(game: Game, arc_name: str, changes: list[Change]) -> BuiltArchive:
    source = game.vanilla_arc(arc_name)
    base = arc.Archive.read(source)
    vanilla_payloads = {e.key: e.payload for e in base.entries}
    vanilla = {e.key: e for e in base.entries}
    replaced, added, unchanged = [], [], []
    added_keys: set[tuple[bytes, int]] = set()
    for c in changes:
        if c.type_id == typemap.BY_EXT["fsm"]:
            old = vanilla.get((c.name, c.type_id))
            check_fsm(f"{arc_name}: {c.label}", c.data, old.data() if old is not None else None)
        status = base.put(c.name, c.type_id, c.data)
        {"replaced": replaced, "added": added, "unchanged": unchanged}[status].append(c.label)
        if status == "added":
            added_keys.add((c.name, c.type_id))
    load_order(base, added_keys, {(c.name, c.type_id) for c in changes})
    built = base.build()
    # Independent check: reparse; changed resources decode to what the mod supplied;
    # every other resource keeps its original stored bytes exactly.
    check = arc.Archive.parse(built)
    want = {(c.name, c.type_id): c.sha256 for c in changes}
    if [e.key for e in check.entries] != [e.key for e in base.entries]:
        raise BuildError(f"{arc_name}: rebuilt archive lists different resources")
    for e in check.entries:
        if e.key in want:
            if hashlib.sha256(e.data()).hexdigest() != want[e.key]:
                raise BuildError(f"{arc_name}: {e.label} did not survive the rebuild")
        elif e.payload != vanilla_payloads.get(e.key):
            raise BuildError(f"{arc_name}: untouched resource {e.label} changed during the rebuild")
    return BuiltArchive(arc_name, built, replaced, added, unchanged)
