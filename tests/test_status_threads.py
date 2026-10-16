"""install.status hashes the install's files a few at a time (``install.hash_each``): the drift it reports is what
one file after another reports, in the same order, from the same digests and the same cache, and an error is the one a
single pass raises.  The reference below is status as it was, one file after another."""
from __future__ import annotations

import hashlib
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401 (sys.path)
import world_fixture
from riftstone import install
from riftstone.errors import RiftError


def serial_status(game) -> dict:
    """``install.status`` before it hashed side by side."""
    state = install.load_state(game)
    drift = []
    mode = state.get("mode", install.mode_for(game))
    for arc_name, entry in state.get("archives", {}).items():
        t = install._target(game, arc_name, mode)
        if not t.is_file() or install.sha256_cached(t) != entry["sha256"]:
            drift.append(arc_name)
    server = state.get("server", {})
    if server:
        assets = Path(state.get("server_assets", ""))
        for rel, entry in server.items():
            t = install._server_target(assets, rel)
            if not t.is_file() or install.sha256_file(t) != entry["sha256"]:
                drift.append("server/" + rel)
    loose = state.get("loose", {})
    for rel, entry in loose.items():
        t = install._loose_target(game, rel)
        if not t.is_file() or install.sha256_cached(t) != entry["sha256"]:
            drift.append("loose/" + rel)
    return {"mode": mode, "loader": game.loader_installed(), "mods": state.get("mods", []),
            "archives": sorted(state.get("archives", {})), "drift": drift,
            "server": sorted(server), "server_assets": state.get("server_assets"), "loose": sorted(loose)}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class StatusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        env = mock.patch.dict(os.environ, {"RIFTSTONE_HOME": str(self.base / "home")})
        env.start()
        self.addCleanup(env.stop)
        self.game = world_fixture.make(self.base / "game")
        self.assets = self.base / "assets"
        install._HASH_CACHE.clear()
        self.addCleanup(install._HASH_CACHE.clear)

    def write(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def record(self, archives=None, server=None, loose=None, mode="direct") -> None:
        state = {"schema": install.STATE_SCHEMA, "mods": [], "mode": mode, "archives": archives or {}}
        if server is not None:
            state["server"], state["server_assets"] = server, str(self.assets)
        if loose is not None:
            state["loose"] = loose
        install.save_state(self.game, state)

    def populate(self, n: int = 30):
        """n archives, server files and loose files, a third of each as recorded, a third changed, a third gone."""
        archives, server, loose = {}, {}, {}
        for i in range(n):
            data = f"payload {i} ".encode() * (50 + i)
            kind = i % 3
            name = f"rom/extra/a{i:02d}"
            if kind != 2:
                self.write(self.game.arc_path(name), data + (b"!" if kind == 1 else b""))
            archives[name] = {"sha256": digest(data)}
            rel = f"s/f{i:02d}.bin"
            if kind != 2:
                self.write(self.assets / rel, data + (b"?" if kind == 1 else b""))
            server[rel] = {"sha256": digest(data)}
            lrel = f"compat/l{i:02d}.lmt"
            if kind != 2:
                self.write(self.game.overlay_dir / lrel, data + (b"." if kind == 1 else b""))
            loose[lrel] = {"sha256": digest(data)}
        return archives, server, loose

    def test_the_drift_is_what_one_file_after_another_finds(self):
        archives, server, loose = self.populate()
        self.record(archives, server, loose)
        want = serial_status(self.game)
        install._HASH_CACHE.clear()
        got = install.status(self.game)
        self.assertEqual(got, want)
        self.assertEqual(len(want["drift"]), 3 * 20)          # of archives, server files and loose files: 10 changed, 10 gone
        self.assertEqual(want["drift"][:2], ["rom/extra/a01", "rom/extra/a02"])         # archives, in state order
        self.assertTrue(want["drift"][20].startswith("server/"))
        self.assertTrue(want["drift"][-1].startswith("loose/"))

    def test_it_hashes_the_same_files_the_same_number_of_times(self):
        """The cache is the same: an archive or loose file is hashed once while its size and time stay, a server file
        every time."""
        archives, server, loose = self.populate(12)
        self.record(archives, server, loose)
        real = install.sha256_file

        def counted(fn):
            seen = []

            def spy(path):
                seen.append(str(path))
                return real(path)
            with mock.patch.object(install, "sha256_file", spy):
                results = [fn(self.game), fn(self.game)]
            return results, sorted(seen)

        install._HASH_CACHE.clear()
        want, want_seen = counted(serial_status)
        install._HASH_CACHE.clear()
        got, got_seen = counted(install.status)
        self.assertEqual(got, want)
        self.assertEqual(got_seen, want_seen)
        self.assertGreater(len(want_seen), 12)

    def test_a_state_with_nothing_to_hash(self):
        self.record()
        self.assertEqual(install.status(self.game), serial_status(self.game))
        self.record({"rom/extra/only": {"sha256": digest(b"x")}})
        self.assertEqual(install.status(self.game)["drift"], ["rom/extra/only"])
        self.write(self.game.arc_path("rom/extra/only"), b"x")
        self.assertEqual(install.status(self.game)["drift"], [])

    def test_overlay_mode_reads_the_overlay_copies(self):
        data = b"overlay copy"
        self.write(self.game.overlay_dir / "rom/extra/o.arc", data)
        self.write(self.game.arc_path("rom/extra/o"), b"the live file is not the one that counts")
        self.record({"rom/extra/o": {"sha256": digest(data)}}, mode="overlay")
        self.assertEqual(install.status(self.game)["drift"], [])
        self.assertEqual(install.status(self.game), serial_status(self.game))

    def test_the_error_is_the_first_one_a_single_pass_raises(self):
        archives, server, loose = self.populate(12)
        archives["../first"] = {"sha256": digest(b"1")}
        archives["../second"] = {"sha256": digest(b"2")}
        server["../server"] = {"sha256": digest(b"3")}
        self.record(archives, server, loose)
        with self.assertRaises(RiftError) as old:
            serial_status(self.game)
        with self.assertRaises(RiftError) as new:
            install.status(self.game)
        self.assertEqual(str(new.exception), str(old.exception))
        self.assertIn("first", str(new.exception))

    def test_a_server_path_error_comes_after_the_archives_are_read(self):
        archives, server, loose = self.populate(6)
        server["a/../b"] = {"sha256": digest(b"3")}
        self.record(archives, server, loose)
        with self.assertRaisesRegex(RiftError, "bad server file path 'a/../b'"):
            install.status(self.game)

    def test_a_record_without_a_digest_fails_only_where_a_file_is_to_be_compared(self):
        self.record({"rom/extra/gone": {"sha256": digest(b"x")}}, loose={"compat/nothere.lmt": {}})
        self.assertEqual(install.status(self.game)["drift"], ["rom/extra/gone", "loose/compat/nothere.lmt"])
        self.write(self.game.overlay_dir / "compat/nothere.lmt", b"now it is there")
        with self.assertRaises(KeyError):
            serial_status(self.game)
        with self.assertRaises(KeyError):
            install.status(self.game)

    def test_a_record_without_a_digest_is_the_first_error_when_a_bad_path_comes_after_it(self):
        """The records are read in order: a file with no recorded digest raises KeyError before the path two records
        on is looked at (the paths are resolved on the worker threads, the records' digests are compared there too)."""
        self.write(self.game.overlay_dir / "compat/first.lmt", b"a file that is there")
        for loose in ({"compat/first.lmt": {}, "../bad": {"sha256": digest(b"5")}},
                      {"compat/first.lmt": {}, "../bad": {"sha256": digest(b"5")}, "compat/gone.lmt": {}}):
            self.record({}, loose=loose)
            with self.assertRaises(KeyError) as old:
                serial_status(self.game)
            with self.assertRaises(KeyError) as new:
                install.status(self.game)
            self.assertEqual(new.exception.args, old.exception.args)

    def test_the_first_record_in_order_raises_whatever_kind_of_error_it_is(self):
        """Mixed faults in one state: the error is the first record's, as one file after another would raise it."""
        self.write(self.game.overlay_dir / "compat/ok.lmt", b"fine")
        ok = {"sha256": digest(b"fine")}
        cases = [
            {"compat/ok.lmt": ok, "../bad": ok, "compat/x.lmt": {}},                   # path error, then a record fault
            {"compat/ok.lmt": {}, "../bad": ok},                                         # record fault, then path error
            {"compat/gone.lmt": {}, "compat/ok.lmt": ok, "../bad": ok},                  # a missing file never reads its record
        ]
        for loose in cases:
            self.record({}, loose=loose)
            try:
                want = repr(serial_status(self.game)["drift"])
            except Exception as e:  # noqa: BLE001
                want = (type(e), e.args)
            try:
                got = repr(install.status(self.game)["drift"])
            except Exception as e:  # noqa: BLE001
                got = (type(e), e.args)
            self.assertEqual(got, want, loose)


class HashEachTest(unittest.TestCase):
    def test_results_are_in_job_order(self):
        jobs = [lambda i=i: f"r{i}" for i in range(40)]
        self.assertEqual(install.hash_each(jobs), [f"r{i}" for i in range(40)])
        self.assertEqual(install.hash_each(iter(jobs)), [f"r{i}" for i in range(40)])

    def test_none_and_one_job(self):
        self.assertEqual(install.hash_each([]), [])
        self.assertEqual(install.hash_each([lambda: None]), [None])
        self.assertEqual(install.hash_each([lambda: "x"]), ["x"])

    def test_jobs_run_side_by_side(self):
        """Two jobs that each wait for the other pass only if they run at the same time."""
        meet = threading.Barrier(2, timeout=20)

        def job():
            meet.wait()
            return "met"
        self.assertEqual(install.hash_each([job, job]), ["met", "met"])

    def test_at_most_the_thread_limit_run_at_once(self):
        lock, state = threading.Lock(), {"now": 0, "most": 0}

        def job():
            with lock:
                state["now"] += 1
                state["most"] = max(state["most"], state["now"])
            threading.Event().wait(0.02)
            with lock:
                state["now"] -= 1
            return "x"
        install.hash_each([job] * 40)
        self.assertLessEqual(state["most"], install.HASH_THREADS)
        self.assertGreater(state["most"], 1)

    def test_the_error_is_the_first_in_job_order_not_the_first_in_time(self):
        late = threading.Event()

        def slow_first():
            late.wait(5)
            raise ValueError("job 1")

        def fast_second():
            try:
                raise KeyError("job 2")
            finally:
                late.set()
        with self.assertRaisesRegex(ValueError, "job 1"):
            install.hash_each([lambda: "ok", slow_first, fast_second])


if __name__ == "__main__":
    unittest.main()
