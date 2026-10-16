"""Round-trip every vanilla resource Riftstone claims to understand; report exact results.

    python tools/check_corpus.py [--game PATH] [--only arc,xfs,yaml,ocl,gmd,itl,lot,arcs,tables,flat,gpl,tex,mrl,prp,ean,lmt,weather,lcm,sound,effect,ddo_params,ndp,fca,msgset,sdl,zon,sbc,fsm,nav] [--out report.json] [--jobs N]

--only takes the check names below (and epv, efl, e2d, efs for that part of the effect check); a name that
is no check is refused (exit 2) rather than silently running nothing.  A check that does not apply to the
game (ddo_params and ndp on Dark Arisen) is listed under "skipped" in the report; a run whose every check
was skipped proved nothing and fails.  A check that finds no resource of its kind fails too, unless ABSENT
records that the game has none.  Every archive is read as the game shipped it: Riftstone's verified backup
when an install replaced the file in nativePC (corpus.source), never Riftstone's own output.

arc   every archive: parse, rebuild from recompressed payloads, compare bytes
xfs   every distinct XFS resource: parse -> build, compare bytes; definition
      block regenerated from the parsed classes, compare bytes; string values
      counted, and those that are not text in the file's encoding (UTF-8 for
      Dark Arisen, Shift-JIS for Online; shown as \\udcXX escapes in YAML)
yaml  every distinct XFS resource: XFS -> YAML text -> XFS, compare bytes
ocl   every distinct collision file: parse -> build and -> YAML -> back, compare bytes
gmd   every distinct text file: parse -> build and -> YAML -> back, compare bytes
itl   the item list: parse -> build and -> YAML -> back, compare bytes
lot   every distinct layout file: parse -> build and -> YAML -> back, compare bytes; copy-then-remove
arcs  every distinct archive reference (ARCS): parse -> build, and it lists the referenced archive's directory
      (no more out-of-date or dangling references than measured, ARCS_MEASURED)
tables  every item set / drop table and recipe table: parse -> build and -> YAML -> back
flat  every flat parameter file (ajp, character-creator, itemlv, skl ...): parse -> build and -> YAML -> back
gpl   every enemy group placement file: parse -> build and -> YAML -> back
tex   every texture: parse -> build (byte-exact, cube maps included); and, for every texture a .dds
      can hold, .tex -> .dds -> .tex, compare bytes (those it cannot are counted by reason)
mrl   every material file: parse -> build, compare bytes; shaders + texture bindings read
lmt   every motion list: parse -> build, compare bytes; every keyframe buffer -> keys -> buffer;
      key deltas add up to frames - 1; rotation keys unit length (reported); the port to the other game
      keeps every motion slot and every track
weather  every weather effect / fog / sky file (and DDO's weather tables): parse -> build and -> YAML -> back
lcm   every camera list: parse -> build and -> YAML -> back
sound every sound cue resource (requests, stream requests, random tables, mixers, physics lists, banks,
      area info): parse -> build and -> YAML -> back
effect  every effect provider / list / 2D effect / strip (.epv .efl .e2d .efs): parse -> build; the first
      three also -> YAML -> back
ddo_params  (DDO) every enemy / stage parameter file (cpe pep prs osp sti sal evtr ndp): parse -> build and
      -> YAML -> back
ndp   (DDO) the named enemy parameters as shipped: parse -> build and -> YAML -> back; equal to the local
      server's named_param.ndp.json field for field (when a local server is found; else not_compared);
      every id named in named_param.gmd; the Solo Balance twins fit
fca / msgset / sdl / zon  facial animations, message sets and serial lists, schedulers, zones:
      parse -> build and -> YAML -> back
sbc   every collision mesh: its sections add up to the file under the loader's layout (sbc.py), every
      part keeps its vertices inside its box, a move by a Gransys cell corner changes only the positional
      floats (boxes, tree lanes, vertices), moving back restores every one of them within float32
      rounding (half a float32 step of the moved value plus half a step of the value moved back), and
      moving twice gives the same bytes; every triangle belongs to one part and names three of that part's
      vertices (sbc.triangles), counted with how many stored normals are their corners' own
fsm   every AI state machine: the readable view and fsmcheck (the game's own transition rules, read in
      the executables and run in native/fsm_exec) on every machine; counts what the checks find, and
      each finding names a state and link the file has
nav   every navigation mesh: parse -> build, compare bytes; each link's cost against its triangles' centroids;
      links that have their reverse counted; nav.NAV_OF against DDDA.exe's stage table (Dark Arisen)

The checks run as processes side by side (--jobs N at a time, one per CPU up to 16 unless told; --jobs 1 runs them one
after another in this process): the long ones (xfs+yaml, ocl, sbc, flat, gmd, lot) are split into shards, shard
I of N taking every resource whose place in a pass is I modulo N (--shard I/N runs one by hand and writes its
part of the report), so each distinct resource is still parsed, rebuilt and round-tripped exactly once.  The
report is the one a single process would write: counts added up, the keys of each tally and the failures in the
order one process would have met them (every shard keeps where it met its own), the verdict worked out from the
added counts; ``seconds`` is the slowest shard's and ``shards`` says how many there were.  Every shard also
digests the sequence of resources it walked; shards that did not walk one sequence (the game changed while the run
was going, or parts run by hand at different times) cannot be added up, so the run fails and the report says
which check ("shard_mismatch").

Every pass reads every archive's table: the game's index (index.py) is a cache that cannot tell an archive that was
swapped for another of the same size and time, and a proof does not take a cache's word.

Reads the game only.  Exit 0 when everything is exact, 1 otherwise.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import struct
import subprocess
import sys
import tempfile
import time
import traceback
import zlib
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from riftstone import arc, corpus, flat, gmd, itl, lot, ocl, tables, typemap, xfs  # noqa: E402
from riftstone.errors import FormatError  # noqa: E402
from riftstone.game import find_game  # noqa: E402

# The checks --only names, in the order they run ("yaml" is the XFS check's YAML round trip, run in the same
# pass), and the effect check's parts, which --only also takes by their report section names.
CHECKS = ("xfs", "yaml", "ocl", "gmd", "itl", "lot", "arcs", "tables", "flat", "gpl", "tex", "mrl", "prp", "ean",
          "lmt", "weather", "lcm", "sound", "effect", "ddo_params", "ndp", "fca", "msgset", "sdl", "zon", "sbc",
          "fsm", "nav", "arc")
EFFECT_PARTS = ("epv", "efl", "e2d", "efs")

# This process's share of each pass of the checks that can be split (--shard I/N): the resources whose place in the
# pass is I modulo N.  None checks everything.  The checks in SHARDS read their resources through _resources.
SHARD: tuple[int, int] | None = None
# Where the resource a check is working on stands, in a shard: (which pass of _resources it is -- flat makes one per
# format -- and its place in that pass).  A shard sees every Nth resource, so what a process would have kept in the
# order it met things (the failures, the keys of a tally) is kept with this and merge_shards puts it back in order.
AT = [0, 0]
# What each pass of this process's check walked (corpus.Walk), in pass order; reported so the shards can be compared.
WALKS: list = []


def _walk(game, types, **kw):
    """corpus.resources for every read of the corpus: the table of every archive is read, never the index's word
    for which archives hold the types (an archive swapped for another of the same size and time looks current to
    it).  This gate is the proof that Riftstone reads the whole game."""
    return corpus.resources(game, types, indexed=False, **kw)


def _resources(game, types):
    """_walk for the main loop of a check that can run as shards: every resource, or this process's share of them
    (the others are not even decompressed), each noted as the one the check is working on (``AT``)."""
    if SHARD is None:
        yield from _walk(game, types)
        return
    AT[0] += 1
    walk = corpus.Walk()
    WALKS.append(walk)
    for r in _walk(game, types, shard=SHARD, walk=walk):
        AT[1] = r.place
        yield r


def _begin() -> None:
    """A check that can run as shards starts counting its passes: the first is pass 1, and no walk is kept yet."""
    AT[:] = [0, 0]
    WALKS.clear()


def _placed(failure: dict) -> dict:
    """A failure as the report keeps it; in a shard, with where it stands in the passes (merge_shards removes it)."""
    if SHARD is not None:
        failure["at"] = [AT[0], AT[1]]
    return failure


def _met(first: dict, key) -> None:
    """In a shard: ``key`` of a tally was met now.  Remembers the first time -- the pass, the place and the turn
    among the keys -- which is what puts the merged tally's keys in the order one process would have met them."""
    if SHARD is not None and key not in first:
        first[key] = [AT[0], AT[1], len(first)]


def _keep_met(section: dict, **first: dict) -> None:
    """In a shard: the keys of the section's tallies and when each was first met (merge_shards uses and drops it)."""
    if SHARD is not None:
        section["first_seen"] = first


def parse_only(text: str) -> tuple[list[str], tuple[str, ...]]:
    """--only -> (the checks to run, in CHECKS order; the effect parts to run).  ValueError for a name that is
    no check, or for none at all: a run that checks nothing must not report "ok"."""
    names = [n.strip() for n in text.split(",") if n.strip()]
    unknown = [n for n in names if n not in CHECKS and n not in EFFECT_PARTS]
    if unknown or not names:
        what = f"not a check: {', '.join(unknown)}" if unknown else "no check named"
        raise ValueError(f"--only: {what} (checks: {', '.join(CHECKS)}; effect parts: {', '.join(EFFECT_PARTS)})")
    parts = tuple(p for p in EFFECT_PARTS if "effect" in names or p in names)
    return [c for c in CHECKS if c in names or (c == "effect" and parts)], parts


def _skip(report, check: str, why: str) -> bool:
    """A check that does not apply to this game: named in the report, and it proves nothing."""
    report.setdefault("skipped", {})[check] = why
    return True


def half_step(v: float) -> float:
    """Half the float32 spacing at v: the most one rounding to float32 moves a value that lands there."""
    a = abs(v)
    if a < 2.0 ** -126:                               # zero and the subnormals: spacing 2^-149
        return 2.0 ** -150
    if not math.isfinite(a):
        return math.inf
    return math.ldexp(1.0, math.frexp(a)[1] - 25)     # a in [2^(e-1), 2^e): spacing 2^(e-24)


def moved_back(original: bytes, moved: bytes, back: bytes, floats) -> tuple[float, int, str | None]:
    """A file moved and moved back, against the original, over the floats the move touches ((offset, axis)
    pairs): (the largest change of a finite value, how many changed, the first value that strays further than
    float32 rounding allows -- half a step at the moved value (the move's rounding) plus half a step at the
    value moved back (the way back's) -- or None).  NaN and infinite values must come back bit for bit."""
    if back == original:
        return 0.0, 0, None
    n = min(len(original), len(moved), len(back)) // 4 * 4
    o_, m_, b_ = (memoryview(x)[:n].cast("f") for x in (original, moved, back))    # little-endian, as the files
    worst, changed, stray = 0.0, 0, None
    for off, _axis in floats:
        if off & 3 or off + 4 > n:
            o, m, b = (struct.unpack_from("<f", x, off)[0] for x in (original, moved, back))
        else:
            o, m, b = o_[off >> 2], m_[off >> 2], b_[off >> 2]
        if b == o:
            continue
        changed += 1
        if not (math.isfinite(o) and math.isfinite(m) and math.isfinite(b)):
            if back[off:off + 4] != original[off:off + 4] and stray is None:
                stray = f"the float at 0x{off:x} ({o!r}) came back as {b!r}"
            continue
        d = abs(b - o)
        if d > worst:
            worst = d
        if d > half_step(m) + half_step(b) and stray is None:
            stray = (f"the float at 0x{off:x} came back as {b!r}, {d:.6g} from {o!r}: more than float32 rounding "
                     f"of the move (to {m!r}) allows")
    return worst, changed, stray


