"""A resource as a mod has it -- its YAML or its binary -- else the game's original; and back.

Commands that change a resource inside a mod (text, items, spawns) load it with ``load``
and write it with ``save``, which keeps the form the mod already uses."""
from __future__ import annotations

from pathlib import Path

from . import arc, arcfolder, fsmap, params
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


def save(out: Path, data: bytes, name: bytes, type_id: int) -> None:
    if out.suffix == ".yaml":
        text = params.resource_to_yaml(data, name.decode("latin-1"), type_id)
        if text is None:
            raise RiftError(f"{out.name}: this resource has no YAML form")
        arcfolder.write_file(out, text.encode("utf-8"))
    else:
        arcfolder.write_file(out, data)
