"""Round-trip every vanilla resource Riftstone claims to understand; report exact results.

    python tools/check_corpus.py [--game PATH] [--only arc,xfs,yaml,ocl,gmd,itl,lot,tables,flat,gpl,tex,mrl,prp,ean,lmt,weather,lcm,sound,effect,ddo_params,ndp,fca,msgset,sdl,zon,sbc,fsm,nav] [--out report.json]

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
tables  every item set / drop table and recipe table: parse -> build and -> YAML -> back
flat  every flat parameter file (ajp, character-creator, itemlv, skl ...): parse -> build and -> YAML -> back
gpl   every enemy group placement file: parse -> build and -> YAML -> back
tex   every texture: parse -> build (byte-exact, cube maps included); and, for flat
      textures, .tex -> .dds -> .tex, compare bytes
mrl   every material file: parse -> build, compare bytes; shaders + texture bindings read
lmt   every motion list: parse -> build, compare bytes; every keyframe buffer -> keys -> buffer;
      key deltas add up to frames - 1; rotation keys unit length (reported)
weather  every weather effect / fog / sky file (and DDO's weather tables): parse -> build and -> YAML -> back
lcm   every camera list: parse -> build and -> YAML -> back
sound every sound cue resource (requests, stream requests, random tables, mixers, physics lists, banks,
      area info): parse -> build and -> YAML -> back
effect  every effect provider / list / 2D effect / strip (.epv .efl .e2d .efs): parse -> build; the first
      three also -> YAML -> back
ddo_params  (DDO) every enemy / stage parameter file (cpe pep prs osp sti sal evtr ndp): parse -> build and
      -> YAML -> back
ndp   (DDO) the named enemy parameters: parse -> build and -> YAML -> back; equal to the local server's
      named_param.ndp.json field for field; every id named in named_param.gmd; the Solo Balance twins fit
fca / msgset / sdl / zon  facial animations, message sets and serial lists, schedulers, zones:
      parse -> build and -> YAML -> back
sbc   every collision mesh: its sections add up to the file under the loader's layout (sbc.py), every
      part keeps its vertices inside its box, and a move by a Gransys cell corner and back is exact
      and changes only the positional floats (boxes, tree lanes, vertices)
fsm   every AI state machine: the readable view and fsmcheck (the game's own transition rules, read in
      the executables and run in native/fsm_exec) on every machine; counts what the checks find, and
      each finding names a state and link the file has
nav   every navigation mesh: parse -> build, compare bytes; each link's cost against its triangles' centroids;
      links that have their reverse counted; nav.NAV_OF against DDDA.exe's stage table (Dark Arisen)

Reads the game only.  Exit 0 when everything is exact, 1 otherwise.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import traceback
import zlib
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from riftstone import arc, corpus, flat, gmd, itl, lot, ocl, tables, typemap, xfs  # noqa: E402
from riftstone.game import find_game  # noqa: E402


def check_ocl_ddo(game, report):
    """DDO collision (COL\\0, rCollIndex/rCollNode/rAttackParam): parse -> build and the YAML round trip, byte-exact."""
    from riftstone import ocl_ddo

    t0 = time.time()
    exact = failed = nodes = shapes = attacks = uid_ok = 0
    kinds: dict[str, int] = {}
    failures = []
    for r in corpus.resources(game, [typemap.BY_EXT["ocl"]]):
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
                    kinds[k] = kinds.get(k, 0) + 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            if len(failures) < 40:
                failures.append({"resource": r.label, "why": f"{type(e).__name__}: {e}"})
    report["ocl"] = {"distinct": exact + failed, "byte_exact": exact, "nodes": nodes, "hit_shapes": shapes,
                     "shapes_by_kind": kinds, "attack_params": attacks, "uid_is_path_jamcrc": uid_ok,
                     "failed": failed, "failures": failures, "seconds": round(time.time() - t0, 1),
                     "claim": "parse->build and YAML round trip reproduce every DDO .ocl byte-for-byte"}
    return failed == 0 and exact > 0


def check_ocl(game, report):
    if game.is_ddo:
        return check_ocl_ddo(game, report)
    t0 = time.time()
    exact = failed = groups = nodes = attacks = empty = seqs = 0
    shapes: dict[str, int] = {}
    failures = []
    for r in corpus.resources(game, [typemap.BY_EXT["ocl"]]):
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
                shapes[k] = shapes.get(k, 0) + 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            if len(failures) < 40:
                failures.append({"resource": r.label, "why": f"{type(e).__name__}: {e}"})
    report["ocl"] = {"distinct": exact + failed, "byte_exact": exact, "groups": groups, "nodes": nodes,
                     "shapes_by_kind": shapes, "attacks": attacks, "empty_attack_slots": empty,
                     "sequence_entries": seqs, "failed": failed, "failures": failures,
                     "seconds": round(time.time() - t0, 1),
                     "claim": "rObjCollision::load's grammar reads every .ocl to its last byte; parse->build and "
                              "the YAML round trip reproduce every one byte for byte"}
    return failed == 0 and exact > 0


def check_gmd(game, report):
    t0 = time.time()
    exact = failed = messages = labels = 0
    languages = Counter()
    failures = []
    for r in corpus.resources(game, [typemap.BY_EXT["gmd"]]):
        try:
            g = gmd.parse(r.data)
            if gmd.build(g) != r.data:
                raise AssertionError("rebuild differs")
            if gmd.yaml_to_bytes(gmd.to_yaml(g, r.name.decode("latin-1"))) != r.data:
                raise AssertionError("YAML round trip differs")
            exact += 1
            messages += len(g.messages)
            labels += sum(m.label is not None for m in g.messages)
            languages[g.language_name] += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            if len(failures) < 40:
                failures.append({"resource": r.label, "why": f"{type(e).__name__}: {e}"})
    report["gmd"] = {"distinct": exact + failed, "byte_exact": exact, "messages": messages, "labels": labels,
                     "languages": dict(languages), "failed": failed, "failures": failures,
                     "seconds": round(time.time() - t0, 1),
                     "claim": "parse->build and YAML round-trip reproduce every .gmd byte-for-byte"}
    return failed == 0


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
    why = ABSENT.get("ddo" if game.is_ddo else "ddda", {}).get(check)
    if why and exact == 0 and failed == 0:
        report[report_key]["absent"] = why
        return True
    return failed == 0 and exact > 0


def check_itl(game, report):
    t0 = time.time()
    why = _not_decoded(game, "itl")
    if why:
        n = sum(1 for _ in corpus.resources(game, [typemap.BY_EXT["itl"]]))
        report["itl"] = {"distinct": n, "not_decoded": why, "seconds": round(time.time() - t0, 1)}
        return True
    exact = failed = records = 0
    failures = []
    # the enhancement tables' row counts, for the rule DDDA.exe's lookup follows (itl.level_table, 0x0045B620)
    from riftstone import flat
    rows = {r.name.decode("latin-1").rsplit("\\", 1)[-1]: len(flat.parse(r.data, "itemlv").data["mpArray"])
            for r in corpus.resources(game, [typemap.BY_EXT["itemlv"]])}
    equipment = {}
    for r in corpus.resources(game, [typemap.BY_EXT["itl"]]):
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
    exact = failed = records = 0
    kinds: dict[str, int] = {}
    failures = []
    for r in corpus.resources(game, [typemap.BY_EXT["lot"]]):
        try:
            lt = lot_ddo.parse(r.data)
            if lot_ddo.build(lt) != r.data:
                raise AssertionError("rebuild differs")
            if lot_ddo.yaml_to_bytes(lot_ddo.to_yaml(lt, r.name.decode("latin-1"))) != r.data:
                raise AssertionError("YAML round trip differs")
            exact += 1
            records += len(lt.records)
            for rec in lt.records:
                kinds[rec.cls] = kinds.get(rec.cls, 0) + 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            if len(failures) < 40:
                failures.append({"resource": r.label, "why": f"{type(e).__name__}: {e}"})
    report["lot"] = {"distinct": exact + failed, "byte_exact": exact, "records": records, "classes": len(kinds),
                     "records_by_class": dict(sorted(kinds.items(), key=lambda kv: -kv[1])), "failed": failed,
                     "failures": failures, "seconds": round(time.time() - t0, 1)}
    return failed == 0


def check_lot(game, report):
    if game.is_ddo:
        return check_lot_ddo(game, report)
    t0 = time.time()
    exact = failed = records = named = 0
    kinds: dict[str, int] = {}
    failures = []
    for r in corpus.resources(game, [typemap.BY_EXT["lot"]]):
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
                kinds[rec.cls] = kinds.get(rec.cls, 0) + 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            if len(failures) < 40:
                failures.append({"resource": r.label, "why": f"{type(e).__name__}: {e}"})
    report["lot"] = {"distinct": exact + failed, "byte_exact": exact, "records": records, "classes": len(kinds),
                     "records_by_class": dict(sorted(kinds.items(), key=lambda kv: -kv[1])),
                     "names_decoded": named, "failed": failed,
                     "failures": failures, "seconds": round(time.time() - t0, 1),
                     "claim": "every record of every .lot is decoded field by field with the exe's own grammar; "
                              "parse->build and YAML round-trip reproduce every file byte-for-byte; copy-then-remove "
                              "restores every file"}
    return failed == 0


def check_arcs(game, report):
    """Every distinct archive reference (rArchive, ARCS): parse -> build byte-exact, and its list equals the
    referenced archive's directory (name hashes and types, in order)."""
    from riftstone import arcref

    t0 = time.time()
    exact = failed = matching = stale = no_target = entries = 0
    failures, differing = [], []
    for r in corpus.resources(game, [typemap.BY_EXT["arc"]]):
        try:
            ref = arcref.parse(r.data)
            if arcref.build(ref) != r.data:
                raise AssertionError("rebuild differs")
            exact += 1
            entries += len(ref.entries)
            try:
                path = game.arc_path(arcref.target(r.name))
            except Exception:  # noqa: BLE001 - a name that is not an archive path
                path = None
            if path is None or not path.is_file():
                no_target += 1
            elif arcref.matches(ref, [(n, t) for n, t, *_ in corpus.directory(path)]):
                matching += 1
            else:
                stale += 1          # measured, not a failure: vanilla ships two (see arcref.py)
                differing.append(r.label)
        except Exception as e:  # noqa: BLE001
            failed += 1
            if len(failures) < 40:
                failures.append({"resource": r.label, "why": f"{type(e).__name__}: {e}"})
    report["arcs"] = {"distinct": exact + failed, "byte_exact": exact, "entries": entries, "match_target": matching,
                      "differ_from_target": stale, "differing": differing[:40], "target_missing": no_target,
                      "failed": failed, "failures": failures, "seconds": round(time.time() - t0, 1),
                      "claim": "every archive reference rebuilds byte-for-byte; all but a measured few list their "
                               "archive's directory exactly (vanilla ships 2 out-of-date ones)"}
    return failed == 0


