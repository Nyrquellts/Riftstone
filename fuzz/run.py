"""Run the fuzz targets in parallel and write a report.

    python fuzz/run.py --seconds 120 --workers 16 [--targets cli,xfs,...]

Seeds come from the installed game (cached in fuzz/.seeds, which is ignored
by git because it holds game data) or, without the game, from synthetic
samples.  Findings (input + traceback) land in fuzz/findings/.  Exit 1 when
anything was found.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import pickle
import shutil
import struct
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE.parent / "tests"))

SEEDS = HERE / ".seeds" / "seeds.pkl"
FINDINGS = HERE / "findings"
REPORTS = HERE / "reports"
# The encodings of the cached seeds, where a target's changed: a cache made before gets that target's seeds anew.
SEED_FORMATS_KEY = "_formats"
SEED_FORMATS = {"fsmap": 2}      # 2 (2026-09-26): a two-byte type selector; one byte reached 256 of the types
# A run's scratch folder in --tmp: this prefix and this marker file inside, so sweep_stale removes only its own.
SCRATCH_PREFIX = "riftstone-fuzz-run-"
SCRATCH_MARK = "riftstone-fuzz-scratch.txt"


def _synthetic() -> dict[str, list[bytes]]:
    import helpers
    from riftstone import arc, arcfolder, params, typemap, xfs

    x = helpers.sample_xfs()
    xraw = xfs.build(x)
    text = params.to_yaml(x, "fuzz\\sample", 0x215896C2).encode()
    import test_fsm
    import test_fsmcheck
    import test_items
    import test_gpl
    import test_lot
    import test_playtest
    import test_runtime
    import test_tables
    from riftstone import flat as _flat
    from riftstone import gpl as _gplm
    from riftstone import lot as _lotmod
    from riftstone import tex as _tex
    from riftstone import mrl as _mrl
    from riftstone import prp as _prp
    _prp_seed = _prp.build(x)
    _prp_glossed = _prp.build(xfs.Xfs(4, [xfs.ClassDef(typemap.jamcrc("rFuzzPrp"), 0x10, (    # fields prp.GLOSS names
        helpers.prop("攻撃力", "f32"), helpers.prop("スケール値", "f32"), helpers.prop("経験値", "u32"),
        helpers.prop("耐久聖", "f32", 0x20)))], xfs.Obj(0, [[250.0], [1.5], [65], [1.0, 2.0]])))
    from riftstone import ean as _ean
    _ean_seed = _ean.build(_ean.Ean(1, bytes(56)))
    _tex_seed = _tex.build(_tex.Tex(0x20000, _tex.VERSION, 2, 8, 8, 1, 20, 1,
                                    struct.pack("<2I", 24, 24 + _tex._mip_size(8, 8, 0, 20))
                                    + bytes(_tex._mip_size(8, 8, 0, 20) + _tex._mip_size(8, 8, 1, 20))))
    import world_fixture
    _skin_tex = world_fixture.chimera_texture()
    _fexts = __import__("targets").flat_order()
    _ajp = _flat.build(_flat.Flat("ajp", 0, {"version": 1, "mpArray": [1.0, -2.0, 0.5]}))
    _fed = _flat.build(_flat.Flat("fed", 0, {"version": 1, "mpMarkerArray": [{"x": 1.0, "y": 2.0, "z": 3.0}]}))
    lot_text = _lotmod.to_yaml(_lotmod.parse(test_lot.SAMPLE), "x")
    import base64
    import test_texcodec
    from riftstone import texcodec as _tc

    _mk = test_texcodec.make_png
    _pngs = [_tc.png(4, 3, bytes(range(48))), _mk(5, 5, 0, 8, bytes(range(25))), _mk(3, 4, 2, 16, bytes(range(72))),
             _mk(4, 4, 4, 8, bytes(range(32))), _mk(6, 5, 6, 8, bytes(range(120))),
             _mk(4, 2, 3, 8, bytes([0, 1, 2, 1, 0, 2, 2, 1]), bytes(range(9)), b"\x00\x80")]
    _pic = base64.b64encode(_tc.png(16, 16, bytes(v for i in range(256) for v in (i, 255 - i, 90, 255)))).decode()
    _dds = base64.b64encode(_tex.to_dds(_tex.parse(_skin_tex))).decode()
    _files_cases = (
        {"route": "files/list", "q": {"mod": "Fuzz"}},
        {"route": "files/preview", "q": {"mod": "Fuzz", "rel": "@tex"}},
        {"route": "files/preview", "q": {"mod": "Fuzz", "rel": "@gpl"}},
        {"route": "files/preview", "q": {"mod": "Fuzz", "rel": "@mrl"}},
        {"route": "files/download", "q": {"mod": "Fuzz", "rel": "@tex", "as": "png"}},
        {"route": "files/download", "q": {"mod": "Fuzz", "rel": "@tex", "as": "dds"}},
        {"route": "files/download", "q": {"mod": "Fuzz", "rel": "@lot"}},
        {"route": "files/replace", "body": {"mod": "Fuzz", "rel": "@tex", "b64": _pic}},
        {"route": "files/replace", "body": {"mod": "Fuzz", "rel": "@tex", "b64": _dds}},
        {"route": "files/replace", "body": {"mod": "Fuzz", "rel": "@gpl", "text": "riftstone: gpl/1\ngroups: []\n"}},
        {"route": "files/replace", "body": {"mod": "Fuzz", "rel": "@gpl", "edit": ["mDLCNo: 0", "mDLCNo: 1"]}},
        {"route": "files/replace", "body": {"mod": "Fuzz", "rel": "@gpl", "edit": ["version: 158", "version: 158\n# edited"]}},
        {"route": "files/replace", "body": {"mod": "Fuzz", "rel": "@gpl", "text": lot_text}},
        {"route": "files/replace", "body": {"mod": "Fuzz", "rel": "@lot", "edit": ["records:", "# a note\nrecords:"]}},
        {"route": "files/replace", "body": {"mod": "Fuzz", "rel": "@lot", "text": lot_text}},
        {"route": "files/replace", "body": {"mod": "Fuzz", "rel": "@mrl", "b64": base64.b64encode(
            _mrl.build(_mrl.Mrl(0x20, 0xb46006d5, [], [], b""))).decode()}},
        {"route": "files/preset", "body": {"mod": "Fuzz", "rel": "@tex", "preset": "frost"}},
        {"route": "files/preset", "body": {"mod": "Fuzz", "rel": "@tex", "preset": "lava", "strength": 0.5}},
        {"route": "files/aside", "body": {"mod": "Fuzz", "rel": "@tex"}},
        {"route": "files/aside", "body": {"mod": "Fuzz", "rel": "@gpl"}},
        {"route": "files/aside", "body": {"mod": "Fuzz", "rel": "aside/@tex", "back": True}, "aside": 1},
        {"route": "files/aside", "body": {"mod": "Fuzz", "rel": "@tex"}, "aside": 1},
        {"route": "files/export", "q": {"mod": "Fuzz"}},
        {"route": "skins"},
        {"route": "skins/make", "body": {"family": "chimera", "mod": "Fuzz", "textures": {"e5200_face_BM": {"b64": _pic}}}},
        {"route": "skins/make", "body": {"family": "chimera", "new_mod": "Fuzz Two", "number": 2, "title": "t",
                                         "textures": {"e5200_skin_BM": {"b64": _dds}}}},
        {"route": "skins/make", "body": {"family": "chimera", "mod": "Fuzz", "number": 1}},
        {"route": "skins/export", "q": {"mod": "Fuzz", "family": "chimera", "number": "1", "as": "dds"}})
    from riftstone import gmd, itl

    gmd_raw = gmd.build(gmd.Gmd(1, "TextWeb", [gmd.Message("What is it, Arisen?", "0"), gmd.Message(""),
                                               gmd.Message("line one\r\nline two <ICON 3>", "2")], 0x3B2F0DE0))
    a = arc.Archive([arc.Entry.from_data(b"param\\status\\enemy", 0x215896C2, xraw),
                     arc.Entry.from_data(b"model\\x ", typemap.BY_EXT["tex"], b"TEX\0" + bytes(64))]).build()
    manifest = json.dumps({"schema": arcfolder.SCHEMA, "archive": "x", "source_sha256": "0" * 64, "version": 7,
                           "entries": [{"path": "a/x.tex", "name": "a\\x", "type": f"{typemap.BY_EXT['tex']:08x}",
                                        "flags": 2, "sha256": "0" * 64, "yaml": False}]}).encode()
    mod_case = json.dumps([["files/param/status/enemy.statusparam.yaml", text.decode()],
                           ["archives/rom/enemy/em0100.arc/model/x.tex", "TEX"],
                           ["files/../../escape.tex", "x"], ["files/CON.tex", "x"]]).encode()
    studio_cases = [json.dumps(c).encode() for c in (
        {"route": "state"}, {"route": "search", "q": {"q": "status", "limit": "20"}}, {"route": "types"},
        {"route": "search", "q": {"q": "", "type": "tex"}},
        {"route": "resource", "q": {"path": "param/status/enemy.statusparam"}},
        {"route": "mods/extract", "post": 1, "body": {"mod": "Fuzz", "path": "model/x.tex"}},
        {"route": "file", "q": {"mod": "Fuzz", "rel": "files/README.txt"}},
        {"route": "file", "post": 1, "body": {"mod": "Fuzz", "rel": "files/README.txt", "text": "hello"}},
        {"route": "validate", "post": 1, "body": {"text": text.decode()}},
        {"route": "crash", "q": {"name": "crash-1.txt"}}, {"route": "mods/new", "post": 1, "body": {"name": "B"}},
        {"route": "crash", "q": {"name": "fatal-20260101-000000.txt"}}, {"route": "live"},
        {"route": "safe-mode", "post": 1, "body": {}},
        {"route": "lot", "post": 1, "body": {"text": lot_text, "op": "copy", "number": 0, "at": [1, 2, 3]}},
        {"route": "lot", "post": 1, "body": {"text": lot_text, "op": "remove", "number": 1}},
        {"route": "world"}, {"route": "world/stage", "q": {"n": "st424"}}, {"route": "world/enemy", "q": {"q": "goblin"}},
        {"route": "encounter", "post": 1, "body": {"mod": "Fuzz", "stage": 424, "enemy": "goblin", "count": 10,
                                                   "at": [0, 0, 0], "dry_run": True}},
        {"route": "encounter", "post": 1, "body": {"mod": "Fuzz", "stage": 424, "enemy": "chimera", "count": 2,
                                                   "at": "group:4", "hours": "20,3", "story": "post", "skin": "1,2",
                                                   "like": 4, "dry_run": True}})]
    ddo_xfs, ddo_yaml = _ddo_xfs_seeds()
    import test_audio
    import test_sound
    from riftstone import sound as _sound
    return {"audio": [test_audio.synthetic_ogg()], "audio_bank": [test_audio.synthetic_bank()],
            "audio_cli": [test_audio.synthetic_ogg()],
            "audio_cue": [struct.pack("<HH", 500, 25) + _sound.build(test_sound.srq_ddda()),
                          struct.pack("<HH", 500, 25) + _sound.build(test_sound.srd())],
            "registration": [b'{}', b'{"items":[{"key":"a","template":1,"name":"New"}]}', b'{"items":[{"key":"a","template":1,"name":"New","id":4,"weight":0.2}]}'],
            "vfs": [b'{}', b'{"a":1,"b":{"c":2}}', b'{"x":-0.0,"y":true}', b'{"array":[1,2]}'],
            "arc": [a], "xfs": [xraw, ddo_xfs], "params": [text, ddo_yaml], "yaml": [text],
            "xfs_ddo_text": [b"\x83\x5c\x83\x6c\x83\x8b\0C:\\\x95\x5c\\\x94\x5c\x8d\x5c\0\x87\x90\0\x81\0plain\\text",
                             "待機 ソネル \\ \u2015".encode("cp932"), "a \u2014 b ¥ \\".encode("utf-8")],
            "fsmap": _fsmap_seeds(),
            "pack": [manifest], "mod": [mod_case], "cli": [b"\x00" + a, b"\x01" + xraw, b"\x02" + text, b"\x05" + manifest],
            "studio": studio_cases,
            "ocl": [struct.pack("<5I", 0x20121225, 0, 0, 0, 0)],
            "ocl_yaml": [b"riftstone: ocl/1\nmagic_header: 25121220000000000000000000000000000000\nform: primitive\nprimitives: []\ntail: \"\"\n",
                         b"riftstone: ocl/1\nmagic_header: 2512122000000000000000000000000000000000\nbody: \"\"\nprimitives: []\n",
                         b"riftstone: ocl/2\nmVersion: 0x20121225\nmResourceID: 0\ngroups: []\nattacks:\n  - mNo: 0x7fffffff\nseqIndex: []\n"],
            "gmd": [gmd_raw], "gmd_yaml": [gmd.to_yaml(gmd.parse(gmd_raw), "fuzz/seed_eng").encode()],
            "fsm": [xfs.build(test_fsm.sample())],
            "fsmcheck": [xfs.build(test_fsm.sample()), xfs.build(test_fsmcheck.machine([test_fsmcheck.state("a", 0, [test_fsmcheck.link(42, 90), test_fsmcheck.link(1, 91)]), test_fsmcheck.state("b", 1, entry=90, setting=8)]))],
            "itl": [test_items.item_list()], "itl_yaml": [itl.to_yaml(itl.parse(test_items.item_list())).encode()],
            "author": [json.dumps(c).encode() for c in (
                {"op": "stats", "args": ["1"]}, {"op": "set", "args": ["Greenwarish", "mAttack", "2047"]},
                {"op": "set", "args": ["1", "mFireDefenseRate", "-128"]}, {"op": "set", "args": ["1", "mHp", "1.5"]},
                {"op": "set", "args": ["1", "attack", "5"]},
                {"op": "find", "args": ["thing", 1, 5]}, {"op": "add", "args": ["id/npc_wind/stage/st100_eng.gmd", "Hi."]},
                {"op": "new", "args": ["Rift Tonic", "greenwarish", "Heals.", None, 500, None], "weight": 0.25},
                {"op": "shop", "args": ["n007", 4, 3, 5]}, {"op": "item", "args": ["Thing 2"]},
                {"op": "add", "args": ["id/npc_wind/stage/st100_fre", "Salut.", None], "label": "x", "all": False},
                {"op": "recipe", "args": [1, 2, 3, 2]}, {"op": "drop", "args": [5, 7, 10]},
                {"op": "drop", "args": [5, 0, 1], "reward": True}, {"op": "sets", "args": [1227]})],
            "tables": [test_tables.SETS, test_tables.MIX],
            "tables_yaml": [__import__("riftstone.tables", fromlist=["x"]).to_yaml(
                __import__("riftstone.tables", fromlist=["x"]).parse(d)).encode() for d in (test_tables.SETS, test_tables.MIX)],
            "flat": [bytes([_fexts.index("ajp")]) + _ajp, bytes([_fexts.index("fed")]) + _fed],
            "flat_yaml": [_flat.to_yaml(_flat.parse(_ajp, "ajp")).encode(), _flat.to_yaml(_flat.parse(_fed, "fed")).encode()],
            "gpl": [_gplm.build(test_gpl.SAMPLE)], "gpl_yaml": [_gplm.to_yaml(test_gpl.SAMPLE).encode()],
            "lot": [test_lot.SAMPLE], "lot_yaml": [__import__("riftstone.lot", fromlist=["x"]).to_yaml(
                __import__("riftstone.lot", fromlist=["x"]).parse(test_lot.SAMPLE)).encode()],
            "tex": [_tex_seed], "tex_dds": [_tex.to_dds(_tex.parse(_tex_seed))],
            "mrl": [_mrl.build(_mrl.Mrl(0x20, 0xb46006d5, [], [], b""))],
            "prp": [_prp_seed, _prp_glossed], "ean": [_ean_seed],
            "lmt": [__import__("riftstone.lmt", fromlist=["x"]).build(__import__("test_lmt").sample(v, s, fl))
                    for v in (66, 67) for s in (True, False) for fl in (True, False)],
            "port_lmt": [bytes([v == 67]) + __import__("riftstone.lmt", fromlist=["x"]).build(__import__("test_lmt").sample(v))
                         for v in (66, 67)],
            "ocl_ddo": [__import__("riftstone.ocl_ddo", fromlist=["x"]).build(__import__("riftstone.ocl_ddo", fromlist=["x"]).OclDdo()),
                        __import__("riftstone.ocl_ddo", fromlist=["x"]).build(__import__("test_ocl_ddo").sample())],
            "ocl_ddo_yaml": [__import__("riftstone.ocl_ddo", fromlist=["x"]).to_yaml(__import__("test_ocl_ddo").sample(), "fuzz").encode()],
            "compat": [bytes([22]) + __import__("riftstone.ocl_ddo", fromlist=["x"]).build(__import__("test_ocl_ddo").sample())],
            "loose": [json.dumps([["compat/job09/a.lmt", "x"], ["compat/a.skills", "skill a\nend\n"],
                                  ["effect/epv/pl/p.epv", "e"]]).encode()],
            "gpl_ddo": _ddo_gpl_seeds(), "gpl_ddo_yaml": [__import__("riftstone.gpl_ddo", fromlist=["x"]).to_yaml(
                __import__("riftstone.gpl_ddo", fromlist=["x"]).parse(_ddo_gpl_seeds()[0]), "fuzz").encode()],
            "save": [__import__("riftstone.saves", fromlist=["x"]).pack(
                b'<?xml version="1.0" encoding="utf-8"?>\n<class name="dd_savedata"><u32 name="n" value="%d"/></class>\n'
                % n) for n in (1, 2)],
            "save_knowledge": _knowledge_seeds(),
            "lot_ddo": _ddo_lot_seeds(), "lot_ddo_yaml": [_lot_ddo_yaml()],
            **_weather_camera_seeds(),
            "ddo_tables": [struct.pack("<II", 1, 1) + struct.pack("<4I", 1, 0, 1, 0x010100),
                           b"slt\0" + struct.pack("<II", 0x22, 1) + struct.pack("<IIBII", 200, 1, 0, 1, 0x100)],
            "ddo_names": [b"wolves\ncyclopes\nst0200\nstage 7\n0x015001\n5\nwhite dragon\ndire wolf",
                          "Goblin Fighters\nstymphalides\nfoot-biters\nem010100\n²\n999\n？\n".encode()],
            "names": [b"charparam/em/em0100_cmn.prp", b"scr/st424/etc/st424_00m00n_e03.lot.yaml",
                      b"id/npc_wind/stage/st424_eng.gmd", b"model/em/e03/e0300/e0300.mod", b"scr\\st424\\etc\\st424_e_dlc01.gpl",
                      b"event/st330/ev10/FSM/Em0100_cons_00.fsm", b"goblins", b"gran soren", b"stage 424", b"0100", b"ox"],
            "mod_names": [b"Harder Goblins", b"hydrastorm  mk", b"HydraStormMK", b"b", b"c", b"..", b"...", b"../outside",
                          b"mods\\x", b"CON", b"New Mod", b"not a mod", b" padded "],
            "port": [bytes([0]) + _tex_seed, bytes([4]) + _ddo_tex_seed(), bytes([1]) + gmd_raw,
                     bytes([3]) + _mrl.build(_mrl.Mrl(0x20, 0xb46006d5, [], [], b""))],
            "dye": __import__("targets").dye_seeds(), "dye_tables": __import__("targets").dye_table_seeds(),
            "arcs": [b"ARCS" + struct.pack("<HHII", 7, 1, 0x1234ABCD, typemap.BY_EXT["tex"]), b"ARCS\x07\x00\x00\x00"],
            "encounter": [json.dumps(c).encode() for c in (
                {"stage": "424", "enemy": "goblin", "count": 100, "at": "group:0"},
                {"stage": "st424", "enemy": "em0101", "count": 4, "at": "100,-350,-8800", "story": "post"},
                {"stage": 424, "enemy": "hob", "count": 12, "at": "0,0,0", "points": 3, "spread": 40.5, "group": 7},
                {"op": "query", "stage": "424", "enemy": "goblins", "group": 3, "type": "e"},
                {"op": "query", "stage": "st424", "enemy": "0100", "at": "1,2,3"},
                {"stage": "424", "enemy": "em5200", "count": 1, "at": "0,-350,40", "like": 3, "skin": 1},
                {"stage": "424", "enemy": "chimera", "count": 3, "at": "group:4", "skin": 99, "group": 5},
                {"stage": "424", "enemy": "goblin", "count": 2, "at": "0,0,0", "like": 0},
                # next to, and like, the stand-in's DLC-list group 1 (as Everfall's groups 20-22 are)
                {"stage": "424", "enemy": "goblin", "count": 3, "at": "300,-350,-8800"},
                {"stage": "424", "enemy": "goblin", "count": 3, "at": "0,-350,-8800", "like": 1})],
            # mods (byte 0: 2 + n % 3), then (mod, op, arg) triples; ops: 0 add group arg % 9 with a layout,
            # 1 count, 2 remove, 3 flip an mSetBit bit, 4 share a wander area with arg % 9, 5 story start
            "gpl_merge": [bytes(s) for s in (
                [0, 0, 0, 2, 1, 0, 11],                   # two mods add group 2 differently: one moves
                [1, 0, 0, 2, 1, 0, 2, 2, 0, 11],          # three: two alike (one group), one moves
                [0, 0, 0, 2, 1, 0, 11, 1, 4, 2],          # a group of the second shares the new one's area
                [0, 0, 0, 2, 1, 0, 11, 1, 3, 5],          # the second flips an mSetBit bit
                [0, 0, 2, 0, 1, 1, 40, 1, 0, 11, 0, 5, 7],  # a removal, a count, an addition, a story start
                [2, 0, 0, 2, 1, 0, 2, 2, 0, 5, 3, 0, 14, 2, 4, 5])],
            # mods (byte 0: 2 + n % 3), then (mod, op, arg) triples on the goblin layout; ops: 0 copy a record
            # (next id), 1 copy under id arg % 8, 2 move, 3 order, 4 remove
            "lot_merge": [bytes(s) for s in (
                [0, 0, 0, 1, 1, 0, 2],                   # a copy in each: both take id 3, one moves
                [1, 0, 0, 1, 1, 0, 1, 2, 0, 5],          # three: two alike, one moves
                [0, 0, 2, 7, 1, 3, 7, 0, 4, 0, 1, 2, 9],  # one record moved and ordered, another removed
                [0, 0, 1, 11, 1, 1, 19, 1, 1, 27])],     # the same id chosen by both, differently
            # byte 0: bit 0 shops (else the spawn table), bits 1-2 mods - 2, bit 7 a hostile last copy (the tail
            # after 90 bytes); then (mod, op, arg) triples
            "server_merge": [bytes(s) for s in (
                [0, 0, 0, 3, 1, 0, 200, 0, 3, 1, 1, 3, 2],     # rows and drop items from two mods
                [0, 0, 2, 4, 1, 2, 4],                          # one row's level two ways
                [2, 0, 0, 3, 1, 0, 3, 2, 1, 1, 0, 5, 1],        # three mods: one place, a removal, a new table
                [3, 0, 0, 1, 1, 0, 2, 2, 2, 5, 0, 3, 9],        # shops: goods added, a price, a wallet
                [1, 0, 1, 0, 1, 2, 2, 1, 4, 0, 0, 5, 1],        # shops: a removal each, a new shop, a gone shop
            )] + [bytes([0x80] + [0] * 90) + b'{"schemas": {"enemies": []}, "enemies": [[1]]}',
                  bytes([0x81] + [0] * 90) + b'[{"ShopId": 1, "Data": {"GoodsParamList": [{"ItemId": 1}]}}]'],
            "encounter_plan": [json.dumps({"format": "riftstone-encounters/1", "game": "ddda", "encounters": es}).encode()
                               for es in (
                [{"stage": 330, "enemy": "em0100", "total": 100, "at": "group:35", "points": 10, "spread": None,
                  "hours": [20, 3], "story": "post", "group": None, "like": None, "skin": None, "rule": 0, "line": 13}],
                [{"stage": 706, "enemy": "em0100", "total": 12, "at": [1200.5, -1340, -3700], "points": 6,
                  "spread": 300.0, "hours": [6, 17], "story": None, "group": 7, "like": 3, "skin": 2},
                 {"stage": 424, "enemy": "goblin", "total": 1, "at": "group:0"}],
                [{"stage": 424, "enemy": "em5200", "total": 3, "at": [0, 0, 0], "hours": [0, 23], "story": "any"}],
                # the stand-in game's stage (fuzz/targets.py applies these as a dry run and for real)
                [{"stage": 424, "enemy": "goblin", "total": 20, "at": "group:0"},
                 {"stage": 424, "enemy": "em0101", "total": 4, "at": [0, 0, 0], "hours": [4, 19], "story": "post"}],
                [{"stage": 424, "enemy": "em0100", "total": 3, "at": "group:0", "group": 7},
                 {"stage": 424, "enemy": "hob", "total": 5, "at": "group:3", "group": 7, "rule": 1, "line": 4}],
                [{"stage": 424, "enemy": "em0100", "total": 2, "at": "group:0", "group": 2},
                 {"stage": 424, "enemy": "em0100", "total": 12, "at": [300, -350, -8800], "points": 3, "spread": 40.5},
                 {"stage": 424, "enemy": "em5200", "total": 1, "at": [0, -350, 40], "like": 4, "skin": 1},
                 {"stage": 424, "enemy": "dragon", "total": 1, "at": "group:0"}])],
            "waves": [json.dumps(c).encode() for c in (
                {"stage": 424, "after": 0, "waves": [["goblin", 3], ["em0101", 2]]},
                {"stage": "st424", "after": 4, "waves": [["em5200", 1]], "like": 1, "at": "0,-350,40", "spread": 90},
                {"stage": 424, "after": 0, "waves": [["em0100", 31]] * 3, "at": "group:4"},
                {"stage": 424, "after": 3, "waves": [["goblin", 2]]},
                {"stage": 424, "after": 1, "waves": [["hob", 2]], "like": 0},
                {"op": "parse", "text": "skeleton   mage : 4"}, {"op": "flags", "stage": "424"},
                {"op": "machine", "stage": 424, "links": [[0, [0, 1, 2], 121], [5, [3, 0], 120], [6, [31], None]]},
                {"op": "machine", "stage": 100, "links": [[294, [0], 255], [1, [1], None]]},
                {"op": "machine", "stage": 803, "links": [[0, [0], 121], [2, [0], None]]},
                {"stage": 800, "after": 0, "waves": [["goblin", 2]]})],
            **_level_seeds(),
            "skintex": [_skin_tex, _skin_tex[:4] + struct.pack("<I", 0x2000209D) + _skin_tex[8:],
                        _tex.to_dds(_tex.parse(_skin_tex))],
            "monster": _monster_seeds(),
            "terrain": [helpers.cell_model(), helpers.cell_model(version=0xD2, envelopes=0),
                        helpers.cell_model(meshes=[(0xD8297028, 24, 0, 5), (0xD8297028, 24, 1, 3)], groups=3),
                        helpers.cell_model(points=[(-4000.0, 1.0, 14000.0), (0.0, 0.0, 0.0)]),
                        helpers.cell_collision(), helpers.cell_collision(parts=2, two_level=True),
                        helpers.cell_collision(tree_kind=2)],
            "png": _pngs, "studio_files": [json.dumps(c).encode() for c in _files_cases],
            "package": [json.dumps(c).encode() for c in (
                {"files": [["archives/rom/enemy/em5200.arc/model/em/e52/e5200/s01/e5200_skin_BM.tex", "TEX\u0000new"]],
                 "plugins": ["enemy_cap.asi"], "out": "pack.zip", "name": "Fuzz pack"},
                {"files": [["files/model/em/e52/e5200/e5200_skin_BM.tex", "TEX\u0000replaced"]],
                 "plugins": ["a.asi", "b.dll"], "out": "b.zip"},
                {"files": [], "out": "empty.zip"},
                {"files": [["archives/rom/enemy/em5200.arc/x/new.tex", "x"]], "plugins": ["dinput8.dll"], "out": "c.zip"},
                {"files": [["archives/rom/enemy/em5200.arc/x/new.tex", "x"]], "out": "d.rar"})],
            "live_block": [test_runtime.live_block(), test_runtime.live_block(frames=5, flags=63, stage=-1,
                                                                               resource_slots=0),
                           test_runtime.live_block(flags=1 | 4 | 64 | 128 | 256, d3d_managed=1200 << 20,
                                                   d3d_managed_peak=1400 << 20, d3d_objects=5000, d3d_provider=1,
                                                   pressure=1, pressure_episodes=2, va_used_peak=(4 << 30) - (300 << 20),
                                                   d3d_path="C:\\WINDOWS\\SYSTEM32\\d3d9.dll")],
            "pe": [helpers.pe_file(), helpers.pe_file(machine=0x8664, plus=True), helpers.pe_file(dll=False, laa=True),
                   helpers.pe_file(exports=()), helpers.pe_file(exports=("Direct3DCreate9", "Direct3DCreate9Ex", "x"))],
            "d3d9_source": _d3d9_sources(),
            "report": [test_runtime.CRASH.encode(), test_runtime.FATAL.encode(), test_runtime.HANG.encode()],
            "session": [test_runtime.STATE_CLOSED.encode(), test_runtime.STATE_RUNNING.encode(),
                        test_runtime.STATE_KILLED.encode(),
                        (test_runtime.STATE_CLOSING + "\n#note\nkind=crash\nuptime_ms=2590000\n"
                         "report=C:\\g\\riftstone\\logs\\crash-20260925-214511.txt\n").encode()],
            "package_install": __import__("targets").package_seeds(),
            "package_plugins": [json.dumps(c).encode() for c in (
                {"plugins": ["enemy_cap.asi"], "inis": {"enemy_cap.asi": "; enemy_cap -- more enemies\n[x]\n"},
                 "out": "Riftstone-Player.zip", "name": "Riftstone plugins"},
                {"plugins": ["twin.asi", "twin.dll"], "inis": {"twin.asi": "[a]\n", "twin.dll": "[b]\n"}, "out": "t.zip"},
                {"plugins": ["a.asi", "a.asi"], "out": "dup.zip"},
                {"plugins": [], "out": "none.zip"},
                {"plugins": ["dinput8.dll"], "out": "loader.zip"},
                {"plugins": ["x.asi"], "inis": {"x.asi": "\u00a9 CAPCOM"}, "out": "c.zip", "name": "t\u0000itle"},
                {"plugins": ["y.asi"], "out": "y.rar"},
                {"plugins": ["enemy_cap.asi"], "ninput": "PE:XInputGetState,XInputSetState", "out": "n.zip"},
                {"plugins": ["x.asi"], "ninput": "MZ but not a DLL", "out": "bad.zip"},
                {"plugins": ["x.asi"], "ninput": "PE:Direct3DCreate9", "out": "d3d.zip"})],
            "sources": __import__("targets").sources_seeds(),
            "delta": __import__("targets").delta_seeds(),
            "playtest": [(test_playtest.LOADER_LOG + "\n#cap\n" + test_playtest.CAP_LOG + "\n#sprint\n"
                          + test_playtest.SPRINT_LOG + "\n#state\n" + test_playtest.STATE).encode(),
                         test_playtest.LOADER_LOG.encode(), b"#cap\n\n#sprint\n\n#state\n",
                         (test_playtest.LOADER_LOG + "\n#cap\n\n#sprint\n\n#state\n\n#config\n[GRAPHICS]\nHDR=FLOAT\n"
                          "\n#graphics\n" + json.dumps({"schema": "riftstone-graphics-state/1", "profile": "modern_remaster",
                                                        "title": "ENB over DXVK", "applied": "2026-09-27T18:40:00",
                                                        "files": {}})
                          + "\n#enblocal\n[PROXY]\nEnableProxyLibrary=true\nProxyLibrary=riftstone\\dxvk\\d3d9.dll\n"
                          + "\n#portcrystals\nportcrystals: 15 Portcrystals placed at once (the game allows 10); 46 sites, "
                          "4 runs and 3 hooks patched (game)\n20:01:02  saved: 1 crystal(s) placed past the save's ten "
                          "(slots 11-15)\n20:05:00  loaded: 1 crystal(s) past the save's ten came back from the sidecar\n"
                          ).encode()],
            "minidump": [__import__("test_minidump").dump([__import__("test_minidump").RENDER,
                                                           __import__("test_minidump").WORKER,
                                                           __import__("test_minidump").CAP])],
            "portcrystals": [__import__("riftstone.portcrystals", fromlist=["x"]).build(
                [__import__("test_portcrystals").record(n, fp=k) for k, n in enumerate((5, 0, 22))]),
                __import__("riftstone.portcrystals", fromlist=["x"]).build([])],
            "portcrystals_save": [__import__("test_portcrystals").save_xml(
                [(100, -10661.911133, 33612.117188, 157458.890625)] * 10)],
            "portcrystals_names": [b"; how many\r\n[portcrystals]\r\nslots = 15\r\n[names]\r\n"
                                   b"44C06000,45747800,44C72000 = 277\r\n0,1,2 = 5\r\n; note\r\n[other]\r\nx=1\r\n",
                                   b"[portcrystals]\nslots = 12\n"],
            "graphics_profile": [json.dumps(c).encode() for c in (
                {"schema": "riftstone-graphics/1", "title": "Modern Remaster", "notes": ["ENB over DXVK"],
                 "files": {"d3d9.dll": {"sha256": "0" * 64, "from": "enb.zip: WrapperVersion/d3d9.dll"},
                           "enblocal.ini": {"sha256": "1" * 64}, "enbseries/Shader Functions/ENB PP.fxh": {"sha256": "2" * 64}},
                 "ini": {"enblocal.ini": {"PROXY": {"EnableProxyLibrary": "true", "ProxyLibrary": "riftstone\\dxvk\\d3d9.dll"}}},
                 "config": {"GRAPHICS": {"HDR": "FLOAT", "AltAntiAlias": "NONE"}, "DISPLAY": {"VSYNC": "OFF"}},
                 "loader": {"d3d9": {"chain": ""}}},
                {"schema": "riftstone-graphics/1", "files": {"../d3d9.dll": {"sha256": "0" * 64}}},
                {"schema": "riftstone-graphics/1", "files": {}, "config": {"GRAPHICS": {"HDR": "HIGH"}}})],
            "graphics_ini": [b"[PROXY]\r\nEnableProxyLibrary=false\r\nProxyLibrary=\r\n#set\nPROXY\0ProxyLibrary\0riftstone\\dxvk\\d3d9.dll",
                             b"[GRAPHICS]\nHDR=DEFAULT\n[DISPLAY]\nVSYNC = ON\n#set\ndisplay\0vsync\0OFF",
                             "﻿[A]\r\nK=1\r\n".encode("utf-16-le") + b"\n#set\nA\0K\x002"],
            "plugin_ini": [b"; enemy_cap -- more enemies\n[enemy_cap]\nslots = 30\nrecord = 1\n#set\nenemy_cap\0enemy_cap\0slots\x0048",
                           b"[lod]\n; on\nEnabled = 1\nPopPixels = 24\nScale = auto\n#set\nlod_tuner\0lod\0Scale\0auto",
                           b"[fps]\nmax_fps = 165\n[overlay]\nkey = F10\n#set\nloader\0overlay\0key\0f7",
                           b"[draw]\n; on\nEnabled = 1\nObjects = 3\nGrass = 3\nHumanEnemies = 0\n#set\ndraw_distance\0draw\0Objects\x002.5",
                           b"[draw]\nGrass = 3\nHumanEnemies = 0\n#set\ndraw_distance\0draw\0HumanEnemies\x00250",
                           b"[a]\nk=1\n[a]\nk=2\n#set\nx\0a\0k\0v",
                           # an ini as Windows reads it (runtime.ini_text): a value in the code page, a UTF-8
                           # mark hiding the first section, a CR CR, a form feed inside a line, a UTF-16 file
                           b"[backup]\r\nFolder = auto\r\n\n#set\nsave_backup\0backup\0Folder\0D:\\Spielst\xc3\xa4nde",
                           b"\xef\xbb\xbf; mark\r\n[backup]\r\nKeep = 20\r\n\n#set\nsave_backup\0backup\0Keep\x0025",
                           b"[backup]\r\r\nKeep = 20\r\n\n#set\nsave_backup\0backup\0Keep\x0025",
                           b"[backup]\r\nKeep = 20\x0cjunk\nFolder = auto\x0b\r\n\n#set\nsave_backup\0backup\0Keep\x0025",
                           b"\xff\xfe" + "; save_backup\r\n[backup]\r\nFolder = D:\\Spielst\u00e4nde \u65e5\u672c\r\nKeep = 20\r\n"
                           .encode("utf-16-le") + b"\n#set\nsave_backup\0backup\0Folder\0D:\\\xe6\x97\xa5\xe6\x9c\xac"]}


def _fsmap_seeds(game=None) -> list[bytes]:
    """The fsmap target's inputs (targets.fsmap_seed: a two-byte type selector, then a name): a synthetic one,
    and with a game the names and types of the first entries of its 200 smallest archives."""
    import targets
    from riftstone import corpus, typemap

    out = [targets.fsmap_seed(sorted(typemap.BY_ID)[5], b"model\\em\\e01 ")]
    if game is not None:
        names = []
        for p in sorted(game.archives(), key=lambda p: p.stat().st_size)[:200]:
            names += [targets.fsmap_seed(t, n) for n, t, *_ in corpus.directory(p)[:5] if t in typemap.BY_ID]
        out += names[:400]
    return out


def _monster_seeds() -> list[bytes]:
    """The monster target's four inputs (first byte): rigged models, motion lists, what people type, two rigs."""
    import monster_fixture as mf
    import test_lmt
    from riftstone import lmt as _lmt

    def rig(joints, fmt_byte):
        out = bytes([len(joints)])
        ids = [j for j, _, _ in joints]
        for j, parent, (x, y, z) in joints:
            out += bytes([j, ids.index(parent) if parent is not None else 255,
                          int(x) + 128 & 0xFF, int(y) + 128 & 0xFF, int(z * 4) + 128 & 0xFF])
        return out + bytes([fmt_byte])

    return [bytes([0]) + mf.model(mf.WOLF, [1, 2, 3, 4, 5]),
            bytes([0]) + mf.model(mf.CHIMERA + mf.GOAT, [1, 2, 10, 11], 0xD2, fmts=(mf.FMT, mf.DDO_ONLY_FMT)),
            bytes([0]) + mf.model([], []),
            bytes([1]) + mf.motions(66, [1, 2, 9]), bytes([1]) + _lmt.build(test_lmt.sample(67)),
            bytes([2]) + "wolf\nEM010203\n0x015202\nwhite chimera\nundead\nstymphalides\nem2000\ne5503\n".encode(),
            bytes([3]) + rig(mf.WOLF, 1) + rig(mf.WOLF, 1) + b"\0",
            bytes([3]) + rig(mf.WOLF, 0) + rig(mf.SKELETON, 1) + b"\1"]


