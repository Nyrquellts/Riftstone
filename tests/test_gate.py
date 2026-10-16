"""The corpus gate's own tools, driven on stand-in games: tools/check_corpus.py (what --only takes, a check that
finds nothing, the motion-list port, the collision move, the originals an install replaced, archive references,
textures, the named-param table), tools/type_census.py and tools/terrain_proof.py.  Each test drives the tool the
way the gate does, so a check that cannot fail shows up here.  The fuzzer's are in test_fuzz_gate.py."""
from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import os
import struct
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401 (sys.path)
from riftstone import arc, arcref, corpus, ddo_params, ddo_solo, gmd, lmt, lot, port, sbc, terrain, tex, typemap, xfs
from riftstone.game import Game

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.append(str(ROOT / "tools"))

import check_corpus  # noqa: E402
import terrain_proof  # noqa: E402
import type_census  # noqa: E402

GMD, LMT, SBC, TEX, ARC, NDP = (typemap.BY_EXT[e] for e in ("gmd", "lmt", "sbc", "tex", "arc", "ndp"))


def stand_in(base: Path, kind: str = "ddda", live=None, vanilla=None) -> Game:
    """A stand-in game: a stub exe, nativePC/rom, the archives ``live`` names ({archive: [Entry] or raw bytes}),
    and Riftstone's backups of originals it replaced, riftstone/vanilla (``vanilla``, the same form)."""
    root = Path(base) / kind
    (root / "nativePC" / "rom").mkdir(parents=True)
    (root / ("DDO.exe" if kind == "ddo" else "DDDA.exe")).write_bytes(b"stub")
    for folder, arcs in ((root / "nativePC", live), (root / "riftstone" / "vanilla", vanilla)):
        for name, entries in (arcs or {}).items():
            p = folder / (name + ".arc")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(entries if isinstance(entries, bytes) else
                          arc.Archive(entries, encrypted=kind == "ddo").build())
    return Game(root, kind)


def entry(name: bytes, tid: int, data: bytes, kind: str = "ddda") -> arc.Entry:
    return arc.Entry.from_data(name, tid, data, encrypted=kind == "ddo")


def text(words: str) -> bytes:
    return gmd.build(gmd.Gmd(1, "TextWeb", [gmd.Message(words, "0")]))


def run_main(*args: str) -> tuple[int, dict | None]:
    """check_corpus.main on these arguments, in this process unless they say ``--jobs``: (exit code, the report it
    wrote, or None)."""
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "report.json"
        jobs = [] if "--jobs" in args else ["--jobs", "1"]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                code = check_corpus.main([*args, *jobs, "--out", str(out)])
            except SystemExit as e:
                code = e.code
        return code, json.loads(out.read_text(encoding="utf-8")) if out.is_file() else None


class OnlyTest(unittest.TestCase):
    """--only names checks; a name that is none of them must not run nothing and pass."""

    def setUp(self):
        import test_effect
        from riftstone import effect_efl

        self.tmp = tempfile.TemporaryDirectory()
        self.game = stand_in(Path(self.tmp.name), live={"rom/game_main": [
            entry(b"message\\hello_eng", GMD, text("Hello")),
            entry(b"effect\\efl\\x", typemap.BY_EXT["efl"], effect_efl.build(test_effect.efl_sample()))]})

    def tearDown(self):
        self.tmp.cleanup()

    def test_unknown_names_are_refused(self):
        for only in ("lmts,xsf", "gmd,efx", "", ","):
            with self.subTest(only=only):
                self.assertEqual(run_main("--game", str(self.game.root), "--only", only), (2, None))

    def test_effect_sections_run_their_part(self):
        code, rep = run_main("--game", str(self.game.root), "--only", "efl")
        self.assertEqual(code, 0)
        self.assertEqual(rep["efl"]["byte_exact"], 1)
        self.assertNotIn("epv", rep)                                     # only the part asked for

    def test_checks_that_do_not_apply_are_named(self):
        code, rep = run_main("--game", str(self.game.root), "--only", "gmd,ddo_params,ndp")
        self.assertEqual(code, 0)
        self.assertEqual(sorted(rep["skipped"]), ["ddo_params", "ndp"])
        self.assertEqual(rep["gmd"]["byte_exact"], 1)
        code, rep = run_main("--game", str(self.game.root), "--only", "ndp")      # nothing left to check
        self.assertEqual(code, 1)
        self.assertIn("nothing_checked", rep)


class NothingFoundTest(unittest.TestCase):
    """A check that finds no resource of its kind proved nothing: it fails (ABSENT lists the measured exceptions)."""

    CHECKS = {"xfs": lambda g, r: check_corpus.check_xfs(g, r, True), "ocl": check_corpus.check_ocl,
              "gmd": check_corpus.check_gmd, "lot": check_corpus.check_lot, "arcs": check_corpus.check_arcs,
              "arc": check_corpus.check_arc}

    def test_an_empty_game_fails(self):
        with tempfile.TemporaryDirectory() as d:
            for kind in ("ddda", "ddo"):
                game = stand_in(Path(d), kind)
                for name, check in self.CHECKS.items():
                    with self.subTest(kind=kind, check=name):
                        self.assertFalse(check(game, {}))
                code, rep = run_main("--game", str(game.root), "--only", "xfs,yaml,ocl,gmd,lot,arcs,arc")
                self.assertEqual(code, 1)
                self.assertIn("none_found", rep["gmd"])

    def test_one_resource_passes(self):
        with tempfile.TemporaryDirectory() as d:
            game = stand_in(Path(d), live={"rom/game_main": [entry(b"message\\hello_eng", GMD, text("Hello"))]})
            self.assertTrue(check_corpus.check_gmd(game, {}))
            self.assertTrue(check_corpus.check_arc(game, {}))


