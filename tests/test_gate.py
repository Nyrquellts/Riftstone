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
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401 (sys.path)
from riftstone import arc, arcref, corpus, ddo_params, ddo_solo, gmd, lmt, port, sbc, terrain, tex, typemap
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
    """check_corpus.main on these arguments: (exit code, the report it wrote, or None)."""
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "report.json"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                code = check_corpus.main([*args, "--out", str(out)])
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
        real = corpus.resources

        def spy(game, types=None, *a, **k):
            asked.setdefault(game.kind, set()).update(types or ())
            return real(game, types, *a, **k)
        with tempfile.TemporaryDirectory() as d, mock.patch.object(corpus, "resources", spy), \
                mock.patch.dict(os.environ, {"RIFTSTONE_DDO_ASSETS": ""}):
            for kind in ("ddda", "ddo"):
                game = stand_in(Path(d), kind)
                for name in check_corpus.CHECKS:
                    check_corpus.run_check(name, game, {})
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


if __name__ == "__main__":
    unittest.main()
