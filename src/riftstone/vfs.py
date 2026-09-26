"""Read-only overlay snapshots and deterministic base-relative semantic merging.

This module never installs a mod or changes a base archive. Ordered priorities
resolve conflicting edits; this is a three-way merge policy, not a CRDT.
"""
from __future__ import annotations

from dataclasses import dataclass
import copy
import hashlib
import io
import json
import math
import struct
from pathlib import Path
from types import MappingProxyType

from . import arc, params, yamlish
from .errors import RiftError, UnsafePathError

MAX_BYTES = 256 * 1024 * 1024
MAX_FILES = 100000
MISSING = object()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def path_key(value: str) -> str:
    """Normalize a Windows filesystem path, never an engine resource identifier."""
    if not isinstance(value, str) or not value:
        raise UnsafePathError("VFS path must be a nonempty relative filename")
    parts = value.replace("\\", "/").split("/")
    reserved = {"con", "prn", "aux", "nul"} | {f"{p}{i}" for p in ("com", "lpt") for i in range(1, 10)}
    for part in parts:
        if (part in ("", ".", "..") or part.endswith((" ", ".")) or
                any(ord(c) < 32 or c in '<>:"|?*' for c in part) or
                part.split(".", 1)[0].lower() in reserved):
            raise UnsafePathError(f"unsafe VFS path: {value!r}")
    return "/".join(parts).lower()


def _json(data: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise RiftError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    def invalid(value): raise RiftError(f"non-finite JSON value: {value}")
    try:
        return json.loads(data.decode("utf-8-sig"), object_pairs_hook=pairs, parse_constant=invalid)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise RiftError(f"invalid VFS JSON: {exc}") from None


@dataclass(frozen=True)
class _Atom:
    text: str
    style: str


def _tree(node):
    if isinstance(node, yamlish.Map): return {k.text: _tree(v) for k, v in node.items}
    if isinstance(node, yamlish.Seq): return [_tree(v) for v in node.items]
    return _Atom(node.text, node.style)


def _node(tree):
    if isinstance(tree, dict): return yamlish.Map([(yamlish.Scalar(k, "plain" if yamlish.quote(k) == k else "double"), _node(v)) for k, v in tree.items()])
    if isinstance(tree, list): return yamlish.Seq([_node(v) for v in tree])
    return yamlish.Scalar(tree.text, tree.style)


def _same(a, b):
    if a is MISSING or b is MISSING: return a is b
    if type(a) is not type(b): return False
    if isinstance(a, float): return struct.pack("!d", a) == struct.pack("!d", b)
    if isinstance(a, dict): return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list): return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return a == b


def _summary(value):
    if value is MISSING: return {"deleted": True}
    if isinstance(value, bytes): return {"bytes": len(value), "sha256": digest(value)}
    if isinstance(value, _Atom): return {"text": value.text, "style": value.style}
    if isinstance(value, dict): return {str(k): _summary(v) for k, v in value.items()}
    if isinstance(value, list): return [_summary(v) for v in value]
    return value


def merge_tree(base, variants: list[tuple[str, object]], path=""):
    """All variants are compared to the same base; arrays are atomic values.

    Caller supplies low-to-high priority. Inputs are copied, never mutated.
    Deletion versus nested edits is a conflict at their common ancestor.
    """
    if any(not isinstance(name, str) or not name for name, _ in variants) or len({name for name, _ in variants}) != len(variants):
        raise RiftError("merge layer names must be unique")
    for document in [base] + [value for _, value in variants]:
        pending, nodes = [(document, 0)], 0
        while pending:
            value, depth = pending.pop()
            nodes += 1
            if depth > 128 or nodes > 1000000: raise RiftError("merge tree exceeds depth/node budget")
            if isinstance(value, dict):
                if any(not isinstance(key, str) for key in value): raise RiftError("merge mapping keys must be strings")
                pending.extend((v, depth + 1) for v in value.values())
            elif isinstance(value, list): pending.extend((v, depth + 1) for v in value)
            elif isinstance(value, float) and not math.isfinite(value): raise RiftError("merge values must be finite")
            elif value is not MISSING and not isinstance(value, (str, int, float, bool, bytes, _Atom, type(None))):
                raise RiftError("unsupported merge value")
    conflicts = []
    def visit(old, edits, pointer, depth):
        if depth > 128: raise RiftError("merge tree exceeds 128 levels")
        changed = [(name, value) for name, value in edits if not _same(old, value)]
        if not changed: return MISSING if old is MISSING else copy.deepcopy(old)
        if all(_same(changed[0][1], value) for _, value in changed):
            return MISSING if changed[0][1] is MISSING else copy.deepcopy(changed[0][1])
        if all(isinstance(value, dict) for _, value in changed) and (isinstance(old, dict) or old is MISSING):
            original = old if isinstance(old, dict) else {}
            keys = set(original)
            for _, value in changed: keys.update(value)
            result = {}
            for key in sorted(keys):
                if not isinstance(key, str): raise RiftError("merge mapping keys must be strings")
                token = key.replace("~", "~0").replace("/", "~1")
                value = visit(original.get(key, MISSING), [(name, value.get(key, MISSING)) for name, value in changed], pointer + "/" + token, depth + 1)
                if value is not MISSING: result[key] = value
            return result
        winner, value = changed[-1]
        conflicts.append({"path": pointer or "/", "winner": winner, "base": _summary(old),
                          "edits": [{"layer": name, "value": _summary(v)} for name, v in changed]})
        return MISSING if value is MISSING else copy.deepcopy(value)
    return visit(base, variants, path, 0), conflicts


