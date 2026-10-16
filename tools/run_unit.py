"""The unit tests, as the release gate runs them: one process per test module, several at a time.

    python tools/run_unit.py [--jobs N] [test_module ...]

Every ``tests/test_*.py`` runs (the same files ``unittest discover -s tests -p "test_*.py"`` loads, found afresh each
time, so a new test file is in the gate without being listed anywhere); each module in its own process with
``python -B -m unittest``, so what it runs is what discover runs for it.  Exit 0 only when every module's
process did, and the totals say how many tests ran.

The processes share one scratch home, ``RIFTSTONE_HOME``, seeded from the real one (its caches of the installed
games: the resource index of each, the world and monster tables -- the tests that read the real installs need
them) and brought up to date once before the first process starts, and each has a temp folder of its own.  A
test that writes under ``RIFTSTONE_HOME`` (an index of a stand-in game, a mod's saves) writes into the shared
scratch home, which is deleted at the end: the real ``%LOCALAPPDATA%\\Riftstone`` is read, never written -- not even
a ``-shm`` is left beside an index that was read (``index.read_only``) -- and two modules never share a temp path.
The cache files the tests build there are each written whole by a writer with a file of its own
(``index.write_cache``), so two modules that build the same cache at once do not trip over one another.

The scratch folder and the temp folder of each module have short names (``<TEMP>\\r1a2b3c\\12``): the tests build
paths under TEMP, and a path longer than Windows' 260 characters fails some of them, so a run adds at most 12
characters to the length of the system temp folder a test would have seen under ``unittest discover``.

Ctrl-C stops the run: the test processes are stopped (each with the processes it started), no module still queued
is started, the scratch folder is removed and the exit status is 130.

The modules that scan the whole installed games (``HEAVY``) start first; the order is only a schedule.
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import re
import secrets
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
PATTERN = "test_*.py"
# The modules that read the installed games' whole corpora (the longest), started first.
HEAVY = ("test_effect", "test_msgset", "test_weather", "test_sound", "test_ocl_ddo", "test_ddodye", "test_camera",
         "test_ddo_params", "test_schedule", "test_gpl_ddo", "test_package", "test_research_tools", "test_waves")
NO_TESTS = 5                                               # unittest's exit status when it ran nothing (3.12+)
INTERRUPTED = 130                                          # the shell's status for a run that ended on Ctrl-C
MAX_JOBS = 12                                              # the heaviest modules already hold a core each
POLL = 0.25                                                # seconds between looks for Ctrl-C while modules run


def modules(tests: Path = TESTS) -> list[str]:
    """The test modules ``unittest discover`` loads from ``tests``: the files that match PATTERN and are named
    like a module, in name order.  A matching file in a sub-folder is refused (discover would load it from a
    package; this runner would not), so none is left out unseen."""
    valid = re.compile(r"[_a-z]\w*\.py$", re.IGNORECASE)
    found = sorted(p.name[:-3] for p in tests.iterdir()
                   if p.is_file() and fnmatch.fnmatch(p.name, PATTERN) and valid.match(p.name))
    nested = [p for p in tests.rglob(PATTERN) if p.parent != tests and p.is_file()]
    if nested:
        raise SystemExit(f"run_unit: test files below {tests} are not run by this runner: "
                         + ", ".join(str(p.relative_to(tests)) for p in nested))
    return found


def _index_module():
    """``riftstone.index``, from this checkout's ``src``."""
    src = str(ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from riftstone import index
    return index


def make_scope() -> Path:
    """A new folder for one run, ``r`` and six hex digits under the system temp folder: the scratch home is
    ``h`` in it and the temp folder of the module at position N is ``N``."""
    base = Path(tempfile.gettempdir())
    for _ in range(100):
        scope = base / ("r" + secrets.token_hex(3))
        try:
            scope.mkdir(mode=0o700)
            return scope
        except FileExistsError:
            continue
    raise OSError(f"run_unit: no unused folder name under {base}")


def remove_tree(path: Path) -> list[str]:
    """Delete ``path`` and everything in it; the paths that could not be deleted (none, usually).  A read-only file
    (git writes its objects that way, and Windows will not delete one) is made writable and tried again, and a file
    some process still has open is tried a few times more, as a scanner that is looking at it lets go in a moment."""
    left: list[str] = []

    def again(function, name, _error):
        try:
            os.chmod(name, stat.S_IWRITE | stat.S_IREAD)
            function(name)
        except OSError:
            left.append(str(name))

    for attempt in range(4):
        if not Path(path).exists():
            return []
        left.clear()
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=again)
        else:
            shutil.rmtree(path, onerror=again)
        if not left:
            return []
        time.sleep(0.25 * (attempt + 1))
    return left