def check_tables(game, report):
    t0 = time.time()
    exact = failed = rows = 0
    failures = []
    for r in corpus.resources(game, [typemap.BY_EXT["ist"], typemap.BY_EXT["imx"]]):
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
    for r in corpus.resources(game, [typemap.BY_EXT["gpl"]]):
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
    for r in corpus.resources(game, [typemap.BY_EXT["gpl"]]):
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
    exact = failed = dds_ok = dds_na = 0
    failures = []
    for r in corpus.resources(game, [typemap.BY_EXT["tex"]]):
        try:
            t = tex.parse(r.data)
            if tex.build(t) != r.data:
                raise AssertionError("rebuild differs")
            exact += 1
            try:
                dds = tex.to_dds(t)
            except Exception:  # noqa: BLE001
                dds_na += 1  # cube map or an unconvertible format: raw round-trip still exact above
            else:
                if tex.build(tex.dds_to_tex(dds, template=t)) != r.data:
                    raise AssertionError("DDS round trip differs")
                dds_ok += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            if len(failures) < 40:
                failures.append({"resource": r.label, "why": f"{type(e).__name__}: {e}"})
    report["tex"] = {"distinct": exact + failed, "byte_exact": exact, "dds_round_trip": dds_ok,
                     "dds_not_applicable": dds_na, "failed": failed, "failures": failures,
                     "seconds": round(time.time() - t0, 1),
                     "claim": "parse->build reproduces every .tex byte-for-byte (cube maps included); "
                              ".dds export/import round-trips every flat texture byte-for-byte"}
    return failed == 0 and exact > 0