def _weather_camera_seeds() -> dict[str, list[bytes]]:
    """Every weather/fog/sky kind and both camera-list versions, from the tests' samples; the weather
    target's first byte picks the resource type."""
    import test_camera
    import test_weather
    from riftstone import camera, weather

    ws = test_weather.samples()
    cams = (test_camera.ddda_sample(), test_camera.ddo_sample())
    import test_sound
    from riftstone import sound

    snd = [f() for f in test_sound.SAMPLES]
    import test_effect
    from riftstone import effect, effect_e2d, effect_efl

    epvs = [test_effect.epv_sample(v) for v in effect.VERSIONS]
    efl = test_effect.efl_sample()
    return {"weather": [bytes([weather.EXTS.index(weather.KINDS[k].ext)]) + weather.build(w) for k, w in ws.items()],
            "weather_yaml": [weather.to_yaml(w).encode() for w in ws.values()],
            "lcm": [camera.build(c) for c in cams], "lcm_yaml": [camera.to_yaml(c).encode() for c in cams],
            "sound": [sound.build(s) for s in snd], "sound_yaml": [sound.to_yaml(s).encode() for s in snd],
            "epv": [effect.build(e) for e in epvs], "epv_yaml": [effect.to_yaml(e).encode() for e in epvs],
            "efl": [effect_efl.build(efl)], "efl_yaml": [effect_efl.to_yaml(efl).encode()],
            "e2d": [effect_e2d.build(test_effect.e2d_sample())],
            "efs": [b"EFS\0" + struct.pack("<I", 0x20080912) + bytes(24)],   # a header; real strips join below
            **_ddo_params_seeds(), "rebake": _rebake_seeds(), **_face_msg_schedule_seeds()}


