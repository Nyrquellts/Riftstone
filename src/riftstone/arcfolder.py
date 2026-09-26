"""Unpack an archive to an editable folder and pack it back, verified.

A folder carries riftstone-arc.json: the archive's resources in their original
order with exact engine names, type ids, flags and content hashes.  XFS
parameter resources are written as YAML (``name.ext.yaml``) unless --raw.
Packing recompresses every resource (zlib level 6, as Capcom did), so an
untouched folder packs back to the original archive byte for byte; files
added to the folder are appended; a listed file that is missing stops the
build instead of silently dropping a resource the game may need.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from . import arc, fsmap, params, typemap
from .errors import BuildError, FormatError, RiftError, UnsafePathError

MANIFEST = "riftstone-arc.json"
SCHEMA = "riftstone.arc-folder/1"
IGNORED = {MANIFEST.lower(), "desktop.ini", "thumbs.db", ".ds_store"}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _unique_path(rel: str, taken: set[str]) -> str:
    """Escape letters until the path no longer collides case-insensitively (Windows)."""
    if rel.lower() not in taken:
        return rel
    head, _, last = rel.rpartition("/")
    stem, dot, ext = last.rpartition(".")
    chars = list(stem)
    for i, ch in enumerate(chars):
        if ch.isalpha():
            chars[i] = f"%{ord(ch):02X}"
            cand = (head + "/" if head else "") + "".join(chars) + dot + ext
            if cand.lower() not in taken:
                return cand
    raise UnsafePathError(f"cannot give {rel!r} a unique file name")


def write_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".riftstone-tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


@dataclass
class UnpackResult:
    folder: Path
    resources: int
    as_yaml: int
    bytes: int


def unpack(arc_file: Path, out: Path, yaml: bool = True, archive_name: str | None = None, progress=None) -> UnpackResult:
    raw = Path(arc_file).read_bytes()
    a = arc.Archive.parse(raw)
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise RiftError(f"{out} already exists and is not empty; choose another folder or remove it first")
    out.mkdir(parents=True, exist_ok=True)
    taken: set[str] = set()
    entries = []
    as_yaml = total = 0
    for e in a.entries:
        data = e.data()
        rel = _unique_path(fsmap.encode_name(e.name, e.type_id), taken)
        use_yaml = yaml and params.is_editable_resource(data, e.type_id)
        if use_yaml:
            try:
                text = params.resource_to_yaml(data, e.name.decode("latin-1"), e.type_id)
                if text is None or params.yaml_to_resource(text) != data:
                    raise FormatError("resource", "YAML would not rebuild this resource exactly")
                payload = text.encode("utf-8")
                rel_file = rel + ".yaml"
                as_yaml += 1
            except (FormatError, RiftError):
                use_yaml = False
        if not use_yaml:
            payload = data
            rel_file = rel
        taken.add(rel.lower())
        write_file(out / rel_file, payload)
        total += len(data)
        entries.append({"path": rel_file, "name": e.name.decode("latin-1"), "type": f"{e.type_id:08x}",
                        "class": typemap.class_name(e.type_id), "flags": e.flags, "sha256": _sha(data),
                        "yaml": use_yaml})
        if progress:
            progress.advance(1, rel)
    manifest = {"schema": SCHEMA, "archive": archive_name or Path(arc_file).stem,
                "source_sha256": _sha(raw), "source_size": len(raw), "version": a.version, "entries": entries}
    if a.encrypted:
        manifest["encrypted"] = True   # ARCC (Dragon's Dogma Online): pack encrypts again
    write_file(out / MANIFEST, json.dumps(manifest, indent=1, ensure_ascii=False).encode("utf-8"))
    return UnpackResult(out, len(entries), as_yaml, total)


@dataclass
class PackResult:
    data: bytes
    unchanged: int = 0
    changed: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    identical_to_source: bool = False
    source_sha256: str | None = None


_WIN_BAD = set('<>:"|?*') | {chr(c) for c in range(32)}
_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"{p}{i}" for p in ("COM", "LPT") for i in range(1, 10)}


def safe_member(folder: Path, rel: str) -> Path:
    """folder/rel, refusing anything that would leave the folder or that Windows would
    reinterpret: drive letters, '..', ':' (NTFS alternate streams), reserved device
    names, and trailing dots or spaces (silently stripped).  Manifests and page
    requests are untrusted input; paths Riftstone writes (fsmap) never need any of these."""
    if not isinstance(rel, str) or not rel:
        raise UnsafePathError(f"bad path {rel!r}")
    parts = rel.replace("\\", "/").split("/")
    for p in parts:
        if (p in ("", ".", "..") or any(c in _WIN_BAD for c in p) or p[-1] in " ."
                or p.split(".")[0].upper().rstrip(" ") in _WIN_RESERVED):
            raise UnsafePathError(f"path {rel!r} leaves the folder or uses a name Windows reinterprets")
    target = folder.joinpath(*parts)
    root = folder.resolve()
    if root not in target.resolve().parents:
        raise UnsafePathError(f"{MANIFEST}: path {rel!r} resolves outside the folder")
    return target


def read_resource_file(path: Path, rel: str) -> bytes:
    data = path.read_bytes()
    if rel.lower().endswith(".yaml"):
        return params.yaml_to_resource(params.decode_text(data, rel), source=rel)
    return data


def pack(folder: Path) -> PackResult:
    folder = Path(folder)
    mpath = folder / MANIFEST
    if not mpath.is_file():
        raise RiftError(f"{folder} has no {MANIFEST}; unpack an archive with Riftstone first")
    def no_constants(c):
        raise ValueError(f"{c} is not allowed")

    try:
        manifest = json.loads(mpath.read_text(encoding="utf-8"), parse_constant=no_constants)
    except (ValueError, UnicodeDecodeError, RecursionError) as e:
        raise RiftError(f"{MANIFEST} is damaged: {e}") from None
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        raise RiftError(f"{MANIFEST} is not a Riftstone archive manifest")
    result = PackResult(b"", source_sha256=manifest.get("source_sha256"))
    enc = manifest.get("encrypted", False)
    if type(enc) is not bool:
        raise RiftError(f"{MANIFEST}: 'encrypted' must be true or false")
    entries: list[arc.Entry] = []
    expected: dict[tuple[bytes, int], str] = {}
    listed: set[str] = set()
    items = manifest.get("entries")
    if not isinstance(items, list):
        raise RiftError(f"{MANIFEST} has no entry list")
    for item in items:
        # Every field is checked for its exact JSON type: a hand-edited or hostile manifest
        # gets a clear refusal, never a crash.
        rel, name_s, type_s = (item.get(k) if isinstance(item, dict) else None for k in ("path", "name", "type"))
        flags = item.get("flags", arc.DEFAULT_FLAGS) if isinstance(item, dict) else None
        want_sha = item.get("sha256") if isinstance(item, dict) else None
        if not (isinstance(rel, str) and isinstance(name_s, str) and isinstance(type_s, str)
                and isinstance(want_sha, str) and type(flags) is int):
            raise RiftError(f"{MANIFEST}: damaged entry {str(item)[:120]}")
        try:
            name = name_s.encode("latin-1")
            type_id = int(type_s, 16)
        except (ValueError, UnicodeEncodeError):
            raise RiftError(f"{MANIFEST}: damaged entry {str(item)[:120]}") from None
        if not 0 <= type_id <= 0xFFFFFFFF or not 0 <= flags <= 7:
            raise RiftError(f"{MANIFEST}: entry {rel!r} has an out-of-range type or flags")
        f = safe_member(folder, rel)
        listed.add(rel.lower())
        if not f.is_file():
            alt = rel[:-5] if rel.endswith(".yaml") else rel + ".yaml"
            if safe_member(folder, alt).is_file():
                f, rel = safe_member(folder, alt), alt
                listed.add(alt.lower())
            else:
                raise BuildError(f"{rel} is listed in {MANIFEST} but missing. Put it back, or remove its entry "
                                 "from the manifest if you really mean to delete the resource.")
        data = read_resource_file(f, rel)
        entries.append(arc.Entry.from_data(name, type_id, data, flags, enc))
        if (name, type_id) in expected:
            raise BuildError(f"{MANIFEST} lists {rel} twice")
        expected[(name, type_id)] = _sha(data)
        if _sha(data) == want_sha:
            result.unchanged += 1
        else:
            result.changed.append(rel)
    for f in sorted(folder.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(folder).as_posix()
        if rel.lower() in listed or f.name.lower() in IGNORED or f.name.endswith((".riftstone-tmp", ".bak", "~")):
            continue
        target = rel[:-5] if rel.lower().endswith(".yaml") else rel
        name, type_id = fsmap.decode_path(target)
        if (name, type_id) in expected:
            raise BuildError(f"{rel} duplicates a resource already listed in {MANIFEST}")
        data = read_resource_file(f, rel)
        entries.append(arc.Entry.from_data(name, type_id, data, encrypted=enc))
        expected[(name, type_id)] = _sha(data)
        result.added.append(rel)
    if manifest.get("version", arc.VERSION) != arc.VERSION:
        raise RiftError(f"{MANIFEST}: archive version {manifest.get('version')!r}; Dragon's Dogma PC uses 7")
    built = arc.Archive(entries, encrypted=enc).build()
    arc.verify_build(built, expected)
    result.data = built
    result.identical_to_source = _sha(built) == manifest.get("source_sha256")
    return result