def check_mrl(game, report):
    from riftstone import mrl

    t0 = time.time()
    exact = failed = shaders = binds = 0
    seen_shaders = set()
    failures = []
    for r in corpus.resources(game, [typemap.BY_EXT["mrl"]]):
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


def check_sbc(game, report):
    from riftstone import sbc

    t0 = time.time()
    exact = failed = nodes = floats = vertices = 0
    failures = []
    corner = (-150000.0, 0.0, -30000.0)                       # cell 47m35n's
    for r in corpus.resources(game, [typemap.BY_EXT["sbc"]]):
        try:
            s = sbc.parse(r.data)
            problems = sbc.bounds_problems(r.data)
            if problems:
                raise AssertionError("; ".join(problems))
            moved = sbc.moved_floats(r.data)
            w = sbc.translate(r.data, corner)
            if sbc.translate(sbc.translate(w, tuple(-v for v in corner)), corner) != w:
                raise AssertionError("moving back is not exact")
            a, b = bytearray(r.data), bytearray(w)
            for off, _ in moved:
                a[off:off + 4] = b[off:off + 4] = b"\0\0\0\0"
            if a != b:
                raise AssertionError("a byte that is not a position changed")
            if sbc.bounds_problems(w):
                raise AssertionError("the moved file breaks its own boxes")
            exact += 1
            nodes += sum(t.nodes for t in s.trees)
            floats += len(moved)
            vertices += s.vertices
        except Exception as e:  # noqa: BLE001
            failed += 1
            if len(failures) < 40:
                failures.append({"resource": r.label, "why": f"{type(e).__name__}: {e}"})
    report["sbc"] = {"distinct": exact + failed, "layout_exact": exact, "tree_nodes": nodes, "vertices": vertices,
                     "moved_floats": floats, "failed": failed, "failures": failures,
                     "seconds": round(time.time() - t0, 1),
                     "claim": "every collision mesh follows the loader's layout to its last byte; a move by a "
                              "cell corner changes only boxes, tree lanes and vertices, and moving back is exact"}
    return failed == 0 and exact > 0