def _face_msg_schedule_seeds() -> dict[str, list[bytes]]:
    """Facial animation, DDO message set, both games' schedulers and zones (the tests' samples); DDDA's
    message sets and serial lists join from the game."""
    import test_facial
    import test_msgset
    import test_schedule
    from riftstone import facial, msgset, schedule

    fca = test_facial.sample()
    msg = test_msgset.ddo_sample()
    sch = [test_schedule.sdl_ddda(), test_schedule.sdl_ddo(), test_schedule.zon_type1(), test_schedule.zon_type2(),
           test_schedule.zon_type2(ddo=True)]
    return {"fca": [facial.build(fca)], "fca_yaml": [facial.to_yaml(fca).encode()],
            "msgset": [msgset.build(msg)], "msgset_yaml": [msgset.to_yaml(msg).encode()],
            "schedule": [schedule.build(s) for s in sch], "schedule_yaml": [schedule.to_yaml(s).encode() for s in sch]}


def _real_face_msg_schedule(game, seeds: dict) -> None:
    """The 12 smallest real files of each type (and the YAML of a few)."""
    from riftstone import corpus, facial, msgset, schedule, typemap

    for target, mod, exts in (("fca", facial, ("fca",)), ("msgset", msgset, ("mss", "msl")),
                              ("schedule", schedule, ("sdl", "zon"))):
        for ext in exts:
            files = sorted((r.data for r in corpus.resources(game, [typemap.BY_EXT[ext]]) if len(r.data) < 65536),
                           key=len)[:12]
            seeds[target] = seeds.get(target, []) + files
            seeds[target + "_yaml"] = seeds.get(target + "_yaml", []) + [mod.to_yaml(mod.parse(d)).encode()
                                                                         for d in files[:3]]