def merge_bytes(base: bytes | None, variants: list[tuple[str, bytes]], path: str, type_id=None):
    """Merge editable formats through existing strict parsers; opaque data uses priority."""
    changed = [(n, b) for n, b in variants if b != base]
    if not changed: return base or b"", []
    if all(b == changed[0][1] for _, b in changed): return changed[0][1], []
    if base is not None and base[:4] in (arc.MAGIC, arc.MAGIC_ENC):
        return _merge_archive(base, changed, path)
    if path.lower().endswith(".json"):
        old = _json(base) if base is not None else MISSING
        tree, conflicts = merge_tree(old, [(n, _json(b)) for n, b in changed], path)
        return (json.dumps(tree, ensure_ascii=True, allow_nan=False, sort_keys=True, indent=2) + "\n").encode(), conflicts
    yaml = path.lower().endswith(".yaml")
    if base is not None:
        original = params.decode_text(base, path) if yaml else params.resource_to_yaml(base, type_id=type_id)
        if original is not None:
            texts = [(n, params.decode_text(b, path) if yaml else params.resource_to_yaml(b, type_id=type_id)) for n, b in changed]
            if any(text is None for _, text in texts): raise RiftError(f"{path}: a mod changed the resource format")
            tree, conflicts = merge_tree(_tree(yamlish.parse(original)), [(n, _tree(yamlish.parse(t))) for n, t in texts], path)
            text = yamlish.emit(_node(tree))
            output = text.encode("utf-8") if yaml else params.yaml_to_resource(text, source=path)
            # Parse again: a field merge must still satisfy the format's schema.
            if yaml: params.yaml_to_resource(text, source=path)
            else: params.resource_to_yaml(output, type_id=type_id)
            return output, conflicts
    winner, output = changed[-1]
    return output, [{"path": path, "winner": winner, "kind": "opaque-file-priority",
                     "base": _summary(base if base is not None else MISSING),
                     "edits": [{"layer": n, "value": _summary(b)} for n, b in changed]}]


def _merge_archive(base_bytes, variants, path):
    from .mod import load_order
    base = arc.Archive.parse(base_bytes)
    documents = [(name, arc.Archive.parse(data)) for name, data in variants]
    if any(a.version != base.version or a.encrypted != base.encrypted for _, a in documents):
        raise RiftError(f"{path}: mixed archive revisions/encryption")
    original = {e.key: e for e in base.entries}
    maps = [(name, {e.key: e for e in a.entries}) for name, a in documents]
    all_keys = set(original)
    for _, mapping in maps: all_keys.update(mapping)
    order = [e.key for e in base.entries] + sorted(all_keys - set(original))
    output, conflicts, added, changed_keys = [], [], set(), set()
    for key in order:
        old_entry = original.get(key)
        old_data = old_entry.data() if old_entry else MISSING
        values = [(n, mapping[key].data() if key in mapping else MISSING) for n, mapping in maps]
        changes = [(n, v) for n, v in values if not _same(v, old_data)]
        flags_changed = any(key in m and old_entry is not None and m[key].flags != old_entry.flags for _, m in maps)
        if not changes and not flags_changed:
            if old_entry: output.append(old_entry)
            continue
        label = key[0].decode("latin-1") + ":%08x" % key[1]
        location = path + "!/" + label
        if not changes:
            selected, found = old_data, []
        elif any(v is MISSING for _, v in changes):
            selected, found = merge_tree(old_data, changes, location)
        else:
            selected, found = merge_bytes(None if old_data is MISSING else old_data, changes, location, key[1])
        conflicts.extend(found)
        if selected is MISSING: continue
        flags_old = old_entry.flags if old_entry else MISSING
        flags, flag_conflicts = merge_tree(flags_old, [(n, m[key].flags) for n, m in maps if key in m], location + "/@flags")
        conflicts.extend(flag_conflicts)
        output.append(arc.Entry.from_data(key[0], key[1], selected, flags=flags, encrypted=base.encrypted))
        changed_keys.add(key)
        if key not in original: added.add(key)
    merged = arc.Archive(output, base.version, base.encrypted)
    load_order(merged, added, changed_keys)
    data = merged.build()
    check = arc.Archive.parse(data)
    if [(e.key, e.data(), e.flags) for e in check.entries] != [(e.key, e.data(), e.flags) for e in merged.entries]:
        raise RiftError(f"{path}: merged archive failed independent reparse")
    return data, conflicts


@dataclass(frozen=True)
class Layer:
    name: str
    priority: int
    files: dict[str, bytes]