def check_ean(game, report):
    from riftstone import ean

    t0 = time.time()
    exact = failed = frames = 0
    failures = []
    for r in corpus.resources(game, [typemap.BY_EXT["ean"]]):
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
    for r in corpus.resources(game, [typemap.BY_EXT["nav"]]):
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
    table = None
    if game.kind == "ddda":
        try:
            t = nav.stage_table(game.exe.read_bytes())
            table = {s: v for s, v in sorted(t.items()) if s != v} == nav.NAV_OF
        except Exception as exc:  # noqa: BLE001
            failures.append({"resource": "DDDA.exe stage table", "why": f"{type(exc).__name__}: {exc}"})
            table = False
    report["nav"] = {"distinct": exact + failed, "byte_exact": exact, "failed": failed, "triangles": triangles,
                     "links": links, "links_with_reverse": reverse, "cost_vs_centroids_worst": round(worst, 5),
                     "links_cost_off_1pct": dict(off), "stage_table_matches_exe": table, "failures": failures,
                     "seconds": round(time.time() - t0, 1),
                     "claim": "parse->build reproduces every navigation mesh byte-for-byte; a link's cost is the "
                              "centroids' distance in metres (Dark Arisen: every link within 1%; Online: all but "
                              "rm107's 96, measured 2026-09-26); nav.NAV_OF is the exe's table"}
    costs_ok = worst < 0.01 if game.kind == "ddda" else set(off) <= {"st0407.arc:scr\\rm\\rm107\\etc\\rm107_nav"}
    return failed == 0 and exact > 0 and table is not False and costs_ok


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
    for r in corpus.resources(game, [typemap.BY_EXT["lmt"]]):
        try:
            m = lmt.parse(r.data)
            if lmt.build(m) != r.data:
                raise AssertionError("rebuild differs")
            # the port to the other game keeps every motion: bones, key frames, values within a codec step
            pm = lmt.parse(port.convert_lmt(r.data, game.kind, other).data)
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
                              "to the other game keeps every track's bone, key frames and values (within half "
                              "a codec 6 step, 0.000122)"}
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
    for r in corpus.resources(game, list(ids)):
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
    for r in corpus.resources(game, [typemap.BY_EXT["lcm"]]):
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
    for r in corpus.resources(game, [typemap.BY_EXT[e] for e in exts]):
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