def _rebake_seeds() -> list[bytes]:
    """The retarget test's chain motions (DDO v67), alone and with a shared track list."""
    import test_retarget

    t = test_retarget.RebakeTest().tracks()
    return [test_retarget.motion_list(t), test_retarget.motion_list(t, shared=True),
            test_retarget.motion_list(t[:2], frames=5)]


def _ddo_params_seeds() -> dict[str, list[bytes]]:
    """Every DDO enemy/stage parameter kind (and a flying enemy), from the tests' samples; the binary
    target's first byte picks the kind."""
    import test_ddo_params
    from riftstone import ddo_params

    kinds = list(ddo_params.KINDS)
    ms = [test_ddo_params.sample(k) for k in kinds] + [test_ddo_params.sample("cpe", fly=True)]
    return {"ddo_params": [bytes([kinds.index(m.kind)]) + ddo_params.build(m) for m in ms],
            "ddo_params_yaml": [ddo_params.to_yaml(m).encode() for m in ms], **_solo_seeds(), **_ddo_access_seeds()}


def _ddo_access_seeds() -> dict[str, list[bytes]]:
    """Solo-access: mission_params JSON objects across the cases -- gated (min 4), already solo, the key
    omitted (defaults to 4), an under-manned raid, and a non-object -- for ddo_access's audit and fix."""
    import json as _json

    cases = [
        {"group": 1, "minimum_members": 4, "maximum_members": 4, "max_pawns": 3, "phase_groups": []},
        {"group": 1, "minimum_members": 1, "maximum_members": 4, "max_pawns": 3, "phase_groups": []},
        {"group": 1, "maximum_members": 4, "max_pawns": 3, "phase_groups": []},
        {"group": 1, "minimum_members": 1, "maximum_members": 8, "max_pawns": 3, "phase_groups": []},
        {"minimum_members": 99, "maximum_members": 20, "max_pawns": 0, "solo_only": True},
    ]
    return {"ddo_access": [_json.dumps(c).encode() for c in cases] + [b"[]", b"7", b"{}"]}