def lossy(real):
    """A port that keeps only the first motion and its first track (the pairs zip compares look fine)."""
    def convert(data, src, dst, *a, **k):
        out = real(data, src, dst, *a, **k)
        m = lmt.parse(out.data)
        first = next(mo for mo in m.motions if mo is not None)
        kept = dataclasses.replace(first, tracks=lmt.TrackList(first.tracks.tracks[:1]))
        return dataclasses.replace(out, data=lmt.build(lmt.Lmt(m.version, [kept])))
    return convert


class LmtPortTest(unittest.TestCase):
    """The port check counts motion slots and tracks before comparing them pair by pair."""

    def test_a_lossy_port_fails(self):
        import test_lmt

        data = lmt.build(test_lmt.sample(66, share_tracks=False))       # 2 motions of 4 tracks in 3 slots
        with tempfile.TemporaryDirectory() as d:
            game = stand_in(Path(d), live={"rom/motion": [entry(b"motion\\x", LMT, data)]})
            rep = {}
            check_corpus.check_lmt(game, rep)
            self.assertEqual((rep["lmt"]["failed"], rep["lmt"]["ported_to_ddo"]), (0, 1))
            with mock.patch.object(port, "convert_lmt", lossy(port.convert_lmt)):
                rep = {}
                self.assertFalse(check_corpus.check_lmt(game, rep))
            self.assertEqual(rep["lmt"]["failed"], 1)
            self.assertIn("motion slots", rep["lmt"]["failures"][0]["why"])


X = struct.unpack("<f", struct.pack("<f", -425.617075))[0]       # moved by -150000 cm it rounds up by 0.0077 cm


def nudging(real):
    """A move right towards negative x but 0.007 cm off the other way: a second move rounds it back to the same
    float (the old checks), yet X comes back 0.0147 cm from where it was -- twice float32's rounding there."""
    def translate(data, d):
        out = bytearray(real(data, d))
        if d[0] > 0:
            for off, axis in sbc.moved_floats(data):
                if axis == 0:
                    struct.pack_into("<f", out, off, struct.unpack_from("<f", out, off)[0] + 0.007)
        return bytes(out)
    return translate


class MoveBackTest(unittest.TestCase):
    """Moving a collision mesh by a cell corner and back must give the original within float32 rounding; moving
    twice landing on the same bytes (all the checks tested) does not show a way back that misses it."""

    CORNER = (-150000.0, 0.0, -30000.0)                   # terrain.Cell(47, 35), check_corpus's corner

    def mesh(self):
        return helpers.cell_collision(points=[(X, 5000.0, 200.0), (9800.0, 5100.0, 300.0), (400.0, 5200.0, 9700.0),
                                              (9900.0, 4900.0, 9900.0)])

    def test_the_nudge_is_stable_twice(self):          # why the old checks passed it
        data, bad = self.mesh(), nudging(sbc.translate)
        w = bad(data, self.CORNER)
        back = bad(w, tuple(-v for v in self.CORNER))
        self.assertEqual(bad(back, self.CORNER), w)
        first = struct.unpack_from("<f", back, sbc.parse(data).vertex_offset)[0]
        self.assertGreater(abs(first - X), 0.014)
        self.assertIsNotNone(check_corpus.moved_back(data, w, back, sbc.moved_floats(data))[2])

    def test_check_corpus(self):
        with tempfile.TemporaryDirectory() as d:
            game = stand_in(Path(d), live={"rom/stage/x": [entry(b"scr\\x\\collision\\x", SBC, self.mesh())]})
            rep = {}
            self.assertTrue(check_corpus.check_sbc(game, rep))
            self.assertLessEqual(rep["sbc"]["moved_back_largest_change_cm"], 0.0078125)
            with mock.patch.object(sbc, "translate", nudging(sbc.translate)):
                self.assertFalse(check_corpus.check_sbc(game, rep))
            self.assertIn("does not restore the original", rep["sbc"]["failures"][0]["why"])

    def test_terrain_proof(self):
        c = terrain.Cell(47, 35)
        self.assertEqual(c.offset, self.CORNER)
        self.assertEqual(terrain_proof.round_trip(self.mesh(), c)[:2], (True, True))
        with mock.patch.object(sbc, "translate", nudging(sbc.translate)):
            restored, stable, worst, exact = terrain_proof.round_trip(self.mesh(), c)
        self.assertEqual((restored, stable, exact), (False, True, False))
        self.assertGreater(worst, 0.014)

    def test_rounding_bound(self):
        self.assertEqual(check_corpus.half_step(274236.25), 2.0 ** -6)          # [2^18, 2^19): steps of 2^-5
        self.assertEqual(check_corpus.half_step(-1.0), 2.0 ** -24)
        self.assertEqual(check_corpus.half_step(0.0), 2.0 ** -150)
        o = struct.pack("<f", float("nan"))
        self.assertIsNotNone(check_corpus.moved_back(o, o, struct.pack("<f", 1.0), [(0, 0)])[2])
        self.assertIsNone(check_corpus.moved_back(o, struct.pack("<f", 2.0), o, [(0, 0)])[2])