class Snapshot:
    """Immutable bytes: open handles keep their original contents across new snapshots."""
    def __init__(self, base: dict[str, bytes], layers: list[Layer], semantic=True):
        if len({l.name for l in layers}) != len(layers): raise RiftError("layer names must be unique")
        def normalize(files):
            out = {}
            for path, data in files.items():
                key = path_key(path)
                if key in out: raise RiftError(f"case-colliding VFS path: {path}")
                if not isinstance(data, bytes) or len(data) > MAX_BYTES: raise RiftError("invalid or oversized VFS file")
                out[key] = data
            return out
        base = normalize(base)
        ordered = sorted(layers, key=lambda l: (l.priority, l.name))
        layers = [Layer(l.name, l.priority, normalize(l.files)) for l in ordered]
        paths = set(base)
        for layer in layers: paths.update(layer.files)
        if len(paths) > MAX_FILES: raise RiftError("VFS file count exceeds limit")
        for path in paths:
            pieces = path.split("/")
            if any("/".join(pieces[:i]) in paths for i in range(1, len(pieces))):
                raise RiftError("VFS file/directory collision: " + path)
        files, provenance, conflicts, total = {}, {}, [], 0
        for path in sorted(paths):
            edits = [(l.name, l.files[path]) for l in layers if path in l.files]
            if not edits: data, found = base[path], []
            elif semantic: data, found = merge_bytes(base.get(path), edits, path)
            else: data, found = edits[-1][1], []
            total += len(data)
            if total > 1024 * 1024 * 1024: raise RiftError("VFS snapshot exceeds 1 GiB byte budget")
            files[path] = data
            conflicts.extend(found)
            provenance[path] = {"sha256": digest(data), "bytes": len(data), "layers": [n for n, _ in edits],
                                "base_sha256": digest(base[path]) if path in base else None}
        self.files = MappingProxyType(files)
        self.report = {"schema": "riftstone.vfs/1", "status": "SNAPSHOT_READY", "live_game_mounted": False,
                       "policy": "base-relative-field-merge, ascending (priority,name), last edit wins" if semantic else "last layer wins",
                       "layers": [{"name": l.name, "priority": l.priority} for l in layers],
                       "files": provenance, "conflicts": conflicts}

    def open(self, path):
        try: return io.BytesIO(self.files[path_key(path)])
        except KeyError: raise FileNotFoundError(path) from None

    def list(self, directory=""):
        prefix = path_key(directory) + "/" if directory else ""
        return sorted({p[len(prefix):].split("/", 1)[0] for p in self.files if p.startswith(prefix)})

    def bundle(self):
        """Deterministic RSV1 transfer to the native runtime, already semantically merged."""
        chunks = [b"RSV1", struct.pack("<I", len(self.files))]
        for path, data in sorted(self.files.items()):
            name = path.encode("utf-8")
            chunks.extend((struct.pack("<II", len(name), len(data)), name, data))
        return b"".join(chunks)


def _read_tree(root: Path):
    root = root.resolve(strict=True)
    if not root.is_dir(): raise RiftError(f"not a directory: {root}")
    result, total = {}, 0
    for item in sorted(root.rglob("*")):
        if not item.is_file(): continue
        resolved = item.resolve(strict=True)
        if not resolved.is_relative_to(root): raise UnsafePathError(f"link escapes VFS layer: {item}")
        size = item.stat().st_size
        total += size
        if size > MAX_BYTES or total > 1024 * 1024 * 1024: raise RiftError("VFS layer exceeds byte budget")
        result[item.relative_to(root).as_posix()] = item.read_bytes()
        if len(result) > MAX_FILES: raise RiftError("VFS layer exceeds file budget")
    return result


def mount_manifest(folder: Path):
    """Load folder/vfs.json into memory. No persistent/live game mount occurs."""
    folder = Path(folder).resolve(strict=True)
    config = _json((folder / "vfs.json").read_bytes())
    if not isinstance(config, dict) or set(config) - {"schema", "base", "layers", "semantic_merge"}:
        raise RiftError("invalid VFS manifest keys")
    if config.get("schema") != "riftstone.vfs/1": raise RiftError("expected schema riftstone.vfs/1")
    if not isinstance(config.get("base"), str) or not isinstance(config.get("layers"), list): raise RiftError("manifest needs base path and layers list")
    if len(config["layers"]) > 256: raise RiftError("VFS supports at most 256 layers")
    if type(config.get("semantic_merge", True)) is not bool: raise RiftError("semantic_merge must be Boolean")
    base_root = (folder / config["base"]).resolve(strict=True)
    layers = []
    roots = [base_root]
    for layer in config["layers"]:
        if (not isinstance(layer, dict) or set(layer) != {"name", "priority", "path"} or
                not isinstance(layer["name"], str) or not layer["name"] or type(layer["priority"]) is not int or not isinstance(layer["path"], str)):
            raise RiftError("each layer needs name, integer priority, and path")
        root = (folder / layer["path"]).resolve(strict=True)
        roots.append(root)
        layers.append(Layer(layer["name"], layer["priority"], _read_tree(root)))
    return Snapshot(_read_tree(base_root), layers, config.get("semantic_merge", True)), roots
