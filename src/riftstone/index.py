"""Which archives hold which resources: a local SQLite index of the vanilla directories.

Built from each archive's original bytes (Riftstone's backup when it has
replaced a file), refreshed only for archives whose size or time changed.
Stored under %LOCALAPPDATA%\\Riftstone (or $RIFTSTONE_HOME), never in the game.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from . import typemap
from .corpus import directory
from .errors import RiftError
from .game import ArcFile, Game

SCHEMA = 2


def home() -> Path:
    base = os.environ.get("RIFTSTONE_HOME")
    if base:
        return Path(base)
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / "Riftstone"


def write_cache(path: Path, text: str) -> None:
    """``text`` as the cache file ``path``, whole or not at all: written to a file of its own beside ``path`` (a name
    no other writer has: processes that build the same cache at once each write theirs) and renamed over it.  A
    rename that fails because another process just replaced ``path``, or has it open (Windows refuses), leaves their
    file, which is the same cache; with no ``path`` to fall back on it is an OSError, as any write is.  No half-written
    file stays: the writer's own is removed whatever happens."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f"{path.stem}-", suffix=".tmp", dir=path.parent)
    tmp = Path(name)
    try:
        with open(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                return
            except OSError:
                if attempt == 4:
                    if not path.is_file():
                        raise
                    return                                  # another writer's complete file stands
                time.sleep(0.02 * (attempt + 1))
    finally:
        tmp.unlink(missing_ok=True)


def index_path(game: Game) -> Path:
    """Where this install's index lives: one file per game folder under ``home()``."""
    key = hashlib.sha1(str(game.root.resolve()).lower().encode()).hexdigest()[:12]
    return home() / f"index-{key}.sqlite"


def _known(db: sqlite3.Connection) -> dict[str, tuple[int, int]]:
    """{archive: (size, mtime_ns)} as the index last read each one."""
    return {row[0]: (row[1], row[2]) for row in db.execute("SELECT arc, size, mtime FROM arcs")}


@contextmanager
def read_only(path: Path) -> Iterator[sqlite3.Connection]:
    """The index file ``path`` opened to read: a consistent snapshot (the write-ahead log included) that changes
    nothing in the folder.  SQLite makes the ``-shm`` and an empty ``-wal`` beside a WAL-mode database it opens
    read-only, and a read-only connection cannot remove them; the ones this open made are removed again when it
    ends.  A file another process holds open stays (Windows refuses to delete it), and so does a log with anything
    in it, which is a writer's.  Raises ``sqlite3.Error`` as ``sqlite3.connect`` does."""
    sidecars = [(Path(str(path) + suffix), suffix == "-wal") for suffix in ("-wal", "-shm")]
    existed = [p.exists() for p, _ in sidecars]
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        yield db
    finally:
        db.close()
        for (p, is_log), had in zip(sidecars, existed):
            if had:
                continue
            try:
                if not is_log or p.stat().st_size == 0:
                    p.unlink()
            except OSError:
                pass


def indexed_archives(game: Game, type_ids: Iterable[int]) -> tuple[set[str], list[ArcFile]] | None:
    """(the names of the archives that hold a resource of one of ``type_ids``, every archive of the install as
    ``Game.archive_files`` lists them), read from the game's index; or None when the answer cannot be trusted and
    the caller must read every archive: no index, one of another schema, that cannot be opened or whose tables
    disagree (an archive's recorded resource count is not the number of rows it has, or rows belong to an archive
    it does not list), or one that is not current right now.  Current means every archive's size and modification
    time (the original bytes' -- a backup in ``vanilla_dir`` over the file in nativePC), read from the files at this
    call, are what ``refresh`` recorded, and the index holds no archive the install lacks -- ``Index.pending() ==
    0``.  Reads only: the index file is opened read-only (``read_only``) and never created.  An archive replaced
    by another of the same size that keeps its time is not noticed -- only reading its table would -- which is why
    a proof reads every archive (``corpus.resources(indexed=False)``)."""
    path = index_path(game)
    if not path.is_file():
        return None
    ids = sorted(set(type_ids))
    try:
        with read_only(path) as db:
            if db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA:
                return None
            known = _known(db)
            try:
                files = game.archive_files()
            except (RiftError, OSError):
                return None
            if len(known) != len(files) or any(known.get(f.name) != (f.size, f.mtime_ns) for f in files):
                return None
            rows = dict(db.execute("SELECT arc, COUNT(*) FROM res GROUP BY arc"))
            if any(arc not in known for arc in rows) \
                    or any(rows.get(arc, 0) != n for arc, n in db.execute("SELECT arc, count FROM arcs")):
                return None
            names: set[str] = set()
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                names.update(r[0] for r in db.execute(
                    f"SELECT DISTINCT arc FROM res WHERE type IN ({','.join('?' * len(chunk))})", chunk))
            return names, files
    except sqlite3.Error:
        return None


class Index:
    def __init__(self, game: Game, path: Path | None = None):
        self.game = game
        self.path = path or index_path(game)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.execute("PRAGMA journal_mode=WAL")
        ver = self.db.execute("PRAGMA user_version").fetchone()[0]
        if ver != SCHEMA:
            self.db.executescript("""
                DROP TABLE IF EXISTS arcs; DROP TABLE IF EXISTS res;
                CREATE TABLE arcs (arc TEXT PRIMARY KEY, size INT, mtime INT, count INT);
                CREATE TABLE res (arc TEXT, idx INT, name BLOB, name_lc TEXT, type INT, size INT, zsize INT);
                CREATE INDEX res_key ON res(name, type);
                CREATE INDEX res_arc ON res(arc);
            """)
            self.db.execute(f"PRAGMA user_version={SCHEMA}")
            self.db.commit()

    def close(self) -> None:
        self.db.close()

    def refresh(self, progress=None) -> dict:
        """Re-read the directories of new or changed archives; drop vanished ones."""
        files = self.game.archive_files()
        live = {f.name for f in files}
        known = _known(self.db)
        changed = 0
        todo = [f for f in files if known.get(f.name) != (f.size, f.mtime_ns)]
        gone = [a for a in known if a not in live]
        for arc in gone:
            self.db.execute("DELETE FROM arcs WHERE arc=?", (arc,))
            self.db.execute("DELETE FROM res WHERE arc=?", (arc,))
        for f in todo:
            rows = directory(f.src)
            self.db.execute("DELETE FROM res WHERE arc=?", (f.name,))
            self.db.executemany("INSERT INTO res VALUES (?,?,?,?,?,?,?)",
                                [(f.name, i, n, n.decode("latin-1").lower(), t, s, z) for i, (n, t, z, s, _) in enumerate(rows)])
            self.db.execute("INSERT OR REPLACE INTO arcs VALUES (?,?,?,?)", (f.name, f.size, f.mtime_ns, len(rows)))
            changed += 1
            if progress:
                progress.advance(1, f.name)
        self.db.commit()
        return {"archives": len(live), "rescanned": changed, "removed": len(gone),
                "resources": self.db.execute("SELECT COUNT(*) FROM res").fetchone()[0]}

    def pending(self) -> int:
        """How many archives refresh() would re-read (0 means the index is current)."""
        known = _known(self.db)
        live: set[str] = set()
        n = 0
        for f in self.game.archive_files():
            live.add(f.name)
            if known.get(f.name) != (f.size, f.mtime_ns):
                n += 1
        return n + sum(1 for a in known if a not in live and not self.game.arc_path(a).is_file())

    def archives_with(self, name: bytes, type_id: int) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT arc FROM res WHERE name=? AND type=? ORDER BY arc", (name, type_id))]

    def names_under(self, folder: str, type_id: int) -> list[bytes]:
        """Every resource name of one type under a folder (``scr\\st424\\etc\\``; any case), each once."""
        prefix = folder.lower().replace("/", "\\").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = self.db.execute("SELECT DISTINCT name FROM res WHERE type=? AND name_lc LIKE ? ESCAPE '\\'",
                               (type_id, prefix + "%"))
        return [r[0] for r in rows]

    def search(self, text: str, type_id: int | None = None, limit: int = 200) -> list[dict]:
        """Names containing text.  An extension is understood too: 'shl' also lists every .shl
        resource, 'em0100.shl' means name contains em0100 and type is .shl."""
        # Names are ASCII; anything SQLite cannot even encode (lone surrogates) cannot match.
        text = text.strip().encode("utf-8", "ignore").decode("utf-8")
        if type_id is None and text:
            head, dot, tail = text.rpartition(".")
            as_ext = typemap.type_for_extension(tail) if dot else typemap.type_for_extension(text)
            if dot and as_ext is not None:
                return self._search(head, as_ext, limit)
            if not dot and as_ext is not None and len(text) >= 2:
                by_type = self._search("", as_ext, limit)
                seen = {(r["name"], r["type"]) for r in by_type}
                rest = [r for r in self._search(text, None, limit) if (r["name"], r["type"]) not in seen]
                return (by_type + rest)[:limit]
        return self._search(text, type_id, limit)

    def _search(self, text: str, type_id: int | None, limit: int) -> list[dict]:
        # the engine's separator is also the LIKE escape: double it before escaping the wildcards
        pattern = "%" + (text.lower().replace("/", "\\").replace("\\", "\\\\")
                         .replace("%", r"\%").replace("_", r"\_")) + "%"
        # NOT INDEXED: the planner would walk res_key (name, type) and fetch every row from the table for the
        # LIKE, 3-8 times slower than one scan of the table; the groups and their order are the same
        q = "SELECT name, type, size, COUNT(*), MIN(arc) FROM res NOT INDEXED WHERE name_lc LIKE ? ESCAPE '\\'"
        args: list = [pattern]
        if type_id is not None:
            q += " AND type=?"
            args.append(type_id)
        q += " GROUP BY name, type ORDER BY name_lc LIMIT ?"
        args.append(limit)
        return [{"name": n, "type": t, "ext": typemap.extension(t), "size": s, "archives": c, "first_arc": a}
                for n, t, s, c, a in self.db.execute(q, args)]

    def entries(self, arc: str) -> list[dict]:
        return [{"name": n, "type": t, "ext": typemap.extension(t), "size": s, "zsize": z}
                for n, t, s, z in self.db.execute("SELECT name, type, size, zsize FROM res WHERE arc=? ORDER BY idx", (arc,))]

    def stats(self) -> dict:
        arcs, = self.db.execute("SELECT COUNT(*) FROM arcs").fetchone()
        res, = self.db.execute("SELECT COUNT(*) FROM res").fetchone()
        types = self.db.execute("SELECT type, COUNT(*) FROM res GROUP BY type ORDER BY COUNT(*) DESC").fetchall()
        return {"archives": arcs, "resources": res,
                "types": [{"ext": typemap.extension(t), "class": typemap.class_name(t), "count": c} for t, c in types]}