class OriginalsTest(unittest.TestCase):
    """With mods installed straight into nativePC (no loader), Riftstone keeps the originals in riftstone/vanilla;
    the proofs read those, never Riftstone's own output."""

    def test_resources_come_from_the_backup(self):
        with tempfile.TemporaryDirectory() as d:
            game = stand_in(Path(d), live={"rom/game_main": [entry(b"message\\hello_eng", GMD, text("Modded"))]},
                            vanilla={"rom/game_main": [entry(b"message\\hello_eng", GMD, text("Vanilla"))]})
            got = list(corpus.resources(game, [GMD]))
            self.assertEqual([gmd.parse(r.data).messages[0].text for r in got], ["Vanilla"])
            self.assertEqual((got[0].label, game.arc_name(got[0].arc)), ("game_main.arc:message\\hello_eng",
                                                                         "rom/game_main"))
            for p in game.archives():
                self.assertEqual(corpus.source(game, p), game.vanilla_arc(game.arc_name(p)))

    def test_archives_are_rebuilt_from_the_backup(self):
        with tempfile.TemporaryDirectory() as d:
            good = arc.Archive([entry(b"message\\hello_eng", GMD, text("Vanilla"))]).build()
            game = stand_in(Path(d), live={"rom/game_main": b"ARC\0 Riftstone's output, not the original"},
                            vanilla={"rom/game_main": good})
            rep = {}
            self.assertTrue(check_corpus.check_arc(game, rep))
            self.assertEqual((rep["arc"]["exact"], rep["arc"]["read_from_backup"]), (1, 1))
            code, rep = run_main("--game", str(game.root), "--only", "gmd")
            self.assertEqual((code, rep["read_from_backup"]), (0, ["rom/game_main"]))

    def test_named_params_are_the_shipped_table(self):
        import test_ddo_solo as solo

        shipped = ddo_params.build(solo.small_table())
        installed = ddo_params.build(ddo_solo.make_twins(solo.small_table())[0])
        names = gmd.build(solo.names_file())
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"RIFTSTONE_DDO_ASSETS": ""}):
            game = stand_in(Path(d), "ddo",
                            live={"rom/game_common": [entry(ddo_solo.NDP_NAME, NDP, installed, "ddo")],
                                  "rom/ui/gui_cmn": [entry(ddo_solo.GMD_NAME, GMD, names, "ddo")]},
                            vanilla={"rom/game_common": [entry(ddo_solo.NDP_NAME, NDP, shipped, "ddo")]})
            built, real = [], ddo_params.build
            with mock.patch.object(ddo_params, "build", lambda m: (built.append(real(m)), built[-1])[1]):
                rep = {}
                ok = check_corpus.check_ndp(game, rep)
        e = rep["ndp"]
        self.assertTrue(ok, e["failures"])
        self.assertIn(shipped, built)                        # the table as shipped is the one round-tripped...
        self.assertNotIn(installed, built)                   # ... never Riftstone's twinned copy
        self.assertEqual((e["distinct"], e["byte_exact"], e["yaml_exact"], e["records"]), (1, 1, 1, 4))
        self.assertEqual(e["original"], {"archive": "rom/game_common", "round_trip": True,
                                         "installed_copy_differs": True})
        # no local server here: nothing compared, and the claim does not say it was
        self.assertIn("not_compared", e["server_json"])
        self.assertNotIn("server", e["claim"])


class ArcsTest(unittest.TestCase):
    """Archive references: more out-of-date or dangling lists than the game ships (ARCS_MEASURED) fail."""

    def game(self, d, differ=0, missing=0, match=1) -> Game:
        live, refs = {}, []
        for i in range(match + differ):
            name = f"rom/t{i}"
            directory = [(f"x\\file{i}_{k}".encode(), GMD) for k in range(2)]
            live[name] = [entry(n, t, text(f"{i} {k}")) for k, (n, t) in enumerate(directory)]
            listed = directory if i < match else directory[:1]
            refs.append(entry(name.replace("/", "\\").encode(), ARC, arcref.build(arcref.for_names(listed))))
        for i in range(missing):
            refs.append(entry(f"rom\\gone{i}".encode(), ARC, arcref.build(arcref.for_names([(f"y{i}".encode(), GMD)]))))
        live["rom/refs"] = refs
        return stand_in(Path(d), live=live)

    def test_measured_counts_bound_the_check(self):
        m = check_corpus.ARCS_MEASURED["ddda"]
        for differ, missing, want in ((m["differ_from_target"], m["target_missing"], True),
                                      (m["differ_from_target"] + 1, 0, False), (0, m["target_missing"] + 1, False)):
            with self.subTest(differ=differ, missing=missing), tempfile.TemporaryDirectory() as d:
                rep = {}
                self.assertEqual(check_corpus.check_arcs(self.game(d, differ, missing), rep), want)
                self.assertEqual((rep["arcs"]["differ_from_target"], rep["arcs"]["target_missing"]), (differ, missing))
                self.assertEqual("more_than_measured" in rep["arcs"], not want)