def seed_home(real: Path, dest: Path, indexes: list[str]) -> None:
    """The scratch home: every cache file of the real one except the resource indexes (a few MB: the world and
    monster tables the real-install tests read), plus the indexes ``indexes`` names (file names under ``real``), each
    copied as one consistent snapshot of the database, its write-ahead log included.  The real home is only read:
    what the read of an index makes beside it, ``index.read_only`` takes away."""
    dest.mkdir(parents=True, exist_ok=True)
    if real.is_dir():
        for p in real.iterdir():
            if p.is_file() and not p.name.startswith("index-"):
                shutil.copy2(p, dest / p.name)
    for name in indexes:
        src = real / name
        if not src.is_file():
            continue
        with _index_module().read_only(src) as old:
            try:
                new = sqlite3.connect(dest / name)
                try:
                    old.backup(new)
                finally:
                    new.close()
            except sqlite3.Error:
                (dest / name).unlink(missing_ok=True)       # no index is better than half of one


def installed_games() -> list:
    """The installs the tests find (``find_game``), or none."""
    _index_module()
    from riftstone.errors import RiftError
    from riftstone.game import find_game

    games = []
    for which in ("ddda", "ddo"):
        try:
            games.append(find_game(which))
        except RiftError:
            pass
    return games


def prepare_home(dest: Path, games: list) -> None:
    """Seed ``dest`` from the real home and bring the scratch copy of each installed game's index up to date, so the
    first test to read an index finds it current and no two processes build one (the tests that call ``refresh`` find
    nothing to do)."""
    index = _index_module()

    seed_home(index.home(), dest, [index.index_path(g).name for g in games])
    kept = os.environ.get("RIFTSTONE_HOME")
    os.environ["RIFTSTONE_HOME"] = str(dest)
    try:
        for g in games:
            idx = index.Index(g)
            try:
                idx.refresh()
            finally:
                idx.db.close()
    finally:
        if kept is None:
            os.environ.pop("RIFTSTONE_HOME", None)
        else:
            os.environ["RIFTSTONE_HOME"] = kept


