"""A resource as a mod has it -- its YAML or its binary -- else the game's original; and back.

Commands that change a resource inside a mod (text, items, spawns) load it with ``load``
and write it with ``save``, which keeps the form the mod already uses."""
from __future__ import annotations

from pathlib import Path

from . import arc, arcfolder, fsmap, params, typemap
from .errors import RiftError


def paths(mod_root: Path, name: bytes, type_id: int) -> tuple[Path, Path]:
    rel = fsmap.encode_name(name, type_id)
    return mod_root / "files" / (rel + ".yaml"), mod_root / "files" / rel


def load(game, idx, mod_root: Path | None, name: bytes, type_id: int) -> tuple[bytes, Path | None]:
    """The resource's bytes and the file the mod keeps it in (YAML unless the mod holds the binary)."""
    rel = fsmap.encode_name(name, type_id)
    if mod_root is not None:
        as_yaml, as_bin = paths(mod_root, name, type_id)
        if as_yaml.is_file() and as_bin.is_file():
            raise RiftError(f"the mod has both {rel} and {rel}.yaml; keep one")
        if as_yaml.is_file():
            return params.yaml_to_resource(params.decode_text(as_yaml.read_bytes(), str(as_yaml)), str(as_yaml)), as_yaml
        if as_bin.is_file():
            return as_bin.read_bytes(), as_bin
    arcs = idx.archives_with(name, type_id)
    e = _entry(game.vanilla_arc(arcs[0]), name, type_id) if arcs else None
    if e is None:
        raise RiftError(f"the game has no {rel}")
    return e.data(), (paths(mod_root, name, type_id)[0] if mod_root is not None else None)


def _entry(path: Path, name: bytes, type_id: int) -> arc.Entry | None:
    """One archive entry, reading only the directory and that entry's stored bytes: a stage archive is up to
    56 MB, and parsing all of it for one group list or layout was most of an encounter's time.  The entry's own
    ``data()`` checks the stream as ``Archive.read`` would; an archive whose directory does not read is parsed
    whole, so its error is the same."""
    from .corpus import directory

    rows = directory(path)
    if not rows:
        a = arc.Archive.read(path)
        return a.find(name, type_id)
    for n, t, stored, size, off in rows:
        if n == name and t == type_id:
            with open(path, "rb") as fh:
                encrypted = fh.read(4) == arc.MAGIC_ENC
                fh.seek(off)
                payload = fh.read(stored)
            if len(payload) != stored:
                raise RiftError(f"{path.name}: {name.decode('latin-1')} runs past the end of the archive")
            return arc.Entry(n, t, size, payload, encrypted=encrypted)
    return None


def resources(mod_root: Path | None, type_id: int):
    """(engine name, the file) of every resource of this type the mod holds, in files/ or archives/."""
    if mod_root is None:
        return
    ext = "." + typemap.extension(type_id)
    for base in ("files", "archives"):
        folder = mod_root / base
        if not folder.is_dir():
            continue
        for f in sorted(folder.rglob("*")):
            n = f.name.lower()
            if not f.is_file() or not (n.endswith(ext) or n.endswith(ext + ".yaml")):
                continue
            rel = f.relative_to(folder).as_posix()
            if base == "archives":
                parts = rel.split("/")
                cut = next((i for i, p in enumerate(parts) if p.lower().endswith(".arc")), None)
                if cut is None:
                    continue
                rel = "/".join(parts[cut + 1:])
            try:
                name, tid = fsmap.decode_path(rel[:-5] if rel.lower().endswith(".yaml") else rel)
            except Exception:
                continue
            if tid == type_id:
                yield name.decode("latin-1"), f


def read(f: Path) -> bytes:
    """A mod file's resource bytes (its YAML made binary)."""
    raw = f.read_bytes()
    if f.name.lower().endswith(".yaml"):
        return params.yaml_to_resource(params.decode_text(raw, str(f)), str(f))
    return raw


def group_ids(game, idx, mod_root: Path | None, name: bytes) -> tuple[set[int], int | None]:
    """What a new record in layout ``name`` keeps clear of, and the bound its id stays under while it can: the ids the
    game's own copy of the layout has (a machine of the game may name one the mod removed), the ids its group uses
    in its other layouts, as the mod holds them, else the game's (the game finds a placement by group and id, and
    every group in the game uses each id once across its layouts), and for an enemy group its kill record's bits
    (``lot.KILL_BITS``: ids past 31 wrap onto another placement's)."""
    from . import lot

    LOT = typemap.BY_EXT["lot"]
    label = name.decode("latin-1")

    def ids(data: bytes) -> set[int]:
        try:
            return {r.id for r in lot.parse(data).records}
        except RiftError:
            return set()

    def game_data(n: bytes) -> bytes | None:
        arcs = idx.archives_with(n, LOT)
        e = _entry(game.vanilla_arc(arcs[0]), n, LOT) if arcs else None
        return e.data() if e is not None else None

    own = game_data(name)
    reserved = ids(own) if own is not None else set()
    ln = lot.parse_name(label)
    if ln is None or ln.type == "s":
        return reserved, None
    key = (ln.stage, ln.type, ln.number)

    def same(n: str) -> bool:
        m = lot.parse_name(n)
        return m is not None and (m.stage, m.type, m.number) == key and n.lower() != label.lower()

    others: dict[str, object] = {}
    for n in idx.names_under(f"scr\\st{ln.stage:03d}\\etc\\", LOT):
        if same(n.decode("latin-1")):
            others[n.decode("latin-1").lower()] = n
    for n, f in resources(mod_root, LOT):
        if same(n):
            others[n.lower()] = f                 # the mod's copy, not the game's
    for held in others.values():
        data = read(held) if isinstance(held, Path) else game_data(held)
        if data is not None:
            reserved |= ids(data)
    return reserved, (lot.KILL_BITS if ln.type == "e" else None)


def save(out: Path, data: bytes, name: bytes, type_id: int) -> None:
    if out.suffix == ".yaml":
        text = params.resource_to_yaml(data, name.decode("latin-1"), type_id)
        if text is None:
            raise RiftError(f"{out.name}: this resource has no YAML form")
        arcfolder.write_file(out, text.encode("utf-8"))
    else:
        arcfolder.write_file(out, data)
