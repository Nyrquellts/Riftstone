"""Which archives hold which resources: a local SQLite index of the vanilla directories.

Built from each archive's original bytes (Riftstone's backup when it has
replaced a file), refreshed only for archives whose size or time changed.
Stored under %LOCALAPPDATA%\\Riftstone (or $RIFTSTONE_HOME), never in the game.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path

from . import typemap
from .corpus import directory
from .game import Game

SCHEMA = 2


def home() -> Path:
    base = os.environ.get("RIFTSTONE_HOME")
    if base:
        return Path(base)
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / "Riftstone"


class Index:
    def __init__(self, game: Game, path: Path | None = None):
        self.game = game
        key = hashlib.sha1(str(game.root.resolve()).lower().encode()).hexdigest()[:12]
        self.path = path or home() / f"index-{key}.sqlite"
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
        live = {self.game.arc_name(p): p for p in self.game.archives()}
        known = {row[0]: (row[1], row[2]) for row in self.db.execute("SELECT arc, size, mtime FROM arcs")}
        changed = 0
        todo = []
        for arc in live:
            src = self.game.vanilla_arc(arc)
            st = src.stat()
            if known.get(arc) != (st.st_size, st.st_mtime_ns):
                todo.append((arc, src, st))
        gone = [a for a in known if a not in live]
        for arc in gone:
            self.db.execute("DELETE FROM arcs WHERE arc=?", (arc,))
            self.db.execute("DELETE FROM res WHERE arc=?", (arc,))
        for arc, src, st in todo:
            rows = directory(src)
            self.db.execute("DELETE FROM res WHERE arc=?", (arc,))
            self.db.executemany("INSERT INTO res VALUES (?,?,?,?,?,?,?)",
                                [(arc, i, n, n.decode("latin-1").lower(), t, s, z) for i, (n, t, z, s, _) in enumerate(rows)])
            self.db.execute("INSERT OR REPLACE INTO arcs VALUES (?,?,?,?)", (arc, st.st_size, st.st_mtime_ns, len(rows)))
            changed += 1
            if progress:
                progress.advance(1, arc)
        self.db.commit()
        return {"archives": len(live), "rescanned": changed, "removed": len(gone),
                "resources": self.db.execute("SELECT COUNT(*) FROM res").fetchone()[0]}

    def pending(self) -> int:
        """How many archives refresh() would re-read (0 means the index is current)."""
        known = {row[0]: (row[1], row[2]) for row in self.db.execute("SELECT arc, size, mtime FROM arcs")}
        n = 0
        for p in self.game.archives():
            arc = self.game.arc_name(p)
            st = self.game.vanilla_arc(arc).stat()
            if known.get(arc) != (st.st_size, st.st_mtime_ns):
                n += 1
        return n + sum(1 for a in known if not self.game.arc_path(a).is_file())

    def archives_with(self, name: bytes, type_id: int) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT arc FROM res WHERE name=? AND type=? ORDER BY arc", (name, type_id))]

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
        pattern = "%" + text.lower().replace("/", "\\").replace("%", r"\%").replace("_", r"\_") + "%"
        q = "SELECT name, type, size, COUNT(*), MIN(arc) FROM res WHERE name_lc LIKE ? ESCAPE '\\'"
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