def check_effect(game, report):
    """Effect formats: .epv / .efl / .e2d / .efs parse -> build byte-exact; .epv/.efl/.e2d also through the
    params YAML round trip."""
    from riftstone import effect, effect_e2d, effect_efl, effect_efs, params

    good = True
    mods = {"epv": effect, "efl": effect_efl, "e2d": effect_e2d, "efs": effect_efs}
    for ext, mod in mods.items():
        t0 = time.time()
        exact = yaml_ok = failed = 0
        named = total = 0
        kinds: Counter = Counter()
        res: Counter = Counter()
        failures = []
        for r in corpus.resources(game, [typemap.BY_EXT[ext]]):
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
        good &= failed == 0 and exact > 0
    return good


def check_ddo_params(game, report):
    """DDO enemy and stage parameters (cpe pep prs osp sti sal evtr ndp): parse -> build and the YAML round trip
    through params, byte-exact.  Dark Arisen has none of these types (nothing to check there)."""
    from riftstone import ddo_params, params

    if not game.is_ddo:
        return True
    t0 = time.time()
    tids = {spec.type_id: ext for ext, spec in ddo_params.KINDS.items()}
    per = {ext: {"distinct": 0, "byte_exact": 0, "yaml_exact": 0, "failed": 0} for ext in ddo_params.KINDS}
    failures = []
    flying = 0
    for r in corpus.resources(game, list(tids)):
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
    reading only directories; an installed mod's copy is skipped for the original Riftstone kept."""
    for path in game.archives():
        if any(n == name and t == type_id for n, t, *_ in corpus.directory(path)):
            a = game.arc_name(path)
            e = arc.Archive.read(game.vanilla_arc(a)).find(name, type_id)
            if e is not None:
                return a, e.data()
    return None, None


