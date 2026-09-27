"""The fuzzer's own invariants and plumbing (fuzz/targets.py, fuzz/run.py), driven so that a check that cannot fail
shows up: the motion-list port and terrain targets against a lossy port and a move back that misses, what the port
target counts as a refusal, the fsmap target's type selector and its cached seeds, which scratch folders a run
removes, and the filesystem targets' "nothing outside the mod" invariants."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import pickle
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401 (sys.path)
from riftstone import lmt, port, sbc, typemap

from test_gate import ROOT, X, lossy, nudging

if str(ROOT / "fuzz") not in sys.path:
    sys.path.append(str(ROOT / "fuzz"))

import targets  # noqa: E402

_spec = importlib.util.spec_from_file_location("riftstone_fuzz_run", ROOT / "fuzz" / "run.py")
fuzz_run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fuzz_run)


class PortLmtTargetTest(unittest.TestCase):
    """The port_lmt target counts motion slots and tracks before comparing them pair by pair."""

    def test_a_lossy_port_is_a_finding(self):
        import test_lmt

        case = bytes([0]) + lmt.build(test_lmt.sample(66, share_tracks=False))
        targets.t_port_lmt(case)
        with mock.patch.object(port, "convert_lmt", lossy(port.convert_lmt)), self.assertRaises(AssertionError):
            targets.t_port_lmt(case)


class TerrainTargetTest(unittest.TestCase):
    """The terrain target compares what localize gives back with the original, not only with a second move."""

    def test_a_move_back_that_misses_is_a_finding(self):
        mesh = helpers.cell_collision(points=[(X, 5000.0, 200.0), (9800.0, 5100.0, 300.0), (400.0, 5200.0, 9700.0),
                                              (9900.0, 4900.0, 9900.0)])
        targets.t_terrain(mesh)
        with mock.patch.object(sbc, "translate", nudging(sbc.translate)), self.assertRaises(AssertionError):
            targets.t_terrain(mesh)


class PortTargetTest(unittest.TestCase):
    def test_only_a_refusal_is_allowed(self):
        def crash(*a, **k):
            raise ValueError("a bug in the converter")
        with mock.patch.object(port, "convert", crash), self.assertRaises(ValueError):
            targets.t_port(bytes([0]) + b"TEX\0" + bytes(60))


class FsmapTest(unittest.TestCase):
    def test_every_type_is_reached(self):
        ids = sorted(typemap.BY_ID)
        self.assertGreater(len(ids), 256)
        self.assertEqual({targets.fsmap_type(struct.pack("<H", i))[0] for i in range(1 << 16)}, set(ids))
        seen = []
        real = targets.fsmap.encode_name
        with mock.patch.object(targets.fsmap, "encode_name", lambda n, t: (seen.append(t), real(n, t))[1]):
            for tid in ids:
                case = targets.fsmap_seed(tid, b"model\\x")
                self.assertEqual(targets.fsmap_type(case), (tid, b"model\\x"))
                seen.clear()
                with contextlib.suppress(targets.RiftError):          # the whole input read as a user path
                    targets.t_fsmap(case)
                self.assertEqual(seen[0], tid)

    def test_old_seeds_are_replaced(self):
        """An fsmap seed cached with the one-byte selector would pick another type now: the cache gets new ones."""
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d) / "seeds.pkl"
            cache.write_bytes(pickle.dumps({"fsmap": [b"\x05model\\em\\e01 "], "yaml": [b"a: 1"]}))
            with mock.patch.object(fuzz_run, "SEEDS", cache), mock.patch.object(fuzz_run, "_seed_game", lambda: None), \
                    mock.patch.object(fuzz_run, "_synthetic", lambda: {"fsmap": fuzz_run._fsmap_seeds()}):
                seeds = fuzz_run.load_seeds(False)
                again = pickle.loads(cache.read_bytes())
        self.assertEqual(seeds["fsmap"], fuzz_run._fsmap_seeds())
        self.assertEqual(again[fuzz_run.SEED_FORMATS_KEY], fuzz_run.SEED_FORMATS)
        self.assertIn(b"a: 1", seeds["yaml"])
        self.assertEqual(targets.fsmap_type(seeds["fsmap"][0])[0], sorted(typemap.BY_ID)[5])


class SweepTest(unittest.TestCase):
    """--tmp may be a shared folder: only the fuzzer's own idle scratch folders are removed."""

    def test_only_the_fuzzers_own_leftovers_go(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            other = root / "run-someone-elses"
            (other / "work").mkdir(parents=True)
            fake = root / (fuzz_run.SCRATCH_PREFIX + "no-marker")
            fake.mkdir()
            old = Path(fuzz_run.scratch(root))
            fresh = Path(fuzz_run.scratch(root))
            then = time.time() - 7200
            for p in (other, fake, old):
                os.utime(p, (then, then))
            fuzz_run.sweep_stale(root)
            self.assertEqual((other.exists(), fake.exists(), old.exists(), fresh.exists()), (True, True, False, True))

    def test_the_fuzzers_own_folder_needs_no_marker(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(fuzz_run, "HERE", Path(d)):
            root = Path(d) / ".tmp"
            unmarked, legacy, other = (root / (fuzz_run.SCRATCH_PREFIX + "x"), root / "run-x", root / "keep-me")
            for p in (unmarked, legacy, other):
                p.mkdir(parents=True)
                os.utime(p, (time.time() - 7200,) * 2)
            fuzz_run.sweep_stale(root)
            self.assertEqual((unmarked.exists(), legacy.exists(), other.exists()), (False, False, True))

    @unittest.skipUnless(os.name == "nt", "a file held open blocks its removal on Windows only")
    def test_a_folder_that_cannot_be_emptied_keeps_its_marker(self):
        """A file still open (a finished target's index database) stops the removal; the marker stays, so the
        next run's sweep knows the folder (it once went first, leaving a folder nothing would ever remove)."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            tmp = Path(fuzz_run.scratch(root))
            (tmp / "case").mkdir()
            held = open(tmp / "case" / "index.sqlite", "wb")
            try:
                with mock.patch.object(fuzz_run.time, "sleep", lambda s: None):
                    fuzz_run._rmtree(str(tmp))
                self.assertTrue((tmp / fuzz_run.SCRATCH_MARK).is_file())
            finally:
                held.close()
            os.utime(tmp, (time.time() - 7200,) * 2)
            fuzz_run.sweep_stale(root)
            self.assertFalse(tmp.exists())

    def test_a_replay_leaves_no_scratch(self):
        """A replay runs the targets in its own process, whose stand-in games keep their index databases open in
        the scratch folder: it closes them first, so the folder goes."""
        cases = {"author-crash-a.bin": b'{"op": "item", "args": ["Rift"]}',
                 "encounter-crash-b.bin": b'{"stage": "424", "enemy": "goblin", "count": 3, "at": "0,-350,-8800"}'}
        targets.close_caches()
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ), \
                contextlib.redirect_stdout(io.StringIO()):
            findings, root = Path(d) / "findings", Path(d) / "tmp"
            findings.mkdir()
            root.mkdir()
            for name, data in cases.items():
                (findings / name).write_bytes(data)
            with mock.patch.object(fuzz_run, "FINDINGS", findings):
                self.assertEqual(fuzz_run.replay(root), 0)
            self.assertEqual(list(root.iterdir()), [])


class OutsideTest(unittest.TestCase):
    """The filesystem targets' "only inside the mod" invariants see a write anywhere else in their stand-in
    (its game's archives, home), not just a new name at the top."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.env = mock.patch.dict(os.environ, {"RIFTSTONE_FUZZ_TMP": cls.tmp.name})
        cls.env.start()
        cls.saved = (targets._AUTHOR, targets._WORLD, targets._STUDIO)
        targets._AUTHOR = targets._WORLD = targets._STUDIO = None

    @classmethod
    def tearDownClass(cls):
        targets.close_caches()
        targets._AUTHOR, targets._WORLD, targets._STUDIO = cls.saved
        cls.env.stop()
        cls.tmp.cleanup()

    @staticmethod
    def stray(path: Path) -> None:
        with open(path, "ab") as fh:
            fh.write(b"stray")

    def test_author(self):
        from riftstone import items

        case = b'{"op": "new", "args": ["Rift Tonic", "greenwarish", "Heals.", null, 500, null]}'
        targets.t_author(case)
        game = targets._author()[0]
        real = items.new

        def new(g, *a, **k):
            out = real(g, *a, **k)
            self.stray(game.root / "nativePC" / "rom" / "game.arc")
            return out
        with mock.patch.object(items, "new", new), self.assertRaisesRegex(AssertionError, "outside the mod"):
            targets.t_author(case)

    def test_encounter(self):
        from riftstone import encounter

        case = b'{"stage": "424", "enemy": "goblin", "count": 3, "at": "0,-350,-8800"}'
        targets.t_encounter(case)
        base = targets._world_game()[3]
        real = encounter.write

        def write(enc, root):
            out = real(enc, root)
            self.stray(base / "home" / "stray.txt")
            return out
        with mock.patch.object(encounter, "write", write), self.assertRaisesRegex(AssertionError, "outside the mod"):
            targets.t_encounter(case)

    def test_plan(self):
        from riftstone import encounter_plan

        entries = encounter_plan.parse(json.dumps({"format": "riftstone-encounters/1", "game": "ddda", "encounters": [
            {"stage": 424, "enemy": "goblin", "total": 20, "at": "group:0"}]}))
        targets._plan_dry_and_real(entries)
        real = encounter_plan.apply

        def apply(game, *a, dry_run=False, **k):
            out = real(game, *a, dry_run=dry_run, **k)
            if not dry_run:
                self.stray(game.root / "nativePC" / "rom" / "stray.bin")
            return out
        with mock.patch.object(encounter_plan, "apply", apply), self.assertRaisesRegex(AssertionError, "outside"):
            targets._plan_dry_and_real(entries)

    def test_studio(self):
        for case in ({"route": "validate", "post": 1, "body": {"text": "x: 1"}}, {"route": "safe-mode", "post": 1},
                     {"route": "search", "q": {"q": "status"}}, {"route": "mods/new", "post": 1, "body": {"name": "B"}}):
            targets.t_studio(json.dumps(case).encode())
        s, base = targets._studio()
        real = s.api

        def api(*a):
            self.stray(base / "game" / "nativePC" / "rom" / "game_main.arc")
            return real(*a)
        with mock.patch.object(s, "api", api), self.assertRaisesRegex(AssertionError, "outside its workspace"):
            targets.t_studio(b'{"route": "validate", "post": 1, "body": {"text": "x: 1"}}')

    def test_studio_leaves_the_installed_games_alone(self):
        for route in ("launch", "switch", "port", "monsters", "monsters/convert", "plugins/toggle"):
            with self.subTest(route=route), self.assertRaises(targets.RiftError):
                targets.t_studio(json.dumps({"route": route, "post": 1, "body": {"kind": "ddda"}}).encode())


if __name__ == "__main__":
    unittest.main()
