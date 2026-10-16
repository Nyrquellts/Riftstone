"""The cache files under RIFTSTONE_HOME (the world map, the monster census, the flag census) are written whole or not
at all, by writers that do not share a file name: the unit gate runs its modules side by side in one scratch home, and
two processes that build the same cache at once must both finish with the cache in place."""
from __future__ import annotations

import gc
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401 (sys.path)
import world_fixture
from riftstone import index, monsters, waves, world
from riftstone.index import Index, write_cache

setUpModule, tearDownModule = helpers.module_env("RIFTSTONE_HOME")
SRC = Path(__file__).resolve().parents[1] / "src"


def left_over(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir() if p.name.endswith(".tmp"))


class WriteCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        gc.collect()
        self.tmp.cleanup()

    def test_writes_the_text_and_leaves_nothing_beside_it(self):
        path = self.dir / "sub" / "cache.json"
        write_cache(path, '{"a":1}')
        self.assertEqual(path.read_text(encoding="utf-8"), '{"a":1}')
        write_cache(path, '{"a":2,"é":"ü"}')                                       # replaces, whatever is there
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"a": 2, "é": "ü"})
        self.assertEqual(left_over(path.parent), [])

    def test_a_failed_write_leaves_the_old_cache_and_no_temp_file(self):
        path = self.dir / "cache.json"
        write_cache(path, "old")
        with mock.patch.object(os, "replace", side_effect=PermissionError("held open")), \
                mock.patch.object(time, "sleep"):
            write_cache(path, "new")                    # another writer's complete file stands: not an error
            self.assertEqual(path.read_text(encoding="utf-8"), "old")
            path.unlink()
            with self.assertRaises(PermissionError):    # nothing stands: the write failed
                write_cache(path, "new")
        self.assertEqual(left_over(self.dir), [])
        self.assertFalse(path.exists())

    def test_a_write_that_dies_halfway_leaves_no_temp_file(self):
        path = self.dir / "cache.json"
        with mock.patch.object(os, "replace", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            write_cache(path, "x" * 1000)
        self.assertEqual(left_over(self.dir), [])

    def test_writers_in_other_processes_all_finish_with_a_whole_cache(self):
        path = self.dir / "cache.json"
        go = self.dir / "go"
        script = self.dir / "writer.py"
        script.write_text(
            "import sys, time, json\n"
            "from pathlib import Path\n"
            f"sys.path.insert(0, {str(SRC)!r})\n"
            "from riftstone.index import write_cache\n"
            "n = int(sys.argv[1])\n"
            f"while not Path({str(go)!r}).exists():\n"
            "    time.sleep(0.005)\n"
            "for i in range(40):\n"
            f"    write_cache(Path({str(path)!r}), json.dumps({{'writer': n, 'i': i, 'pad': 'x' * 200000}}))\n",
            encoding="utf-8")
        procs = [subprocess.Popen([sys.executable, "-B", str(script), str(n)], stderr=subprocess.PIPE, text=True)
                 for n in range(6)]
        time.sleep(0.5)
        go.write_text("go", encoding="utf-8")
        errors = [p.communicate(timeout=120)[1] for p in procs]
        self.assertEqual([p.returncode for p in procs], [0] * 6, errors)
        final = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(final["pad"]), 200000)
        self.assertEqual(left_over(self.dir), [])


class WritersMeetOneAnotherTest(unittest.TestCase):
    """Each cache writer, with another process writing the same cache between its write and its rename (the writer
    that ran second is the one a fixed temp name made fail)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.base = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"RIFTSTONE_HOME": str(self.base / "home")})
        self.env.start()
        self.opened: list[Index] = []

    def tearDown(self):
        for i in self.opened:
            i.close()
        self.env.stop()
        gc.collect()
        self.tmp.cleanup()

    def racing(self, other):
        """os.replace as the first writer reaches it: ``other`` (the second writer, the whole of it) runs first, once."""
        real, state = os.replace, {"done": False}

        def replace(src, dst, *a, **k):
            if not state["done"]:
                state["done"] = True
                other()
            return real(src, dst, *a, **k)
        return mock.patch.object(os, "replace", replace), state

    def test_the_world_map_is_built_by_two_loaders_at_once(self):
        g = world_fixture.make(self.base / "game")
        idx = Index(g)
        self.opened.append(idx)
        idx.refresh()
        patch, state = self.racing(lambda: world.load(g, idx, rebuild=True))
        with patch:
            w = world.load(g, idx, rebuild=True)                  # the outer loader finishes its write too
        self.assertTrue(state["done"])
        self.assertTrue(w.data["stages"])
        self.assertEqual(world.load(g, idx).data["signature"], w.data["signature"])
        self.assertEqual(left_over(world.cache_path(g).parent), [])

    def test_the_monster_census_is_saved_by_two_writers_at_once(self):
        path = index.home() / "monsters-test.json"
        patch, state = self.racing(lambda: monsters.save({"census": "second"}, path))
        with patch:
            monsters.save({"census": "first"}, path)
        self.assertTrue(state["done"])
        self.assertIn(json.loads(path.read_text(encoding="utf-8"))["census"], ("first", "second"))
        self.assertEqual(left_over(path.parent), [])

    def test_the_flag_census_is_kept_through_the_shared_writer(self):
        """waves.census writes its cache from inside a longer function that treats any OSError as "only a cache", so
        a race would not show: what shows is that it writes through ``write_cache`` and leaves nothing beside it."""
        g = world_fixture.make(self.base / "game", extras=True, cells=True)
        idx = Index(g)
        self.opened.append(idx)
        idx.refresh()
        w = world.load(g, idx)
        with mock.patch.object(index, "write_cache", wraps=index.write_cache) as spy:
            first = waves.census(g, idx, w, rebuild=True)
        self.assertEqual([c.args[0] for c in spy.call_args_list], [waves._census_path(g)])
        self.assertTrue(waves.census_ready(g, idx))
        self.assertEqual(first, waves.census(g, idx, w))
        self.assertEqual(left_over(index.home()), [])


if __name__ == "__main__":
    unittest.main()