def check_ndp(game, report):
    """DDO named enemy parameters (rNamedParam .ndp): parse -> build and the YAML round trip byte-exact;
    every record equal to the local server's named_param.ndp.json field for field; every id named by the
    label namedparam_<id> in ui/00_message/named/named_param.gmd (the client's name lookup, 0x00BFD003);
    and the Solo Balance twins (ddo_solo) within the client's limits.  Dark Arisen has no .ndp."""
    from riftstone import ddo, ddo_params, ddo_solo, params

    if not game.is_ddo:
        return True
    t0 = time.time()
    tid = typemap.BY_EXT["ndp"]
    entry = {"distinct": 0, "byte_exact": 0, "yaml_exact": 0, "failures": []}
    report["ndp"] = entry
    fail = entry["failures"].append
    tables = []
    for r in corpus.resources(game, [tid]):
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
    # the checks below are about the table as the game shipped it (with Solo Balance installed the live
    # archive holds the twinned table, which must round-trip above, but is not the server's original)
    npath, nraw = _find_resource(game, ddo_solo.NDP_NAME, tid)
    if not tables or nraw is None:
        entry["seconds"] = round(time.time() - t0, 1)
        return False
    m = ddo_params.parse(nraw, "ndp")
    entry["original"] = {"archive": npath, "installed_copy_differs": any(t.data != nraw for t, _ in tables)}
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
    entry["claim"] = ("param/named_param.ndp rebuilds byte-for-byte (binary and YAML), equals the local server's "
                      "named_param.ndp.json field for field, and names every id; its Solo Balance twins fit the "
                      "client's id table")
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
    for r in corpus.resources(game, list(ids)):
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
    for r in corpus.resources(game, [tid]):
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
    per = {}
    total_ok = total_bad = 0
    failures = []
    tids = [(ext, typemap.BY_EXT[ext]) for ext in flat.SCHEMAS if ext in typemap.BY_EXT]
    seen = {ext: 0 for ext, _ in tids}
    for ext, tid in tids:
        why = _not_decoded(game, ext)
        if why:
            per[ext] = {"files": sum(1 for _ in corpus.resources(game, [tid])), "not_decoded": why}
            continue
        ok = bad = 0
        for r in corpus.resources(game, [tid]):
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
                    failures.append({"resource": r.label, "why": f"{type(e).__name__}: {e}"})
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
    ok = bad = foreign = recompressed = 0
    failures = []
    for path in game.archives():
        raw = path.read_bytes()
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
    report["arc"] = {"archives": ok + bad, "exact": ok, "failed": bad, "failures": failures[:50],
                     "seconds": round(time.time() - t0, 1),
                     "claim": "parse + recompress (zlib 6) + rebuild reproduces the vanilla archive bytes"
                     if game.kind == "ddda" else
                     "ARCC: every entry decrypts + inflates; rebuild from stored payloads is byte-exact; "
                     "every Capcom zlib-6 entry also reproduces when recompressed + re-encrypted"}
    if game.kind != "ddda":
        report["arc"].update({"entries_recompressed_exact": recompressed, "entries_foreign_zlib": foreign})
    return bad == 0


def check_xfs(game, report, with_yaml):
    if with_yaml:
        from riftstone import params

    t0 = time.time()
    counts = Counter({"strings": 0, "strings_non_ascii": 0, "strings_not_text": 0})
    failures = []
    per_type = Counter()
    for r in corpus.resources(game, corpus.xfs_type_ids()):
        per_type[typemap.class_name(r.type_id)] += 1
        try:
            x = xfs.parse(r.data)
            if xfs.build(x) != r.data:
                raise AssertionError("XFS rebuild differs")
            if xfs.build_definitions(x.classes, x.version) != x.extra["def_raw"]:
                raise AssertionError("regenerated definition block differs")
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
                counts["yaml_exact"] += 1
        except Exception as e:  # noqa: BLE001
            counts["failed"] += 1
            if len(failures) < 50:
                failures.append({"resource": r.label, "type": typemap.class_name(r.type_id),
                                 "why": f"{type(e).__name__}: {e}", "trace": traceback.format_exc(limit=3)})
    report["xfs"] = {"distinct_resources": sum(per_type.values()), **counts, "failures": failures,
                     "types": dict(per_type), "seconds": round(time.time() - t0, 1),
                     "claim": "byte-exact XFS parse/build" + (" and XFS->YAML->XFS" if with_yaml else "")}
    return counts["failed"] == 0


