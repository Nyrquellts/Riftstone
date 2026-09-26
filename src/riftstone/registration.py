"""Offline DDDA registration through measured ITL/GMD/ARC/ARCS codecs.

Builds new archive copies. Never grows engine ID tables, installs files, or
claims that a new resource name registers a native enemy class.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import tempfile

from . import arc, arcref, fsmap, gmd, itl, params, prp, typemap
from .errors import RiftError
from .game import Game

ITL = typemap.BY_EXT["itl"]
GMD = typemap.BY_EXT["gmd"]
ARCS = typemap.BY_EXT["arc"]
PRP = typemap.BY_EXT["prp"]
ITEM_LIST = (b"etc\\item\\itemList", ITL)
CAPACITY = 1901


def _keys(value, allowed, required=()):
    if not isinstance(value, dict) or set(value)-set(allowed) or set(required)-set(value):
        raise RiftError(f"expected fields {sorted(allowed)}, required {sorted(required)}")


def _integer(value, name, low=0, high=0xffffffff):
    if type(value) is not int or not low <= value <= high:
        raise RiftError(f"{name} must be an integer in {low}..{high}")
    return value


def _string(value, name, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()) or "\0" in value or len(value)>65536:
        raise RiftError(f"invalid {name}")
    return value


def archive_name(value):
    _string(value, "archive")
    dummy = Game(Path("registration-root"))
    path = dummy.arc_path(value)
    return path.relative_to(dummy.native).with_suffix("").as_posix()


def parse_request(raw: bytes):
    if len(raw)>16*1024*1024:
        raise RiftError("registration request exceeds 16 MiB")
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result: raise RiftError(f"duplicate request field {key}")
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda token: (_ for _ in ()).throw(RiftError(f"nonfinite {token}")))
    except (ValueError, RecursionError) as exc:
        raise RiftError(f"invalid registration request: {exc}") from None


@dataclass
class Build:
    archives: dict[str, bytes]
    report: dict


def build(sources: dict[str, bytes], request: dict) -> Build:
    """Pure transactional build: input bytes are never changed, all output reparsed."""
    _keys(request, {"schema", "archives", "items", "param_copies", "enemy_archetypes"}, {"schema", "archives"})
    if request["schema"] != "riftstone.registration/1": raise RiftError("unknown registration schema")
    if not isinstance(request.get("enemy_archetypes", []), list): raise RiftError("enemy_archetypes must be a list")
    if request.get("enemy_archetypes"):
        raise RiftError("UNAVAILABLE: new enemy archetype lookup/class registration has no verified DDDA profile")
    declarations = request["archives"]
    if not isinstance(declarations, list) or not 1 <= len(declarations) <= 256:
        raise RiftError("archives must list 1..256 pinned input archives")
    archives, before = {}, {}
    for declaration in declarations:
        _keys(declaration, {"name", "sha256"}, {"name", "sha256"})
        name = archive_name(declaration["name"])
        if name.casefold() in {existing.casefold() for existing in archives} or name not in sources: raise RiftError(f"duplicate or missing archive {name}")
        raw = sources[name]
        digest = hashlib.sha256(raw).hexdigest()
        if declaration["sha256"] != digest: raise RiftError(f"input SHA-256 mismatch: {name}")
        parsed = arc.Archive.parse(raw)
        if parsed.encrypted or parsed.version != 7: raise RiftError("registrar accepts DDDA ARC v7 only")
        archives[name], before[name] = parsed, digest
    if set(sources) != set(archives): raise RiftError("source set differs from pinned archive set")
    # A duplicated shared resource must have one meaning across every supplied holder.
    def shared(key):
        hits = [entry.data() for value in archives.values() for entry in value.entries if entry.key == key]
        if not hits: raise RiftError(f"missing required shared resource {fsmap.encode_name(*key)}")
        if any(raw != hits[0] for raw in hits[1:]): raise RiftError(f"conflicting copies of {fsmap.encode_name(*key)}")
        return hits[0]
    def replace_shared(key, raw):
        for value in archives.values():
            if value.find(*key) is not None: value.put(*key, raw)
    records = request.get("items", [])
    copies = request.get("param_copies", [])
    if not isinstance(records,list) or len(records)>CAPACITY or not isinstance(copies,list) or len(copies)>1024:
        raise RiftError("items/param_copies must be bounded lists")
    if not records and not copies: raise RiftError("request has no supported registrations")
    allocations = []
    if records:
        table = itl.parse(shared(ITEM_LIST))
        if table.stamp != 0x01330611 or len(table.records)>CAPACITY:
            raise RiftError("UNAVAILABLE: unknown ITL stamp or list exceeds measured 1901-slot engine profile")
        messages = {}
        for suffix in gmd.SUFFIXES.values():
            for kind in ("itemName", "itemInfo"):
                key = (f"id\\message\\item\\{kind}_{suffix}".encode(), GMD)
                doc = gmd.parse(shared(key))
                if doc.version != gmd.VERSION or gmd.SUFFIXES.get(doc.language) != suffix or len(doc.messages)<len(table.records):
                    raise RiftError(f"text language/version/slot coverage mismatch: {key[0]!r}")
                messages[suffix,kind] = (key,doc)
        english = messages["eng","itemName"][1]
        free = {i for i,record in enumerate(table.records)
                if english.messages[i].text == "Unknown Item" and itl.ItemList.prices(record) == (0,0)}
        used_keys = set()
        for entry in records:
            _keys(entry, {"key", "template", "name", "description", "id", "buy", "sell", "weight", "translations"},
                  {"key", "template", "name"})
            key = _string(entry["key"], "item key")
            if key in used_keys: raise RiftError(f"duplicate item key {key}")
            used_keys.add(key)
            template = _integer(entry["template"], "template", high=len(table.records)-1)
            if english.messages[template].text == "Unknown Item": raise RiftError("template is an unused item slot")
            if not free: raise RiftError("no unused item slot remains; table growth is unavailable")
            slot = _integer(entry.get("id", min(free)), "id", high=len(table.records)-1)
            if slot not in free: raise RiftError(f"item slot {slot} is occupied or already reserved")
            free.remove(slot)
            name = _string(entry["name"], "item name")
            description = _string(entry.get("description", ""), "description", True)
            translations = entry.get("translations", {})
            _keys(translations, set(gmd.SUFFIXES.values()))
            record = bytearray(table.records[template]); itl.ItemList.set_id(record, slot)
            for field,offset in (("buy",0x48),("sell",0x4c)):
                if field in entry: struct.pack_into("<I",record,offset,_integer(entry[field],field))
            if "weight" in entry:
                weight = entry["weight"]
                if type(weight) not in (int,float) or not math.isfinite(weight) or not 0<=weight<=1e6:
                    raise RiftError("weight must be a finite value in 0..1000000")
                struct.pack_into("<f",record,0x44,weight)
            table.records[slot] = record
            for suffix in gmd.SUFFIXES.values():
                localized = translations.get(suffix, {})
                _keys(localized, {"name", "description"})
                messages[suffix,"itemName"][1].messages[slot].text = _string(localized.get("name",name),"localized name")
                messages[suffix,"itemInfo"][1].messages[slot].text = _string(localized.get("description",description),"localized description",True)
            if messages["eng","itemName"][1].messages[slot].text == "Unknown Item":
                raise RiftError("new item cannot retain the unused-slot placeholder name")
            allocations.append({"key":key,"id":slot,"template":template})
        replace_shared(ITEM_LIST, itl.build(table))
        for key,doc in messages.values(): replace_shared(key,gmd.build(doc))
    added = []
    for copy in copies:
        _keys(copy, {"from_archive", "from_resource", "to_archive", "to_resource", "yaml"},
              {"from_archive", "from_resource", "to_archive", "to_resource"})
        source, destination = archive_name(copy["from_archive"]), archive_name(copy["to_archive"])
        if source not in archives or destination not in archives: raise RiftError("param copy archive is not pinned")
        old, new = fsmap.decode_path(_string(copy["from_resource"],"source resource")), fsmap.decode_path(_string(copy["to_resource"],"target resource"))
        if old[1] != PRP or new[1] != PRP: raise RiftError("only measured PRP resource variants can be copied")
        entry = archives[source].find(*old)
        if entry is None or archives[destination].find(*new) is not None: raise RiftError("missing source or occupied destination param")
        original = prp.parse(entry.data())
        raw = params.yaml_to_resource(_string(copy["yaml"],"param YAML")) if "yaml" in copy else entry.data()
        if prp.parse(raw).root_class.type_id != original.root_class.type_id: raise RiftError("PRP class changed")
        archives[destination].put(*new,raw)
        added.append({"archive":destination,"resource":fsmap.encode_name(*new),"kind":"parameter_resource_variant",
                      "enemy_archetype_registered":False})
    # Rebuild references only where a referenced directory changed, preserving
    # pre-existing vanilla exceptions for all other archive references.
    changed_directories = {name for name,value in archives.items()
                           if [entry.key for entry in value.entries] != [entry.key for entry in arc.Archive.parse(sources[name]).entries]}
    references = []
    for name,value in archives.items():
        for entry in list(value.entries):
            target = arcref.target(entry.name) if entry.type_id == ARCS else None
            if target in changed_directories:
                prior = arcref.parse(entry.data())
                if prior.version != 7: raise RiftError("unsupported ARCS reference revision")
                rebuilt = arcref.for_names([resource.key for resource in archives[target].entries])
                value.put(*entry.key, arcref.build(rebuilt))
                references.append({"archive":name,"target":target})
    outputs, hashes = {}, {}
    for name,value in archives.items():
        expected = {entry.key:arc.sha256(entry.data()) for entry in value.entries}
        raw = value.build(); arc.verify_build(raw,expected)
        outputs[name] = raw; hashes[name] = arc.sha256(raw)
    return Build(outputs, {"schema":"riftstone.registration-result/1", "status":"BUILT",
        "input_sha256":before,"output_sha256":hashes,"items":allocations,"param_resources":added,
        "rebuilt_references":references,"table_growth":False,"gameplay":"UNKNOWN","requires_dll":False,
        "coverage":"only the explicitly pinned archive set; external resource copies/references are not inspected",
        "claim":"offline ARC byte structure and resource content verified; native class registration not claimed"})


def _read_bounded(path: Path, maximum: int):
    if path.stat().st_size>maximum: raise RiftError(f"input exceeds {maximum} bytes: {path.name}")
    with path.open("rb") as stream: data=stream.read(maximum+1)
    if len(data)>maximum: raise RiftError(f"input grew beyond {maximum} bytes: {path.name}")
    return data


def _run(request_path: Path, source_root: Path, output: Path | None = None):
    request = parse_request(_read_bounded(request_path,16*1024*1024))
    _keys(request, {"schema","archives","items","param_copies","enemy_archetypes"}, {"schema","archives"})
    if not isinstance(request["archives"], list): raise RiftError("archives must be a list")
    source_root = source_root.resolve(strict=True)
    sources, paths = {}, {}
    for declaration in request["archives"]:
        _keys(declaration,{"name","sha256"},{"name","sha256"})
        name = archive_name(declaration["name"])
        path = (source_root / (name+".arc")).resolve(strict=True)
        if not path.is_relative_to(source_root): raise RiftError("source archive escapes root")
        paths[name] = path; sources[name] = _read_bounded(path,arc.MAX_ARCHIVE)
    result = build(sources,request)
    if output is None: return result.report
    output = output.resolve()
    if output.exists() or output.is_relative_to(source_root) or source_root.is_relative_to(output):
        raise RiftError("output must be a new directory outside the input root")
    if any((parent/"DDDA.exe").exists() or (parent/"DDO.exe").exists() for parent in (output,*output.parents)):
        raise RiftError("registration output cannot be inside a game installation")
    output.parent.mkdir(parents=True,exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=".registration-",dir=output.parent))
    for name,raw in result.archives.items():
        path = staged / "nativePC" / (name+".arc"); path.parent.mkdir(parents=True,exist_ok=True)
        with path.open("xb") as stream: stream.write(raw)
        if arc.sha256(path.read_bytes()) != result.report["output_sha256"][name]: raise RiftError("staged output hash mismatch")
    for name,path in paths.items():
        if arc.sha256(_read_bounded(path,arc.MAX_ARCHIVE)) != result.report["input_sha256"][name]: raise RiftError("source changed during build; staged output was not published")
    with (staged/"registration.json").open("x",encoding="utf-8") as stream: json.dump(result.report,stream,indent=2)
    # Windows rename refuses a destination that appeared after preflight.
    os.rename(staged,output)
    return result.report


def run(request_path: Path, source_root: Path, output: Path | None = None):
    try:
        return _run(request_path,source_root,output)
    except OSError as exc:
        raise RiftError(f"registration filesystem operation failed: {exc}") from None