def _solo_seeds() -> dict[str, list[bytes]]:
    """Solo Balance: named-param tables under every tier choice (ndp's first byte), settings scripts with each
    change, Jackson JSON, --set values and script tiers (solo's first byte: mode in the low 2 bits, the change
    above); plus a slice of the game's own table and the server's own templates when both are here."""
    import test_ddo_params
    import test_ddo_solo
    from riftstone import ddo, ddo_params

    small = ddo_params.build(test_ddo_solo.small_table())
    ndp = [bytes([s]) + small for s in (0, 4, 8, 12, 5, 7, 13)] + \
        [bytes([4]) + ddo_params.build(test_ddo_params.sample("ndp"))]
    gs, pm = test_ddo_solo.GS_TEMPLATE.encode(), test_ddo_solo.PM_TEMPLATE.encode()
    solo = [bytes([c << 2]) + t for c, t in ((0, gs), (1, gs), (2, pm), (3, pm), (4, gs), (5, gs), (6, gs + pm))]
    solo += [bytes([1]) + ddo.dumps_style(test_ddo_solo.server_doc(test_ddo_solo.small_table()), ("jackson", "")),
             bytes([1]) + b'{"a": [1, {"b": []}], "c": {}, "d": "\\u0001x"}',
             bytes([2]) + b"1.5", bytes([2]) + b"true", bytes([2]) + b"0.000001",
             bytes([3]) + b"field,boss,exm;4000,8000,12000;0.85;1;0.55;0.85;0.4;0.8",
             bytes([7]) + b"boss;;;;0.6;17/20;;", bytes([3]) + b"exm;1,2,3;;;;;5;0.05"]
    if not os.environ.get("RIFTSTONE_SKIP_GAME"):
        try:
            from riftstone import arc, ddo_solo, typemap
            from riftstone.game import find_game

            game = find_game("ddo")
            e = arc.Archive.read(game.vanilla_arc("rom/game_common")).find(ddo_solo.NDP_NAME, typemap.BY_EXT["ndp"])
            real = ddo_params.parse(e.data(), "ndp")
            part = ddo_params.DdoParams("ndp", {"mpArray": real.data["mpArray"][:120]})
            ndp += [bytes([4]) + ddo_params.build(part), bytes([9]) + ddo_params.build(part)]
            assets = ddo.need_assets(game)
            for i, name in enumerate(("GameServerSettings", "PointModifierSettings")):
                f = assets / "scripts" / "settings" / "templates" / f"{name}.csx"
                if f.is_file():
                    solo += [bytes([c << 2]) + f.read_bytes() for c in ((0, 4) if i == 0 else (2, 3))]
        except Exception:  # noqa: BLE001 - the game's seeds are a bonus
            pass
    return {"ndp": ndp, "solo": solo}