def check_ocl_ddo(game, report):
    """DDO collision (COL\\0, rCollIndex/rCollNode/rAttackParam): parse -> build and the YAML round trip, byte-exact."""
    from riftstone import ocl_ddo

    t0 = time.time()
    _begin()
    exact = failed = nodes = shapes = attacks = uid_ok = 0
    kinds: dict[str, int] = {}
    first: dict[str, list] = {}
    failures = []
    for r in _resources(game, [typemap.BY_EXT["ocl"]]):
        name = r.name.decode("latin-1")
        try:
            o = ocl_ddo.parse(r.data)
            if ocl_ddo.build(o) != r.data:
                raise AssertionError("rebuild differs")
            if ocl_ddo.yaml_to_bytes(ocl_ddo.to_yaml(o, name), r.label) != r.data:
                raise AssertionError("YAML round trip differs")
            exact += 1
            nodes += len(o.nodes)
            shapes += o.shape_count
            attacks += len(o.attacks)
            uid_ok += o.node_uid == o.attack_uid == ocl_ddo.path_uid(name)
            for n in o.nodes:
                for g in n["geoms"]:
                    k = ocl_ddo.SHAPES.get(g["mShape"], f"mShape {g['mShape']}")
                    _met(first, k)
                    kinds[k] = kinds.get(k, 0) + 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            if len(failures) < 40:
                failures.append(_placed({"resource": r.label, "why": f"{type(e).__name__}: {e}"}))
    report["ocl"] = {"distinct": exact + failed, "byte_exact": exact, "nodes": nodes, "hit_shapes": shapes,
                     "shapes_by_kind": kinds, "attack_params": attacks, "uid_is_path_jamcrc": uid_ok,
                     "failed": failed, "failures": failures, "seconds": round(time.time() - t0, 1),
                     "claim": "parse->build and YAML round trip reproduce every DDO .ocl byte-for-byte"}
    _keep_met(report["ocl"], shapes_by_kind=first)
    return failed == 0 and exact > 0


def check_ocl(game, report):
    if game.is_ddo:
        return check_ocl_ddo(game, report)
    t0 = time.time()
    _begin()
    exact = failed = groups = nodes = attacks = empty = seqs = 0
    shapes: dict[str, int] = {}
    first: dict[str, list] = {}
    failures = []
    for r in _resources(game, [typemap.BY_EXT["ocl"]]):
        try:
            o = ocl.parse(r.data)
            if ocl.build(o) != r.data:
                raise AssertionError("rebuild differs")
            if ocl.yaml_to_bytes(ocl.to_yaml(o, r.name.decode("latin-1")), r.label) != r.data:
                raise AssertionError("YAML round trip differs")
            exact += 1
            groups += len(o.groups)
            nodes += len(o.nodes)
            attacks += sum(1 for a in o.attacks if a is not None)
            empty += sum(1 for a in o.attacks if a is None)
            seqs += len(o.seqs)
            for n in o.nodes:
                k = ocl.SHAPES.get(n["mShape"], f"mShape {n['mShape']}")
                _met(first, k)
                shapes[k] = shapes.get(k, 0) + 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            if len(failures) < 40:
                failures.append(_placed({"resource": r.label, "why": f"{type(e).__name__}: {e}"}))
    report["ocl"] = {"distinct": exact + failed, "byte_exact": exact, "groups": groups, "nodes": nodes,
                     "shapes_by_kind": shapes, "attacks": attacks, "empty_attack_slots": empty,
                     "sequence_entries": seqs, "failed": failed, "failures": failures,
                     "seconds": round(time.time() - t0, 1),
                     "claim": "rObjCollision::load's grammar reads every .ocl to its last byte; parse->build and "
                              "the YAML round trip reproduce every one byte for byte"}
    _keep_met(report["ocl"], shapes_by_kind=first)
    return _absent(game, "ocl", exact, failed, "ocl", report)


def check_gmd(game, report):
    t0 = time.time()
    _begin()
    exact = failed = messages = labels = 0
    languages = Counter()
    first: dict[str, list] = {}
    failures = []
    for r in _resources(game, [typemap.BY_EXT["gmd"]]):
        try:
            g = gmd.parse(r.data)
            if gmd.build(g) != r.data:
                raise AssertionError("rebuild differs")
            if gmd.yaml_to_bytes(gmd.to_yaml(g, r.name.decode("latin-1"))) != r.data:
                raise AssertionError("YAML round trip differs")
            exact += 1
            messages += len(g.messages)
            labels += sum(m.label is not None for m in g.messages)
            _met(first, g.language_name)
            languages[g.language_name] += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            if len(failures) < 40:
                failures.append(_placed({"resource": r.label, "why": f"{type(e).__name__}: {e}"}))
    report["gmd"] = {"distinct": exact + failed, "byte_exact": exact, "messages": messages, "labels": labels,
                     "languages": dict(languages), "failed": failed, "failures": failures,
                     "seconds": round(time.time() - t0, 1),
                     "claim": "parse->build and YAML round-trip reproduce every .gmd byte-for-byte"}
    _keep_met(report["gmd"], languages=first)
    return _absent(game, "gmd", exact, failed, "gmd", report)


# What a game has that Riftstone does not decode yet (docs/ONBOARDING.md, docs/roadmap.md): listed in
# the report as not decoded, not counted as failures -- this gate proves what Riftstone claims to read.
NOT_DECODED = {
    "ddo": {"itl": "Online's item list is its ipa form (counted tables of rItemParamXml / rEquipParamS8), "
                   "not Dark Arisen's ITL2; not decoded yet",
            "nnl": "Online's NPC ledger is another revision of the format; not decoded yet"},
}


def _not_decoded(game, ext: str) -> str | None:
    return NOT_DECODED.get("ddo" if game.is_ddo else "ddda", {}).get(ext)


# Types a game has no files of at all (measured on the whole client): a check finding none there passes
# and says so; anywhere else, finding none is a failure (it would mean nothing was checked).
ABSENT = {"ddo": {"tables": "Online keeps item sets, drops and recipes on its server (no .ist / .imx in the client)",
                  "prp": "the Online client has no .prp files",
                  "fca": "the Online client has no .fca facial animations"}}


def _absent(game, check: str, exact: int, failed: int, report_key: str, report) -> bool:
    """A check's verdict: nothing failed and something was checked -- or the game has none of the kind at all
    (ABSENT, measured), which the report says."""
    why = ABSENT.get("ddo" if game.is_ddo else "ddda", {}).get(check)
    if why and exact == 0 and failed == 0:
        report[report_key]["absent"] = why
        return True
    if exact == 0 and failed == 0:
        report[report_key]["none_found"] = "no resource of this kind was found, so nothing was checked"
    return failed == 0 and exact > 0


def check_itl(game, report):
    t0 = time.time()
    why = _not_decoded(game, "itl")
    if why:
        n = sum(1 for _ in _walk(game, [typemap.BY_EXT["itl"]]))
        report["itl"] = {"distinct": n, "not_decoded": why, "seconds": round(time.time() - t0, 1)}
        return True
    exact = failed = records = 0
    failures = []
    # the enhancement tables' row counts, for the rule DDDA.exe's lookup follows (itl.level_table, 0x0045B620)
    from riftstone import flat
    rows = {r.name.decode("latin-1").rsplit("\\", 1)[-1]: len(flat.parse(r.data, "itemlv").data["mpArray"])
            for r in _walk(game, [typemap.BY_EXT["itemlv"]])}
    equipment = {}
    for r in _walk(game, [typemap.BY_EXT["itl"]]):
        try:
            t = itl.parse(r.data)
            if itl.build(t) != r.data:
                raise AssertionError("rebuild differs")
            if itl.yaml_to_bytes(itl.to_yaml(t, r.name.decode("latin-1"))) != r.data:
                raise AssertionError("YAML round trip differs")
            for i, rec in enumerate(t.records):         # every equipment item's row is inside its table
                table = itl.level_table(itl.BY_NAME["mKind"].get(rec))
                if table is None:
                    continue
                row = itl.BY_NAME["mLevelUpType"].get(rec)
                if row >= rows.get(table, 0):
                    raise AssertionError(f"item {i}'s enhancement row {row} is not in {table} ({rows.get(table)} rows)")
                equipment[table] = equipment.get(table, 0) + 1
            exact += 1
            records += len(t.records)
        except Exception as e:  # noqa: BLE001
            failed += 1
            failures.append({"resource": r.label, "why": f"{type(e).__name__}: {e}"})
    report["itl"] = {"distinct": exact + failed, "byte_exact": exact, "items": records, "failed": failed,
                     "equipment_rows": equipment, "failures": failures, "seconds": round(time.time() - t0, 1),
                     "claim": "parse->build and YAML round-trip (every field by name) reproduce the item list "
                              "byte-for-byte; every weapon, armour and accessory finds its enhancement row inside "
                              "the table its mKind picks"}
    return failed == 0 and exact > 0


def check_lot_ddo(game, report):
    """DDO layouts: parse -> build and the YAML round trip, byte-exact."""
    from riftstone import lot_ddo

    t0 = time.time()
    _begin()
    exact = failed = records = 0
    kinds: dict[str, int] = {}
    first: dict[str, list] = {}
    failures = []
    for r in _resources(game, [typemap.BY_EXT["lot"]]):
        try:
            lt = lot_ddo.parse(r.data)
            if lot_ddo.build(lt) != r.data:
                raise AssertionError("rebuild differs")
            if lot_ddo.yaml_to_bytes(lot_ddo.to_yaml(lt, r.name.decode("latin-1"))) != r.data:
                raise AssertionError("YAML round trip differs")
            exact += 1
            records += len(lt.records)
            for rec in lt.records:
                _met(first, rec.cls)
                kinds[rec.cls] = kinds.get(rec.cls, 0) + 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            if len(failures) < 40:
                failures.append(_placed({"resource": r.label, "why": f"{type(e).__name__}: {e}"}))
    report["lot"] = {"distinct": exact + failed, "byte_exact": exact, "records": records, "classes": len(kinds),
                     "records_by_class": dict(sorted(kinds.items(), key=lambda kv: -kv[1])), "failed": failed,
                     "failures": failures, "seconds": round(time.time() - t0, 1),
                     "claim": "parse->build and YAML round-trip reproduce every DDO .lot byte-for-byte"}
    _keep_met(report["lot"], records_by_class=first)
    return _absent(game, "lot", exact, failed, "lot", report)


