"""tools/run_unit.py, the unit step of the release gate: every tests/test_*.py module in a process of its own, several at
a time, with a scratch RIFTSTONE_HOME and a temp folder per module -- and the same tests as ``unittest discover``."""
from __future__ import annotations

import _thread
import contextlib
import io
import os
import signal
import sqlite3
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401 (sys.path)
import world_fixture
from riftstone import index

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import run_unit  # noqa: E402

PROBE = '''import os
import tempfile
import unittest
from pathlib import Path


class T(unittest.TestCase):
    def test_where_it_runs(self):
        folder = Path(os.environ["RUN_UNIT_PROBE"])
        (folder / "NAME.txt").write_text("\\n".join([os.environ["RIFTSTONE_HOME"], os.environ["TEMP"],
                                                    tempfile.gettempdir(), os.getcwd()]), encoding="utf-8")
'''
SLEEPER = '''import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

BEAT = """
import sys, time
end = time.time() + 40
while time.time() < end:
    with open(sys.argv[1], "a") as f:
        f.write("x")
    time.sleep(0.05)
"""


class T(unittest.TestCase):
    def test_waits(self):
        folder = Path(os.environ["RUN_UNIT_PROBE"])
        (folder / "NAME.started").write_text("started")
        subprocess.Popen([sys.executable, "-c", BEAT, str(folder / "NAME.beat")]).wait()
'''
GOOD = '''import unittest


class T(unittest.TestCase):
    def test_one(self):
        pass

    def test_two(self):
        pass

    @unittest.skip("not here")
    def test_three(self):
        pass
'''
BAD = '''import unittest


class T(unittest.TestCase):
    def test_fails(self):
        self.fail("this module fails on purpose")
'''


class RunUnitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name) / "tests"
        self.dir.mkdir()
        self.probe = Path(self.tmp.name) / "probe"
        self.probe.mkdir()

    def write(self, name: str, text: str) -> None:
        (self.dir / name).write_text(text, encoding="utf-8")

    def run_it(self, only=None, jobs=2, games=()):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"RUN_UNIT_PROBE": str(self.probe)}):
            rc = run_unit.run(self.dir, only=only, jobs=jobs, games=list(games), out=out)
        return rc, out.getvalue()

    def test_every_module_runs_and_the_totals_add_up(self):
        self.write("test_good.py", GOOD)
        self.write("test_probe_a.py", PROBE.replace("NAME", "a"))
        self.write("helpers.py", "")                       # not a test module: never run
        self.write("notes.txt", "")
        rc, said = self.run_it()
        self.assertEqual(rc, 0, said)
        self.assertIn("Ran 4 tests in 2 modules", said)
        self.assertIn("OK (skipped=1)", said)
        self.assertTrue((self.probe / "a.txt").is_file())
        for name in ("test_good", "test_probe_a"):
            self.assertIn(name, said)

    def test_a_failing_module_fails_the_run_and_its_own_report_is_shown(self):
        self.write("test_good.py", GOOD)
        self.write("test_bad.py", BAD)
        rc, said = self.run_it()
        self.assertEqual(rc, 1, said)
        self.assertIn("FAIL test_bad", said)
        self.assertIn("this module fails on purpose", said)           # the module's own traceback
        self.assertIn("FAILED (1 of 2 modules: test_bad)", said)
        self.assertIn("ok   test_good", said)
        self.assertNotIn("OK", said.split("FAILED")[-1])

    def test_a_module_that_does_not_import_fails_the_run(self):
        self.write("test_broken.py", "import no_such_module_anywhere\n")
        rc, said = self.run_it()
        self.assertEqual(rc, 1, said)
        self.assertIn("FAIL test_broken", said)

    def test_an_empty_module_is_what_discover_makes_of_it(self):
        """``unittest discover`` fails only a run of no test at all (exit 5): a module with none among others passes,
        and the run of nothing but such modules does not."""
        self.write("test_empty.py", "import unittest\n")
        self.write("test_good.py", GOOD)
        rc, said = self.run_it()
        self.assertEqual(rc, 0, said)
        self.assertIn("Ran 3 tests in 2 modules", said)
        rc, said = self.run_it(only=["test_empty"])
        self.assertEqual(rc, 5, said)
        self.assertIn("NO TESTS RAN", said)

    def test_a_name_that_is_not_a_module_is_refused(self):
        self.write("test_good.py", GOOD)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            rc, said = self.run_it(only=["test_good", "test_nope"])
        self.assertEqual(rc, 2)
        self.assertEqual(said, "")
        self.assertIn("not a test module", err.getvalue())
        self.assertIn("test_nope", err.getvalue())

    def test_modules_picked_by_name_run_alone(self):
        self.write("test_good.py", GOOD)
        self.write("test_bad.py", BAD)
        rc, said = self.run_it(only=["test_good"])
        self.assertEqual(rc, 0, said)
        self.assertIn("Ran 3 tests in 1 modules", said)

    def test_each_module_has_its_own_temp_folder_and_they_share_one_scratch_home(self):
        for n in ("a", "b", "c"):
            self.write(f"test_probe_{n}.py", PROBE.replace("NAME", n))
        real_home = os.environ.get("RIFTSTONE_HOME")
        rc, said = self.run_it(jobs=3)
        self.assertEqual(rc, 0, said)
        seen = {n: (self.probe / f"{n}.txt").read_text(encoding="utf-8").split("\n") for n in "abc"}
        homes = {s[0] for s in seen.values()}
        temps = [s[1] for s in seen.values()]
        self.assertEqual(len(homes), 1)
        self.assertEqual(len(set(temps)), 3)
        for home, temp, tempdir, cwd in seen.values():
            self.assertEqual(tempdir, temp)                                   # tempfile follows TEMP
            self.assertEqual(Path(cwd), self.dir.resolve())
            self.assertEqual(Path(temp).parent, Path(home).parent)              # all inside the one scratch scope
            self.assertFalse(Path(home).exists(), "the scratch scope is deleted at the end")
            # a path built under TEMP is 12 characters longer than under the system temp folder, at most (Windows
            # fails the paths of some tests past 260 characters, and ``unittest discover`` has no such addition)
            self.assertLessEqual(len(temp) - len(str(Path(tempfile.gettempdir()))), 12, temp)
            self.assertNotEqual(home, real_home)
        self.assertEqual(os.environ.get("RIFTSTONE_HOME"), real_home)         # the caller's environment is untouched

    def test_the_real_home_is_not_written(self):
        """The whole listing of the real home -- every name, size and time -- is the same after a run, with an
        installed game whose index is in it (reading a database in WAL mode makes a ``-shm`` and a ``-wal`` beside
        it unless they are taken away again)."""
        self.write("test_probe_a.py", PROBE.replace("NAME", "a"))
        real = Path(self.tmp.name) / "real-home"
        real.mkdir()
        (real / "world.bin").write_bytes(b"w")
        game = world_fixture.make(Path(self.tmp.name) / "game")

        def listing():
            return sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in real.iterdir())

        with mock.patch.dict(os.environ, {"RIFTSTONE_HOME": str(real)}):
            idx = index.Index(game)
            idx.refresh()
            idx.close()
            before = listing()
            self.assertIn(index.index_path(game).name, [name for name, _, _ in before])
            self.assertEqual(len(before), 2, before)              # the premise: no sidecar is there to start with
            rc, said = self.run_it(games=[game])
        self.assertEqual(rc, 0, said)
        self.assertEqual(listing(), before)

    def test_seed_home_copies_the_caches_and_snapshots_only_the_named_indexes(self):
        real, dest = Path(self.tmp.name) / "real", Path(self.tmp.name) / "dest"
        real.mkdir()
        (real / "world-ddda.bin").write_bytes(b"world")
        (real / "monsters.json").write_bytes(b"{}")
        for name in ("index-aaa.sqlite", "index-bbb.sqlite"):
            db = sqlite3.connect(real / name)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE t(x)")
            db.execute("INSERT INTO t VALUES (1)")
            db.commit()                      # in the write-ahead log: only a snapshot of the database sees it
            self.addCleanup(db.close)
        before = sorted(p.name for p in real.iterdir())
        run_unit.seed_home(real, dest, ["index-aaa.sqlite", "index-missing.sqlite"])
        self.assertEqual(sorted(p.name for p in real.iterdir()), before)       # the writers' files stay, none is added
        self.assertEqual(sorted(p.name for p in dest.iterdir() if p.suffix not in (".sqlite-wal", ".sqlite-shm")),
                         ["index-aaa.sqlite", "monsters.json", "world-ddda.bin"])
        self.assertEqual((dest / "world-ddda.bin").read_bytes(), b"world")
        copy = sqlite3.connect(dest / "index-aaa.sqlite")
        try:
            self.assertEqual(copy.execute("SELECT x FROM t").fetchall(), [(1,)])
        finally:
            copy.close()

    def test_seed_home_leaves_nothing_beside_an_index_nobody_has_open(self):
        real, dest = Path(self.tmp.name) / "real", Path(self.tmp.name) / "dest"
        real.mkdir()
        db = sqlite3.connect(real / "index-aaa.sqlite")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE t(x)")
        db.execute("INSERT INTO t VALUES (7)")
        db.commit()
        db.close()                                                         # the last connection: the sidecars go

        def listing():
            return sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in real.iterdir())

        before = listing()
        self.assertEqual([name for name, _, _ in before], ["index-aaa.sqlite"])
        run_unit.seed_home(real, dest, ["index-aaa.sqlite"])
        self.assertEqual(listing(), before)
        copy = sqlite3.connect(dest / "index-aaa.sqlite")
        try:
            self.assertEqual(copy.execute("SELECT x FROM t").fetchall(), [(7,)])
        finally:
            copy.close()

    def test_a_missing_real_home_seeds_an_empty_scratch_home(self):
        dest = Path(self.tmp.name) / "dest"
        run_unit.seed_home(Path(self.tmp.name) / "nowhere", dest, ["index-x.sqlite"])
        self.assertEqual(list(dest.iterdir()), [])

    def test_a_scope_has_a_short_name_nobody_else_has(self):
        folder = Path(self.tmp.name) / "systemp"
        folder.mkdir()
        with mock.patch.object(tempfile, "tempdir", str(folder)):
            a, b = run_unit.make_scope(), run_unit.make_scope()
            self.assertNotEqual(a, b)
            for scope in (a, b):
                self.assertTrue(scope.is_dir())
                self.assertEqual(scope.parent, folder)
                self.assertRegex(scope.name, r"^r[0-9a-f]{6}$")
            with mock.patch.object(run_unit.secrets, "token_hex", side_effect=["aaaaaa", "aaaaaa", "bbbbbb"]):
                first, second = run_unit.make_scope(), run_unit.make_scope()        # a name taken is not reused
            self.assertEqual((first.name, second.name), ("raaaaaa", "rbbbbbb"))

    def test_a_scope_that_cannot_be_removed_is_said_and_does_not_fail_the_run(self):
        self.write("test_good.py", GOOD)
        folder = Path(self.tmp.name) / "systemp"
        folder.mkdir()
        with mock.patch.object(tempfile, "tempdir", str(folder)), \
                mock.patch.object(run_unit, "remove_tree", return_value=["one", "two"]):
            rc, said = self.run_it()
        self.assertEqual(rc, 0, said)
        self.assertIn("2 paths could not be removed", said)
        self.assertIn("(the first: one)", said)

    @unittest.skipUnless(os.name == "nt", "run_unit stops the processes' trees with taskkill: Windows is what it runs on")
    def test_ctrl_c_stops_the_run_and_everything_it_started(self):
        for n in "abcdef":
            self.write(f"test_sleep_{n}.py", SLEEPER.replace("NAME", n))
        folder = Path(self.tmp.name) / "systemp"
        folder.mkdir()
        old = signal.signal(signal.SIGINT, signal.default_int_handler)
        self.addCleanup(signal.signal, signal.SIGINT, old)

        def two_have_started_then_ctrl_c():
            # both children beating, not only both modules started: a module writes .started before its child
            # starts, and an interrupt between the two left one .beat (seen under load, 2026-10-16)
            end = time.time() + 120
            while (len(list(self.probe.glob("*.started"))) < 2 or len(list(self.probe.glob("*.beat"))) < 2) \
                    and time.time() < end:
                time.sleep(0.05)
            _thread.interrupt_main()

        threading.Thread(target=two_have_started_then_ctrl_c, daemon=True).start()
        t0 = time.time()
        with mock.patch.object(tempfile, "tempdir", str(folder)):
            rc, said = self.run_it(jobs=2)
        took = time.time() - t0
        self.assertEqual(rc, 130, said)
        self.assertIn("INTERRUPTED", said)
        self.assertEqual(len(list(self.probe.glob("*.started"))), 2)      # the four still queued were never started
        self.assertLess(took, 30, "each module would have run 40 s")
        beats = {p.name: p.stat().st_size for p in self.probe.glob("*.beat")}
        self.assertEqual(len(beats), 2)
        time.sleep(0.6)
        self.assertEqual({p.name: p.stat().st_size for p in self.probe.glob("*.beat")}, beats)     # nothing still runs
        self.assertEqual(list(folder.iterdir()), [], "the scratch folder is removed")

    def test_test_files_below_the_tests_folder_are_refused_not_left_out(self):
        self.write("test_good.py", GOOD)
        (self.dir / "sub").mkdir()
        (self.dir / "sub" / "test_inner.py").write_text(GOOD, encoding="utf-8")
        with self.assertRaises(SystemExit) as raised:
            run_unit.modules(self.dir)
        self.assertIn("test_inner.py", str(raised.exception))

    def test_it_runs_the_tests_discover_loads(self):
        """The gate's step ran ``unittest discover -s tests -p "test_*.py"``: the modules are the same files, and
        loading each by name gives exactly the test ids discover finds."""
        def ids(suite):
            for t in suite:
                if isinstance(t, unittest.TestSuite):
                    yield from ids(t)
                else:
                    yield t.id()

        tests = ROOT / "tests"
        names = run_unit.modules(tests)
        loader = unittest.TestLoader()
        found = sorted(ids(loader.discover(str(tests), pattern="test_*.py", top_level_dir=str(tests))))
        by_name = sorted(i for n in names for i in ids(loader.loadTestsFromName(n)))
        self.assertGreater(len(found), 100)
        self.assertEqual(by_name, found)
        self.assertIn("test_run_unit", names)
        self.assertEqual(names, sorted(names))


class RemoveTreeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "scope"

    def test_a_tree_of_read_only_files_goes_as_git_leaves_one(self):
        objects = self.root / "repo" / ".git" / "objects" / "ab"
        objects.mkdir(parents=True)
        for n in range(3):
            (objects / f"obj{n}").write_bytes(b"x")
            os.chmod(objects / f"obj{n}", stat.S_IREAD)
        self.assertEqual(run_unit.remove_tree(self.root), [])
        self.assertFalse(self.root.exists())

    def test_a_folder_that_is_not_there_is_nothing_to_remove(self):
        self.assertEqual(run_unit.remove_tree(self.root), [])

    @unittest.skipUnless(os.name == "nt", "other systems delete a file that is open")
    def test_what_cannot_be_removed_is_named_not_raised(self):
        self.root.mkdir()
        held = open(self.root / "held.txt", "w", encoding="utf-8")
        self.addCleanup(held.close)
        (self.root / "free.txt").write_text("x", encoding="utf-8")
        with mock.patch.object(run_unit.time, "sleep"):
            left = run_unit.remove_tree(self.root)
        self.assertIn(str(self.root / "held.txt"), left)
        self.assertFalse((self.root / "free.txt").exists())               # the rest is gone
        held.close()
        self.assertEqual(run_unit.remove_tree(self.root), [])             # and the next try finishes it
        self.assertFalse(self.root.exists())


if __name__ == "__main__":
    unittest.main()