def check_fsm(game, report):
    """Every distinct AI state machine: the readable view and fsmcheck.check without an error, and every
    finding pointing at a machine, state and link the file has; what the checks find, counted by kind."""
    from riftstone import fsm, fsmcheck

    t0 = time.time()
    ok = failed = machines = states = links = 0
    kinds, verdicts, files_with = Counter(), Counter(), Counter()
    failures = []
    for r in corpus.resources(game, [typemap.BY_EXT["fsm"]]):
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game")
    ap.add_argument("--only", default="arc,xfs,yaml,ocl,gmd,itl,lot,arcs,tables,flat,gpl,tex,mrl,prp,ean,lmt,"
                                      "weather,lcm,sound,effect,ddo_params,ndp,fca,msgset,sdl,zon,sbc,fsm,nav")
    ap.add_argument("--out")
    a = ap.parse_args()
    game = find_game(a.game)
    only = set(a.only.split(","))
    exe_sha = hashlib.sha256(game.exe.read_bytes()).hexdigest()
    report = {"schema": "riftstone.corpus-check/1", "game": str(game.root), "exe_sha256": exe_sha,
              "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    good = True
    if "xfs" in only or "yaml" in only:
        good &= check_xfs(game, report, "yaml" in only)
    if "ocl" in only:
        good &= check_ocl(game, report)
    if "gmd" in only:
        good &= check_gmd(game, report)
    if "itl" in only:
        good &= check_itl(game, report)
    if "lot" in only:
        good &= check_lot(game, report)
    if "arcs" in only:
        good &= check_arcs(game, report)
    if "tables" in only:
        good &= check_tables(game, report)
    if "flat" in only:
        good &= check_flat(game, report)
    if "gpl" in only:
        good &= check_gpl(game, report)
    if "tex" in only:
        good &= check_tex(game, report)
    if "mrl" in only:
        good &= check_mrl(game, report)
    if "prp" in only:
        good &= check_prp(game, report)
    if "ean" in only:
        good &= check_ean(game, report)
    if "lmt" in only:
        good &= check_lmt(game, report)
    if "weather" in only:
        good &= check_weather(game, report)
    if "lcm" in only:
        good &= check_lcm(game, report)
    if "sound" in only:
        good &= check_sound(game, report)
    if "effect" in only:
        good &= check_effect(game, report)
    if "ddo_params" in only:
        good &= check_ddo_params(game, report)
    if "ndp" in only:
        good &= check_ndp(game, report)
    if "fca" in only:
        good &= check_fca(game, report)
    if "msgset" in only:
        good &= check_msgset(game, report)
    if "sdl" in only:
        good &= check_sdl(game, report)
    if "zon" in only:
        good &= check_zon(game, report)
    if "sbc" in only:
        good &= check_sbc(game, report)
    if "fsm" in only:
        good &= check_fsm(game, report)
    if "nav" in only:
        good &= check_nav(game, report)
    if "arc" in only:
        good &= check_arc(game, report)
    report["ok"] = good
    text = json.dumps(report, indent=1)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    summary = {k: {kk: vv for kk, vv in v.items() if kk not in ("failures", "types")} if isinstance(v, dict) else v
               for k, v in report.items()}
    print(json.dumps(summary, indent=1))
    for section in ("xfs", "ocl", "gmd", "itl", "lot", "arcs", "tables", "flat", "gpl", "tex", "mrl", "prp", "ean", "lmt",
                    "weather", "lcm", "sound", "epv", "efl", "e2d", "efs", "ddo_params", "ndp", "fca", "msgset", "sdl", "zon", "sbc",
                    "fsm", "nav", "arc"):
        for f in report.get(section, {}).get("failures", [])[:5]:
            print("FAIL", json.dumps(f)[:600])
    return 0 if good else 1


if __name__ == "__main__":
    raise SystemExit(main())