def check_lot(game, report):
    if game.is_ddo:
        return check_lot_ddo(game, report)
    t0 = time.time()
    _begin()
    exact = failed = records = named = 0
    kinds: dict[str, int] = {}
    first: dict[str, list] = {}
    failures = []
    for r in _resources(game, [typemap.BY_EXT["lot"]]):
        try:
            name = r.name.decode("latin-1")
            lt = lot.parse(r.data)
            if lot.build(lt) != r.data:
                raise AssertionError("rebuild differs")
            if lot.yaml_to_bytes(lot.to_yaml(lt, name)) != r.data:
                raise AssertionError("YAML round trip differs")
            if lt.records:
                c = lot.copy(lt, len(lt.records) // 2)
                if lot.build(lot.remove(c, lt.count)) != r.data:
                    raise AssertionError("copy then remove is not the original")
            exact += 1
            records += lt.count
            named += lot.parse_name(name) is not None
            for rec in lt.records:
                _met(first, rec.cls)
                kinds[rec.cls] = kinds.get(rec.cls, 0) + 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            if len(failures) < 40:
                failures.append(_placed({"resource": r.label, "why": f"{type(e).__name__}: {e}"}))
    report["lot"] = {"distinct": exact + failed, "byte_exact": exact, "records": records, "classes": len(kinds),
                     "records_by_class": dict(sorted(kinds.items(), key=lambda kv: -kv[1])),
                     "names_decoded": named, "failed": failed,
                     "failures": failures, "seconds": round(time.time() - t0, 1),
                     "claim": "every record of every .lot is decoded field by field with the exe's own grammar; "
                              "parse->build and YAML round-trip reproduce every file byte-for-byte; copy-then-remove "
                              "restores every file"}
    _keep_met(report["lot"], records_by_class=first)
    return _absent(game, "lot", exact, failed, "lot", report)


# Archive references that do not list their archive's directory, as each game ships them (measured on the
# whole client, 2026-09-26): Dark Arisen keeps 2 out-of-date lists (om8505's and om11001's, arcref.py) and 2
# naming an archive it does not have; Online's lists are all exact and 5 name an archive it does not have.
# More of either fails the check.
ARCS_MEASURED = {"ddda": {"differ_from_target": 2, "target_missing": 2},
                 "ddo": {"differ_from_target": 0, "target_missing": 5}}


def check_arcs(game, report):
    """Every distinct archive reference (rArchive, ARCS): parse -> build byte-exact, and its list equals the
    referenced archive's directory (name hashes and types, in order), but for the measured few."""
    from riftstone import arcref

    t0 = time.time()
    exact = failed = matching = stale = no_target = entries = 0
    failures, differing, missing = [], [], []
    for r in _walk(game, [typemap.BY_EXT["arc"]]):
        try:
            ref = arcref.parse(r.data)
            if arcref.build(ref) != r.data:
                raise AssertionError("rebuild differs")
            exact += 1
            entries += len(ref.entries)
            try:
                path = game.vanilla_arc(arcref.target(r.name))      # the referenced archive as shipped
            except Exception:  # noqa: BLE001 - a name that is not an archive path
                path = None
            if path is None or not path.is_file():
                no_target += 1
                missing.append(r.label)
            elif arcref.matches(ref, [(n, t) for n, t, *_ in corpus.directory(path)]):
                matching += 1
            else:
                stale += 1          # the game ships some (ARCS_MEASURED, arcref.py); more than those fail
                differing.append(r.label)
        except Exception as e:  # noqa: BLE001
            failed += 1
            if len(failures) < 40:
                failures.append({"resource": r.label, "why": f"{type(e).__name__}: {e}"})
    measured = ARCS_MEASURED["ddo" if game.is_ddo else "ddda"]
    over = {k: n for k, n in (("differ_from_target", stale), ("target_missing", no_target)) if n > measured[k]}
    report["arcs"] = {"distinct": exact + failed, "byte_exact": exact, "entries": entries, "match_target": matching,
                      "differ_from_target": stale, "differing": differing[:40], "target_missing": no_target,
                      "missing": missing[:40], "measured": measured, "failed": failed, "failures": failures,
                      "seconds": round(time.time() - t0, 1),
                      "claim": f"every archive reference rebuilds byte-for-byte and lists its archive's directory "
                               f"exactly, but for at most {measured['differ_from_target']} out of date and "
                               f"{measured['target_missing']} naming an archive the game does not have (the counts "
                               f"the game ships, ARCS_MEASURED)"}
    if over:
        report["arcs"]["more_than_measured"] = over
    return _absent(game, "arcs", exact, failed, "arcs", report) and not over


def check_tables(game, report):
    t0 = time.time()
    exact = failed = rows = 0
    failures = []
    for r in _walk(game, [typemap.BY_EXT["ist"], typemap.BY_EXT["imx"]]):
        try:
            t = tables.parse(r.data)
            if tables.build(t) != r.data:
                raise AssertionError("rebuild differs")
            if tables.yaml_to_bytes(tables.to_yaml(t, r.name.decode("latin-1"))) != r.data:
                raise AssertionError("YAML round trip differs")
            exact += 1
            rows += len(t.rows)
        except Exception as e:  # noqa: BLE001
            failed += 1
            failures.append({"resource": r.label, "why": f"{type(e).__name__}: {e}"})
    report["tables"] = {"distinct": exact + failed, "byte_exact": exact, "rows": rows, "failed": failed,
                        "failures": failures, "seconds": round(time.time() - t0, 1),
                        "claim": "parse->build and YAML round-trip reproduce every item set, drop and recipe table"}
    return _absent(game, "tables", exact, failed, "tables", report)


def check_gpl_ddo(game, report):
    """DDO group lists (.gpl v70): parse -> build and the YAML round trip, byte-exact."""
    from riftstone import gpl_ddo

    t0 = time.time()
    exact = failed = groups = cells = shapes = 0
    failures = []
    for r in _walk(game, [typemap.BY_EXT["gpl"]]):
        try:
            x = gpl_ddo.parse(r.data)
            if gpl_ddo.build(x) != r.data:
                raise AssertionError("rebuild differs")
            if gpl_ddo.yaml_to_bytes(gpl_ddo.to_yaml(x, r.name.decode("latin-1"))) != r.data:
                raise AssertionError("YAML round trip differs")
            exact += 1
            groups += len(x.groups)
            cells += sum(len(g["mLayoutIDArray"]) for g in x.groups)
            shapes += sum(1 for g in x.groups for _ in gpl_ddo.group_shapes(g))
        except Exception as e:  # noqa: BLE001
            failed += 1
            if len(failures) < 40:
                failures.append({"resource": r.label, "why": f"{type(e).__name__}: {e}"})
    report["gpl"] = {"distinct": exact + failed, "byte_exact": exact, "groups": groups, "layout_cells": cells,
                     "area_shapes": shapes, "unit_kinds": 0, "failed": failed, "failures": failures,
                     "seconds": round(time.time() - t0, 1),
                     "claim": "parse->build and YAML round-trip reproduce every DDO .gpl (v70) byte-for-byte; "
                              "group conditions, MaxCount and area shapes editable (DDO has no unit list)"}
    return failed == 0 and exact > 0


def check_gpl(game, report):
    if game.is_ddo:
        return check_gpl_ddo(game, report)
    from riftstone import gpl

    t0 = time.time()
    exact = failed = groups = units = 0
    failures = []
    for r in _walk(game, [typemap.BY_EXT["gpl"]]):
        try:
            x = gpl.parse(r.data)
            if gpl.build(x) != r.data:
                raise AssertionError("rebuild differs")
            if gpl.yaml_to_bytes(gpl.to_yaml(x, r.name.decode("latin-1"))) != r.data:
                raise AssertionError("YAML round trip differs")
            exact += 1
            groups += len(x.groups)
            units += sum(len(g["mUnitKindList"]) for g in x.groups)
        except Exception as e:  # noqa: BLE001
            failed += 1
            if len(failures) < 40:
                failures.append({"resource": r.label, "why": f"{type(e).__name__}: {e}"})
    report["gpl"] = {"distinct": exact + failed, "byte_exact": exact, "groups": groups, "unit_kinds": units,
                     "failed": failed, "failures": failures, "seconds": round(time.time() - t0, 1),
                     "claim": "parse->build and YAML round-trip reproduce every .gpl byte-for-byte; "
                              "enemy groups, unit lists and spawn caps editable"}
    return failed == 0 and exact > 0


def check_tex(game, report):
    from riftstone import tex

    t0 = time.time()
    exact = failed = dds_ok = 0
    no_dds: Counter = Counter()          # why a texture has no .dds form: cube map, format, mip layout
    examples: dict[str, str] = {}
    failures = []
    for r in _walk(game, [typemap.BY_EXT["tex"]]):
        try:
            t = tex.parse(r.data)
            if tex.build(t) != r.data:
                raise AssertionError("rebuild differs")
            exact += 1
            try:
                dds = tex.to_dds(t)
            except FormatError:          # to_dds's refusal; any other exception is a failure, counted below
                # its reasons, in its order: more than one face, then a format .dds has no code for, then the mips
                why = (("cube map" if (t.attr1 >> 16) & 0xF == 6 else f"depth {t.depth}") if t.is_cube
                       else f"format {t.fmt}" if t.fmt not in tex._BLOCK and t.fmt not in tex._UNCOMP
                       else "mip layout")
                no_dds[why] += 1
                examples.setdefault(why, r.label)
            else:
                if tex.build(tex.dds_to_tex(dds, template=t)) != r.data:
                    raise AssertionError("DDS round trip differs")
                dds_ok += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            if len(failures) < 40:
                failures.append({"resource": r.label, "why": f"{type(e).__name__}: {e}"})
    order = sorted(no_dds, key=lambda k: (k != "cube map", k.split()[0],
                                          int(k.split()[1]) if k[:6] in ("format", "depth ") else 0))
    kinds = ", ".join(f"{k} ({no_dds[k]})" for k in order)
    report["tex"] = {"distinct": exact + failed, "byte_exact": exact, "dds_round_trip": dds_ok,
                     "dds_not_applicable": sum(no_dds.values()), "no_dds_form": {k: no_dds[k] for k in order},
                     "no_dds_form_example": examples, "failed": failed, "failures": failures,
                     "seconds": round(time.time() - t0, 1),
                     "claim": "parse->build reproduces every .tex byte-for-byte (cube maps included); "
                              ".dds export/import round-trips every texture a .dds can hold byte-for-byte"
                              + (f"; no .dds form: {kinds}" if kinds else "")}
    return _absent(game, "tex", exact, failed, "tex", report)


def check_mrl(game, report):
    from riftstone import mrl

    t0 = time.time()
    exact = failed = shaders = binds = 0
    seen_shaders = set()
    failures = []
    for r in _walk(game, [typemap.BY_EXT["mrl"]]):
        try:
            m = mrl.parse(r.data)
            if mrl.build(m) != r.data:
                raise AssertionError("rebuild differs")
            exact += 1
            binds += len(m.textures)
            for mat in m.materials:
                seen_shaders.add(mat.shader)
        except Exception as e:  # noqa: BLE001
            failed += 1
            if len(failures) < 40:
                failures.append({"resource": r.label, "why": f"{type(e).__name__}: {e}"})
    report["mrl"] = {"distinct": exact + failed, "byte_exact": exact, "texture_bindings": binds,
                     "distinct_shaders": len(seen_shaders), "failed": failed, "failures": failures,
                     "seconds": round(time.time() - t0, 1),
                     "claim": "parse->build reproduces every .mrl material file byte-for-byte; "
                              "material shader hashes and texture bindings are read out"}
    return failed == 0 and exact > 0


def _sbc_claim(worst: float, restored_files: int, exact: int, triangles: int, own_normal: int, flat: int) -> str:
    return (f"every collision mesh follows the loader's layout to its last byte; a move by a "
            f"cell corner changes only boxes, tree lanes and vertices; moving back restores every "
            f"value within float32 rounding (largest change {worst:g} cm; {restored_files} of "
            f"{exact} files byte for byte), and moving twice gives the same bytes; every one of "
            f"{triangles:,} triangles names three vertices of its own part, and the stored normal "
            f"is the corners' own for {own_normal:,} ({flat:,} have no area)")


def check_sbc(game, report):
    from riftstone import sbc

    t0 = time.time()
    _begin()
    exact = failed = nodes = floats = vertices = restored_files = changed = triangles = own_normal = flat = 0
    worst = 0.0
    failures = []
    corner = (-150000.0, 0.0, -30000.0)                       # cell 47m35n's

    def facing(a, b, c, n):
        """None for a triangle with no area, else whether its stored normal is its corners' own (either side)."""
        u, v = (b[0] - a[0], b[1] - a[1], b[2] - a[2]), (c[0] - a[0], c[1] - a[1], c[2] - a[2])
        cr = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
        size = math.sqrt(cr[0] * cr[0] + cr[1] * cr[1] + cr[2] * cr[2])
        if not size > 1e-6:
            return None
        return abs(cr[0] * n[0] + cr[1] * n[1] + cr[2] * n[2]) / size >= 0.99

    back_by = tuple(-v for v in corner)
    for r in _resources(game, [typemap.BY_EXT["sbc"]]):
        try:
            s = sbc.parse(r.data)
            problems = sbc.bounds_problems(r.data)
            if problems:
                raise AssertionError("; ".join(problems))
            moved = sbc.moved_floats(r.data)
            w = sbc.translate(r.data, corner)
            back = sbc.translate(w, back_by)
            # moving back gives the original to within float32 rounding (the move's own, on each value)...
            far, n, stray = moved_back(r.data, w, back, moved)
            if stray:
                raise AssertionError(f"moving back does not restore the original: {stray}")
            # ... and a second move lands on the same bytes as the first
            if sbc.translate(back, corner) != w:
                raise AssertionError("moving twice does not give the same bytes")
            mask = bytearray(b"\xff") * len(r.data)          # every byte but the moved floats
            for off, _ in moved:
                mask[off:off + 4] = b"\0\0\0\0"
            keep = int.from_bytes(mask, "little")
            fixed = int.from_bytes(r.data, "little") & keep
            if len(w) != len(r.data) or int.from_bytes(w, "little") & keep != fixed:
                raise AssertionError("a byte that is not a position changed")
            if len(back) != len(r.data) or int.from_bytes(back, "little") & keep != fixed:
                raise AssertionError("moving back changed a byte that is not a position")
            if sbc.bounds_problems(w):
                raise AssertionError("the moved file breaks its own boxes")
            tris = sbc.triangles(r.data)                      # refuses a vertex number past its part's
            if len(tris) != s.triangles:
                raise AssertionError(f"the parts hold {len(tris)} of the file's {s.triangles} triangles")
            faces = [facing(*tri) for tri in tris]
            triangles += len(tris)
            own_normal += faces.count(True)
            flat += faces.count(None)
            exact += 1
            nodes += sum(t.nodes for t in s.trees)
            floats += len(moved)
            vertices += s.vertices
            worst = max(worst, far)
            changed += n
            restored_files += back == r.data
        except Exception as e:  # noqa: BLE001
            failed += 1
            if len(failures) < 40:
                failures.append(_placed({"resource": r.label, "why": f"{type(e).__name__}: {e}"}))
    report["sbc"] = {"distinct": exact + failed, "layout_exact": exact, "tree_nodes": nodes, "vertices": vertices,
                     "moved_floats": floats, "moved_back_floats_changed": changed, "triangles": triangles,
                     "triangles_whose_normal_is_their_corners": own_normal, "triangles_without_area": flat,
                     "moved_back_largest_change_cm": worst, "moved_back_files_byte_exact": restored_files,
                     "failed": failed, "failures": failures, "seconds": round(time.time() - t0, 1),
                     "claim": _sbc_claim(worst, restored_files, exact, triangles, own_normal, flat)}
    return _absent(game, "sbc", exact, failed, "sbc", report)


def check_ean(game, report):
    from riftstone import ean

    t0 = time.time()
    exact = failed = frames = 0
    failures = []
    for r in _walk(game, [typemap.BY_EXT["ean"]]):
        try:
            e = ean.parse(r.data)
            if ean.build(e) != r.data:
                raise AssertionError("rebuild differs")
            exact += 1
            frames += e.count
        except Exception as exc:  # noqa: BLE001
            failed += 1
            if len(failures) < 40:
                failures.append({"resource": r.label, "why": f"{type(exc).__name__}: {exc}"})
    report["ean"] = {"distinct": exact + failed, "byte_exact": exact, "frames": frames,
                     "failed": failed, "failures": failures, "seconds": round(time.time() - t0, 1),
                     "claim": "parse->build reproduces every .ean effect-anim file byte-for-byte "
                              "(header + opaque frame payload, as the engine loads it)"}
    return failed == 0 and exact > 0


def check_nav(game, report):
    """Navigation meshes (rNavigationMesh .nav, both games): parse -> build byte-exact; every link's cost is
    the distance between the two centroids in metres; links counted with and without their reverse; the
    stage -> mesh table (nav.NAV_OF) equals DDDA.exe's own (Dark Arisen)."""
    import math

    from riftstone import nav

    t0 = time.time()
    exact = failed = triangles = links = reverse = 0
    worst = 0.0
    off: Counter = Counter()           # links whose cost is more than 1% from the centroids' distance, per mesh
    failures = []
    for r in _walk(game, [typemap.BY_EXT["nav"]]):
        try:
            n = nav.parse(r.data)
            if nav.build(n) != r.data:
                raise AssertionError("rebuild differs")
            exact += 1
            m = nav.Mesh(n)
            triangles += len(n.triangles)
            for i, t in enumerate(n.triangles):
                for lk in t.links:
                    links += 1
                    reverse += any(back.to == i for back in n.triangles[lk.to].links)
                    d = math.dist(m.centroid(i), m.centroid(lk.to)) / 100.0
                    if d > 0.5:
                        rel = abs(lk.cost - d) / d
                        worst = max(worst, rel)
                        if rel > 0.01:
                            off[r.label] += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            if len(failures) < 40:
                failures.append({"resource": r.label, "why": f"{type(exc).__name__}: {exc}"})
    table = collision = None
    if game.kind == "ddda":
        from riftstone import scene
        try:
            exe = game.exe.read_bytes()
            t = nav.stage_table(exe)
            table = {s: v for s, v in sorted(t.items()) if s != v} == nav.NAV_OF
            t = nav.stage_table(exe, nav.COLLISION_FIELD)
            collision = {s: v for s, v in sorted(t.items()) if s != v} == scene.COLLISION_OF
        except Exception as exc:  # noqa: BLE001
            failures.append({"resource": "DDDA.exe stage table", "why": f"{type(exc).__name__}: {exc}"})
            table = collision = False
    report["nav"] = {"distinct": exact + failed, "byte_exact": exact, "failed": failed, "triangles": triangles,
                     "links": links, "links_with_reverse": reverse, "cost_vs_centroids_worst": round(worst, 5),
                     "links_cost_off_1pct": dict(off), "stage_table_matches_exe": table,
                     "collision_table_matches_exe": collision, "failures": failures,
                     "seconds": round(time.time() - t0, 1),
                     "claim": "parse->build reproduces every navigation mesh byte-for-byte; a link's cost is the "
                              "centroids' distance in metres (Dark Arisen: every link within 1%; Online: all but "
                              "rm107's 96, measured 2026-09-26); nav.NAV_OF and scene.COLLISION_OF are the exe's "
                              "table"}
    costs_ok = worst < 0.01 if game.kind == "ddda" else set(off) <= {"st0407.arc:scr\\rm\\rm107\\etc\\rm107_nav"}
    return _absent(game, "nav", exact, failed, "nav", report) and table is not False and costs_ok


def port_lmt_counts(before, after) -> None:
    """AssertionError unless a ported motion list has the original's motion slots (empty ones where it had
    them) and, in each motion, as many tracks."""
    if len(after.motions) != len(before.motions):
        raise AssertionError(f"the port has {len(after.motions)} motion slots, the original {len(before.motions)}")
    for i, (ma, mb) in enumerate(zip(before.motions, after.motions)):
        if (ma is None) != (mb is None):
            raise AssertionError(f"the port {'emptied' if mb is None else 'filled'} motion slot {i}")
        if ma is not None and len(mb.tracks.tracks) != len(ma.tracks.tracks):
            raise AssertionError(f"motion {i}: the port has {len(mb.tracks.tracks)} tracks, the original "
                                 f"{len(ma.tracks.tracks)}")


def check_lmt(game, report):
    """Motion lists: parse -> build byte-exact; every keyframe buffer splits into keys and packs back
    to the same bytes; every buffer's key deltas add up to its motion's frame count - 1; rotation keys
    dequantise to unit quaternions (reported, not gated: 7- and 9-bit keys are coarse)."""
    import math

    from riftstone import lmt, lmtcodec, port

    def vals(t):
        return None if t.buffer is None else lmtcodec.values(
            t.codec, t.buffer.data, t.extremes.data if t.extremes is not None else None, t.reference)

    other = "ddo" if game.kind == "ddda" else "ddda"
    t0 = time.time()
    exact = failed = motions = tracks = buffers = keys = span_bad = ported = 0
    port_worst = 0.0
    rot = Counter()
    failures = []
    for r in _walk(game, [typemap.BY_EXT["lmt"]]):
        try:
            m = lmt.parse(r.data)
            if lmt.build(m) != r.data:
                raise AssertionError("rebuild differs")
            # the port to the other game keeps every motion slot and every track (counted: the pairs below
            # stop at the shorter list): bones, key frames, values within a codec step
            pm = lmt.parse(port.convert_lmt(r.data, game.kind, other).data)
            port_lmt_counts(m, pm)
            for ma, mb in zip(m.motions, pm.motions):
                for ta, tb in zip(ma.tracks.tracks if ma else (), mb.tracks.tracks if mb else ()):
                    if (ta.bone, ta.usage, ta.reference) != (tb.bone, tb.usage, tb.reference):
                        raise AssertionError("the port changed a track's bone, usage or reference")
                    va, vb = vals(ta), vals(tb)
                    if (va is None) != (vb is None) or (va and [f for f, _ in va] != [f for f, _ in vb]):
                        raise AssertionError("the port moved key frames")
                    for (_, x), (_, y) in zip(va or (), vb or ()):
                        port_worst = max(port_worst, max(abs(p - q) for p, q in zip(x, y)))
            ported += 1
            seen = set()
            for mo in m.motions:
                if mo is None:
                    continue
                motions += 1
                for t in mo.tracks.tracks:
                    tracks += 1
                    if t.buffer is None:
                        continue
                    buf = t.buffer.data
                    if id(t.buffer) not in seen:
                        seen.add(id(t.buffer))
                        ks = lmtcodec.keys(t.codec, buf)
                        if lmtcodec.pack(t.codec, ks) != buf:
                            raise AssertionError(f"codec {t.codec} keys do not pack back")
                        buffers += 1
                        keys += len(ks)
                    if lmtcodec.span(t.codec, buf) != mo.frames - 1:
                        span_bad += 1
                    if t.usage in (0, 3) and t.codec in lmtcodec.PACKED_QUAT + (6,):
                        ext = t.extremes.data if t.extremes is not None else None
                        for _, q in lmtcodec.values(t.codec, buf, ext, t.reference)[:16]:
                            rot["unit" if abs(math.sqrt(sum(c * c for c in q)) - 1) < 0.05 else "off"] += 1
            exact += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            if len(failures) < 40:
                failures.append({"resource": r.label, "why": f"{type(e).__name__}: {e}"})
    report["lmt"] = {"distinct": exact + failed, "byte_exact": exact, "motions": motions, "tracks": tracks,
                     "buffers_repacked": buffers, "keys": keys, "span_mismatches": span_bad,
                     "rotation_keys_unit": rot["unit"], "rotation_keys_off": rot["off"],
                     "ported_to_" + other: ported, "port_largest_value_change": round(port_worst, 6),
                     "failed": failed, "failures": failures, "seconds": round(time.time() - t0, 1),
                     "claim": "parse->build reproduces every .lmt byte-for-byte; every keyframe buffer splits "
                              "into keys and packs back identically; key deltas add up to frames - 1; the port "
                              "to the other game keeps every motion slot and every track, and each track's "
                              "bone, key frames and values (within half a codec 6 step, 0.000122)"}
    return failed == 0 and span_bad == 0 and exact > 0 and port_worst <= 0.000123


def check_weather(game, report):
    """Weather effect / fog / sky (DDDA wep wfp sky; DDO wep sky and the weather tables wtf wte wtl wsi
    wta): parse -> build and -> YAML -> back, byte-exact."""
    from riftstone import params, weather

    t0 = time.time()
    exts = ("wep", "sky", "wtf", "wte", "wtl", "wsi", "wta") if game.is_ddo else ("wep", "wfp", "sky")
    ids = {typemap.BY_EXT[e]: e for e in exts}
    per = Counter()
    exact = failed = 0
    failures = []
    for r in _walk(game, list(ids)):
        ext = ids[r.type_id]
        try:
            w = weather.parse(r.data, ext)
            if weather.build(w) != r.data:
                raise AssertionError("rebuild differs")
            text = params.resource_to_yaml(r.data, r.name.decode("latin-1"), r.type_id)
            if params.yaml_to_resource(text, r.label) != r.data:
                raise AssertionError("YAML round trip differs")
            exact += 1
            per[ext] += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            if len(failures) < 40:
                failures.append({"resource": r.label, "why": f"{type(e).__name__}: {e}"})
    report["weather"] = {"distinct": exact + failed, "byte_exact": exact, "per_type": dict(per), "failed": failed,
                         "failures": failures, "seconds": round(time.time() - t0, 1),
                         "claim": "parse->build and the YAML round trip (through params, as the editor does) "
                                  "reproduce every weather effect, fog, sky and DDO weather table byte-for-byte"}
    return failed == 0 and exact > 0


def check_lcm(game, report):
    """Camera lists (.lcm: DDDA v3 per-frame arrays, DDO v5 compressed tracks): parse -> build and the
    YAML round trip, byte-exact."""
    from riftstone import camera, params

    t0 = time.time()
    exact = failed = cams = frames = 0
    failures = []
    for r in _walk(game, [typemap.BY_EXT["lcm"]]):
        try:
            cl = camera.parse(r.data)
            if camera.build(cl) != r.data:
                raise AssertionError("rebuild differs")
            text = params.resource_to_yaml(r.data, r.name.decode("latin-1"), r.type_id)
            if params.yaml_to_resource(text, r.label) != r.data:
                raise AssertionError("YAML round trip differs")
            exact += 1
            cams += len(cl.cameras)
            frames += sum(len(c.frames) if cl.version == 3 else c.frame_num for c in cl.cameras.values())
        except Exception as e:  # noqa: BLE001
            failed += 1
            if len(failures) < 40:
                failures.append({"resource": r.label, "why": f"{type(e).__name__}: {e}"})
    report["lcm"] = {"distinct": exact + failed, "byte_exact": exact, "cameras": cams, "frames": frames,
                     "failed": failed, "failures": failures, "seconds": round(time.time() - t0, 1),
                     "claim": "parse->build and the YAML round trip reproduce every camera list byte-for-byte"}
    return failed == 0 and exact > 0


def check_sound(game, report):
    """Sound cue resources (DDDA srq stq srd smx spl; DDO srq stq smx sbkr sar): parse -> build and the
    YAML round trip through params, byte-exact."""
    from riftstone import params, sound

    t0 = time.time()
    exts = ("srq", "stq", "smx", "sbkr", "sar") if game.is_ddo else ("srq", "stq", "srd", "smx", "spl")
    rows = {e: {"distinct": 0, "byte_exact": 0, "yaml_exact": 0} for e in exts}
    failures = []
    for r in _walk(game, [typemap.BY_EXT[e] for e in exts]):
        row = rows[typemap.extension(r.type_id)]
        row["distinct"] += 1
        try:
            s = sound.parse(r.data)
            if sound.build(s) != r.data:
                raise AssertionError("rebuild differs")
            row["byte_exact"] += 1
            text = params.resource_to_yaml(r.data, r.name.decode("latin-1"), r.type_id)
            if params.yaml_to_resource(text, r.label) != r.data:
                raise AssertionError("YAML round trip differs")
            row["yaml_exact"] += 1
        except Exception as e:  # noqa: BLE001
            if len(failures) < 40:
                failures.append({"resource": r.label, "why": f"{type(e).__name__}: {e}"})
    failed = sum(v["distinct"] - v["yaml_exact"] for v in rows.values())
    report["sound"] = {"by_ext": rows, "distinct": sum(v["distinct"] for v in rows.values()), "failed": failed,
                       "failures": failures, "seconds": round(time.time() - t0, 1),
                       "claim": "parse->build and the YAML round trip (through params) reproduce every sound "
                                "cue resource byte-for-byte"}
    return failed == 0 and all(v["distinct"] for v in rows.values())


def check_effect(game, report, parts=EFFECT_PARTS):
    """Effect formats: .epv / .efl / .e2d / .efs parse -> build byte-exact; .epv/.efl/.e2d also through the
    params YAML round trip.  ``parts``: which of the four (--only takes each by its report section)."""
    from riftstone import effect, effect_e2d, effect_efl, effect_efs, params

    good = True
    mods = {"epv": effect, "efl": effect_efl, "e2d": effect_e2d, "efs": effect_efs}
    for ext, mod in mods.items():
        if ext not in parts:
            continue
        t0 = time.time()
        exact = yaml_ok = failed = 0
        named = total = 0
        kinds: Counter = Counter()
        res: Counter = Counter()
        failures = []
        for r in _walk(game, [typemap.BY_EXT[ext]]):
            try:
                x = mod.parse(r.data)
                if mod.build(x) != r.data:
                    raise AssertionError("rebuild differs")
                exact += 1
                if hasattr(mod, "to_yaml"):
                    text = params.resource_to_yaml(r.data, r.name.decode("latin-1"), r.type_id)
                    if params.yaml_to_resource(text, r.label) != r.data:
                        raise AssertionError("YAML round trip differs")
                    yaml_ok += 1
                if hasattr(mod, "coverage"):
                    c = mod.coverage(x)
                    named += c["named"]
                    total += c["bytes"]
                if hasattr(mod, "resources"):
                    for _, _, cls, _ in mod.resources(x):
                        res[cls] += 1
                if ext == "efl":
                    kinds.update(effect_efl.particle_kind(e.particle_type) for e in x.entries)
                elif ext == "epv":
                    kinds["elements"] += sum(len(i) for i in x.indices)
                    kinds["motion-sync"] += len(x.motsync)
                    kinds["events"] += len(x.events)
            except Exception as e:  # noqa: BLE001
                failed += 1
                if len(failures) < 40:
                    failures.append({"resource": r.label, "why": f"{type(e).__name__}: {e}"})
        entry = {"distinct": exact + failed, "byte_exact": exact, "failed": failed, "failures": failures,
                 "seconds": round(time.time() - t0, 1)}
        if hasattr(mod, "to_yaml"):
            entry["yaml_exact"] = yaml_ok
        if total:
            entry["named_bytes"] = named
            entry["bytes"] = total
            entry["named_share"] = round(named / total, 4)
        if res:
            entry["resources_by_class"] = dict(res.most_common())
        if kinds:
            entry["counts"] = dict(kinds.most_common())
        entry["claim"] = {
            "epv": "every effect provider parses, rebuilds and round-trips through YAML byte-for-byte "
                   "(indices -> elements -> effect paths + placement; motion-sync and event lists)",
            "efl": "every effect list parses, rebuilds and round-trips through YAML byte-for-byte; header, units, "
                   "joint table and every referenced structure decoded, generator/particle heads as named fields",
            "e2d": "every 2D effect parses, rebuilds and round-trips through YAML byte-for-byte",
            "efs": "every effect strip parses (parts, vertices, records) and rebuilds byte-for-byte"}[ext]
        report[ext] = entry
        good &= _absent(game, ext, exact, failed, ext, report)
    return good


def check_ddo_params(game, report):
    """DDO enemy and stage parameters (cpe pep prs osp sti sal evtr ndp): parse -> build and the YAML round trip
    through params, byte-exact.  Dark Arisen has none of these types (nothing to check there)."""
    from riftstone import ddo_params, params

    if not game.is_ddo:
        return _skip(report, "ddo_params", "Online's enemy and stage parameter types; Dark Arisen has none")
    t0 = time.time()
    tids = {spec.type_id: ext for ext, spec in ddo_params.KINDS.items()}
    per = {ext: {"distinct": 0, "byte_exact": 0, "yaml_exact": 0, "failed": 0} for ext in ddo_params.KINDS}
    failures = []
    flying = 0
    for r in _walk(game, list(tids)):
        ext = tids[r.type_id]
        c = per[ext]
        c["distinct"] += 1
        try:
            m = ddo_params.parse(r.data, ext)
            if ddo_params.build(m) != r.data:
                raise AssertionError("rebuild differs")
            c["byte_exact"] += 1
            text = params.resource_to_yaml(r.data, r.name.decode("latin-1"), r.type_id)
            if params.yaml_to_resource(text, r.label) != r.data:
                raise AssertionError("YAML round trip differs")
            c["yaml_exact"] += 1
            flying += ext == "cpe" and m.data["cCharParamEnemyFly"] is not None
        except Exception as e:  # noqa: BLE001
            c["failed"] += 1
            if len(failures) < 40:
                failures.append({"resource": r.label, "why": f"{type(e).__name__}: {e}"})
    report["ddo_params"] = {"per_kind": per, "distinct": sum(c["distinct"] for c in per.values()),
                            "cpe_flying": flying, "failures": failures, "seconds": round(time.time() - t0, 1),
                            "claim": "parse->build and the YAML round trip (through params) reproduce every DDO "
                                     "enemy/stage parameter file (cpe pep prs osp sti sal evtr ndp) byte-for-byte"}
    return all(c["failed"] == 0 and c["distinct"] > 0 for c in per.values())


def _find_resource(game, name: bytes, type_id: int):
    """(archive name, bytes as the game shipped them) of the first archive that holds this resource,
    reading only directories -- of the originals (corpus.source), never an installed mod's copy."""
    kept = corpus.backups(game)
    for path in game.archives():
        src = corpus.source(game, path, kept)
        if any(n == name and t == type_id for n, t, *_ in corpus.directory(src)):
            e = arc.Archive.read(src).find(name, type_id)
            if e is not None:
                return game.arc_name(path), e.data()
    return None, None


def check_ndp(game, report):
    """DDO named enemy parameters (rNamedParam .ndp), the table as the game shipped it: parse -> build and the
    YAML round trip byte-exact; every record equal to the local server's named_param.ndp.json field for field
    (when a local server is found, else not_compared); every id named by the label namedparam_<id> in
    ui/00_message/named/named_param.gmd (the client's name lookup, 0x00BFD003); and the Solo Balance twins
    (ddo_solo) within the client's limits.  Dark Arisen has no .ndp."""
    from riftstone import ddo, ddo_params, ddo_solo, params

    if not game.is_ddo:
        return _skip(report, "ndp", "Online's named enemy parameters (rNamedParam); Dark Arisen has no .ndp")
    t0 = time.time()
    tid = typemap.BY_EXT["ndp"]
    entry = {"distinct": 0, "byte_exact": 0, "yaml_exact": 0, "failures": []}
    report["ndp"] = entry
    fail = entry["failures"].append
    tables = []
    for r in _walk(game, [tid]):
        entry["distinct"] += 1
        try:
            m = ddo_params.parse(r.data, "ndp")
            if ddo_params.build(m) != r.data:
                raise AssertionError("rebuild differs")
            entry["byte_exact"] += 1
            text = params.resource_to_yaml(r.data, r.name.decode("latin-1"), r.type_id)
            if params.yaml_to_resource(text, r.label) != r.data:
                raise AssertionError("YAML round trip differs")
            entry["yaml_exact"] += 1
            tables.append((r, m))
        except Exception as e:  # noqa: BLE001
            fail({"resource": r.label, "why": f"{type(e).__name__}: {e}"})
    # corpus.resources reads the originals (Riftstone's backup when Solo Balance is installed straight into
    # nativePC), so the table round-tripped above is the one the game shipped; the checks below are about it
    npath, nraw = _find_resource(game, ddo_solo.NDP_NAME, tid)
    if not tables or nraw is None:
        entry["seconds"] = round(time.time() - t0, 1)
        return False
    m = ddo_params.parse(nraw, "ndp")
    live = arc.Archive.read(game.arc_path(npath)).find(ddo_solo.NDP_NAME, tid)     # nativePC's copy, maybe a mod's
    entry["original"] = {"archive": npath, "round_trip": any(r.data == nraw for r, _ in tables),
                         "installed_copy_differs": live is None or live.data() != nraw}
    if not entry["original"]["round_trip"]:
        fail({"resource": f"{npath}.arc:{ddo_solo.NDP_NAME.decode('latin-1')}",
              "why": "the table as shipped did not round-trip"})
    recs = m.data["mpArray"]
    entry["records"] = len(recs)
    entry["ids"] = [min(x["mID"] for x in recs), max(x["mID"] for x in recs)]
    entry["neutral_2298"] = next((x for x in recs if x["mID"] == ddo_solo.NEUTRAL_ID), None) == \
        {"mID": ddo_solo.NEUTRAL_ID, "mType": 2, "mHpRate": 100, **{k: 100 for k in ddo_params.NDP_RATES}}
    assets = ddo.server_assets(game)
    server = {"assets": str(assets) if assets else None}
    entry["server_json"] = server
    if assets is not None and (assets / ddo_solo.SERVER_JSON).is_file():
        from riftstone.install import load_state
        rel = ddo_solo.SERVER_JSON
        kept = game.state_dir / "server-vanilla" / rel
        src = kept if load_state(game).get("server", {}).get(rel, {}).get("vanilla_sha256") and kept.is_file() \
            else assets / rel
        doc = json.loads(src.read_bytes().decode("utf-8-sig"))
        lst = doc.get("namedParamList", [])
        server.update(path=str(src), records=len(lst), fields_compared=0, mismatches=0)
        if len(lst) != len(recs):
            fail({"resource": str(src), "why": f"{len(lst)} entries, the client has {len(recs)}"})
        for x, e in zip(recs, lst):
            for k, j in ddo_solo.JSON_KEYS.items():
                server["fields_compared"] += 1
                if e.get(j) != x[k]:
                    server["mismatches"] += 1
                    if server["mismatches"] <= 5:
                        fail({"resource": str(src), "why": f"id {x['mID']}: {j} {e.get(j)!r}, the client {x[k]}"})
        if doc.get("fileSize") != len(nraw):
            fail({"resource": str(src), "why": f"fileSize {doc.get('fileSize')}, the client file {len(nraw)}"})
    else:
        server["not_compared"] = (f"no {ddo_solo.SERVER_JSON} in {assets}" if assets is not None else
                                  "no local server was found (RIFTSTONE_DDO_ASSETS, or the DDO toolkit's server)")
    gpath, graw = _find_resource(game, ddo_solo.GMD_NAME, typemap.BY_EXT["gmd"])
    if graw is None:
        fail({"resource": "ui/00_message/named/named_param.gmd", "why": "not in the client"})
    else:
        g = gmd.parse(graw)
        labels = {x.label for x in g.messages}
        named = sum(1 for x in recs if ddo_solo.LABEL.format(x["mID"]) in labels)
        entry["name_labels"] = {"archive": gpath, "messages": len(g.messages), "ids_labelled": named}
        if named != len(recs):
            fail({"resource": "named_param.gmd", "why": f"{len(recs) - named} id(s) have no namedparam_<id> label"})
        try:
            twins, pairs = ddo_solo.make_twins(m)
            names, unnamed = ddo_solo.twin_names(g, pairs)
            ids = [x["mID"] for x in twins.data["mpArray"]]
            gmd.parse(gmd.build(names))
            entry["solo_twins"] = {"records": len(ids), "largest_id": max(ids), "largest_id_mod_4": max(ids) % 4,
                                   "messages": len(names.messages), "unnamed": unnamed}
        except Exception as e:  # noqa: BLE001
            fail({"resource": "solo twins", "why": f"{type(e).__name__}: {e}"})
    entry["seconds"] = round(time.time() - t0, 1)
    entry["claim"] = ("param/named_param.ndp as the game shipped it rebuilds byte-for-byte (binary and YAML), "
                      + ("" if "not_compared" in server else
                         "equals the local server's named_param.ndp.json field for field, ")
                      + "and names every id; its Solo Balance twins fit the client's id table")
    return not entry["failures"] and entry["distinct"] > 0


def _check_module(game, report, key, exts, module, claim):
    """parse -> build and the YAML round trip through params, byte-exact, for every distinct resource of
    these types (the module dispatches on magic)."""
    import importlib

    from riftstone import params

    mod = importlib.import_module(f"riftstone.{module}")
    t0 = time.time()
    ids = {typemap.BY_EXT[e]: e for e in exts}
    exact = failed = 0
    per = Counter()
    failures = []
    for r in _walk(game, list(ids)):
        try:
            m = mod.parse(r.data)
            if mod.build(m) != r.data:
                raise AssertionError("rebuild differs")
            text = params.resource_to_yaml(r.data, r.name.decode("latin-1"), r.type_id)
            if params.yaml_to_resource(text, r.label) != r.data:
                raise AssertionError("YAML round trip differs")
            exact += 1
            per[ids[r.type_id]] += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            if len(failures) < 40:
                failures.append({"resource": r.label, "why": f"{type(e).__name__}: {e}"})
    report[key] = {"distinct": exact + failed, "byte_exact": exact, "per_type": dict(per), "failed": failed,
                   "failures": failures, "seconds": round(time.time() - t0, 1), "claim": claim}
    return _absent(game, key, exact, failed, key, report)


def check_fca(game, report):
    return _check_module(game, report, "fca", ("fca",), "facial",
                         "every facial animation (.fca) byte-for-byte, binary and YAML")


def check_msgset(game, report):
    return _check_module(game, report, "msgset", ("mss", "msl"), "msgset",
                         "every message set / serial list (.mss, .msl) byte-for-byte, binary and YAML")


def check_sdl(game, report):
    return _check_module(game, report, "sdl", ("sdl",), "schedule",
                         "every scheduler (.sdl) byte-for-byte, binary and YAML")


def check_zon(game, report):
    return _check_module(game, report, "zon", ("zon",), "schedule",
                         "every zone (.zon) byte-for-byte, binary and YAML")


def check_prp(game, report):
    from riftstone import params, prp

    t0 = time.time()
    exact = yaml_exact = failed = 0
    classes = set()
    failures = []
    tid = typemap.BY_EXT["prp"]
    for r in _walk(game, [tid]):
        try:
            doc = prp.parse(r.data)
            if prp.build(doc) != r.data:
                raise AssertionError("rebuild differs")
            exact += 1
            classes.add(doc.root_class.name)
            y = params.resource_to_yaml(r.data, r.label.split(":")[-1], tid)
            if params.yaml_to_resource(y) == r.data:
                yaml_exact += 1
            else:
                raise AssertionError("YAML round-trip differs")
        except Exception as e:  # noqa: BLE001
            failed += 1
            if len(failures) < 40:
                failures.append({"resource": r.label, "why": f"{type(e).__name__}: {e}"})
    report["prp"] = {"distinct": exact + failed, "byte_exact": exact, "yaml_round_trip": yaml_exact,
                     "enemy_classes": len(classes), "failed": failed, "failures": failures,
                     "seconds": round(time.time() - t0, 1),
                     "claim": "parse->build and resource->YAML->resource reproduce every enemy/character "
                              "param file (.prp = PRPZ wrapper around XFS) byte-for-byte"}
    return _absent(game, "prp", exact, failed, "prp", report)


def check_flat(game, report):
    from riftstone import flat

    t0 = time.time()
    _begin()
    per = {}
    total_ok = total_bad = 0
    failures = []
    tids = [(ext, typemap.BY_EXT[ext]) for ext in flat.SCHEMAS if ext in typemap.BY_EXT]
    seen = {ext: 0 for ext, _ in tids}
    for ext, tid in tids:
        why = _not_decoded(game, ext)
        if why:
            per[ext] = {"files": sum(1 for _ in _resources(game, [tid])), "not_decoded": why}
            continue
        ok = bad = 0
        for r in _resources(game, [tid]):
            try:
                f = flat.parse(r.data, ext)
                if flat.build(f) != r.data:
                    raise AssertionError("rebuild differs")
                if flat.yaml_to_bytes(flat.to_yaml(f, r.name.decode("latin-1"))) != r.data:
                    raise AssertionError("YAML round trip differs")
                ok += 1
            except Exception as e:  # noqa: BLE001
                bad += 1
                if len(failures) < 40:
                    failures.append(_placed({"resource": r.label, "why": f"{type(e).__name__}: {e}"}))
        per[ext] = {"exact": ok, "failed": bad}
        total_ok += ok
        total_bad += bad
    report["flat"] = {"formats": len(tids), "byte_exact": total_ok, "failed": total_bad, "per_format": per,
                      "failures": failures, "seconds": round(time.time() - t0, 1),
                      "claim": "parse->build and YAML round-trip reproduce every flat parameter file byte-for-byte"}
    return total_bad == 0 and total_ok > 0


def check_arc(game, report):
    """DDDA: recompressing every payload (zlib 6) must reproduce the archive. DDO (ARCC): every
    entry must decrypt + inflate, the archive must rebuild byte-exact from its stored payloads,
    and every Capcom (78 9C) stream must also reproduce when recompressed and re-encrypted; the
    English patch's SharpZipLib streams (78 01) cannot be reproduced by zlib and are counted."""
    t0 = time.time()
    ok = bad = foreign = recompressed = from_backup = 0
    failures = []
    kept = corpus.backups(game)
    for path in game.archives():
        src = corpus.source(game, path, kept)          # the archive as shipped, never Riftstone's own output
        from_backup += src != path
        raw = src.read_bytes()
        try:
            a = arc.Archive.parse(raw)
            if not a.encrypted:
                rebuilt = arc.Archive([arc.Entry.from_data(e.name, e.type_id, e.data(), e.flags)
                                       for e in a.entries]).build()
            else:
                rebuilt = a.build()
                for e in a.entries:
                    d = e.data()
                    again = arc.Entry.from_data(e.name, e.type_id, d, e.flags, True)
                    if again.payload == e.payload:
                        recompressed += 1
                    else:
                        from riftstone.cipher import arc_cipher
                        head = arc_cipher().decrypt(e.payload[:8])[:2]
                        if head == b"\x78\x9c":
                            raise ValueError(f"{e.label}: a zlib-6 stream did not reproduce")
                        foreign += 1
            if rebuilt == raw:
                ok += 1
            else:
                bad += 1
                failures.append({"arc": game.arc_name(path), "why": "rebuilt bytes differ"})
        except Exception as e:  # noqa: BLE001 - report every failure, keep going
            bad += 1
            failures.append({"arc": game.arc_name(path), "why": f"{type(e).__name__}: {e}"})
    report["arc"] = {"archives": ok + bad, "exact": ok, "failed": bad, "read_from_backup": from_backup,
                     "failures": failures[:50], "seconds": round(time.time() - t0, 1),
                     "claim": "parse + recompress (zlib 6) + rebuild reproduces the vanilla archive bytes"
                     if game.kind == "ddda" else
                     "ARCC: every entry decrypts + inflates; rebuild from stored payloads is byte-exact; "
                     "every Capcom zlib-6 entry also reproduces when recompressed + re-encrypted"}
    if game.kind != "ddda":
        report["arc"].update({"entries_recompressed_exact": recompressed, "entries_foreign_zlib": foreign})
    if ok + bad == 0:
        report["arc"]["none_found"] = "no archive was found, so nothing was checked"
    return bad == 0 and ok > 0


def check_xfs(game, report, with_yaml):
    if with_yaml:
        from riftstone import params

    t0 = time.time()
    _begin()
    counts = Counter({"strings": 0, "strings_non_ascii": 0, "strings_not_text": 0})
    failures = []
    per_type = Counter()
    first_type: dict[str, list] = {}
    first_count: dict[str, list] = {}
    for r in _resources(game, corpus.xfs_type_ids()):
        _met(first_type, typemap.class_name(r.type_id))
        per_type[typemap.class_name(r.type_id)] += 1
        try:
            x = xfs.parse(r.data)
            if xfs.build(x) != r.data:
                raise AssertionError("XFS rebuild differs")
            if xfs.build_definitions(x.classes, x.version) != x.extra["def_raw"]:
                raise AssertionError("regenerated definition block differs")
            _met(first_count, "xfs_exact")
            counts["xfs_exact"] += 1
            for o in xfs.walk(x.root):          # text: read in the file's encoding, how much needs escapes
                for p, vals in zip(x.classes[o.cls].props, o.fields):
                    if p.type in xfs.STRING_TYPES:
                        for v in vals:
                            counts["strings"] += 1
                            if not v.isascii():
                                counts["strings_non_ascii"] += 1
                                if any(0xDC80 <= ord(c) <= 0xDCFF for c in xfs.decode_text(v, x.version)):
                                    counts["strings_not_text"] += 1
            if with_yaml:
                text = params.to_yaml(x, name=r.name.decode("latin-1"), type_id=r.type_id)
                back = params.from_yaml(text, source=r.label)
                if xfs.build(back) != r.data:
                    raise AssertionError("YAML round trip differs")
                _met(first_count, "yaml_exact")
                counts["yaml_exact"] += 1
        except Exception as e:  # noqa: BLE001
            _met(first_count, "failed")
            counts["failed"] += 1
            if len(failures) < 50:
                failures.append(_placed({"resource": r.label, "type": typemap.class_name(r.type_id),
                                         "why": f"{type(e).__name__}: {e}", "trace": traceback.format_exc(limit=3)}))
    report["xfs"] = {"distinct_resources": sum(per_type.values()), **counts, "failures": failures,
                     "types": dict(per_type), "seconds": round(time.time() - t0, 1),
                     "claim": "byte-exact XFS parse/build" + (" and XFS->YAML->XFS" if with_yaml else "")}
    _keep_met(report["xfs"], types=first_type, counts=first_count)
    return _absent(game, "xfs", counts["xfs_exact"], counts["failed"], "xfs", report)


def check_fsm(game, report):
    """Every distinct AI state machine: the readable view and fsmcheck.check without an error, and every
    finding pointing at a machine, state and link the file has; what the checks find, counted by kind."""
    from riftstone import fsm, fsmcheck

    t0 = time.time()
    ok = failed = machines = states = links = 0
    kinds, verdicts, files_with = Counter(), Counter(), Counter()
    failures = []
    for r in _walk(game, [typemap.BY_EXT["fsm"]]):
        try:
            x = xfs.parse(r.data)
            if not fsm.decompile(x, r.label).startswith("state machine"):
                raise AssertionError("the readable view has no header")
            m = fsmcheck.read(x)
            found = fsmcheck.check(m)
            for f in found:
                if f.level == fsmcheck.FILE:
                    continue
                if not 0 <= f.level < len(m.levels):
                    raise AssertionError(f"a finding names machine {f.level} of {len(m.levels)}")
                lv = m.levels[f.level]
                if f.state is not None and not 0 <= f.state < len(lv.states):
                    raise AssertionError(f"a finding names state {f.state} of {len(lv.states)}")
                if f.link is not None and not 0 <= f.link < len(lv.states[f.state].links):
                    raise AssertionError(f"a finding names link {f.link} of state {f.state}")
            ok += 1
            machines += len(m.levels)
            states += sum(len(lv.states) for lv in m.levels)
            links += sum(len(s.links) for lv in m.levels for s in lv.states)
            verdicts.update(m.verdicts.values())
            kinds.update(f"{f.severity}: {f.kind}" for f in found)
            files_with.update({f"{f.severity}: {f.kind}" for f in found})
        except Exception as e:  # noqa: BLE001
            failed += 1
            if len(failures) < 40:
                failures.append({"resource": r.label, "why": f"{type(e).__name__}: {e}"})
    report["fsm"] = {"distinct": ok + failed, "checked": ok, "failed": failed, "machines": machines,
                     "states": states, "links": links, "conditions": dict(verdicts),
                     "findings": {k: {"count": n, "files": files_with[k]} for k, n in sorted(kinds.items())},
                     "failures": failures, "seconds": round(time.time() - t0, 1),
                     "claim": "every AI state machine reads, shows and checks; findings as counted "
                              "(static, with the game's own rules; in game UNKNOWN)"}
    return failed == 0 and ok > 0


def run_check(name: str, game, report, only=CHECKS, parts=EFFECT_PARTS) -> bool:
    """One of CHECKS (the XFS check runs its YAML round trip when "yaml" is in ``only``)."""
    if name in ("xfs", "yaml"):
        return check_xfs(game, report, "yaml" in only)
    if name == "effect":
        return check_effect(game, report, parts)
    return globals()[f"check_{name}"](game, report)


# What the checks cost in one process on the installed Dark Arisen (seconds, measured; "arc" is the 30 minutes the full
# gate quotes), and the number of shards each check that can be split is cut into -- about six seconds a shard.  Every
# other check runs whole, beside them.  The longest shards start first.
SHARDS = {"xfs": 16, "sbc": 12, "flat": 12, "lot": 4, "gmd": 4, "ocl": 4}
COST = {"arc": 1800, "xfs": 105, "sbc": 70, "flat": 65, "lot": 24, "gmd": 11, "ocl": 7, "fsm": 6, "gpl": 5, "nav": 4,
        "arcs": 3, "itl": 3, "tables": 2}
MAX_JOBS = 16                                              # about 100 MB a process
UNKNOWN_COST = 120                                         # a whole check not measured: started early
SECTION = {"xfs": "xfs", "ocl": "ocl", "gmd": "gmd", "lot": "lot", "sbc": "sbc", "flat": "flat"}
# the counts a merged section's verdict is worked out from: (exact, failed), and the failures kept
VERDICT_KEYS = {"xfs": ("xfs_exact", "failed"), "ocl": ("byte_exact", "failed"), "gmd": ("byte_exact", "failed"),
                "lot": ("byte_exact", "failed"), "sbc": ("layout_exact", "failed"), "flat": ("byte_exact", "failed")}
FAILURES_KEPT = {"xfs": 50}
HEADER_KEYS = {"schema", "game", "exe_sha256", "utc", "checks", "read_from_backup", "ok", "skipped",
               "nothing_checked", "verdicts", "walks"}
# the tallies of each check that runs as shards, whose keys one process would have met in order
TALLIES = {"xfs": ("types",), "ocl": ("shapes_by_kind",), "gmd": ("languages",)}


def parse_shard(text: str) -> tuple[int, int]:
    """'I/N' -> (I, N), 0 <= I < N."""
    m = re.fullmatch(r"(\d+)/(\d+)", text.strip())
    if not m or not 0 <= int(m[1]) < int(m[2]):
        raise ValueError(f"--shard: {text!r} is not I/N with 0 <= I < N")
    return int(m[1]), int(m[2])


def _add(a, b):
    """Two parts of one report section added up: numbers summed, dicts key by key, lists joined; anything else
    (a claim, a note) is the same in every part, so the first stays."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            out[k] = _add(out[k], v) if k in out else v
        return out
    if isinstance(a, list) and isinstance(b, list):
        return a + b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) \
            and not isinstance(b, bool):
        return a + b
    return a


def _met_order(sections: list[dict], field: str) -> list:
    """The keys of one tally in the order one process would have met them: by the earliest (pass, place, turn) any
    shard noted for the key.  Two keys met at one place were met by one shard, in its own turn."""
    first: dict = {}
    for sec in sections:
        for key, at in sec.get("first_seen", {}).get(field, {}).items():
            if key not in first or at < first[key]:
                first[key] = at
    return sorted(first, key=first.__getitem__)


def merge_shards(game, check: str, sections: list[dict], report: dict) -> bool:
    """The shards of one check (their sections, in shard order) as the section one process would write, into
    ``report``; its verdict.  The verdict is worked out again from the added counts (a shard may hold none of the
    resources, or all the failures), what ``_absent`` says about a game with none of a kind is said once, and the
    keys of a tally and the failures come in the order one process would have met them (each shard noted where it
    met its own: ``first_seen``, and ``at`` on a failure)."""
    key = SECTION[check]
    parts = [{k: v for k, v in sec.items() if k not in ("none_found", "absent", "first_seen")} for sec in sections]
    out = parts[0]
    for part in parts[1:]:
        out = _add(out, part)
    keys = list(parts[0])
    if "failures" in keys:                                  # a counter only some shards met (failed) goes with the others
        met = _met_order(sections, "counts")                # (xfs: counters exist from their first increment)
        keys = [k for k in keys if k not in met]
        at = keys.index("failures")
        keys = keys[:at] + met + [k for k in out if k not in keys and k not in met] + keys[at:]
        out = {k: out[k] for k in keys}
    out["seconds"] = max(sec.get("seconds", 0.0) for sec in sections)
    found = sorted((f for sec in sections for f in sec["failures"]), key=lambda f: f.get("at", [0, 0]))
    out["failures"] = [{k: v for k, v in f.items() if k != "at"} for f in found[:FAILURES_KEPT.get(check, 40)]]
    out["shards"] = len(sections)
    for field in TALLIES.get(check, ()):
        order = _met_order(sections, field)
        if field in out and set(order) == set(out[field]):
            out[field] = {k: out[field][k] for k in order}
    if check == "lot":
        by_class = out["records_by_class"]
        order = _met_order(sections, "records_by_class")
        if set(order) != set(by_class):                     # parts that did not say when they met each class: by name
            order = sorted(by_class)
        out["records_by_class"] = {k: by_class[k] for k in sorted(order, key=lambda k: -by_class[k])}
        out["classes"] = len(by_class)
    elif check == "sbc":
        out["moved_back_largest_change_cm"] = max(sec["moved_back_largest_change_cm"] for sec in sections)
        out["claim"] = _sbc_claim(out["moved_back_largest_change_cm"], out["moved_back_files_byte_exact"],
                                  out["layout_exact"], out["triangles"], out["triangles_whose_normal_is_their_corners"],
                                  out["triangles_without_area"])
    elif check == "flat":
        out["formats"] = sections[0]["formats"]
    report[key] = out
    exact_key, failed_key = VERDICT_KEYS[check]
    exact, failed = out.get(exact_key, 0), out.get(failed_key, 0)
    if check == "flat":
        return failed == 0 and exact > 0
    return _absent(game, check, exact, failed, key, report)


def _units(only: list[str]) -> list[str]:
    """The checks as processes run them: the XFS check's one pass does the "yaml" check too."""
    return list(dict.fromkeys("xfs" if n == "yaml" else n for n in only))


def _plan(only: list[str], parts: tuple[str, ...], text: str) -> list[tuple[str, int, int, str]]:
    """The processes to run: (check, shard, shards, its --only), longest first.  ``text``: the --only as asked."""
    names = [n.strip() for n in text.split(",") if n.strip()]
    tasks = []
    for name in _units(only):
        if name == "xfs":
            child = "xfs,yaml" if "yaml" in only else "xfs"
        elif name == "effect":
            child = ",".join(n for n in names if n == "effect" or n in EFFECT_PARTS)
        else:
            child = name
        n = SHARDS.get(name, 1)
        tasks += [(name, i, n, child) for i in range(n)]
    tasks.sort(key=lambda t: -COST.get(t[0], UNKNOWN_COST) / t[2])      # stable: ties keep the order of CHECKS
    return tasks


def _run_task(task, game, tmp: str) -> dict:
    """One process of the plan: this script on the same game with --shard, its report read back."""
    name, i, n, child = task
    out = os.path.join(tmp, f"{name}-{i}of{n}.json")
    cmd = [sys.executable, "-B", str(Path(__file__).resolve()), "--game", str(game.root), "--only", child,
           "--shard", f"{i}/{n}", "--jobs", "1", "--out", out]
    # at this process's own priority: a below-normal class starved the shards on a busy PC (3-4 times slower)
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        rep = json.loads(Path(out).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rep = None
    return {"task": task, "report": rep, "rc": proc.returncode, "tail": (proc.stdout + proc.stderr)[-800:]}


def _walk_mismatch(runs: list[dict]) -> str | None:
    """None when the shards of one check (in shard order) walked one sequence of resources in every pass; else
    what differs.  Each shard counts the places of the whole sequence and digests what stands at each, so shards
    that read a game that changed between them, or that were run by hand at different times, are told apart."""
    name = runs[0]["task"][0]
    walks = [d["report"].get("walks", {}).get(name) for d in runs]
    for d, w in zip(runs, walks):
        shard = f"{d['task'][1]}/{d['task'][2]}"
        if w is None:
            return f"shard {shard} did not say what it walked"
        if len(w) != len(walks[0]):
            return f"shard {shard} made {len(w)} passes over the resources, shard 0/{d['task'][2]} made {len(walks[0])}"
        for p, (mine, first) in enumerate(zip(w, walks[0]), 1):
            if mine != first:
                return (f"shard {shard} and shard 0/{d['task'][2]} did not walk the same resources (pass {p}: "
                        f"{mine['places']} places against {first['places']}, digests {mine['sha256'][:12]} and "
                        f"{first['sha256'][:12]})")
    return None


def run_parallel(game, only, parts, text: str, jobs: int, tasks) -> tuple[dict, bool]:
    """The checks as processes ``jobs`` at a time; (the sections and skips of the report one process would write
    -- every check's, in CHECKS order -- and whether all of them passed)."""
    with tempfile.TemporaryDirectory(prefix="rs-corpus-") as tmp, ThreadPoolExecutor(max_workers=jobs) as pool:
        done = list(pool.map(lambda t: _run_task(t, game, tmp), tasks))
    by_check: dict[str, list[dict]] = {}
    for d in done:
        by_check.setdefault(d["task"][0], []).append(d)
    report: dict = {}
    good = True
    for name in _units(only):
        runs = sorted(by_check[name], key=lambda d: d["task"][1])
        broken = [d for d in runs if d["report"] is None]
        if broken:
            good = False
            report.setdefault("crashed", {})[name] = [{"shard": f"{d['task'][1]}/{d['task'][2]}", "rc": d["rc"],
                                                       "output": d["tail"]} for d in broken]
            continue
        for d in runs:
            for k, why in d["report"].get("skipped", {}).items():
                report.setdefault("skipped", {})[k] = why
        if SHARDS.get(name, 1) > 1:
            why = _walk_mismatch(runs)
            if why:                                         # their counts are of different games: not added up
                good = False
                report.setdefault("shard_mismatch", {})[name] = why
                continue
            good &= merge_shards(game, name, [d["report"][SECTION[name]] for d in runs], report)
        else:
            rep = runs[0]["report"]
            for k, v in rep.items():
                if k not in HEADER_KEYS:
                    report[k] = v
            good &= all(rep.get("verdicts", {}).values())
    return report, good


def main(argv: list[str] | None = None) -> int:
    global SHARD
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game")
    ap.add_argument("--only", default=",".join(CHECKS), help="checks to run, comma-separated (default: all)")
    ap.add_argument("--out")
    ap.add_argument("--jobs", type=int, default=min(os.cpu_count() or 1, MAX_JOBS),
                    help=f"processes at a time (default: one per CPU, at most {MAX_JOBS}); "
                         "1 runs every check in this process")
    ap.add_argument("--shard", help="I/N: run only the resources whose place in each pass is I modulo N, and write "
                                    "that part of the report (what the processes of a parallel run are)")
    a = ap.parse_args(argv)
    try:
        only, parts = parse_only(a.only)
        shard = parse_shard(a.shard) if a.shard else None
    except ValueError as e:
        ap.error(str(e))                                    # exit 2, nothing run
    game = find_game(a.game)
    report: dict = {}
    if shard is None:
        kept = corpus.backups(game)
        exe_sha = hashlib.sha256(game.exe.read_bytes()).hexdigest()
        report = {"schema": "riftstone.corpus-check/1", "game": str(game.root), "exe_sha256": exe_sha,
                  "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "checks": only,
                  # archives an install replaced in nativePC: every check reads Riftstone's backup of the original
                  "read_from_backup": sorted(game.arc_name(p) for p in game.archives()
                                             if corpus.source(game, p, kept) != p)}
    tasks = _plan(only, parts, a.only)
    good = True
    if shard is None and a.jobs > 1 and len(tasks) > 1:
        sections, good = run_parallel(game, only, parts, a.only, a.jobs, tasks)
        report.update(sections)
    else:
        SHARD = shard
        verdicts = {}
        for name in only:
            if name == "yaml" and "xfs" in only:            # the XFS check's one pass does both
                continue
            WALKS.clear()
            verdicts[name] = run_check(name, game, report, only, parts)
            good &= verdicts[name]
            if shard is not None:                           # what this part walked, for the others to be compared with
                report.setdefault("walks", {})[name] = [{"places": w.places, "sha256": w.digest} for w in WALKS]
        if shard is not None:
            report["verdicts"] = verdicts
    skipped = report.get("skipped", {})
    if all(name in skipped for name in only):
        good = False
        report["nothing_checked"] = f"every check asked for is skipped for {game.title}"
    report["ok"] = good
    text = json.dumps(report, indent=1)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    if shard is not None:
        return 0 if good else 1
    summary = {k: {kk: vv for kk, vv in v.items() if kk not in ("failures", "types")} if isinstance(v, dict) else v
               for k, v in report.items()}
    print(json.dumps(summary, indent=1))
    for section in ("xfs", "ocl", "gmd", "itl", "lot", "arcs", "tables", "flat", "gpl", "tex", "mrl", "prp", "ean", "lmt",
                    "weather", "lcm", "sound", "epv", "efl", "e2d", "efs", "ddo_params", "ndp", "fca", "msgset", "sdl", "zon", "sbc",
                    "fsm", "nav", "arc"):
        for f in report.get(section, {}).get("failures", [])[:5]:
            print("FAIL", json.dumps(f)[:600])
    for name, runs in report.get("crashed", {}).items():
        for r in runs:
            print("CRASHED", name, json.dumps(r)[:800])
    for name, why in report.get("shard_mismatch", {}).items():
        print("SHARDS DISAGREE", name, why)
    return 0 if good else 1


if __name__ == "__main__":
    raise SystemExit(main())
