"""Turn whole changed archives (another tool's output, an old archive mod, DDO's staged translation)
into a Riftstone mod that holds only what changed, so it installs, uninstalls and merges with other
mods like any mod made here.

Each input archive is matched to the game's archive by its path (the longest tail that names one,
e.g. ...\\nativePC\\rom\\ui\\gui_cmn.arc or rom\\ui\\gui_cmn.arc) or, failing that, by a unique file
name.  Resources are compared by their decoded bytes, so a tool that only recompressed an archive
changes nothing.  A change goes to ``files/`` (every archive holding it) when the inputs change it in
exactly the archives that hold it, every copy in the game was identical and all received the same new
bytes; otherwise to ``archives/<arc>.arc/`` for exactly that archive (an archive that adds a resource
it never had always gets its own copy).  Editable formats are written as YAML when the YAML rebuilds the exact bytes.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import arc, arcfolder, fsmap, params
from .errors import RiftError
from .game import Game


@dataclass
class ImportReport:
    archives: int = 0
    unmatched: list[str] = field(default_factory=list)
    changed: int = 0
    added: int = 0
    removed: int = 0
    unchanged: int = 0
    to_files: int = 0
    to_archives: int = 0
    as_yaml: int = 0
    written: list[Path] = field(default_factory=list)


def _inputs(paths: list[Path]) -> list[tuple[Path, Path]]:
    """(archive file, the folder it was found under) for every .arc given or inside a folder given."""
    out = []
    for p in paths:
        p = Path(p)
        if p.is_file() and p.suffix.lower() == ".arc":
            out.append((p, p.parent))
        elif p.is_dir():
            out.extend((f, p) for f in sorted(p.rglob("*.arc")))
        else:
            raise RiftError(f"{p} is not an archive or a folder of archives")
    return out


def match(game: Game, path: Path, base: Path, names: dict[str, str], by_file: dict[str, list[str]]) -> str | None:
    """The game's archive name for an input archive, or None."""
    try:
        rel = path.relative_to(base)
    except ValueError:
        rel = Path(path.name)
    parts = [x for x in path.with_suffix("").parts]
    for i in range(len(parts)):
        key = "/".join(parts[i:]).lower()
        if key in names:
            return names[key]
    cands = by_file.get(path.stem.lower(), [])
    if len(cands) == 1:
        return cands[0]
    rel_key = rel.with_suffix("").as_posix().lower()
    hits = [n for n in cands if n.lower().endswith(rel_key)]
    return hits[0] if len(hits) == 1 else None


def import_archives(game: Game, index, inputs: list[Path], mod_root: Path, yaml: bool = True,
                    per_archive: bool = False, progress=None) -> ImportReport:
    rep = ImportReport()
    names = {n.lower(): n for n, _ in game.archive_names()}
    by_file: dict[str, list[str]] = defaultdict(list)
    for n in names.values():
        by_file[n.rsplit("/", 1)[-1].lower()].append(n)
    # (name, type) -> {arc: new data}; the vanilla digest per (name, type, arc)
    changes: dict[tuple[bytes, int], dict[str, bytes]] = defaultdict(dict)
    vanilla_digest: dict[tuple[bytes, int, str], str] = {}
    for f, base in _inputs(inputs):
        arc_name = match(game, f, base, names, by_file)
        if arc_name is None:
            rep.unmatched.append(str(f))
            continue
        rep.archives += 1
        new = arc.Archive.read(f)
        old = arc.Archive.read(game.vanilla_arc(arc_name))
        old_by_key = {e.key: e for e in old.entries}
        new_keys = set()
        for e in new.entries:
            new_keys.add(e.key)
            data = e.data()
            o = old_by_key.get(e.key)
            if o is not None:
                od = o.data()
                if od == data:
                    rep.unchanged += 1
                    continue
                rep.changed += 1
                vanilla_digest[(e.name, e.type_id, arc_name)] = hashlib.sha256(od).hexdigest()
            else:
                rep.added += 1
            changes[(e.name, e.type_id)][arc_name] = data
        rep.removed += sum(1 for k in old_by_key if k not in new_keys)
        if progress:
            progress.advance(1, arc_name)
    for (name, tid), per in sorted(changes.items()):
        rel = fsmap.encode_name(name, tid)
        holders = index.archives_with(name, tid) if index is not None else []
        same_new = len({hashlib.sha256(d).digest() for d in per.values()}) == 1
        same_old = len({vanilla_digest.get((name, tid, a)) for a in holders}) == 1
        # files/ reaches the game's holders only: an input archive that adds the resource needs its own copy
        everywhere = bool(holders) and set(holders) == set(per) and same_new and same_old
        data0 = next(iter(per.values()))
        payload, suffix = data0, ""
        if yaml and params.is_editable_resource(data0, tid):
            try:
                text = params.resource_to_yaml(data0, name.decode("latin-1"), tid)
                if text is not None and params.yaml_to_resource(text) == data0:
                    payload, suffix = text.encode("utf-8"), ".yaml"
            except Exception:  # noqa: BLE001 - a YAML form that does not round-trip stays binary
                pass
        if everywhere and not per_archive:
            targets = [mod_root / "files" / (rel + suffix)]
            rep.to_files += 1
        else:
            targets = []
            for a, d in sorted(per.items()):
                p, s = (payload, suffix) if d == data0 else (d, "")
                targets.append((mod_root / "archives" / (a + ".arc") / (rel + s), p))
            rep.to_archives += len(targets)
        for t in targets:
            if isinstance(t, tuple):
                arcfolder.write_file(t[0], t[1])
                rep.written.append(t[0])
            else:
                arcfolder.write_file(t, payload)
                rep.written.append(t)
        rep.as_yaml += suffix == ".yaml"
    return rep
