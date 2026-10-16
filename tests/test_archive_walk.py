"""The archive walk and the index that reads it, against the per-archive code they replaced, on stand-in games:
``Game.archive_files`` (one scandir walk of nativePC and of the backups), ``Index.pending`` and ``Index.refresh``
(the same archives re-read after every kind of change), ``index.indexed_archives`` and the corpus pass that uses it
(the same resources as a pass over every archive, and a pass over every archive whenever the index is not current),
and the search query's plan (the same rows)."""
from __future__ import annotations

import gc
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401 (sys.path)
import test_gate
import world_fixture
from riftstone import arc, corpus, index, typemap
from riftstone import game as game_mod
from riftstone.errors import RiftError
from riftstone.index import Index, indexed_archives

setUpModule, tearDownModule = helpers.module_env("RIFTSTONE_HOME")
GMD, LOT, OCL = typemap.BY_EXT["gmd"], typemap.BY_EXT["lot"], typemap.BY_EXT["ocl"]


def entries(*names: str, tid: int = GMD) -> list:
    return [arc.Entry.from_data(n.encode(), tid, f"payload of {n}".encode()) for n in names]


def write(path: Path, *names: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(arc.Archive(entries(*names)).build())


def old_files(game):
    """What ``Index`` and the corpus read per archive before ``archive_files``: three stat calls an archive."""
    out = []
    for name, path in game.archive_names():
        src = game.vanilla_arc(name)
        st = src.stat()
        out.append((name, path, src, st.st_size, st.st_mtime_ns))
    return out


def old_pending(idx) -> int:
    known = {row[0]: (row[1], row[2]) for row in idx.db.execute("SELECT arc, size, mtime FROM arcs")}
    n = 0
    for name, _p in idx.game.archive_names():
        st = idx.game.vanilla_arc(name).stat()
        if known.get(name) != (st.st_size, st.st_mtime_ns):
            n += 1
    return n + sum(1 for a in known if not idx.game.arc_path(a).is_file())


def old_refresh(idx) -> dict:
    live = dict(idx.game.archive_names())
    known = {row[0]: (row[1], row[2]) for row in idx.db.execute("SELECT arc, size, mtime FROM arcs")}
    changed = 0
    todo = []
    for name in live:
        src = idx.game.vanilla_arc(name)
        st = src.stat()
        if known.get(name) != (st.st_size, st.st_mtime_ns):
            todo.append((name, src, st))
    gone = [a for a in known if a not in live]
    for name in gone:
        idx.db.execute("DELETE FROM arcs WHERE arc=?", (name,))
        idx.db.execute("DELETE FROM res WHERE arc=?", (name,))
    for name, src, st in todo:
        rows = corpus.directory(src)
        idx.db.execute("DELETE FROM res WHERE arc=?", (name,))
        idx.db.executemany("INSERT INTO res VALUES (?,?,?,?,?,?,?)",
                           [(name, i, n, n.decode("latin-1").lower(), t, s, z) for i, (n, t, z, s, _) in enumerate(rows)])
        idx.db.execute("INSERT OR REPLACE INTO arcs VALUES (?,?,?,?)", (name, st.st_size, st.st_mtime_ns, len(rows)))
        changed += 1
    idx.db.commit()
    return {"archives": len(live), "rescanned": changed, "removed": len(gone),
            "resources": idx.db.execute("SELECT COUNT(*) FROM res").fetchone()[0]}


def tables(idx):
    return (idx.db.execute("SELECT arc, size, mtime, count FROM arcs ORDER BY arc").fetchall(),
            idx.db.execute("SELECT * FROM res ORDER BY arc, idx").fetchall())


class WalkCase(unittest.TestCase):
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

    def index(self, game, name: str | None = None) -> Index:
        i = Index(game, self.base / name if name else None)
        self.opened.append(i)
        return i

    def game(self, tag: str = "g", kind: str = "ddda"):
        """Archives in nativePC at three depths, one with an upper-case extension, a folder named like an archive,
        and backups: one of an archive that is there (its original is what the game shipped), one of none."""
        live = {"rom/a": entries("message\\a"), "rom/sub/b": entries("message\\b") + entries("scr\\l", tid=LOT),
                "rom/sub/deep/c": entries("message\\c"), "rom/d": entries("message\\d"),
                "rom/dir.arc/inner": entries("message\\inner")}
        vanilla = {"rom/a": entries("message\\a0", "message\\a1"), "rom/gone": entries("message\\gone")}
        (self.base / tag).mkdir()
        g = test_gate.stand_in(self.base / tag, kind, live=live, vanilla=vanilla)
        os.replace(g.native / "rom" / "d.arc", g.native / "rom" / "D.ARC")
        return g


class ArchiveFilesTest(WalkCase):
    def test_the_same_archives_as_the_per_archive_calls(self):
        g = self.game()
        new = g.archive_files()
        old = old_files(g)
        self.assertEqual(len(new), 5)
        self.assertEqual([f.name for f in new], [o[0] for o in old])
        self.assertEqual([str(f.path) for f in new], [str(o[1]) for o in old])
        self.assertEqual([str(f.src).lower() for f in new], [str(o[2]).lower() for o in old])
        self.assertEqual([(f.size, f.mtime_ns) for f in new], [(o[3], o[4]) for o in old])
        shipped = [f for f in new if f.name == "rom/a"][0]
        self.assertEqual(shipped.src, g.vanilla_dir / "rom" / "a.arc")      # the backup wins over the live file
        self.assertNotEqual(shipped.src, shipped.path)
        self.assertNotIn("rom/gone", [f.name for f in new])                  # a backup of nothing is no archive
        self.assertEqual(sum(f.src != f.path for f in new), 1)
        kept = corpus.backups(g)
        self.assertEqual([str(corpus.source(g, f.path, kept)).lower() for f in new], [str(f.src).lower() for f in new])

    def test_nothing_there(self):
        g = test_gate.stand_in(self.base, "ddo")
        self.assertEqual(g.archive_files(), [])
        self.assertEqual(old_files(g), [])

    def test_a_name_arc_path_refuses_is_refused_alike(self):
        g = self.game()
        (g.native / "rom" / "bad!name.arc").write_bytes(b"x")
        with self.assertRaises(RiftError) as old:
            old_files(g)
        with self.assertRaises(RiftError) as new:
            g.archive_files()
        self.assertEqual(str(new.exception), str(old.exception))
        self.assertIn("bad!name", str(new.exception))
        for bad in ("..\\x", "a/../b", "c:/x", "", "x/", ".", 3, None):          # arc_path is the one rule
            with self.subTest(bad=bad), self.assertRaises(RiftError):
                g.arc_path(bad)


class PendingTest(WalkCase):
    def test_pending_and_refresh_follow_every_kind_of_change(self):
        g = self.game()
        new, ref = self.index(g), self.index(g, "old.sqlite")

        def backup_over_d():
            write(g.vanilla_dir / "rom" / "D.arc", "message\\d0")

        steps = [
            ("empty index", lambda: None),
            ("bytes of an archive replaced", lambda: write(g.native / "rom/sub/b.arc", "message\\b", "message\\b2")),
            ("only the modification time moves",
             lambda: os.utime(g.native / "rom/sub/deep/c.arc", ns=(1, 1_700_000_000_123_456_700))),
            ("backup removed: the live file is the original again", lambda: (g.vanilla_dir / "rom/a.arc").unlink()),
            ("a backup appears over an unchanged archive", backup_over_d),
            ("an archive appears", lambda: write(g.native / "rom/sub/new.arc", "message\\n")),
            ("an archive vanishes", lambda: (g.native / "rom/sub/deep/c.arc").unlink()),
            ("nothing changes", lambda: None),
        ]
        for label, change in steps:
            with self.subTest(step=label):
                change()
                pending = new.pending()
                self.assertEqual(pending, old_pending(new))
                self.assertEqual(ref.pending(), old_pending(ref))
                self.assertEqual(pending == 0, label == "nothing changes")
                self.assertEqual(new.refresh(), old_refresh(ref))
                self.assertEqual(tables(new), tables(ref))
                self.assertEqual((new.pending(), old_pending(new)), (0, 0))
        self.assertEqual(new.stats()["archives"], len(g.archive_files()))

    def test_the_live_file_under_a_backup_is_not_the_original(self):
        g = self.game()
        idx = self.index(g)
        idx.refresh()
        write(g.native / "rom/a.arc", "message\\somebody's own build")             # an install replaced it; the backup is
        self.assertEqual((idx.pending(), old_pending(idx)), (0, 0))                # what the game shipped, and unchanged
        self.assertIsNotNone(indexed_archives(g, [GMD]))

    def test_a_bad_name_stops_pending_and_refresh_with_arc_paths_message(self):
        g = self.game()
        idx = self.index(g)
        (g.native / "rom" / "bad!name.arc").write_bytes(b"x")
        for call in (idx.pending, idx.refresh):
            with self.subTest(call=call.__name__), self.assertRaises(RiftError) as e:
                call()
            self.assertIn("bad archive name 'rom/bad!name'", str(e.exception))


class IndexedArchivesTest(WalkCase):
    def current(self, g):
        idx = self.index(g)
        idx.refresh()
        return idx

    def resources(self, g, types, **kw):
        return [(r.arc, r.name, r.type_id, r.data) for r in corpus.resources(g, types, **kw)]

    def without_the_index(self, g, types, **kw):
        with mock.patch.object(index, "indexed_archives", return_value=None):
            return self.resources(g, types, **kw)

    def counting(self, g, types, **kw):
        """(the resources, the archives whose table was read).  Each of them is opened exactly once: the table,
        whether it is encrypted and the payloads come from one handle."""
        calls, opened, real, real_open = [], [], corpus._read_directory, open

        def read_directory(fh, path):
            calls.append(path)
            return real(fh, path)

        def counted_open(path, *a, **k):
            opened.append(Path(path))
            return real_open(path, *a, **k)

        with mock.patch.object(corpus, "_read_directory", read_directory), mock.patch.object(
                corpus, "open", counted_open, create=True):
            got = self.resources(g, types, **kw)
        self.assertEqual(len(opened), len(calls))
        self.assertEqual(len(set(opened)), len(opened))
        return got, calls

    def test_a_current_index_names_the_archives_that_hold_the_type(self):
        g = self.game()
        self.current(g)
        names, files = indexed_archives(g, [LOT])
        self.assertEqual(names, {"rom/sub/b"})
        self.assertEqual([f.name for f in files], [f.name for f in g.archive_files()])
        self.assertEqual(indexed_archives(g, [GMD])[0],
                         {"rom/a", "rom/sub/b", "rom/sub/deep/c", "rom/D", "rom/dir.arc/inner"})
        self.assertEqual(indexed_archives(g, [])[0], set())

    def test_the_corpus_pass_reads_only_those_archives_and_finds_the_same_resources(self):
        g = self.game()
        self.current(g)
        for types, tables_read in (([LOT], 1), ([GMD], 5), ([GMD, LOT], 5), ([OCL], 0), (None, 5)):
            for kw in ({}, {"unique": False}, {"limit_per_type": 2}, {"shard": (1, 3)}):
                with self.subTest(types=types, **kw):
                    got, calls = self.counting(g, types, **kw)
                    self.assertEqual(got, self.without_the_index(g, types, **kw))
                    self.assertEqual(len(calls), tables_read)

    def test_no_index_is_not_made_by_asking(self):
        g = self.game()
        self.assertIsNone(indexed_archives(g, [GMD]))
        self.assertFalse(index.index_path(g).exists())
        self.assertEqual(len(self.counting(g, [LOT])[1]), 5)                     # every table: nothing to go by
        self.assertFalse(index.index_path(g).exists())

    def test_an_index_that_is_not_current_is_not_used(self):
        changes = {
            "bytes": lambda g: write(g.native / "rom/sub/b.arc", "message\\z"),
            "time only": lambda g: os.utime(g.native / "rom/sub/b.arc", ns=(1, 1_700_000_000_000_000_000)),
            "archive added": lambda g: write(g.native / "rom/new.arc", "message\\n"),
            "archive removed": lambda g: (g.native / "rom/sub/deep/c.arc").unlink(),
            "backup added": lambda g: write(g.vanilla_dir / "rom/sub/b.arc", "message\\bb"),
            "backup removed": lambda g: (g.vanilla_dir / "rom/a.arc").unlink(),
        }
        for label, change in changes.items():
            with self.subTest(change=label):
                g = self.game(label.replace(" ", "_"))
                idx = self.current(g)
                self.assertIsNotNone(indexed_archives(g, [GMD]))
                change(g)
                self.assertIsNone(indexed_archives(g, [GMD]))
                got, calls = self.counting(g, [GMD, LOT])
                self.assertEqual(got, self.without_the_index(g, [GMD, LOT]))
                self.assertEqual(len(calls), len(g.archive_files()))            # every table, the new bytes included
                self.assertGreater(idx.pending(), 0)
                idx.refresh()
                self.assertIsNotNone(indexed_archives(g, [GMD]))                # current again, and the same
                self.assertEqual(self.resources(g, [GMD, LOT]), self.without_the_index(g, [GMD, LOT]))

    def test_an_index_that_cannot_be_trusted_is_not_used(self):
        g = self.game()
        idx = self.current(g)
        idx.db.execute("PRAGMA user_version=1")                                 # another schema
        idx.db.commit()
        self.assertIsNone(indexed_archives(g, [GMD]))
        idx.db.execute(f"PRAGMA user_version={index.SCHEMA}")
        idx.db.commit()
        self.assertIsNotNone(indexed_archives(g, [GMD]))
        idx.db.execute("INSERT INTO arcs VALUES ('rom/phantom', 1, 1, 0)")      # an archive the install lacks
        idx.db.commit()
        self.assertIsNone(indexed_archives(g, [GMD]))
        idx.db.execute("DELETE FROM arcs WHERE arc='rom/phantom'")
        idx.db.commit()
        idx.close()
        self.opened.remove(idx)
        path = index.index_path(g)
        for extra in (path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
            extra.unlink(missing_ok=True)
        path.write_bytes(b"this is not a database" * 100)                       # unreadable: every table instead
        self.assertIsNone(indexed_archives(g, [GMD]))
        self.assertEqual(self.resources(g, [GMD]), self.without_the_index(g, [GMD]))

    def test_an_index_whose_tables_disagree_is_not_used(self):
        """A damaged index (rows lost or added behind an archive's recorded count, rows of an archive it does not
        list) must send the pass to every archive: with its size and time in order it would otherwise name too few
        archives and the pass would miss resources without a word."""
        damage = {
            "an archive's rows lost": lambda db: db.execute("DELETE FROM res WHERE arc='rom/sub/b'"),
            "one row lost": lambda db: db.execute("DELETE FROM res WHERE arc='rom/a' AND idx=0"),
            "a row too many": lambda db: db.execute(
                "INSERT INTO res VALUES ('rom/a', 99, ?, ?, ?, 1, 1)", (b"message\\extra", "message\\extra", GMD)),
            "rows of an archive the index does not list": lambda db: db.execute(
                "INSERT INTO res VALUES ('rom/phantom', 0, ?, ?, ?, 1, 1)", (b"message\\p", "message\\p", GMD)),
        }
        for label, hurt in damage.items():
            with self.subTest(damage=label):
                g = self.game(label.replace(" ", "_").replace("'", ""))
                idx = self.current(g)
                self.assertIsNotNone(indexed_archives(g, [GMD, LOT]))
                hurt(idx.db)
                idx.db.commit()
                self.assertIsNone(indexed_archives(g, [GMD, LOT]))
                got, calls = self.counting(g, [GMD, LOT])
                self.assertEqual(len(calls), len(g.archive_files()))             # every table: nothing to go by
                self.assertEqual(got, self.without_the_index(g, [GMD, LOT]))
                self.assertGreater(len(got), 5)
                self.assertTrue(any(name == b"message\\b" for _, name, _, _ in got))

    def test_a_pass_that_must_see_every_table_does_not_use_the_index(self):
        """An archive swapped for another of the same size that keeps its time looks current: the index cannot tell
        without reading the table.  The default pass trusts the index; ``indexed=False`` (a proof, a generator) reads
        every archive and finds what the swap brought."""
        g = self.game()
        self.current(g)
        b = g.native / "rom/sub/b.arc"
        st = b.stat()
        swapped = arc.Archive([arc.Entry.from_data(b"message\\b", GMD, b"payload of message\\b"),
                               arc.Entry.from_data(b"scr\\l", OCL, b"payload of scr\\l")]).build()
        self.assertEqual(len(swapped), st.st_size)
        b.write_bytes(swapped)
        os.utime(b, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertEqual(os.stat(b).st_mtime_ns, st.st_mtime_ns)
        self.assertIsNotNone(indexed_archives(g, [OCL]))                         # the index's idea of current
        self.assertEqual(self.resources(g, [OCL], indexed=False), self.without_the_index(g, [OCL]))
        self.assertEqual([name for _, name, _, _ in self.resources(g, [OCL], indexed=False)], [b"scr\\l"])
        got, calls = self.counting(g, [GMD, LOT], indexed=False)
        self.assertEqual(len(calls), len(g.archive_files()))
        self.assertEqual(got, self.without_the_index(g, [GMD, LOT]))
        calls = self.counting(g, [LOT], indexed=True)[1]
        self.assertEqual(len(calls), 1)                                          # the default still uses it

    def test_the_index_is_read_without_leaving_files_beside_it(self):
        """A WAL database opened read-only makes a ``-shm`` and an empty ``-wal`` it cannot remove; the folder must
        be as it was after ``indexed_archives``, however many times it is asked."""
        g = self.game()
        idx = self.current(g)
        idx.close()
        self.opened.remove(idx)
        home = index.index_path(g).parent
        before = sorted(p.name for p in home.iterdir())
        self.assertEqual([n for n in before if n.endswith(("-wal", "-shm"))], [])
        for _ in range(3):
            self.assertIsNotNone(indexed_archives(g, [GMD]))
            self.assertEqual(self.resources(g, [LOT]), self.without_the_index(g, [LOT]))
            self.assertEqual(sorted(p.name for p in home.iterdir()), before)

    def test_reading_does_not_take_the_files_of_a_writer_that_has_the_index_open(self):
        g = self.game()
        idx = self.current(g)                                                     # a writer: it has made them itself
        home = index.index_path(g).parent
        before = sorted(p.name for p in home.iterdir())
        self.assertTrue([n for n in before if n.endswith("-shm")])
        for _ in range(2):
            self.assertIsNotNone(indexed_archives(g, [GMD]))
        self.assertEqual(sorted(p.name for p in home.iterdir()), before)
        write(g.native / "rom/sub/new.arc", "message\\n")
        self.assertEqual(idx.pending(), 1)                                        # the writer still works
        idx.refresh()
        self.assertEqual(idx.pending(), 0)
        self.assertIsNotNone(indexed_archives(g, [GMD]))


    def test_files_beside_the_index_that_were_there_before_are_left_alone(self):
        g = self.game()
        idx = self.current(g)
        idx.close()
        self.opened.remove(idx)
        path = index.index_path(g)
        for suffix in ("-wal", "-shm"):
            Path(str(path) + suffix).write_bytes(b"")                             # left by something that was killed
        before = sorted(p.name for p in path.parent.iterdir())
        self.assertIn(path.name + "-shm", before)
        self.assertIsNotNone(indexed_archives(g, [GMD]))
        self.assertEqual(sorted(p.name for p in path.parent.iterdir()), before)


class SizeOfAFileOpenForWritingTest(WalkCase):
    def test_archive_files_sees_the_bytes_written_so_far(self):
        """The size in a directory listing is not updated for a file that is open for writing (Windows, until it is
        closed); an installer or the game writing an archive must not read as the archive it replaced."""
        g = self.game()
        idx = self.index(g)
        idx.refresh()
        path = g.native / "rom/sub/b.arc"
        size = path.stat().st_size
        with open(path, "ab") as fh:
            fh.write(b"\0" * 1000)
            fh.flush()
            files = {f.name: f for f in g.archive_files()}
            self.assertEqual(os.stat(path).st_size, size + 1000)
            self.assertEqual(files["rom/sub/b"].size, size + 1000)
            self.assertEqual(files["rom/sub/b"].mtime_ns, os.stat(path).st_mtime_ns)
            self.assertEqual(idx.pending(), 1)
            self.assertIsNone(indexed_archives(g, [GMD]))
        self.assertEqual(idx.pending(), 1)
        idx.refresh()
        self.assertEqual(idx.pending(), 0)

    def test_many_archives_are_stat_in_chunks_and_come_back_in_order(self):
        g = self.game()
        for i in range(25):
            write(g.native / "rom" / "many" / f"m{i:02d}.arc", f"message\\m{i}")
        with mock.patch.object(game_mod, "STAT_CHUNK", 4), mock.patch.object(game_mod, "STAT_THREADS", 3):
            threaded = g.archive_files()
        with mock.patch.object(game_mod, "STAT_CHUNK", 10_000):
            serial = g.archive_files()
        self.assertEqual(len(threaded), 30)
        self.assertEqual(threaded, serial)
        self.assertEqual([f.size for f in threaded],
                         [os.stat(f.src).st_size for f in threaded])

    def test_the_first_error_is_the_first_paths(self):
        paths = [str(self.base / "there")]
        (self.base / "there").write_bytes(b"x")
        paths += [str(self.base / "missing-1"), str(self.base / "missing-2")]
        for chunk in (1, 2, 1000):
            with self.subTest(chunk=chunk), mock.patch.object(game_mod, "STAT_CHUNK", chunk):
                with self.assertRaises(FileNotFoundError) as e:
                    game_mod._stat_each(paths + paths)
                self.assertIn("missing-1", str(e.exception))


class WalkTest(WalkCase):
    """``corpus.resources(walk=...)``: what a pass counted, so the shards of one pass can be told to have read one game."""

    def shards(self, g, types, n, **kw):
        out = []
        for i in range(n):
            w = corpus.Walk()
            got = list(corpus.resources(g, types, shard=(i, n), walk=w, **kw))
            out.append((got, w))
        return out

    def test_a_place_is_where_a_resource_stands_in_the_whole_pass(self):
        g = self.game()
        whole = list(corpus.resources(g, [GMD, LOT]))
        self.assertEqual([r.place for r in whole], list(range(1, len(whole) + 1)))
        self.assertGreater(len(whole), 6)
        for n in (2, 3, 5):
            with self.subTest(shards=n):
                parts = self.shards(g, [GMD, LOT], n)
                for i, (got, _) in enumerate(parts):
                    self.assertEqual([r.place for r in got], list(range(i + 1, len(whole) + 1, n)))
                merged = sorted((r for got, _ in parts for r in got), key=lambda r: r.place)
                self.assertEqual(merged, whole)

    def test_every_shard_counts_the_whole_sequence(self):
        g = self.game()
        solo = corpus.Walk()
        whole = list(corpus.resources(g, [GMD, LOT], walk=solo))
        self.assertEqual(solo.places, len(whole))
        for kw in ({}, {"unique": False}, {"limit_per_type": 2}):
            with self.subTest(**kw):
                ref = corpus.Walk()
                list(corpus.resources(g, [GMD, LOT], walk=ref, **kw))
                for _, w in self.shards(g, [GMD, LOT], 3, **kw):
                    self.assertEqual((w.places, w.digest), (ref.places, ref.digest))
        # the index changes which archives are opened, never what the pass counts
        idx = self.index(g)
        idx.refresh()
        again = corpus.Walk()
        list(corpus.resources(g, [GMD, LOT], walk=again))
        self.assertEqual((again.places, again.digest), (solo.places, solo.digest))
        full = corpus.Walk()
        list(corpus.resources(g, [GMD, LOT], walk=full, indexed=False))
        self.assertEqual((full.places, full.digest), (solo.places, solo.digest))

    def test_shards_that_read_different_games_differ(self):
        g = self.game()
        ref = corpus.Walk()
        list(corpus.resources(g, [GMD], walk=ref))
        write(g.native / "rom/zz.arc", "message\\late")                           # one more resource, at the end
        more = corpus.Walk()
        list(corpus.resources(g, [GMD], walk=more))
        self.assertEqual(more.places, ref.places + 1)
        self.assertNotEqual(more.digest, ref.digest)
        write(g.native / "rom/zz.arc", "message\\lat3")                           # the same count, other bytes
        same = corpus.Walk()
        list(corpus.resources(g, [GMD], walk=same))
        self.assertEqual(same.places, more.places)
        self.assertNotEqual(same.digest, more.digest)
        write(g.native / "rom/sub/deep/c.arc", "message\\kc")                     # a name changes in the middle
        mid = corpus.Walk()
        list(corpus.resources(g, [GMD], walk=mid))
        self.assertEqual(mid.places, same.places)
        self.assertNotEqual(mid.digest, same.digest)


class SearchPlanTest(WalkCase):
    OLD = "SELECT name, type, size, COUNT(*), MIN(arc) FROM res WHERE name_lc LIKE ? ESCAPE '\\'"

    def old_search(self, idx, text, type_id, limit):
        pattern = "%" + (text.lower().replace("/", "\\").replace("\\", "\\\\")
                         .replace("%", r"\%").replace("_", r"\_")) + "%"
        q, args = self.OLD, [pattern]
        if type_id is not None:
            q += " AND type=?"
            args.append(type_id)
        q += " GROUP BY name, type ORDER BY name_lc LIMIT ?"
        args.append(limit)
        return [{"name": n, "type": t, "ext": typemap.extension(t), "size": s, "archives": c, "first_arc": a}
                for n, t, s, c, a in idx.db.execute(q, args)]

    def test_the_same_rows_in_the_same_order(self):
        g = world_fixture.make(self.base / "world", extras=True, cells=True)
        idx = self.index(g)
        idx.refresh()
        texts = ["", "st424", "scr/st424", "scr\\st424\\etc", "e", "em", "_", "%", "st424_e_dlc0_", "gpl", "zzzz", "ST424",
                 "message", "0", "st", "\\", "etc/st424_e"]
        types = [None, typemap.BY_EXT["gpl"], typemap.BY_EXT["gmd"], typemap.BY_EXT["lot"], typemap.BY_EXT["spn"]]
        checked = 0
        for text in texts:
            for tid in types:
                for limit in (1, 3, 50, 100000):
                    with self.subTest(text=text, type=tid, limit=limit):
                        self.assertEqual(idx._search(text, tid, limit), self.old_search(idx, text, tid, limit))
                        checked += 1
        self.assertEqual(checked, len(texts) * len(types) * 4)
        self.assertGreater(len(idx._search("", None, 100000)), 10)

    def test_the_plan_is_one_scan_of_the_table(self):
        g = self.game()
        idx = self.index(g)
        idx.refresh()
        plan = " ".join(str(r) for r in idx.db.execute(
            "EXPLAIN QUERY PLAN SELECT name, type, size, COUNT(*), MIN(arc) FROM res NOT INDEXED WHERE name_lc LIKE ? "
            "ESCAPE '\\' GROUP BY name, type ORDER BY name_lc LIMIT ?", ("%a%", 5)))
        self.assertIn("SCAN res", plan)
        self.assertNotIn("res_key", plan)


if __name__ == "__main__":
    unittest.main()