class Children:
    """The test processes that are running, so that Ctrl-C can stop them; once ``stop`` has been called no process
    is started."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.live: dict[int, subprocess.Popen] = {}
        self.stopping = False

    def start(self, args, **kw) -> subprocess.Popen | None:
        """The process, or None when the run is being stopped."""
        with self.lock:
            if self.stopping:
                return None
            proc = subprocess.Popen(args, **kw)
            self.live[proc.pid] = proc
            return proc

    def done(self, proc: subprocess.Popen) -> None:
        with self.lock:
            self.live.pop(proc.pid, None)

    def stop(self) -> None:
        """Stop every running process with the processes it started."""
        with self.lock:
            self.stopping = True
            procs = list(self.live.values())
        for proc in procs:
            kill_tree(proc)


def kill_tree(proc: subprocess.Popen) -> None:
    """``proc`` and what it started: a test module runs batch files, game tools, servers.  Windows has no process
    group to signal; ``taskkill /T`` walks the tree."""
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, check=False)
        except OSError:
            pass
    try:
        proc.kill()
    except OSError:
        pass


def run_module(name: str, tests: Path, home: Path, temp: Path, children: Children | None = None) -> dict:
    """One test module in its own process, in its own temp folder; its verdict and counts.  ``ok`` is false, and
    ``stopped`` true, for a module the run was stopped before or during."""
    children = children or Children()
    temp.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, RIFTSTONE_HOME=str(home), TEMP=str(temp), TMP=str(temp))
    t0 = time.time()
    proc = children.start([sys.executable, "-B", "-m", "unittest", "-q", name], cwd=tests, env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                          errors="replace")
    if proc is None:
        return {"module": name, "rc": None, "seconds": 0.0, "output": "", "ran": 0, "skipped": 0, "ok": False,
                "stopped": True}
    try:
        stdout, stderr = proc.communicate()
    finally:
        children.done(proc)
    out = stdout + stderr
    ran = re.search(r"^Ran (\d+) tests? in ", out, re.M)
    skipped = re.search(r"skipped=(\d+)", out.rsplit("Ran ", 1)[-1])
    return {"module": name, "rc": proc.returncode, "seconds": time.time() - t0, "output": out,
            "ran": int(ran[1]) if ran else 0, "skipped": int(skipped[1]) if skipped else 0,
            "ok": ran is not None and (proc.returncode == 0 or (proc.returncode == NO_TESTS and not int(ran[1]))),
            "stopped": children.stopping}


def _settle(children: Children, pool: ThreadPoolExecutor, scope: Path, out) -> None:
    """The end of a run, however it ended: the processes still running are stopped, the modules still queued are
    dropped, and the scratch folder goes -- with a word on what stays."""
    try:
        children.stop()
        pool.shutdown(wait=True, cancel_futures=True)
    except KeyboardInterrupt:                               # a second Ctrl-C: do not wait for the processes
        pool.shutdown(wait=False, cancel_futures=True)
    left = remove_tree(scope)
    if left:
        print(f"run_unit: {len(left)} paths could not be removed from {scope} (the first: {left[0]})",
              file=out, flush=True)


def run(tests: Path = TESTS, only: list[str] | None = None, jobs: int | None = None, games: list | None = None,
        out=None) -> int:
    """Run the modules (``only``, else all); 0 when every one passed, 130 when stopped by Ctrl-C."""
    out = out or sys.stdout
    names = modules(tests)
    if only:
        missing = [m for m in only if m not in names]
        if missing:
            print(f"run_unit: not a test module of {tests}: {', '.join(missing)}", file=sys.stderr)
            return 2
        names = [m for m in names if m in only]
    names.sort(key=lambda m: (m not in HEAVY, HEAVY.index(m) if m in HEAVY else 0))
    jobs = max(1, jobs or min(os.cpu_count() or 1, MAX_JOBS))
    t0 = time.time()
    scope = make_scope()
    children = Children()
    pool = ThreadPoolExecutor(max_workers=jobs)
    results: list[dict] = []
    interrupted = False
    try:
        prepare_home(scope / "h", installed_games() if games is None else games)
        futures = [pool.submit(run_module, m, tests, scope / "h", scope / str(i), children)
                   for i, m in enumerate(names)]
        for f in futures:
            while not wait([f], timeout=POLL)[0]:           # a wait with no limit cannot be interrupted on Windows
                pass
            r = f.result()
            results.append(r)
            print(f"{'ok  ' if r['ok'] else 'FAIL'} {r['module']:28} {r['ran']:5} tests {r['seconds']:7.1f} s",
                  file=out, flush=True)
    except KeyboardInterrupt:
        interrupted = True
    finally:
        _settle(children, pool, scope, out)
    if interrupted:
        print(f"\nINTERRUPTED: {len(results)} of {len(names)} modules had finished; the rest were stopped "
              f"({time.time() - t0:.1f}s)", file=out, flush=True)
        return INTERRUPTED
    bad = [r for r in results if not r["ok"]]
    for r in sorted(bad, key=lambda r: r["module"]):
        print(f"\n{'=' * 70}\n{r['module']} (exit {r['rc']})\n{'=' * 70}\n{r['output']}", file=out)
    ran = sum(r["ran"] for r in results)
    skipped = sum(r["skipped"] for r in results)
    print(f"\nRan {ran} tests in {len(results)} modules in {time.time() - t0:.1f}s ({jobs} at a time)", file=out)
    if bad:
        print(f"FAILED ({len(bad)} of {len(results)} modules: {', '.join(r['module'] for r in sorted(bad, key=lambda r: r['module']))})",
              file=out)
        return 1
    if not ran:                                     # discover's verdict too: a run of no test at all is not a pass
        print("NO TESTS RAN", file=out)
        return NO_TESTS
    print("OK" + (f" (skipped={skipped})" if skipped else ""), file=out)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("modules", nargs="*", help="test modules to run (default: all)")
    ap.add_argument("--jobs", type=int, help=f"processes at a time (default: one per CPU, at most {MAX_JOBS})")
    a = ap.parse_args(argv)
    return run(only=a.modules or None, jobs=a.jobs)


if __name__ == "__main__":
    raise SystemExit(main())