class TexTest(unittest.TestCase):
    """Only to_dds's refusal counts as "no .dds form", by reason; any other error is a failure."""

    def test_reasons_and_crashes(self):
        flat10 = tex.build(tex.Tex(0x20000, tex.VERSION, 1, 4, 4, 1, 10, 1, struct.pack("<I", 20) + bytes(64)))
        mip0, mip1 = tex._mip_size(8, 8, 0, 20), tex._mip_size(8, 8, 1, 20)
        flat20 = tex.build(tex.Tex(0x20000, tex.VERSION, 2, 8, 8, 1, 20, 1,
                                   struct.pack("<2I", 24, 24 + mip0) + bytes(mip0 + mip1)))
        with tempfile.TemporaryDirectory() as d:
            game = stand_in(Path(d), live={"rom/tex": [entry(b"tex\\f10", TEX, flat10), entry(b"tex\\f20", TEX, flat20)]})
            rep = {}
            self.assertTrue(check_corpus.check_tex(game, rep))
            self.assertEqual((rep["tex"]["dds_round_trip"], rep["tex"]["no_dds_form"]), (1, {"format 10": 1}))
            self.assertIn("no .dds form: format 10 (1)", rep["tex"]["claim"])

            def broken(t):
                raise ValueError("a bug in the exporter")
            with mock.patch.object(tex, "to_dds", broken):
                self.assertFalse(check_corpus.check_tex(game, rep))
            self.assertEqual((rep["tex"]["failed"], rep["tex"]["dds_not_applicable"]), (2, 0))


class CensusTest(unittest.TestCase):
    PROVEN = ("sbc wep wfp sky wtf wte wtl wsi wta lcm srq stq srd smx spl sbkr sar epv efl e2d efs cpe pep prs osp "
              "sti sal evtr ndp fca mss msl sdl zon").split()

    def test_corpus_proven_types_have_their_codec(self):
        self.assertEqual(len(self.PROVEN), 34)
        for ext in self.PROVEN:
            with self.subTest(ext=ext):
                self.assertNotEqual(type_census.codec_for(ext, typemap.BY_EXT[ext]), "none")
        self.assertEqual(type_census.codec_for("sbc", typemap.BY_EXT["sbc"]), "sbc (sbc)")
        self.assertEqual(type_census.codec_for("mod", typemap.BY_EXT["mod"]), "none")

    def test_not_decoded_revisions_have_none(self):
        for ext in check_corpus.NOT_DECODED["ddo"]:
            self.assertEqual(type_census.codec_for(ext, typemap.BY_EXT[ext], "ddo"), "none")
            self.assertNotEqual(type_census.codec_for(ext, typemap.BY_EXT[ext], "ddda"), "none")
        self.assertEqual(type_census.codec_for("spc", typemap.BY_EXT["spc"], "ddo"), "none")    # proved on DDDA only

    def test_gates_are_checks(self):
        for ext, (_, gates, _) in type_census.codecs().items():
            for g in gates:
                self.assertTrue(g in check_corpus.CHECKS or g == "check_audio_corpus", (ext, g))

    def test_every_type_the_corpus_gate_reads_has_a_codec(self):
        """Every check's reads (spied on an empty stand-in of each game): each type has its codec in the census."""
        asked: dict[str, set[int]] = {}
        indexed: list = []
        real = corpus.resources

        def spy(game, types=None, *a, **k):
            asked.setdefault(game.kind, set()).update(types or ())
            indexed.append(k.get("indexed", True))
            return real(game, types, *a, **k)
        with tempfile.TemporaryDirectory() as d, mock.patch.object(corpus, "resources", spy), \
                mock.patch.dict(os.environ, {"RIFTSTONE_DDO_ASSETS": ""}):
            for kind in ("ddda", "ddo"):
                game = stand_in(Path(d), kind)
                for name in check_corpus.CHECKS:
                    check_corpus.run_check(name, game, {})
        self.assertGreater(len(indexed), 20)
        self.assertEqual([i for i in indexed if i is not False], [])      # a proof reads every table, whatever the index says
        for kind, tids in asked.items():
            for tid in tids:
                ext = typemap.extension(tid)
                if ext in check_corpus.NOT_DECODED.get(kind, {}) or ext in check_corpus.ABSENT.get(kind, {}):
                    continue
                with self.subTest(kind=kind, ext=ext):
                    self.assertNotEqual(type_census.codec_for(ext, tid, kind), "none")

    def test_the_census_reads_the_originals(self):
        with tempfile.TemporaryDirectory() as d:
            game = stand_in(Path(d), live={"rom/game_main": [entry(b"a", GMD, text("a")), entry(b"b", GMD, text("b"))]},
                            vanilla={"rom/game_main": [entry(b"a", GMD, text("a"))]})
            rep = type_census.census(game, 1)
        self.assertEqual((rep["entries"], rep["read_from_backup"]), (1, 1))