def _real_weather_camera(game, seeds: dict) -> None:
    """A game's own weather/fog/sky files and small camera lists (both games)."""
    from riftstone import corpus, typemap, weather

    exts = [e for e in weather.EXTS if e in typemap.BY_EXT]
    for ext in exts:
        files = sorted((r.data for r in corpus.resources(game, [typemap.BY_EXT[ext]]) if len(r.data) < 32768), key=len)
        seeds["weather"] = seeds.get("weather", []) + [bytes([weather.EXTS.index(ext)]) + d
                                                       for d in files[::max(1, len(files) // 6)][:6]]
    cams = sorted((r.data for r in corpus.resources(game, [typemap.BY_EXT["lcm"]]) if len(r.data) < 65536), key=len)
    seeds["lcm"] = seeds.get("lcm", []) + cams[::max(1, len(cams) // 12)][:12]
    from riftstone import sound
    snd_types = [typemap.BY_EXT[e] for e in ("srq", "stq", "srd", "smx", "spl", "sbkr", "sar") if e in typemap.BY_EXT]
    snds = sorted((r.data for r in corpus.resources(game, snd_types) if len(r.data) < 16384), key=len)
    snds = snds[::max(1, len(snds) // 60)][:60]
    seeds["sound"] = seeds.get("sound", []) + snds
    seeds["sound_yaml"] = seeds.get("sound_yaml", []) + [sound.to_yaml(sound.parse(d)).encode() for d in snds[::6][:10]]
    from riftstone import effect, effect_efl
    for ext in ("epv", "efl", "e2d", "efs"):
        fx = sorted((r.data for r in corpus.resources(game, [typemap.BY_EXT[ext]]) if len(r.data) < 65536), key=len)
        seeds[ext] = seeds.get(ext, []) + fx[:20] + fx[::max(1, len(fx) // 20)][:20]
        if ext in ("epv", "efl"):
            mod = effect if ext == "epv" else effect_efl
            seeds[ext + "_yaml"] = seeds.get(ext + "_yaml", []) + [mod.to_yaml(mod.parse(d), "s").encode()
                                                                   for d in fx[:40:5]]


def _ddo_xfs_seeds() -> tuple[bytes, bytes]:
    """An Online (0x000f) XFS -- every value type, Shift-JIS text with 0x5C trail bytes beside
    backslashes -- and its YAML."""
    import helpers
    from riftstone import params, xfs

    x = helpers.sample_xfs()
    x.classes = [xfs.ClassDef(c.type_id, None, c.props) for c in x.classes]
    x.version = xfs.VERSION_DDO
    x.root.fields[11] = ["ソネル\\表".encode("cp932")]                 # mName
    x.root.fields[16] = ["C:\\構\\能 \u2015".encode("cp932")]               # mTag
    raw = xfs.build(x)
    return raw, params.to_yaml(xfs.parse(raw), "fuzz\\ddo_sample").encode()


def _ddo_lot_seeds() -> list[bytes]:
    from riftstone import lot_ddo

    fields = lot_ddo._layout(1)[1]
    vals = [("em010100" if t == "str" else (0, 0, 0) if t == "v3" else (0, 0, 0, 0) if t == "v4"
             else [] if t.startswith("list") else 0) for _, t in fields]
    return [lot_ddo.build(lot_ddo.LotDdo(records=[lot_ddo.Record(0, 1, vals)])), lot_ddo.build(lot_ddo.LotDdo())]


def _ddo_gpl_seeds() -> list[bytes]:
    """A DDO group list (v70) with one group holding each zone kind the game uses, and an empty one."""
    from riftstone import gpl_ddo

    f1 = 0x3F800000                                                    # 1.0
    head = {"Name": "", "CheckAngle": 0, "CheckRange": 0, "CheckToward": 0, "AngleFlag": 0, "TowardFlag": 0}
    zone = {1: dict(mHeight=f1, mBottom=0, mVertex=[[0] * 4] * 4, mConcaveCrossPos=[0] * 4, mFlgConvex=1,
                    mConcaveStatus=0),
            3: dict(Position0=[0] * 4, Position1=[0, f1, 0, 0], Radius=f1, pad=[0xCDCDCDCD] * 3)}
    shapes = [dict(head, type=k, mDecay=f1, mIsNativeData=0, **z) for k, z in zone.items()]
    g = {"mGroup": 3, "DisableSplit": 0, "SetMarkerPos": 0, "ForceOmGroupAllHardware": 0, "mUnk1C": 0,
         "mLayoutIDArray": [{"Area": 100, "Group": 3, "SplitX": 0, "SplitZ": 0}],
         **{k: 0 for k, _ in gpl_ddo._GROUP_A + gpl_ddo._GROUP_B}, "mUnk26": 0xCDCD, "MaxCount": 10,
         "mAreaHitShapeList": shapes[:1], "mLifeAreaArray": [{"mShapeList": shapes}], "KillAreaType": 1,
         "mKillAreaList": shapes[1:]}
    glist = list(range(gpl_ddo.SLOTS))
    glist[3] |= gpl_ddo.IN_FILE
    return [gpl_ddo.build(gpl_ddo.GplDdo(glist, [g])), gpl_ddo.build(gpl_ddo.GplDdo())]


def _lot_ddo_yaml() -> bytes:
    from riftstone import lot_ddo

    return lot_ddo.to_yaml(lot_ddo.parse(_ddo_lot_seeds()[0]), "fuzz").encode()


def _ddo_tex_seed() -> bytes:
    from riftstone import tex as _t

    return _t.build(_t.Tex(0x20002, _t.VERSION_DDO, 2, 8, 8, 1, 20, 1,
                           struct.pack("<2I", 24, 56) + bytes(40)))


def _d3d9_sources() -> list[bytes]:
    """t_d3d9_source's inputs: a DLL, a DXVK-shaped .tar.gz and .zip, and a folder's DLL."""
    import io
    import tarfile
    import zipfile

    import helpers

    x32 = helpers.pe_file(exports=("Direct3DCreate9", "Direct3DCreate9Ex"), extra=b"DXVK_CONFIG_FILE\0")
    x64 = helpers.pe_file(machine=0x8664, plus=True)
    tar = io.BytesIO()
    with tarfile.open(fileobj=tar, mode="w:gz") as t:
        for name, data in (("dxvk-9.9.9/x32/d3d9.dll", x32), ("dxvk-9.9.9/x64/d3d9.dll", x64)):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("dxvk-9.9.9/x32/d3d9.dll", x32)
        zf.writestr("dxvk-9.9.9/x64/d3d9.dll", x64)
    return [b"D" + x32, b"T" + tar.getvalue(), b"Z" + z.getvalue(), b"F" + x32, b"D" + x64]


def _level_seeds() -> dict[str, list[bytes]]:
    """Navigation meshes, mission grammars, small constraint problems and level-director cases."""
    import nav_fixture
    from riftstone import mission, nav

    corridor = nav_fixture.corridor_with_island()
    one = nav_fixture.build([nav_fixture.floor(0.0, 0.0, 2, 1, 400.0, 0.0)])
    small = {"format": mission.FORMAT, "start": "D", "rules": {"D": [["Fight", "?Fight", "Boss"]]}}
    return {
        "nav": [nav.build(corridor), nav.build(one)],
        "mission": [json.dumps(mission.DEFAULT).encode(), json.dumps(small).encode(),
                    json.dumps({"format": mission.FORMAT, "start": "A", "rules": {
                        "A": [["Fight", "B"], ["Horde"]], "B": [["?Ambush", "Guardian"], ["Boss"]]},
                        "weights": {"A": [3, 1]}}).encode()],
        "wfc": [bytes([3, 2, 7, 1, 2, 3, 0, 1, 0x96, 1, 2, 0x69]), bytes([5, 3, 1] + list(range(40))),
                bytes([6, 4, 9, 255, 0, 255, 0, 255, 0] + [1, 2, 3] * 12)],
        "dungeon": [json.dumps(c).encode() for c in (
            {"seed": 3, "spacing": 10.0, "grammar": small},
            {"seed": 1, "spacing": 8, "pool": "game", "at_once": 4, "grammar": small},
            {"seed": 7, "enemies": ["goblin"], "grammar": small},
            {"seed": 2, "exclude": ["goblin"]},
            {"seed": 0})],
        # the corridor's floor (-350), beside the hole, by the wall, a flyer, flat rings, a tight spot's tiny spread
        "ground": [json.dumps(c).encode() for c in (
            {"at": [2500, -350, -9600], "count": 6},
            {"at": [2000, -350, -9250], "count": 12, "points": 8, "spread": 180.5},
            {"at": [0, -350, -9990], "count": 10, "spread": 0.001},
            {"at": [2500, 0, -9750], "enemy": "harpies", "count": 3, "ground": True},
            {"at": "group:7", "count": 31, "points": 31, "spread": 60, "ground": False},
            {"at": [8500, 650, 8500], "count": 4, "spread": 400})],
    }


def _knowledge_seeds() -> list[bytes]:
    """Save XML holding main pawns (the unit tests' stand-in save): untouched, and part-way up."""
    from riftstone import saves

    make = __import__("test_saves").knowledge_save
    part = make(main=2, frames=[float(g * 13 % 50) for g in range(saves.GROUPS)],
                kills=[g % 9 for g in range(saves.GROUPS)], feats=[i % 4 for i in range(saves.FEAT_SLOTS)])
    return [saves.unpack(make()), saves.unpack(part)]


def build_seeds() -> dict[str, list[bytes]]:
    seeds = _synthetic()
    from riftstone import cipher
    cipher._key = cipher._default = None    # tests/helpers set a stand-in key; the games' seeds need the client's own
    try:
        from riftstone import arc, arcfolder, corpus, params, typemap
        from riftstone.game import find_game

        game = find_game()
    except Exception:  # noqa: BLE001 - no game: synthetic seeds only
        return seeds
    arcs = sorted(game.archives(), key=lambda p: p.stat().st_size)
    small = [p.read_bytes() for p in arcs[:40]]
    seeds["arc"] += small
    by_type: dict[int, list] = {}
    for r in corpus.resources(game, corpus.xfs_type_ids()):
        by_type.setdefault(r.type_id, []).append(r)
    xfs_seeds = []
    for tid, rs in by_type.items():
        rs.sort(key=lambda r: len(r.data))
        xfs_seeds += [r.data for r in rs[:3]]
    seeds["xfs"] += xfs_seeds
    machines = sorted((r.data for r in by_type.get(typemap.BY_EXT["fsm"], []) if len(r.data) < 65536), key=len)
    seeds["fsm"] = seeds.get("fsm", []) + machines[::max(1, len(machines) // 60)][:60]
    seeds["fsmcheck"] = seeds.get("fsmcheck", []) + machines[::max(1, len(machines) // 60)][:60]
    ymls = []
    for tid, rs in list(by_type.items()):
        r = rs[0]
        if len(r.data) < 40000:
            ymls.append(params.xfs_to_yaml_bytes(r.data, r.name.decode("latin-1"), r.type_id).encode())
    seeds["params"] += ymls
    seeds["yaml"] += ymls
    ocl_bin, ocl_yaml = [], []
    for r in corpus.resources(game, [typemap.BY_EXT["ocl"]]):
        ocl_bin.append(r.data)
        if len(ocl_bin) >= 60:
            break
    from riftstone import ocl as _ocl
    for d in ocl_bin[:20]:
        ocl_yaml.append(_ocl.to_yaml(_ocl.parse(d), "seed.ocl").encode())
    seeds["ocl"] = seeds.get("ocl", []) + ocl_bin
    seeds["ocl_yaml"] = seeds.get("ocl_yaml", []) + ocl_yaml
    from riftstone import gmd as _gmd
    texts = sorted((r.data for r in corpus.resources(game, [typemap.BY_EXT["gmd"]]) if len(r.data) < 16384), key=len)
    texts = texts[::max(1, len(texts) // 60)][:60]          # small to mid-sized, spread across the range
    seeds["gmd"] = seeds.get("gmd", []) + texts
    seeds["gmd_yaml"] = seeds.get("gmd_yaml", []) + [_gmd.to_yaml(_gmd.parse(d), "seed_eng").encode()
                                                     for d in texts[::3][:20]]
    seeds["cli"] += [b"\x06" + d for d in texts[:4]]
    from riftstone import itl as _itl
    for r in corpus.resources(game, [typemap.BY_EXT["itl"]]):
        whole = _itl.parse(r.data)
        # a slice of the real list: the whole 243 KB file mutates 100x slower, and check_corpus proves it
        part = _itl.ItemList(whole.stamp, whole.reserved, whole.records[:24])
        seeds["itl"] = seeds.get("itl", []) + [_itl.build(part)]
        seeds["itl_yaml"] = seeds.get("itl_yaml", []) + [_itl.to_yaml(part).encode()]
    from riftstone import tables as _tables
    for r in corpus.resources(game, [typemap.BY_EXT["ist"], typemap.BY_EXT["imx"]]):
        t = _tables.parse(r.data)
        part = _tables.Table(t.magic, t.version, t.rows[:12])          # small real slices mutate fast
        seeds["tables"] = seeds.get("tables", []) + [_tables.build(part)]
        seeds["tables_yaml"] = seeds.get("tables_yaml", []) + [_tables.to_yaml(part).encode()]
    from riftstone import flat as _flatm
    fexts = __import__("targets").flat_order()
    for ext in fexts:
        tid = typemap.BY_EXT.get(ext)
        if tid is None:
            continue
        files = []
        for r in corpus.resources(game, [tid]):
            files.append(r.data)
            if len(files) >= 4:
                break
        seeds["flat"] = seeds.get("flat", []) + [bytes([fexts.index(ext)]) + d for d in files]
        seeds["flat_yaml"] = seeds.get("flat_yaml", []) + [_flatm.to_yaml(_flatm.parse(d, ext)).encode()
                                                           for d in files[:2]]
    from riftstone import gpl as _gpl
    gpls = sorted((r.data for r in corpus.resources(game, [typemap.BY_EXT["gpl"]]) if len(r.data) < 32768), key=len)
    seeds["gpl"] = seeds.get("gpl", []) + gpls[::max(1, len(gpls) // 40)][:40]
    seeds["gpl_yaml"] = seeds.get("gpl_yaml", []) + [_gpl.to_yaml(_gpl.parse(d)).encode() for d in gpls[::8][:8]]
    from riftstone import lot as _lot
    layouts = sorted((r.data for r in corpus.resources(game, [typemap.BY_EXT["lot"]]) if len(r.data) < 8192), key=len)
    layouts = layouts[::max(1, len(layouts) // 40)][:40]
    seeds["lot"] = seeds.get("lot", []) + layouts
    seeds["lot_yaml"] = seeds.get("lot_yaml", []) + [_lot.to_yaml(_lot.parse(d)).encode() for d in layouts[::4][:10]]
    refs = sorted((r.data for r in corpus.resources(game, [typemap.BY_EXT["arc"]]) if len(r.data) < 4096), key=len)
    seeds["arcs"] = seeds.get("arcs", []) + refs[::max(1, len(refs) // 30)][:30]
    from riftstone import tex as _texm
    from riftstone import mrl as _mrlm
    mrls=sorted((r.data for r in corpus.resources(game,[typemap.BY_EXT["mrl"]]) if len(r.data)<65536),key=len)
    seeds["mrl"]=seeds.get("mrl",[])+mrls[::max(1,len(mrls)//40)][:40]
    prps = sorted((r.data for r in corpus.resources(game, [typemap.BY_EXT["prp"]]) if len(r.data) < 65536), key=len)
    prps = prps[::max(1, len(prps) // 40)][:40]                 # a spread of enemy classes
    seeds["prp"] = seeds.get("prp", []) + prps
    eans = sorted((r.data for r in corpus.resources(game, [typemap.BY_EXT["ean"]]) if len(r.data) < 65536), key=len)
    seeds["ean"] = seeds.get("ean", []) + eans
    lmts = sorted((r.data for r in corpus.resources(game, [typemap.BY_EXT["lmt"]]) if len(r.data) < 65536), key=len)
    seeds["lmt"] = seeds.get("lmt", []) + lmts[::max(1, len(lmts) // 30)][:30]
    seeds["port_lmt"] = seeds.get("port_lmt", []) + [bytes([0]) + d for d in lmts[::max(1, len(lmts) // 12)][:12]]
    navs = sorted((r.data for r in corpus.resources(game, [typemap.BY_EXT["nav"]]) if len(r.data) < 1 << 18), key=len)
    seeds["nav"] = seeds.get("nav", []) + navs[:3]
    _real_weather_camera(game, seeds)
    _real_face_msg_schedule(game, seeds)
    try:                                                     # Dragon's Dogma Online, when present: its own formats
        ddo = find_game("ddo")
        _real_weather_camera(ddo, seeds)
        _real_face_msg_schedule(ddo, seeds)
        from riftstone import ddo_params as _dp
        _kinds = list(_dp.KINDS)
        for _k in _kinds:
            _fs = sorted((r.data for r in corpus.resources(ddo, [_dp.KINDS[_k].type_id]) if len(r.data) < 16384), key=len)
            seeds["ddo_params"] = seeds.get("ddo_params", []) + [bytes([_kinds.index(_k)]) + d for d in _fs[:4]]
            seeds["ddo_params_yaml"] = seeds.get("ddo_params_yaml", []) + [
                _dp.to_yaml(_dp.parse(d, _k), "s").encode() for d in _fs[:1]]
        order = __import__("targets").flat_order()
        for ext in order:
            tid = typemap.BY_EXT.get(ext)
            if tid is None or ext in ("gfd",):
                continue
            files = []
            for r in corpus.resources(ddo, [tid]):
                if len(r.data) < 32768:
                    files.append(r.data)
                if len(files) >= 4:
                    break
            seeds["flat"] = seeds.get("flat", []) + [bytes([order.index(ext)]) + d for d in files
                                                     if not _flatm.SCHEMAS[ext][0] or _flatm.magic_ext(d) == ext
                                                     or ext == "ajp"]
        gpls = sorted((r.data for r in corpus.resources(ddo, [typemap.BY_EXT["gpl"]]) if len(r.data) < 16384), key=len)
        seeds["gpl_ddo"] = seeds.get("gpl_ddo", []) + gpls[::max(1, len(gpls) // 20)][:20]
        ocls = sorted((r.data for r in corpus.resources(ddo, [typemap.BY_EXT["ocl"]]) if len(r.data) < 16384), key=len)
        seeds["ocl_ddo"] = seeds.get("ocl_ddo", []) + ocls[::max(1, len(ocls) // 20)][:20]
        seeds["compat"] = seeds.get("compat", []) + [bytes([20 + i]) + d for i, d in enumerate(ocls[:10])]
    except Exception:  # noqa: BLE001 - no DDO client: synthetic seeds only
        pass
    texes = sorted((r.data for r in corpus.resources(game, [typemap.BY_EXT["tex"]]) if len(r.data) < 65536), key=len)
    texes = texes[::max(1, len(texes) // 40)][:40]
    seeds["tex"] = seeds.get("tex", []) + texes
    dds = []
    for d in texes:
        try:
            dds.append(_texm.to_dds(_texm.parse(d)))
        except Exception:  # noqa: BLE001 - cube maps and odd formats have no .dds form
            pass
    seeds["tex_dds"] = seeds.get("tex_dds", []) + dds[:20]
    from riftstone import terrain as _terrain
    cell_models = []
    for c in _terrain.cells():                                   # real Gransys cells, the small ones
        try:
            e = arc.Archive.read(game.arc_path(c.archive)).find(c.model.encode(), typemap.BY_EXT["mod"])
        except Exception:  # noqa: BLE001 - not Dark Arisen, or the archive is missing
            continue
        if e is not None and len(e.data()) < 65536:
            cell_models.append(e.data())
        for cn in c.collisions:                                  # and their collision meshes
            try:
                ce = arc.Archive.read(game.arc_path(c.archive)).find(cn.encode(), typemap.BY_EXT["sbc"])
            except Exception:  # noqa: BLE001
                continue
            if ce is not None and len(ce.data()) < 65536:
                cell_models.append(ce.data())
        if len(cell_models) >= 12:
            break
    seeds["terrain"] = seeds.get("terrain", []) + cell_models
    seeds["fsmap"] = _fsmap_seeds(game)
    seeds["cli"] += [b"\x00" + d for d in small[:10] if len(d) < 200_000] + [b"\x02" + y for y in ymls[:10]] \
        + [b"\x01" + d for d in xfs_seeds[:10]]
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        for p in arcs[:3]:
            out = Path(tmp) / p.stem
            arcfolder.unpack(p, out)
            seeds["pack"].append((out / arcfolder.MANIFEST).read_bytes())
    return seeds


def _seed_game():
    """The installed game the seeds come from, or None."""
    try:
        from riftstone.game import find_game

        return find_game()
    except Exception:  # noqa: BLE001 - no game: synthetic seeds only
        return None


def load_seeds(refresh: bool) -> dict[str, list[bytes]]:
    if SEEDS.is_file() and not refresh:
        seeds = pickle.loads(SEEDS.read_bytes())
        grew = False
        # a target whose input encoding changed since the cache was made gets its seeds anew (an old fsmap seed's
        # one-byte selector would pick another type now)
        formats = seeds.get(SEED_FORMATS_KEY, {})
        for name, version in SEED_FORMATS.items():
            if formats.get(name) != version:
                seeds[name] = {"fsmap": _fsmap_seeds}[name](_seed_game())
                grew = True
        seeds[SEED_FORMATS_KEY] = dict(SEED_FORMATS)
        # built-in seeds added since the cache was made (new targets, new cases) join it
        for name, extra in _synthetic().items():
            have = seeds.setdefault(name, [])
            new = [s for s in extra if s not in have]
            if new:
                have.extend(new)
                grew = True
        if grew:
            SEEDS.write_bytes(pickle.dumps(seeds))
        return seeds
    seeds = build_seeds()
    seeds[SEED_FORMATS_KEY] = dict(SEED_FORMATS)
    SEEDS.parent.mkdir(parents=True, exist_ok=True)
    SEEDS.write_bytes(pickle.dumps(seeds))
    return seeds


def worker(job):
    name, seconds, seed, tmpdir = job
    os.environ["RIFTSTONE_FUZZ_TMP"] = tmpdir
    os.environ["NO_COLOR"] = "1"
    os.environ["RIFTSTONE_HOME"] = tmpdir
    os.environ["RIFTSTONE_SKIP_GAME"] = "1"
    import engine
    import targets

    fn, text, max_len = targets.TARGETS[name]
    seeds = pickle.loads(SEEDS.read_bytes())[name]
    st = engine.fuzz(name, fn, seeds, seconds, seed, targets.ALLOWED, text=text, max_len=max_len,
                     findings_dir=FINDINGS)
    return st.__dict__


def _rmtree(path: str) -> None:
    """rmtree through the \\\\?\\ prefix, which skips Win32 name normalisation: an input that once
    slipped an NTFS stream name past a check left files called 'name.' that plain paths cannot remove.
    A scratch folder's marker (SCRATCH_MARK) goes last, with the folder: one that cannot be emptied yet keeps
    it, so a later run's sweep_stale still knows the folder as the fuzzer's own."""
    p = os.path.abspath(path)
    if os.name == "nt" and not p.startswith("\\\\?\\"):
        p = "\\\\?\\" + p
    # A killed worker's index database can stay open a while longer (seen: over 5 s, likely an antivirus
    # scan of the just-closed file).  Keep trying for about 15 s; sweep_stale() catches what is still left.
    for attempt in range(10):
        try:
            names = [n for n in os.listdir(p) if n != SCRATCH_MARK]
        except OSError:
            names = []
        for n in names:
            child = os.path.join(p, n)
            if os.path.isdir(child) and not os.path.islink(child):
                shutil.rmtree(child, ignore_errors=True)
            else:
                try:
                    os.remove(child)
                except OSError:
                    pass
        try:
            left = [n for n in os.listdir(p) if n != SCRATCH_MARK]
        except OSError:
            left = []
        if not left:
            shutil.rmtree(p, ignore_errors=True)
        if not os.path.exists(p):
            return
        time.sleep(0.3 * (attempt + 1))


def scratch(root: Path) -> str:
    """A new scratch folder for one run or replay in ``root`` (--tmp): the fuzzer's own prefix and a marker file
    inside, so sweep_stale tells its leftovers from anything else there (--tmp may be a shared folder)."""
    tmp = tempfile.mkdtemp(prefix=SCRATCH_PREFIX, dir=root)
    Path(tmp, SCRATCH_MARK).write_text("Scratch of Riftstone's fuzz/run.py; removed when its run ends, or by the "
                                       "next run once idle for an hour.\n", encoding="utf-8")
    return tmp


def sweep_stale(root: Path, idle: float = 3600) -> None:
    """Remove earlier runs' scratch folders that nothing has touched for an hour: only folders this fuzzer made
    (SCRATCH_PREFIX, with SCRATCH_MARK inside), whatever else ``root`` holds.  In the fuzzer's own folder
    (fuzz/.tmp), where nothing else lives, the prefix alone is enough, and the run-* folders earlier versions
    left go too.  A running campaign's folder changes all the time (filesystem targets make one per case), so
    only leftovers are this old."""
    now = time.time()
    own = os.path.normcase(os.path.abspath(root)) == os.path.normcase(os.path.abspath(HERE / ".tmp"))
    for d in root.iterdir():
        try:
            ours = d.name.startswith(SCRATCH_PREFIX) and (own or (d / SCRATCH_MARK).is_file())
            if d.is_dir() and (ours or own and d.name.startswith("run-")) and now - d.stat().st_mtime > idle:
                _rmtree(str(d))
        except OSError:
            pass


def replay(root: Path, slow: float = 3.0) -> int:
    """Run every input in fuzz/findings again. A fixed defect no longer reproduces; exit 1 if any does."""
    tmp = scratch(root)
    os.environ.update(RIFTSTONE_FUZZ_TMP=tmp, RIFTSTONE_HOME=tmp, NO_COLOR="1", RIFTSTONE_SKIP_GAME="1")
    import targets

    def outcome(fn, data: bytes):
        t0 = time.perf_counter()
        try:
            fn(data)
        except targets.ALLOWED:
            pass
        except RecursionError as e:
            return "recursion", e
        except AssertionError as e:
            return "invariant", e
        except MemoryError as e:
            return "memory", e
        except Exception as e:  # noqa: BLE001 - reported below
            return "crash", e
        if time.perf_counter() - t0 > slow:
            again = []
            for _ in range(2):
                t1 = time.perf_counter()
                try:
                    fn(data)
                except Exception:  # noqa: BLE001 - only the time matters here
                    pass
                again.append(time.perf_counter() - t1)
            if min(again) > slow:
                return "slow", TimeoutError(f"{min(again):.1f}s")
        return None

    still, total = [], 0
    try:
        for f in sorted(FINDINGS.glob("*.bin")):
            name = f.stem.split("-")[0]
            if name not in targets.TARGETS:
                print(f"  ?     {f.name}: no target '{name}' any more")
                continue
            total += 1
            r = outcome(targets.TARGETS[name][0], f.read_bytes())
            if r:
                still.append(f.name)
                print(f"  STILL {f.name}: {r[0]} {type(r[1]).__name__}: {str(r[1])[:160]}")
    finally:
        targets.close_caches()      # the targets ran in this process: their index databases are open in the scratch
        _rmtree(tmp)
    print(f"\nreplayed {total} saved findings: {total - len(still)} fixed, {len(still)} still reproduce")
    return 1 if still else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    # Every target by default: a fixed list here once left each target added later out of the release gate.
    ap.add_argument("--targets", default="", help="comma-separated (default: every target in targets.TARGETS)")
    ap.add_argument("--refresh-seeds", action="store_true")
    ap.add_argument("--tmp", default=None, help="where the run's scratch folder goes (default fuzz/.tmp); only the "
                                                "fuzzer's own idle scratch folders there are ever removed")
    ap.add_argument("--replay", action="store_true", help="re-run every saved finding instead of fuzzing")
    a = ap.parse_args()
    root = Path(a.tmp or HERE / ".tmp")
    root.mkdir(parents=True, exist_ok=True)
    sweep_stale(root)
    if a.replay:
        return replay(root)
    import targets
    names = [n for n in a.targets.split(",") if n] or list(targets.TARGETS)
    unknown = [n for n in names if n not in targets.TARGETS]
    if unknown:
        raise SystemExit(f"unknown target(s): {', '.join(unknown)} (known: {', '.join(targets.TARGETS)})")
    seeds = load_seeds(a.refresh_seeds)
    print("seeds:", {n: len(seeds.get(n, [])) for n in names}, flush=True)
    tmp = scratch(root)
    jobs = [(names[i % len(names)], a.seconds, 1000 + i, tmp) for i in range(max(a.workers, len(names)))]
    t0 = time.time()
    try:
        with mp.get_context("spawn").Pool(a.workers) as pool:
            results = pool.map(worker, jobs)
    finally:
        # The pool terminates its workers, so their atexit cleanup never runs: remove their scratch here.
        _rmtree(tmp)
        if os.path.exists(tmp):
            print(f"note: {tmp} is still held open (a worker's index file); the next run removes it", flush=True)
    merged: dict[str, dict] = {}
    for r in results:
        m = merged.setdefault(r["target"], {"iterations": 0, "accepted": 0, "rejected": 0, "workers": 0,
                                            "corpus": 0, "coverage": 0, "stalls": 0, "max_stall": 0.0,
                                            "findings": {}})
        for k in ("iterations", "accepted", "rejected", "corpus", "stalls"):
            m[k] += r[k]
        m["coverage"] = max(m["coverage"], r["coverage"])
        m["max_stall"] = max(m["max_stall"], r["max_stall"])
        m["workers"] += 1
        for key, f in r["findings"].items():
            if key in m["findings"]:
                m["findings"][key]["count"] += f["count"]
            else:
                m["findings"][key] = f
    report = {"schema": "riftstone.fuzz/1", "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "seconds_per_worker": a.seconds, "wall_seconds": round(time.time() - t0, 1), "targets": merged}
    REPORTS.mkdir(exist_ok=True)
    out = REPORTS / f"fuzz-{time.strftime('%Y%m%dT%H%M%S')}.json"
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    total = Counter()
    print(f"\n{'target':<8} {'workers':>7} {'execs':>9} {'exec/s':>7} {'accepted':>9} {'corpus':>7} {'cov':>6}  findings")
    for name in names:
        m = merged.get(name)
        if not m:
            continue
        rate = m["iterations"] / max(a.seconds * m["workers"], 1)
        print(f"{name:<8} {m['workers']:>7} {m['iterations']:>9,} {rate:>7.0f} {m['accepted']:>9,} {m['corpus']:>7,} "
              f"{m['coverage']:>6}  {len(m['findings'])}")
        for key, f in m["findings"].items():
            total[name] += 1
            print(f"    {key}  x{f['count']}: {f['message'][:140]}")
        if m["stalls"]:
            print(f"    stalls x{m['stalls']} (longest {m['max_stall']}s): over the slow limit once, fast on both "
                  f"re-runs, so machine load rather than the input")
    print(f"\nreport: {out}")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