def shard_game(base: Path, kind: str = "ddda") -> Game:
    """A stand-in with a few resources of each kind the sharded checks read, spread over three archives: a payload
    that is in two archives (counted once), a distinct one a check cannot read (a failure), and kinds that tie."""
    import test_lot

    def lot_file(i: int) -> bytes:
        doors = [lot.blank(53, 20 + k, mName=f"om{7000 + k}", mLockMsgNo=i, mPosition=(0.5, 0.25, float(k)))
                 for k in range(i % 4)]
        npc = lot.blank(47, 5, mName="em1000", FSMPath="ai\\npc.fsm", mHumanEnemyKind=3, mPosition=(float(i), 0.0, -4.5))
        return lot.build(lot.Lot(([test_lot.goblin(0)] if i % 2 else []) + [npc, *doors]))

    def mesh(i: int) -> bytes:
        return helpers.cell_collision(points=[(X + i, 5000.0, 200.0), (9800.0, 5100.0 + i, 300.0),
                                              (400.0, 5200.0, 9700.0), (9900.0, 4900.0, 9900.0 - i)])

    def xfs_file(i: int) -> bytes:
        x = helpers.sample_xfs()
        x.root.fields[6] = [-30000 + i]
        return xfs.build(x)

    xfs_tid = corpus.xfs_type_ids()[0]
    made: dict[str, list] = {"rom/a": [], "rom/b": [], "rom/c": []}
    for i in range(13):
        place = made[f"rom/{'abc'[i % 3]}"]
        place.append(entry(b"message\\m%d" % i, GMD, text(f"line {i}"), kind))
        place.append(entry(b"scr\\l%d" % i, typemap.BY_EXT["lot"], lot_file(i), kind))
        place.append(entry(b"scr\\s%d\\collision\\s" % i, SBC, mesh(i), kind))
        place.append(entry(b"param\\p%d" % i, xfs_tid, xfs_file(i), kind))
    made["rom/b"].append(entry(b"message\\again", GMD, text("line 0"), kind))              # the same payload, twice
    made["rom/c"] += [entry(b"message\\broken", GMD, b"not a message file", kind),
                      entry(b"scr\\broken", typemap.BY_EXT["lot"], b"\0" * 40, kind),
                      entry(b"scr\\x\\collision\\broken", SBC, b"\0" * 40, kind),
                      entry(b"param\\broken", xfs_tid, b"XFS?" + b"\0" * 40, kind)]
    return stand_in(base, kind, live=made)


def stable(section: dict) -> str:
    """A report section as two runs must agree on it, as the text it is written as (so the order of the keys and of
    the failures counts): what depends on the clock or the number of processes taken out."""
    return json.dumps({k: v for k, v in section.items() if k not in ("seconds", "shards")}, indent=1)


class ShardTest(unittest.TestCase):
    """The corpus gate's processes each check their share of a pass (shard I of N: the resources whose place in the
    pass is I modulo N); together they must be one process's whole pass, and the merged report the one it writes."""

    CHECKS = {"gmd": check_corpus.check_gmd, "lot": check_corpus.check_lot, "sbc": check_corpus.check_sbc,
              "xfs": lambda g, r: check_corpus.check_xfs(g, r, True)}

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.game = shard_game(Path(cls.tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_shards_are_the_sequence(self):
        types = [GMD, typemap.BY_EXT["lot"], SBC]
        for kwargs in ({}, {"unique": False}, {"limit_per_type": 4}):
            whole = [(r.arc, r.name, r.type_id, r.data) for r in corpus.resources(self.game, types, **kwargs)]
            self.assertGreater(len(whole), 10)
            for n in (1, 2, 3, 5, 64):
                with self.subTest(n=n, **kwargs):
                    parts = [[(r.arc, r.name, r.type_id, r.data)
                              for r in corpus.resources(self.game, types, shard=(i, n), **kwargs)] for i in range(n)]
                    self.assertEqual(parts, [whole[i::n] for i in range(n)])

    def test_merged_shards_are_one_process(self):
        for name, check in self.CHECKS.items():
            whole: dict = {}
            verdict = check(self.game, whole)
            self.assertFalse(verdict)                                      # the broken file of each kind
            self.assertGreater(len(whole[name]["failures"]), 0)
            for n in (2, 3, 5, 40):
                with self.subTest(check=name, shards=n):
                    sections, verdicts = [], []
                    for i in range(n):
                        rep: dict = {}
                        with mock.patch.object(check_corpus, "SHARD", (i, n)):
                            verdicts.append(check(self.game, rep))
                        sections.append(rep[name])
                    merged: dict = {}
                    merged_verdict = check_corpus.merge_shards(self.game, name, sections, merged)
                    self.assertEqual(merged_verdict, verdict)
                    self.assertEqual(stable(merged[name]), stable(whole[name]))
                    self.assertEqual(merged[name]["shards"], n)
                    self.assertEqual(list(merged[name]), list(whole[name]) + ["shards"])      # the same fields

    def test_merged_shards_of_a_clean_game_pass(self):
        with tempfile.TemporaryDirectory() as d:
            game = stand_in(Path(d), live={"rom/a": [entry(b"message\\a", GMD, text("a"))],
                                           "rom/b": [entry(b"message\\b", GMD, text("b"))]})
            sections = []
            for i in range(4):                                             # two of the four shards hold nothing
                rep: dict = {}
                with mock.patch.object(check_corpus, "SHARD", (i, 4)):
                    check_corpus.check_gmd(game, rep)
                sections.append(rep["gmd"])
            merged: dict = {}
            self.assertTrue(check_corpus.merge_shards(game, "gmd", sections, merged))
            self.assertEqual(merged["gmd"]["byte_exact"], 2)
            self.assertNotIn("none_found", merged["gmd"])
            empty = stand_in(Path(d), "ddo")                               # nothing at all: still a failure
            merged = {}
            with mock.patch.object(check_corpus, "SHARD", (0, 2)):
                part = {}
                check_corpus.check_gmd(empty, part)
            self.assertFalse(check_corpus.merge_shards(empty, "gmd", [part["gmd"], part["gmd"]], merged))
            self.assertIn("none_found", merged["gmd"])

    def test_plan_covers_every_check_once(self):
        only, parts = check_corpus.parse_only("xfs,yaml,gmd,itl,effect,fsm")
        tasks = check_corpus._plan(only, parts, "xfs,yaml,gmd,itl,effect,fsm")
        by = {}
        for name, i, n, child in tasks:
            by.setdefault(name, []).append((i, n, child))
        self.assertEqual(sorted(by), ["effect", "fsm", "gmd", "itl", "xfs"])
        for name, runs in by.items():
            self.assertEqual([i for i, _, _ in runs].count(0), 1)
            self.assertEqual(sorted(i for i, _, _ in runs), list(range(len(runs))))
            self.assertEqual({n for _, n, _ in runs}, {len(runs)})
        self.assertEqual({c for _, _, c in by["xfs"]}, {"xfs,yaml"})
        self.assertEqual({c for _, _, c in by["effect"]}, {"effect"})
        self.assertEqual(len(by["xfs"]), check_corpus.SHARDS["xfs"])
        self.assertEqual(check_corpus._plan(*check_corpus.parse_only("yaml"), "yaml")[0][3], "xfs,yaml")

    def test_parallel_processes_write_the_report_of_one(self):
        args = ("--game", str(self.game.root), "--only", "xfs,yaml,gmd,lot,sbc,arcs,effect")
        code1, one = run_main(*args, "--jobs", "1")
        code4, four = run_main(*args, "--jobs", "4")
        self.assertEqual((code1, code4), (1, 1))
        self.assertEqual(list(four), list(one))                            # the same sections, in the same order
        for key, value in one.items():
            with self.subTest(key=key):
                if isinstance(value, dict):
                    self.assertEqual(stable(four[key]), stable(value))
                elif key != "utc":
                    self.assertEqual(four[key], value)
        for name in ("xfs", "gmd", "lot", "sbc"):
            self.assertGreater(four[name]["shards"], 1)
            self.assertEqual(len(four[name]["failures"]), len(one[name]["failures"]))
        code, rep = run_main("--game", str(self.game.root), "--only", "gmd,lot", "--jobs", "4",
                             "--shard", "1/4")                              # one part, by hand: it runs in this process
        self.assertEqual(set(rep["verdicts"]), {"gmd", "lot"})
        self.assertEqual(code, 0 if all(rep["verdicts"].values()) else 1)
        self.assertNotIn("shards", rep["gmd"])
        self.assertEqual(run_main("--game", str(self.game.root), "--only", "gmd", "--shard", "4/4")[0], 2)
        self.assertEqual(run_main("--game", str(self.game.root), "--only", "gmd", "--shard", "x")[0], 2)

    def test_parallel_clean_and_empty_games(self):
        with tempfile.TemporaryDirectory() as d:
            game = stand_in(Path(d), live={"rom/a": [entry(b"message\\a", GMD, text("a"))]})
            code, rep = run_main("--game", str(game.root), "--only", "gmd,ndp", "--jobs", "4")
            self.assertEqual(code, 0)
            self.assertEqual(rep["gmd"]["byte_exact"], 1)
            self.assertEqual(sorted(rep["skipped"]), ["ndp"])
            code, rep = run_main("--game", str(game.root), "--only", "ndp", "--jobs", "4")
            self.assertEqual(code, 1)
            self.assertIn("nothing_checked", rep)
            code, rep = run_main("--game", str(stand_in(Path(d), "ddo").root), "--only", "gmd,lot", "--jobs", "4")
            self.assertEqual(code, 1)
            self.assertIn("none_found", rep["gmd"])
            self.assertIn("none_found", rep["lot"])

    def test_a_shard_that_dies_fails_the_run(self):
        with mock.patch.object(check_corpus, "_run_task",
                               lambda task, game, tmp: {"task": task, "report": None, "rc": 3, "tail": "boom"}):
            code, rep = run_main("--game", str(self.game.root), "--only", "gmd", "--jobs", "4")
        self.assertEqual(code, 1)
        self.assertFalse(rep["ok"])
        self.assertEqual(rep["crashed"]["gmd"][0]["rc"], 3)


def order_game(base: Path, kind: str = "ddda") -> Game:
    """A stand-in whose resources are in an order the shards cannot reproduce by themselves: four XFS classes,
    five languages and three layout classes met in an order that differs from the order each shard meets them in,
    classes of layout record that tie in size, and a broken file of each kind among them."""
    types = corpus.xfs_type_ids()[:4]
    xfs_class = [2, 0, 1, 0, 3, 2, 1, 3, 0, 2, 1, 3]
    language = [4, 1, 2, 1, 0, 3, 2, 4, 5, 0, 1, 7]
    record = [47, 4, 53, 47, 53, 4, 47, 53, 4, 4, 47, 53]
    made = []
    for i, (t, lang, kind_of_record) in enumerate(zip(xfs_class, language, record)):
        x = helpers.sample_xfs()
        x.root.fields[6] = [-30000 + i]
        made.append(entry(b"param\\p%d" % i, types[t], xfs.build(x), kind))
        made.append(entry(b"message\\m%d" % i, GMD, gmd.build(gmd.Gmd(lang, "TextWeb", [gmd.Message(f"line {i}", "0")])),
                          kind))
        made.append(entry(b"scr\\l%d" % i, typemap.BY_EXT["lot"], lot.build(lot.Lot([lot.blank(kind_of_record, 0)])), kind))
        if i % 4 == 1:
            made.append(entry(b"param\\bad%d" % i, types[0], b"XFS?" + b"\0" * (40 + i), kind))
            made.append(entry(b"message\\bad%d" % i, GMD, b"not a message file %d" % i, kind))
            made.append(entry(b"scr\\bad%d" % i, typemap.BY_EXT["lot"], b"\0" * (40 + i), kind))
    return stand_in(base, kind, live={"rom/a": made})


class ShardOrderTest(unittest.TestCase):
    """What a process keeps in the order it meets things -- the keys of a tally, the failures -- the merged shards
    put in the same order, so the report is the one a single process writes down to its text."""

    CHECKS = {"xfs": (lambda g, r: check_corpus.check_xfs(g, r, True), "types"),
              "gmd": (check_corpus.check_gmd, "languages"),
              "lot": (check_corpus.check_lot, "records_by_class")}

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.game = order_game(Path(cls.tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def shards(self, game, check, n):
        out = []
        for i in range(n):
            rep: dict = {}
            with mock.patch.object(check_corpus, "SHARD", (i, n)):
                check(game, rep)
            out.append(rep)
        return out

    def test_the_keys_of_a_tally_and_the_failures_are_in_the_one_process_order(self):
        for name, (check, field) in self.CHECKS.items():
            whole: dict = {}
            check(self.game, whole)
            self.assertGreater(len(whole[name][field]), 2)
            self.assertEqual(len(whole[name]["failures"]), 3)
            differs = 0
            for n in (2, 3, 4, 5, 7):
                with self.subTest(check=name, shards=n):
                    sections = [rep[name] for rep in self.shards(self.game, check, n)]
                    merged: dict = {}
                    check_corpus.merge_shards(self.game, name, sections, merged)
                    self.assertEqual(stable(merged[name]), stable(whole[name]))
                    self.assertNotIn("first_seen", merged[name])
                    self.assertFalse([f for f in merged[name]["failures"] if "at" in f])
                    naive = list(dict.fromkeys(k for s in sections for k in s[field]))     # the shards one after another
                    differs += naive != list(whole[name][field])
            self.assertGreaterEqual(differs, 1, f"{name}: the shards alone would have met the keys in the same order")

    def test_the_failures_kept_are_the_first_ones_of_the_pass(self):
        made = [entry(b"message\\m%d" % i, GMD, text(f"line {i}")) for i in range(0, 30, 3)]
        for i in range(90):
            made.insert(i + i // 3, entry(b"message\\bad%02d" % i, GMD, b"not a message file %d" % i))
        with tempfile.TemporaryDirectory() as d:
            game = stand_in(Path(d), live={"rom/a": made})
            whole: dict = {}
            check_corpus.check_gmd(game, whole)
            self.assertEqual(len(whole["gmd"]["failures"]), 40)                  # as many as one process keeps
            self.assertEqual(whole["gmd"]["failed"], 90)
            for n in (2, 3, 7):
                with self.subTest(shards=n):
                    sections = [rep["gmd"] for rep in self.shards(game, check_corpus.check_gmd, n)]
                    merged: dict = {}
                    check_corpus.merge_shards(game, "gmd", sections, merged)
                    self.assertEqual(stable(merged["gmd"]), stable(whole["gmd"]))
                    self.assertEqual([f["resource"] for f in merged["gmd"]["failures"]],
                                     [f["resource"] for f in whole["gmd"]["failures"]])

    def test_counters_exist_from_their_first_increment(self):
        """A counter a shard met is put where one process would have met it, not after the ones shard 0 has."""
        def part(counts: dict, **seen):
            return {"distinct_resources": 3, "strings": 0, **counts, "failures": [], "types": {}, "seconds": 0.0,
                    "first_seen": {"counts": seen, "types": {}}}
        a = part({"xfs_exact": 2, "failed": 1}, xfs_exact=[1, 1, 0], failed=[1, 7, 1])
        b = part({"yaml_exact": 1, "xfs_exact": 1}, yaml_exact=[1, 2, 0], xfs_exact=[1, 2, 1])
        merged: dict = {}
        with tempfile.TemporaryDirectory() as d:
            game = stand_in(Path(d))
            check_corpus.merge_shards(game, "xfs", [a, b], merged)
        self.assertEqual(list(merged["xfs"]), ["distinct_resources", "strings", "xfs_exact", "yaml_exact", "failed",
                                               "failures", "types", "seconds", "shards"])
        self.assertEqual({k: merged["xfs"][k] for k in ("xfs_exact", "yaml_exact", "failed")},
                         {"xfs_exact": 3, "yaml_exact": 1, "failed": 1})


class ShardWalkTest(unittest.TestCase):
    """The shards of one check add up only if they walked one sequence of resources: a game that changes while the
    run is going (an install, an update), or parts run by hand at different times, are not one game."""

    def clean(self, base: Path) -> Game:
        return stand_in(base, live={"rom/a": [entry(b"message\\m%d" % i, GMD, text(f"line {i}")) for i in range(6)]})

    def test_shards_of_a_game_that_changed_between_them_fail_the_run(self):
        real = check_corpus._run_task
        early = [threading.Event(), threading.Event()]
        lock, changed = threading.Lock(), []

        def run(task, game, tmp):
            name, i, n, _ = task
            if name != "gmd":
                return real(task, game, tmp)
            if i >= 2:                                     # these start after shards 0 and 1 have walked the game
                for e in early:
                    self.assertTrue(e.wait(120))
                with lock:
                    if not changed:
                        changed.append(arc.Archive([entry(b"message\\late", GMD, text("late"))]).build())
                        (game.native / "rom" / "b.arc").write_bytes(changed[0])
            done = real(task, game, tmp)
            if i < 2:
                early[i].set()
            return done

        with tempfile.TemporaryDirectory() as d:
            game = self.clean(Path(d))
            with mock.patch.object(check_corpus, "_run_task", run):
                code, rep = run_main("--game", str(game.root), "--only", "gmd", "--jobs", "4")
            self.assertEqual(code, 1)
            self.assertFalse(rep["ok"])
            self.assertEqual(len(changed), 1)
            self.assertIn("did not walk the same resources", rep["shard_mismatch"]["gmd"])
            self.assertNotIn("gmd", rep)                   # counts of two different games are not added up
            code, rep = run_main("--game", str(game.root), "--only", "gmd", "--jobs", "4")      # the game as it is now
            self.assertEqual(code, 0)
            self.assertNotIn("shard_mismatch", rep)
            self.assertEqual(rep["gmd"]["byte_exact"], 7)

    def test_what_a_shard_says_it_walked(self):
        def run(i, walks, n=3):
            return {"task": ("gmd", i, n, "gmd"), "report": {"walks": {"gmd": walks}} if walks is not None else {}}
        walk = [{"places": 6, "sha256": "a" * 64}]
        self.assertIsNone(check_corpus._walk_mismatch([run(i, walk) for i in range(3)]))
        self.assertIsNone(check_corpus._walk_mismatch([run(i, []) for i in range(3)]))      # a check that read nothing
        self.assertIn("did not say", check_corpus._walk_mismatch([run(0, walk), run(1, None), run(2, walk)]))
        self.assertIn("made 2 passes", check_corpus._walk_mismatch([run(0, walk), run(1, walk * 2), run(2, walk)]))
        more = [{"places": 7, "sha256": "b" * 64}]
        why = check_corpus._walk_mismatch([run(0, walk), run(1, walk), run(2, more)])
        self.assertIn("shard 2/3 and shard 0/3", why)
        self.assertIn("7 places against 6", why)
        same_count = [{"places": 6, "sha256": "c" * 64}]
        self.assertIn("did not walk the same", check_corpus._walk_mismatch([run(0, walk), run(1, same_count), run(2, walk)]))

    def test_every_shard_reports_the_sequence_it_walked(self):
        with tempfile.TemporaryDirectory() as d:
            game = self.clean(Path(d))
            reps = []
            for i in range(3):
                code, rep = run_main("--game", str(game.root), "--only", "gmd,ndp", "--shard", f"{i}/3")
                reps.append(rep)
            self.assertEqual([r["walks"]["gmd"] for r in reps], [reps[0]["walks"]["gmd"]] * 3)
            self.assertEqual(reps[0]["walks"]["gmd"][0]["places"], 6)
            self.assertEqual(reps[0]["walks"]["ndp"], [])                         # skipped: nothing walked
            ref = corpus.Walk()
            list(corpus.resources(game, [GMD], walk=ref))
            self.assertEqual(reps[0]["walks"]["gmd"], [{"places": ref.places, "sha256": ref.digest}])


if __name__ == "__main__":
    unittest.main()
