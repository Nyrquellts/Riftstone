"""Riftstone Studio: the toolchain in a browser tab, served from this PC only.

Binds 127.0.0.1 on a free port.  Every API call must carry the session token
that is baked into the page it served, and the Host header must be the
loopback address, so other websites cannot drive it (no CSRF, no DNS
rebinding).  Paths from the page are never trusted: mods are resolved inside
the workspace folder and resources through fsmap.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources as _res
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__, arc, arcfolder, fsmap, install, params, typemap, xfs
from .errors import RiftError
from .game import KINDS, Game, find_game
from .mod import MOD_FILE, Mod, collect, mods_env, mods_folder

MAX_BODY = 64 * 1024 * 1024       # uploads: an edited 2048 px texture as PNG, base64-encoded
PREVIEW_LIMIT = 400_000
# The only files served besides the page: the display font and two drawings (no token: nothing private).
STATIC = {"fonts/riftstone-blade.woff2": "font/woff2", "mark.svg": "image/svg+xml", "spiral.svg": "image/svg+xml"}
TEXT_FILES = (".yaml", ".txt", ".json")    # what the editor opens and saves
SERVER_EXE = "Arrowgene.Ddon.Cli.exe"   # the local Dragon's Dogma Online server the DDO toolkit runs


def _cut(text: str) -> str:
    return text if len(text) <= PREVIEW_LIMIT else text[:PREVIEW_LIMIT] + "\n# ... (preview cut)\n"


def default_workspace(game: Game | None) -> Path:
    """Where Studio looks for mods when no --workspace is given: the mods folder (mod.mods_folder, the same
    one the command line uses)."""
    return mods_folder(game)


class Studio:
    def __init__(self, game: Game | None, workspace: Path, follow: bool = False):
        self.game = game
        self.workspace = workspace
        self.follow = follow        # no folder was chosen: the mods folder follows the game (default_workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.token = secrets.token_urlsafe(24)
        self.index = None
        self.index_state = {"ready": False, "done": 0, "total": 0, "error": None}
        self.lock = threading.Lock()
        self.activity: list[dict] = []
        self.exe_ok: bool | None = None

    # -- background ---------------------------------------------------------
    def start_index(self) -> None:
        game, state = self.game, self.index_state  # a switch mid-way leaves this run's results behind

        def work():
            from .index import Index

            try:
                idx = Index(game)
                pending = idx.pending()
                state["total"] = pending

                class P:
                    def advance(_, k=1, note=""):
                        state["done"] += k

                idx.refresh(P())
                idx.close()
                state["ready"] = True
                self.log("ok", f"Resource index ready ({game.title})")
            except Exception as e:  # noqa: BLE001 - reported to the page
                state["error"] = str(e)
                self.log("fail", f"Index failed: {e}")
        if self.game:
            threading.Thread(target=work, daemon=True).start()

    def open_index(self):
        from .index import Index

        if not self.index_state["ready"]:
            raise RiftError("the resource index is still being built; try again in a moment")
        return Index(self.game)

    def log(self, level: str, msg: str) -> None:
        with self.lock:
            self.activity.append({"t": time.strftime("%H:%M:%S"), "level": level, "msg": msg})
            del self.activity[:-200]

    # -- helpers ------------------------------------------------------------
    def need_game(self) -> Game:
        if not self.game:
            raise RiftError("Dragon's Dogma was not found; start Studio with --game \"C:\\path\\to\\DDDA\"")
        return self.game

    def games(self) -> list[dict]:
        """Both games as far as they are on this machine (for the switcher; looked up once)."""
        if getattr(self, "_found", None) is None:
            found = {}
            for kind in KINDS:
                try:
                    found[kind] = self.game if self.game and self.game.kind == kind else find_game(kind)
                except RiftError:
                    pass
            self._found = found
        return [{"kind": k, "title": KINDS[k]["title"], "root": str(g.root),
                 "current": bool(self.game and self.game.kind == k)} for k, g in self._found.items()]

    def switch(self, kind: str) -> None:
        if kind not in KINDS:
            raise RiftError(f"unknown game {kind!r}")
        game = find_game(kind)
        workspace = default_workspace(game) if self.follow else self.workspace
        workspace.mkdir(parents=True, exist_ok=True)
        with self.lock:
            self.game = game
            self.workspace = workspace
            self.exe_ok = None
            self.index_state = {"ready": False, "done": 0, "total": 0, "error": None}
            self._world = None              # the last game's map and "is it running" answer
            self._running_at = 0
        self.log("ok", f"Switched to {game.title}" + (f"; mods folder {workspace}" if self.follow else ""))
        self.start_index()

    def plugin_summary(self) -> list[dict]:
        """The loader's plugins in the game folder, on and off (a folder listing: cheap enough for every refresh)."""
        from . import plugins

        g = self.game
        if not g or g.kind != "ddda":
            return []
        out = []
        for folder, on in ((plugins.plugins_dir(g), True), (plugins.off_dir(g), False)):
            if folder.is_dir():
                for p in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
                    if p.is_file() and p.suffix.lower() in plugins.SUFFIXES:
                        info = plugins.CATALOG.get(p.stem.lower(), {})
                        out.append({"name": p.stem, "title": info.get("title", p.stem), "enabled": on})
        return out

    @staticmethod
    def panel_key(g) -> str:
        """The key that shows the in-game panel, from the game's riftstone_loader.ini (Insert by default)."""
        from . import loader as loader_mod

        try:
            return loader_mod.panel_key(g)
        except OSError:
            return loader_mod.OVERLAY_KEY_DEFAULT

    def loader_versions(self) -> dict:
        """The installed loader's version and the one this Riftstone would install (read once per file change)."""
        from . import loader as loader_mod, runtime

        g = self.game
        if not g or g.kind != "ddda":
            return {"installed": None, "available": None}
        cache = getattr(self, "_versions", {})
        out = {}
        for key, path in (("installed", g.root / "dinput8.dll"), ("available", None)):
            if path is None:
                try:
                    path = loader_mod.built_loader() / "dinput8.dll"
                except RiftError:
                    out[key] = None
                    continue
            try:
                st = path.stat()
                stamp = (str(path), st.st_mtime_ns, st.st_size)
            except OSError:
                out[key] = None
                continue
            if cache.get(key, (None,))[0] != stamp:
                cache[key] = (stamp, runtime.loader_version(path) if loader_mod.is_ours(path) else None)
            out[key] = cache[key][1]
        self._versions = cache
        return out

    def ddo_server(self) -> dict | None:
        """Whether the local Dragon's Dogma Online server is running (None when Online is not on this PC)."""
        if "ddo" not in {k for k in (getattr(self, "_found", None) or {})}:
            return None
        now = time.time()
        if now - getattr(self, "_server_at", 0) > 4:
            names = install._process_names() or []
            self._server = any(n.lower() == SERVER_EXE.lower() for n in names)
            self._server_at = now
        return {"running": self._server}

    def launch(self) -> dict:
        """Start the current game the way its owner does: Dark Arisen through Steam, Online through the DDO
        toolkit's Play Solo (which starts the local server first). A person clicked Launch for this."""
        g = self.need_game()
        if self.running():
            raise RiftError(f"{g.title} is already running")
        if g.kind == "ddda":
            from .game import STEAM_APP_ID
            os.startfile(f"steam://rungameid/{STEAM_APP_ID}")  # noqa: S606 - the person clicked Launch
            self.log("ok", f"Launching {g.title} through Steam")
            return {"ok": True, "how": "steam"}
        play = g.root.parent / "Play Solo.cmd"
        if not play.is_file():
            raise RiftError(f"{play} is missing: the DDO toolkit starts Online (ddon.cmd play)")
        os.startfile(str(play))  # noqa: S606 - the person clicked Launch
        self.log("ok", f"Launching {g.title}: Play Solo starts the local server, then the game")
        return {"ok": True, "how": "play-solo"}

    # -- monsters between the games (monsters.py) -----------------------------------------------------
    def _monster_games(self):
        """Both games and their indexes (built the first time), for the census and conversions."""
        from .index import Index

        games = {}
        for k in ("ddda", "ddo"):
            try:
                games[k] = self.game if self.game and self.game.kind == k else find_game(k)
            except RiftError:
                raise RiftError(f"monsters are compared between both games, and {KINDS[k]['title']} was not found "
                                "on this PC") from None
        idxs = {k: Index(g) for k, g in games.items()}
        for k, i in idxs.items():
            if i.pending():
                self.log("ok", f"Indexing {games[k].title} for the monster census (only the first time)")
                i.refresh()
        return games, idxs

    def _census(self, games, idxs):
        from . import monsters

        c = getattr(self, "_monsters", None)
        if c is None:
            c = monsters.load(games, idxs)
            self._monsters = c
            self.log("ok", "Monster census ready: " + ", ".join(
                f"{monsters.title(k)} {c['summary'][k]['families']} families" for k in ("ddo", "ddda")))
        return c

    def monster_list(self) -> dict:
        """Every enemy family of both games with its counterpart in the other game and the verdict."""
        from . import monsters

        games, idxs = self._monster_games()
        try:
            c = self._census(games, idxs)
        finally:
            for i in idxs.values():
                i.close()
        rows = {}
        for k in ("ddo", "ddda"):
            o = monsters.other(k)
            out = []
            for key, f in sorted(c[k]["families"].items()):
                e = c["counterparts"][k][key]
                cp = e.get("counterpart")
                g2 = c[o]["families"].get(cp) if cp else None
                out.append({"family": key, "name": f["name"] or "", "ids": [c[k]["enemies"][a]["id"] for a in f["enemies"]][:6],
                            "counterpart": cp, "counterpart_name": (g2 or {}).get("name") or "",
                            "verdict": (e.get("pair") or {}).get("verdict", "none") if cp else "none",
                            "textures": (e.get("counterpart_textures") or {}).get("verdict")})
            rows[k] = out
        return {"summary": c["summary"], "rows": rows, "skins": sorted(monsters.SKIN_VARIANTS)}

    def monster_convert(self, body: dict) -> dict:
        """Plan (dry_run) or carry out one conversion into a mod of the destination game."""
        from . import monsters, studiofiles

        src = body.get("from")
        if src not in ("ddo", "ddda"):
            raise RiftError("from is ddo or ddda: the game the monster comes from")
        source, target = body.get("source"), body.get("into")
        if not isinstance(source, str) or not source.strip() or not isinstance(target, str) or not target.strip():
            raise RiftError("say which enemy becomes which: source and into")
        skin = body.get("skin")
        if skin in ("", None):
            skin = None
        elif (isinstance(skin, bool) or not isinstance(skin, (int, str))
              or not re.fullmatch(r"[0-9]{1,9}", str(skin).strip())):         # '²' passed isdigit()
            raise RiftError("skin is a number 1..99")
        else:
            skin = int(skin)
        dst = monsters.other(src)
        games, idxs = self._monster_games()
        try:
            c = self._census(games, idxs)
            p = monsters.plan(c, games, idxs, src, source, target, skin)
            out = {"allowed": p["allowed"], "refused": p["refused"], "notes": list(p["notes"]),
                   "verdict": p["pair"]["verdict"], "textures": p["textures"]["verdict"],
                   "source": p["source"]["enemy"], "target": p["target"]["enemy"], "game": dst, "written": []}
            if body.get("dry_run") or not p["allowed"]:
                return out
            root, new = studiofiles.destination(self, body)
            if new is not None:
                Mod.create(root, new, "", dst)
                self.log("ok", f"Created mod {new} ({KINDS[dst]['title']})")
            elif Mod.load(root).game != dst:
                raise RiftError(f"{root.name} is not a {KINDS[dst]['title']} mod: the conversion goes into a mod of "
                                "the game the monster becomes part of")
            done = monsters.convert(p, root, games, idxs)
        finally:
            for i in idxs.values():
                i.close()
        out["written"] = [Path(w).relative_to(root).as_posix() if Path(w).is_relative_to(root) else str(w)
                          for w in done["written"]]
        out["notes"] += done["notes"]
        out["mod"] = root.name
        self.log("ok", f"Converted {out['source']} into {out['target']} -> {root.name}")
        return out

    @staticmethod
    def enemy_peak(game: Game) -> int | None:
        """The session's peak from enemy_cap.log ("peak: N of M slots"), the newest one written."""
        try:
            text = (game.state_dir / "logs" / "enemy_cap.log").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        import re
        # at most 9 digits, whole: int() refuses more than 4,300 (a damaged log)
        found = re.findall(r"peak: (\d{1,9}) of \d+ slots", text)
        return int(found[-1]) if found else None

    def mod_root(self, name: str) -> Path:
        if not isinstance(name, str) or not name or name != name.strip() or any(c in name for c in '\\/:*?"<>|') \
                or name in (".", ".."):
            raise RiftError(f"bad mod name {name!r}")
        root = self.workspace / name
        if not (root / MOD_FILE).is_file():
            raise RiftError(f"no mod named {name!r} in {self.workspace}")
        return root

    def mod_file(self, name: str, rel: str) -> Path:
        return arcfolder.safe_member(self.mod_root(name), rel)

    def mods(self) -> list[dict]:
        enabled = set()
        if self.game:
            try:
                enabled = {Path(m["path"]).resolve() for m in install.load_state(self.game).get("mods", [])}
            except RiftError:
                pass
        out = []
        for d in sorted(self.workspace.iterdir(), key=lambda p: p.name.lower()) if self.workspace.is_dir() else []:
            if not (d / MOD_FILE).is_file():
                continue
            try:
                m = Mod.load(d)
                files = [p.relative_to(d).as_posix() for p in sorted(d.rglob("*"))
                         if p.is_file() and p.relative_to(d).parts[0] in ("files", "archives")
                         and p.name.lower() != "readme.txt"]
                out.append({"name": d.name, "title": m.name, "version": m.version, "author": m.author,
                            "description": m.description, "priority": m.priority, "files": files,
                            "enabled": d.resolve() in enabled, "error": None, "game": m.game})
            except RiftError as e:
                out.append({"name": d.name, "title": d.name, "files": [], "enabled": False, "error": str(e)})
        return out

    # -- API ----------------------------------------------------------------
    def api(self, method: str, route: str, q: dict, body: dict) -> dict:
        g = self.game
        if route == "state":
            st = {"version": __version__, "workspace": str(self.workspace), "index": self.index_state,
                  "game": None, "games": self.games(), "mods": self.mods(), "activity": self.activity[-40:]}
            if g:
                if self.exe_ok is None:
                    self.exe_ok = install.sha256_file(g.exe) == g.known_build_sha256
                try:
                    s = install.status(g)
                except RiftError as e:       # a damaged state.json: the rest still shows, the log says why (once)
                    s = {"loader": g.loader_installed(), "mode": install.mode_for(g), "archives": [],
                         "drift": ["state.json"]}
                    with self.lock:
                        told = any(a["msg"] == str(e) for a in self.activity)
                    if not told:
                        self.log("fail", str(e))
                from . import runtime

                rstate = runtime.runtime_state(g.root)
                running = self.running()
                end = runtime.session_end(g.root, running=running)
                if end:
                    end = {**end, "label": runtime.END_LABELS.get(end["reason"], end["reason"]),
                           "sentence": runtime.describe_end(end), "normal": end["reason"] in runtime.NORMAL_ENDS}
                st["game"] = {"root": str(g.root), "kind": g.kind, "title": g.title, "exe": g.exe.name,
                              "build": KINDS[g.kind]["build"], "loader_supported": g.kind == "ddda",
                              "build_verified": self.exe_ok, "loader": s["loader"],
                              "mode": s["mode"], "archives_changed": len(s["archives"]), "drift": s["drift"],
                              "running": running,
                              "crash_reports": [r["name"] for r in reversed(runtime.list_reports(g.state_dir / "logs"))],
                              "safe_mode": rstate["safe_mode"], "quarantine": rstate["quarantine"],
                              "last_session": end, "plugins": self.plugin_summary(),
                              "loader_version": self.loader_versions(),
                              "panel_key": self.panel_key(g)}
            st["ddo_server"] = self.ddo_server()
            return st
        if route == "plugins" and method == "GET":
            from . import plugins

            game = self.need_game()
            if game.kind != "ddda":
                return {"supported": False, "plugins": [], "loader": None}
            return {"supported": True, "dir": str(plugins.plugins_dir(game)), "plugins": plugins.describe(game),
                    "loader": {**self.loader_versions(), "installed_file": game.loader_installed(),
                               "settings": plugins.loader_settings(game)},
                    "enemy_peak": self.enemy_peak(game)}
        if route in ("plugins/set", "plugins/toggle", "plugins/add") and method == "POST":
            from . import plugins

            game = self.need_game()
            if game.kind != "ddda":
                raise RiftError(f"{game.title} has no loader plugins")
            name = body.get("name")
            if not isinstance(name, str):
                raise RiftError("say which plugin")
            if route == "plugins/set":
                out = plugins.set_value(game, name, body.get("section"), body.get("key"), body.get("value"))
                self.log("ok", f"{name}: {out['key']} = {out['value']} (the game reads it at its next start)")
                return out
            if route == "plugins/toggle":
                out = plugins.toggle(game, name, bool(body.get("on")))
                self.log("ok", f"Plugin {name} {'on' if out['enabled'] else 'off'}")
                return out
            out = plugins.add(game, name)
            self.log("ok", f"Plugin {out['file']} installed")
            return out
        if route == "launch" and method == "POST":
            return self.launch()
        if route == "monsters" and method == "GET":
            return self.monster_list()
        if route == "monsters/convert" and method == "POST":
            return self.monster_convert(body)
        if route == "types":
            idx = self.open_index()
            try:
                return {"types": idx.stats()["types"]}
            finally:
                idx.close()
        if route == "help":
            from . import help as helpmod
            tag, text = q.get("tag", ""), body.get("text")
            if text is not None and not isinstance(text, str):
                raise RiftError("text is the editor's text")
            known = tag if tag in helpmod.TOPICS else None
            # every parameter file's YAML is tagged xfs/1: its path says more (a state machine, .fsm)
            topic = q.get("topic") or (known if known != "xfs" else None) \
                or helpmod.topic_for(q.get("path", ""), None if known == "xfs" else text) or known
            h = helpmod.get(topic, q.get("field") or None) if topic else None
            return {"help": h}
        if route == "search":
            try:
                limit = max(1, min(int(q.get("limit", "200")), 1000))
            except ValueError:
                raise RiftError("limit must be a number") from None
            tid = typemap.type_for_extension(q["type"]) if q.get("type") else None
            if q.get("type") and tid is None:              # was: an unknown type searched every type
                raise RiftError(f"no resource type {q['type']!r}")
            idx = self.open_index()
            try:
                rows = idx.search(q.get("q", ""), tid, limit)
            finally:
                idx.close()
            return {"results": [{"path": fsmap.encode_name(r["name"], r["type"]), "ext": r["ext"],
                                 "class": typemap.class_name(r["type"]), "size": r["size"],
                                 "archives": r["archives"], "first_arc": r["first_arc"],
                                 "yaml": params.has_yaml_form(r["type"])} for r in rows]}
        if route == "resource":
            game = self.need_game()
            name, tid = fsmap.decode_path(q.get("path", ""))
            idx = self.open_index()
            try:
                arcs = idx.archives_with(name, tid)
            finally:
                idx.close()
            data = self.read_resource(game, arcs, name, tid)
            out = {"path": q["path"], "class": typemap.class_name(tid), "size": len(data), "archives": arcs,
                   "magic": data[:4].decode("latin-1"), "yaml": None}
            text = params.resource_to_yaml(data, name.decode("latin-1"), tid)
            if text is not None:
                if data[:4] == b"XFS\0":
                    x = xfs.parse(data)
                    out["objects"] = sum(1 for _ in xfs.walk(x.root))
                    if x.root_class.name == "rAIFSM":
                        from . import fsm
                        out["view"] = _cut(fsm.decompile(x, q["path"]))
                out["yaml"] = _cut(text)
            return out
        if route == "mods/new" and method == "POST":
            name = str(body.get("name", "")).strip()
            if not name or any(c in name for c in '\\/:*?"<>|') or name in (".", ".."):
                raise RiftError("give the mod a name without \\ / : * ? \" < > |")
            kind = body.get("game") or (self.game.kind if self.game else "ddda")
            if not isinstance(kind, str) or kind not in KINDS:
                raise RiftError(f"game is one of {', '.join(KINDS)}")
            m = Mod.create(self.workspace / name, name, str(body.get("author", "")), kind)
            self.log("ok", f"Created mod {m.name}")
            return {"ok": True}
        if route == "port" and method == "POST":
            from . import port
            from .index import Index

            game = self.need_game()
            root = self.mod_root(body.get("mod"))
            m = Mod.load(root)
            if m.game == game.kind:
                raise RiftError(f"{root.name} is a {game.title} mod: use Add to mod")
            for key in ("as", "like"):
                if body.get(key) and not isinstance(body[key], str):
                    raise RiftError(f"{key} is a resource's engine path, e.g. model/em/e52/e5200/e5200.mod")
            other = next((g for k, g in (getattr(self, "_found", None) or {}).items() if k == m.game), None) \
                or find_game(m.game)
            src_idx, dst_idx = self.open_index(), Index(other)
            try:
                if dst_idx.pending():
                    self.log("ok", f"Indexing {other.title} for the port (only the first time)")
                    dst_idx.refresh()
                path = str(body.get("path", ""))
                # a player motion list rebakes the weapon joints by default (retarget.py); "retarget": false
                # ports it as it is
                rebake = bool(body["retarget"]) if "retarget" in body else port.is_player_motion(path)
                res = port.into_mod(root, game, other, src_idx, dst_idx, path,
                                    body.get("as") or None, body.get("like") or None, rebake=rebake)
            finally:
                src_idx.close()
                dst_idx.close()
            files = [Path(w).relative_to(root).as_posix() for w in res.written]
            self.log("ok", f"Ported {body.get('path')} into {root.name} ({other.title})")
            return {"ok": True, "files": files, "notes": res.notes}
        if route == "mods/extract" and method == "POST":
            game = self.need_game()
            root = self.mod_root(body.get("mod"))
            kind = Mod.load(root).game
            if kind != game.kind:
                raise RiftError(f"{root.name} is a {KINDS[kind]['title']} mod; bring {game.title} files into it "
                                f"with: riftstone port <resource> --mod \"{root}\"")
            rel = body.get("path", "")
            if not isinstance(rel, str):
                raise RiftError("path is a resource's engine path, e.g. param/status/enemy.statusparam")
            name, tid = fsmap.decode_path(rel)
            idx = self.open_index()
            try:
                arcs = idx.archives_with(name, tid)
            finally:
                idx.close()
            data = self.read_resource(game, arcs, name, tid)
            as_yaml = params.is_editable_resource(data, tid)
            target = root / "files" / (fsmap.encode_name(name, tid) + (".yaml" if as_yaml else ""))
            if target.exists():
                raise RiftError(f"{target.relative_to(root).as_posix()} is already in the mod")
            payload = params.resource_to_yaml(data, name.decode("latin-1"), tid).encode() if as_yaml else data
            arcfolder.write_file(target, payload)
            self.log("ok", f"Added {rel} to {root.name}")
            return {"ok": True, "file": target.relative_to(root).as_posix()}
        if route == "file" and method == "GET":
            f = self.mod_file(q.get("mod"), q.get("rel", ""))
            if f.suffix.lower() not in TEXT_FILES:
                raise RiftError("only text files open in the editor")
            if not f.is_file():
                raise RiftError("that file does not exist")
            return {"text": f.read_text(encoding="utf-8-sig", errors="replace")}
        if route == "file" and method == "POST":
            f = self.mod_file(body.get("mod"), body.get("rel", ""))
            text = str(body.get("text", ""))
            if f.suffix.lower() not in TEXT_FILES:          # as for opening one: a texture became the text
                raise RiftError("only text files are saved from the editor")
            if not f.is_file():
                raise RiftError("that file does not exist")
            try:
                encoded = text.encode("utf-8")
            except UnicodeEncodeError:
                raise RiftError("the text contains characters that cannot be saved as UTF-8") from None
            arcfolder.write_file(f, encoded)
            result = self.validate(text, f.name) if f.suffix.lower() == ".yaml" else {"ok": True}
            self.log("ok" if result["ok"] else "warn", f"Saved {f.name}" + ("" if result["ok"] else " (has errors)"))
            return result
        if route == "validate" and method == "POST":
            return self.validate(str(body.get("text", "")), "editor")
        if route == "lot" and method == "POST":
            return self.lot_edit(body, self.lot_reserved(body))
        if route == "world" and method == "GET":
            return self.world_overview(self.world())
        if route == "world/stage" and method == "GET":
            return self.world_stage(self.world(), q.get("n", ""))
        if route == "world/backdrop" and method == "GET":
            return self.world_backdrop(q.get("n", ""))
        if route == "world/enemy" and method == "GET":
            w = self.world()
            em = w.find_enemy(str(q.get("q", "")))
            return {"id": em, "name": w.enemies[em]["name"], "placements": w.enemies[em]["placements"],
                    "spawns": [{k: v for k, v in s.items() if k != "group"} for s in w.spawns_of(em)]}
        if route == "encounter" and method == "POST":
            return self.encounter(body)
        if route == "dungeon" and method == "POST":
            return self.dungeon(body)
        if route.startswith(("files/", "skins")):
            from . import studiofiles
            out = studiofiles.api(self, method, route, q, body)
            if out is not None:
                return out
        if route == "install" and method == "POST":
            game = self.need_game()
            names = body.get("mods", [])
            if not isinstance(names, list):
                raise RiftError("mods is the list of mod names to install")
            roots = [self.mod_root(n) for n in names]
            idx = self.open_index()
            try:
                rep = install.apply(game, idx, roots, known_vanilla=install.known_vanilla(game))
            finally:
                idx.close()
            msg = f"Installed {len(roots)} mod(s): {len(rep.written)} archive(s) written, {len(rep.restored)} restored"
            self.log("ok", msg)
            from .merging import kept
            for m in rep.merged:
                self.log("ok", f"{m['resource']}: merged from {' and '.join(m['mods'])}, every mod's {kept(m)} kept")
            for r in rep.renumbered:
                what = (f"record {r['record']} of {r['layout']} is record {r['as']}" if "record" in r else
                        f"group {r['group']} of stage {r['stage']} ({r['type']}) is group {r['as']}")
                self.log("info", f"{r['mod']}: {what} in the game (another mod adds the same number there)")
            from . import stage_enemies
            enemy_lines = stage_enemies.describe(rep.stage_enemies or {})
            for level, line in enemy_lines:
                self.log(level, line)
            return {"ok": True, "message": msg, "written": rep.written, "restored": rep.restored,
                    "conflicts": rep.conflicts, "mode": rep.mode, "merged": rep.merged,
                    "renumbered": rep.renumbered, "unmoved": rep.unmoved,
                    "stage_enemies": [line for _, line in enemy_lines]}
        if route == "restore" and method == "POST":
            done = install.restore_all(self.need_game())
            self.log("ok", f"Restored {len(done)} archive(s) to the originals")
            return {"ok": True, "restored": done}
        if route == "switch" and method == "POST":
            self.switch(str(body.get("kind", "")))
            return {"ok": True, "game": self.game.kind}
        if route == "loader" and method == "POST":
            from . import loader

            action = body.get("action")
            if action not in ("install", "remove"):         # anything else removed the loader
                raise RiftError("action is install or remove")
            game = self.need_game()
            idx = self.open_index()
            try:
                r = loader.install_loader(game, idx) if action == "install" else loader.remove_loader(game, idx)
            finally:
                idx.close()
            self.log("ok", f"Loader {'installed' if action == 'install' else 'removed'}")
            return {"ok": True, **r}
        if route == "crash" and method == "GET":
            game = self.need_game()
            name = q.get("name", "")
            if not name.startswith(("crash-", "fatal-", "hang-")) or not name.endswith(".txt") or \
                    any(c in name for c in '/\\:*?"<>|'):
                raise RiftError("bad report name")
            report = game.state_dir / "logs" / name
            if not report.is_file():
                raise RiftError("no such crash report")
            from . import legal, runtime

            text = report.read_text(encoding="utf-8", errors="replace")
            return {"text": text, "explanation": runtime.explain(runtime.parse_report(text), game.root),
                    "support": legal.SUPPORT}
        if route == "live" and method == "GET":
            from . import runtime

            live = runtime.read_live()
            if not live:
                return {"running": False}
            peak = getattr(self, "_peak", (None, 0))
            if live["enemies_active"] is not None:
                peak = (live["pid"], max(peak[1] if peak[0] == live["pid"] else 0, live["enemies_active"]))
                self._peak = peak
            return {"running": not live["exited"], "lines": runtime.describe_live(live),
                    "frame_times_us": live["frame_times_us"][-120:],
                    "enemy_peak": peak[1] if peak[0] == live["pid"] else None,
                    **{k: live[k] for k in ("game_name", "va_used", "va_total", "va_largest_free", "fps", "frames",
                                            "address_space_low", "safe_mode", "hang", "enemies_active",
                                            "enemies_usable", "enemy_slots", "stage", "va_used_peak",
                                            "loader_version", "plugin_list", "fallbacks", "fatals", "missing")}}
        if route == "safe-mode" and method == "POST":
            from . import runtime

            was = runtime.safe_mode_off(self.need_game().root)
            self.log("ok", "Safe mode off: the next start loads plugins and mods" if was else "Safe mode was not on")
            return {"ok": True, "was_on": was}
        if route == "open" and method == "POST":
            os.startfile(self.mod_root(body.get("mod")))  # noqa: S606 - the person clicked "open folder"
            return {"ok": True}
        raise RiftError(f"unknown request {method} /api/{route}")

    @staticmethod
    def read_resource(game: Game, arcs: list[str], name: bytes, tid: int) -> bytes:
        if not arcs:
            raise RiftError("no archive contains that resource")
        e = arc.Archive.read(game.vanilla_arc(arcs[0])).find(name, tid)
        if e is None:
            raise RiftError("the resource index is out of date; restart Studio to rebuild it")
        return e.data()

    def running(self) -> bool:
        """Whether the game is running, checked at most every few seconds (tasklist is slow)."""
        now = time.time()
        if now - getattr(self, "_running_at", 0) > 4:
            self._running = install.game_running(self.game)
            self._running_at = now
        return self._running

    def lot_reserved(self, body: dict) -> tuple[set[int], int | None]:
        """What a record copied in the editor keeps clear of (``modfiles.group_ids``): its group's other layouts as
        the editor's mod holds them and the game's own ids; nothing when the layout or the game cannot be read."""
        from . import lot, modfiles, yamlish

        if body.get("op") != "copy" or not isinstance(body.get("text"), str) or not self.game or self.game.is_ddo:
            return set(), None
        try:
            res = yamlish.parse(body["text"], "editor").get("resource")
            name = res.text.replace("/", "\\") if isinstance(res, yamlish.Scalar) else ""
            if name.lower().endswith(".lot"):
                name = name[:-4]
            if lot.parse_name(name) is None:
                return set(), None
            mod = body.get("mod")
            root = self.mod_root(mod) if isinstance(mod, str) and mod else None
            idx = self.open_index()
        except RiftError:
            return set(), None
        try:
            return modfiles.group_ids(self.game, idx, root, name.encode("latin-1"))
        except (RiftError, UnicodeEncodeError, OSError):
            return set(), None
        finally:
            idx.close()

    @staticmethod
    def lot_edit(body: dict, keep_clear: tuple = (set(), None)) -> dict:
        """Copy or remove a record in the editor's layout text; returns the new text (writes nothing).  A copy's id
        keeps clear of ``keep_clear`` (``lot_reserved``)."""
        from . import lot, yamlish

        text, op, number = body.get("text"), body.get("op"), body.get("number")
        if not isinstance(text, str) or op not in ("copy", "remove"):
            raise RiftError("send the layout text and op: copy or remove")
        if not isinstance(number, int) or isinstance(number, bool):
            raise RiftError("number is the record's number")
        at = body.get("at")
        if at is not None and not (isinstance(at, list) and len(at) == 3
                                   and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in at)):
            raise RiftError("at is [x, y, z]")
        lt = lot.from_yaml(text, "editor")
        new = lot.copy(lt, number, tuple(at) if at else None, *keep_clear) if op == "copy" else lot.remove(lt, number)
        res = yamlish.parse(text, "editor").get("resource")
        name = res.text if isinstance(res, yamlish.Scalar) else None
        return {"text": lot.to_yaml(new, name), "count": new.count}

    # -- the world map ------------------------------------------------------
    def world(self):
        """The world map (built once from the game, then cached on disk and here)."""
        from . import world

        game = self.need_game()
        with self.lock:
            cached = getattr(self, "_world", None)
        if cached is not None:
            return cached
        idx = self.open_index()
        try:
            w = world.load(game, idx)
        finally:
            idx.close()
        with self.lock:
            self._world = w
        self.log("ok", f"World map ready: {len(w.stages)} stages, {len(w.groups):,} groups, {len(w.placements):,} placements")
        return w

    @staticmethod
    def world_overview(w) -> dict:
        return {"stages": [{"stage": s, "rooms": st["rooms"], "placements": st["placements"],
                            "groups": {t: v["groups"] for t, v in st["group_lists"].items() if "_" not in t}}
                           for s, st in sorted(w.stages.items())],
                "totals": {"stages": len(w.stages), "groups": len(w.groups), "layouts": len(w.layouts),
                           "placements": len(w.placements), "enemies": len(w.enemies)},
                "enemies": [{"id": em, "name": e["name"], "placements": e["placements"]}
                            for em, e in sorted(w.enemies.items()) if e["placements"]]}

    @staticmethod
    def world_stage(w, n) -> dict:
        """One stage for the map: its groups, its layouts and every placement as [x, z, y, layout, record,
        name] (names and layouts by index, to keep the open field's 24,000 placements small)."""
        from .encounter import parse_stage

        s = parse_stage(n)
        st = w.stage(s)
        layouts = sorted(name for name, lay in w.layouts.items() if lay["stage"] == s)
        where = {name: i for i, name in enumerate(layouts)}
        names, name_ix, points = [], {}, []
        for p in w.placements:
            i = where.get(p[0])
            if i is None or p[5] is None:
                continue
            if p[4] not in name_ix:
                name_ix[p[4]] = len(names)
                names.append(p[4])
            points.append([p[5][0], p[5][2], p[5][1], i, p[1], name_ix[p[4]]])
        typemap_ = {"e": "enemies", "n": "NPCs and hostile humans", "p": "objects", "t": "AI sensor targets"}
        groups = [{"type": g["type"], "number": g["number"], "dlc": g["dlc"], "units": g["units"],
                   "count_max": g["count_max"], "respawn": g["respawn"], "appear": g["appear"], "hours": g["hours"],
                   "link": g["link"], "cells": g["cells"]} for g in w.groups_of(s)]
        return {"stage": s, "rooms": st["rooms"], "types": typemap_,
                "layouts": [{"name": n_, "path": fsmap.encode_name(n_.encode("latin-1"), typemap.BY_EXT["lot"]),
                             **{k: w.layouts[n_][k] for k in ("x", "z", "type", "number", "records")}}
                            for n_ in layouts],
                "names": [{"id": nm, "name": w.enemies.get(nm, {}).get("name", "")} for nm in names],
                "points": points, "groups": groups, "free": w.free_groups(s)[:5]}

    def world_backdrop(self, n) -> dict:
        """The ground behind a stage's dots, seen from above: its navigation mesh (where the game's walkers can
        stand) for a stage that has one; for the open field (stage 100, which has none) the square of every terrain
        cell that has a layout, 10,000 units on a side (docs/terrain.md: world = the cell's corner + local).  Read
        from the game and kept; nothing is written."""
        from . import nav
        from .encounter import parse_stage

        s = parse_stage(n)
        game = self.need_game()
        key = (str(game.root).lower(), s)
        with self.lock:
            cache = self.__dict__.setdefault("_backdrops", {})
            if key in cache:
                return cache[key]
        out: dict = {"stage": s, "kind": "none"}
        if game.kind == "ddda":
            idx = self.open_index()
            try:
                mesh = nav.stage_mesh(game, idx, s)
            except RiftError:
                mesh = None                  # a mesh that cannot be read is no picture, not an error for the map
            finally:
                idx.close()
            if mesh is not None:
                out = {"stage": s, "kind": "nav", **nav.backdrop(mesh)}
            elif s == 100:
                w = self.world()
                cells = sorted({(lay["x"], lay["z"]) for lay in w.layouts.values() if lay["stage"] == s})
                # a layout's name is <m>m<n>n, and x, z are those two numbers in that order: world X is n's cell
                out = {"stage": s, "kind": "cells", "size": 10000,
                       "cells": [[10000 * z - 500000, 10000 * x - 500000] for x, z in cells]}
        with self.lock:
            cache[key] = out
        return out

    def encounter(self, body: dict) -> dict:
        """Plan (and unless dry_run, write) an encounter into a mod: the CLI's `encounter`."""
        import math

        from . import encounter, lot, skins, studiofiles

        game = self.need_game()

        def num(key, default=None, kind=int):
            v = body.get(key)
            if v is None:                   # absent or null: the default ({"spread": null} reached plan as None)
                return default
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise RiftError(f"{key} is a number")
            try:
                if kind is int and not float(v).is_integer():
                    raise RiftError(f"{key} is a whole number")
                return kind(v)
            except OverflowError:           # float() refuses an int past 1e308 (a 400-digit JSON number)
                raise RiftError(f"{key} is far too large") from None

        def finite(v) -> bool:
            try:
                return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
            except OverflowError:
                return False

        at = body.get("at")
        if isinstance(at, list):
            if len(at) != 3 or not all(finite(v) for v in at):
                raise RiftError("at is [x, y, z] or \"group:N\"")
            at = ",".join(repr(float(v)) for v in at)
        if not isinstance(at, str):
            raise RiftError("at is [x, y, z] or \"group:N\"")
        story = body.get("story")
        if story == "":
            story = None
        if story is not None and not isinstance(story, str):   # a false value is refused, not taken for "unset"
            raise RiftError("story is any, pre or post")
        like = num("like")
        skin = encounter.parse_skins(body.get("skin"))    # a number, "1,2" or [1, 2]
        hours = body.get("hours")                         # "4,19" or [4, 19]: the group's first and last hour
        if isinstance(hours, (str, list)) and not hours:
            hours = None
        if hours is not None:
            if isinstance(hours, list):
                hours = ",".join(str(h) for h in hours)
            if not isinstance(hours, str):
                raise RiftError("hours is the first and the last whole hour, e.g. 4,19")
            hours = encounter.parse_hours(hours)
        w = self.world()
        # a new mod is only created when the encounter is written; until then it plans against the game
        root, new = studiofiles.destination(self, body, game.kind)
        idx = self.open_index()
        try:
            enc = encounter.plan(game, idx, w, root, str(body.get("stage", "")), str(body.get("enemy", "")),
                                 num("count"), at, num("at_once"), num("spread", 250.0, float), num("group"), story,
                                 like, skin, hours)
        finally:
            idx.close()
        gpl_key = (None, enc.gpl_name.encode("latin-1"), typemap.BY_EXT["gpl"])
        clash = studiofiles.clashes(self, None if new else root.name, gpl_key, game.kind)
        notes = list(enc.notes)
        for n in (skin or []):
            owner = studiofiles.skin_owners(self, skins.family_of_enemy(enc.enemy)).get(n)
            if owner is None:
                notes.append(f"skin {n} is not in any mod here yet: make it on the Skins tab (a placement wearing a "
                             f"skin that is not installed: UNKNOWN in game)")
            elif owner != root.name:
                notes.append(f"skin {n} comes from {owner}: install that mod too")
        out = {"stage": enc.stage, "enemy": enc.enemy, "enemy_name": enc.enemy_name, "total": enc.total,
               "points": enc.points, "at": list(enc.at), "group": enc.group, "template_group": enc.template_group,
               "template_distance": round(enc.template_distance), "layout": enc.layout_name, "cell": list(enc.cell),
               "archives": enc.layout_archives, "horde": enc.horde, "notes": notes,
               "spawn_points": [list(r.vec()) for r in lot.parse(enc.layout_data).records],
               "mod": root.name, "new_mod": new is not None, "like": like, "skin": skin, "written": [],
               "shared": [enc.gpl_name.replace("\\", "/") + ".gpl"], "clashes": clash,
               "note": (f"{' and '.join(clash)} also change{'s' if len(clash) == 1 else ''} this stage's group list: "
                        "installed together, the lists are merged and every mod's groups kept (a number two mods both "
                        "add goes to a free one at install, its layouts renamed with it)." if clash else
                        "adds a layout (never clashes) and changes the stage's group list: another mod changing the "
                        "same list is merged with this one at install, every mod's groups kept")}
        if not body.get("dry_run"):
            studiofiles.make_mod(self, root, new)
            out["written"] = [p.relative_to(root).as_posix() for p in encounter.write(enc, root)]
            self.log("ok", f"Encounter: {enc.total} x {enc.enemy} in stage {enc.stage} as group {enc.group} -> {root.name}")
        return out

    def dungeon(self, body: dict) -> dict:
        """Plan (and unless dry_run, write) a whole dungeon into a mod: the CLI's `dungeon`, with every spawn
        point checked on the stage's navigation mesh."""
        from . import bestiary, dungeon, encounter, encounter_plan, mission, studiofiles

        game = self.need_game()
        if game.is_ddo:
            raise RiftError("the dungeon director works on Dark Arisen's stages (Online's spawns are the server's)")
        seed = body.get("seed", 0)
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise RiftError("seed is a whole number")
        pool = body.get("pool") or "stage"
        exclude = body.get("exclude") or ""
        if not isinstance(pool, str) or not isinstance(exclude, str):
            raise RiftError("pool is stage or game; exclude is a list of enemies, e.g. death,bats")
        # checked before float(): a JSON whole number of 400 digits overflowed it (OverflowError, not a refusal)
        spacing = dungeon._spacing(body.get("spacing", dungeon.SPACING))
        w = self.world()
        root, new = studiofiles.destination(self, body, game.kind)     # never an Online mod for a Dark Arisen dungeon
        dry = bool(body.get("dry_run"))
        stage = encounter.parse_stage(str(body.get("stage", "")))
        # the mods that change this stage's group list, before anything is written (a new mod is not one of them)
        gpl_key = (None, f"scr\\st{stage:03d}\\etc\\st{stage:03d}_e".encode("latin-1"), typemap.BY_EXT["gpl"])
        clash = studiofiles.clashes(self, None if new else root.name, gpl_key, game.kind)
        idx = self.open_index()
        try:
            b = bestiary.load(game, idx, w)
            excl = {w.find_enemy(e) for e in exclude.split(",") if e.strip()}
            d = dungeon.direct(game, idx, w, stage, seed=seed, which=pool, exclude=excl, b=b, spacing=spacing,
                               keep_lots=bool(body.get("keep_lot_flags")), mod_root=root)
            entries = encounter_plan.parse(dungeon.plan_text(d))
            # checked before anything is written, or a new mod made: every encounter planned in a scratch copy of the
            # mod first (a refusal there left a new, empty mod behind), every spawn point found on the ground
            done = encounter_plan.apply(game, idx, w, root, entries, dry_run=True)
            proof = dungeon.check(d.space, [enc for _, enc, _ in done])
            if not dry:
                if proof.problems:
                    raise RiftError(f"{len(proof.problems)} spawn point(s) are not on ground reached from the doors "
                                    f"({proof.problems[0]}); nothing written")
                studiofiles.make_mod(self, root, new)
                done = encounter_plan.apply(game, idx, w, root, entries)
        finally:
            idx.close()
        warnings = [f"{enc.enemy}: {n}" for _, enc, _ in done for n in enc.notes
                    if ("spawn point" in n and ("fit" in n or "from walkable" in n)) or "open ground" in n]
        out = {"stage": d.stage, "nav_stage": d.space.nav_stage, "seed": d.seed, "mission": mission.describe(d.beats),
               "beats": [{"kind": p.beat.kind, "side": p.beat.side, "enemy": p.enemy, "name": p.name, "tier": p.tier,
                          "total": p.total, "points": p.points, "at": [round(v) for v in p.site.point],
                          "depth": round(p.site.depth), "room": round(p.site.room / 100, 1), "role": p.site.role,
                          "place": p.site.place, "like": p.like, "always": p.always,
                          "spawn_points": [[round(v) for v in pt] for pt in enc.positions]}
                         for p, (_, enc, _) in zip(d.placed, done)],
               "doors": [[round(v) for v in s.point] for s in d.space.doors], "deepest": round(d.space.deepest),
               "places": len(d.space.sites), "notes": d.notes + warnings,
               "proof": {"points": proof.points, "on_mesh": proof.on_mesh, "in_region": proof.in_region,
                         "farthest": round(proof.farthest), "problems": proof.problems[:8]},
               "mod": root.name, "new_mod": new is not None, "clashes": clash,
               "written": [f.relative_to(root).as_posix() for _, _, files in done for f in files]}
        if not dry:
            self.log("ok", f"Dungeon: stage {d.stage}, seed {d.seed}, {len(done)} encounters -> {root.name}")
        return out

    @staticmethod
    def validate(text: str, source: str) -> dict:
        try:
            raw = params.yaml_to_resource(text, source)     # any YAML format, by its tag
        except RiftError as e:
            return {"ok": False, "error": str(e), "line": getattr(e, "line", None), "column": getattr(e, "column", None)}
        out = {"ok": True, "bytes": len(raw), "summary": "valid"}
        from . import flat
        ext0 = params.yaml_tag(text).split("/")[0]
        if ext0 in flat.SCHEMAS:
            f = flat.parse(raw, ext0)
            recs = [k for k, v in f.data.items() if isinstance(v, list) and v and isinstance(v[0], dict)]
            out["summary"] = f"{len(f.data[recs[0]])} {recs[0]}" if recs else f"{ext0} parameters"
            return out
        magic = bytes(raw[:4])
        if magic == b"XFS\0":
            out["objects"] = sum(1 for _ in xfs.walk(xfs.parse(raw).root))
            out["summary"] = f"{out['objects']} objects"
        elif magic == b"GMD\0":
            from . import gmd
            out["summary"] = f"{len(gmd.parse(raw).messages)} messages"
        elif magic == b"ITL2":
            from . import itl
            out["summary"] = f"{len(itl.parse(raw).records)} items"
        elif magic == b"lot\0":
            from . import lot
            out["summary"] = f"{lot.parse(raw).count} records"
        elif magic in (b"ist\0", b"imx\0"):
            from . import tables
            kind = "sets" if magic == b"ist\0" else "recipes"
            out["summary"] = f"{len(tables.parse(raw).rows)} {kind}"
        elif magic == b"gpl\0":
            from . import gpl
            out["summary"] = f"{len(gpl.parse(raw).groups)} enemy groups"
        elif magic == b"\x25\x12\x12\x20":
            from . import ocl
            out["summary"] = f"{len(ocl.parse(raw).nodes)} collision shapes"
        return out


def _token_ok(given: str, token: str) -> bool:
    """compare_digest on bytes: on str it raises TypeError for any non-ASCII character, and a web page picks
    what the link or the header holds."""
    return secrets.compare_digest(given.encode("utf-8", "replace"), token.encode("ascii"))


def make_handler(studio: Studio, port_ref: list):
    page = _res.files("riftstone").joinpath("studio/index.html").read_text(encoding="utf-8")

    class Handler(BaseHTTPRequestHandler):
        server_version = "RiftstoneStudio"

        def log_message(self, *a):  # quiet console
            pass

        def _host_ok(self) -> bool:
            host = self.headers.get("Host", "")
            return host in (f"127.0.0.1:{port_ref[0]}", f"localhost:{port_ref[0]}")

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
                             "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

        def _dispatch(self, method: str) -> None:
            if not self._host_ok():
                self._send(403, b"forbidden", "text/plain")
                return
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if u.path in ("/", "/index.html") and method == "GET":
                if not _token_ok(q.get("t", ""), studio.token):
                    self._send(403, b"Open Riftstone Studio from the link the 'riftstone studio' command printed.",
                               "text/plain; charset=utf-8")
                    return
                self._send(200, page.replace("__RIFTSTONE_TOKEN__", studio.token).encode("utf-8"), "text/html; charset=utf-8")
                return
            if u.path.startswith("/static/") and method == "GET":
                name = u.path[len("/static/"):]
                if name not in STATIC:
                    self._send(404, b"not found", "text/plain")
                    return
                try:
                    data = _res.files("riftstone").joinpath("studio/" + name).read_bytes()
                except OSError:
                    self._send(404, b"not found", "text/plain")
                    return
                self._send(200, data, STATIC[name])
                return
            if not u.path.startswith("/api/"):
                self._send(404, b"not found", "text/plain")
                return
            if not _token_ok(self.headers.get("X-Riftstone-Token", ""), studio.token):
                self._json(403, {"error": "missing or wrong session token"})
                return
            body = {}
            if method == "POST":
                length = (self.headers.get("Content-Length") or "0").strip()
                if not re.fullmatch(r"[0-9]{1,12}", length):          # "-1": read(-1) waited for the client to close
                    self._json(400, {"error": "bad Content-Length"})
                    return
                n = int(length)
                if n > MAX_BODY:
                    self._json(413, {"error": "request too large"})
                    return
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except (ValueError, RecursionError):                        # RecursionError: nested too deep
                    self._json(400, {"error": "bad JSON"})
                    return
                if not isinstance(body, dict):
                    self._json(400, {"error": "bad request"})
                    return
            try:
                self._json(200, studio.api(method, u.path[5:], q, body))
            except RiftError as e:
                self._json(400, {"error": str(e)})
            except OSError as e:
                self._json(400, {"error": f"file error: {e.strerror or e}"})
            except Exception as e:  # noqa: BLE001 - never crash the server; show the page what happened
                studio.log("fail", f"internal error: {e}")
                self._json(500, {"error": f"internal error: {e}", "trace": traceback.format_exc(limit=4)})

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

    return Handler


def serve(args) -> int:
    from . import ui

    try:
        game = find_game(getattr(args, "game", None))
    except RiftError as e:
        ui.warn(str(e))
        game = None
    chosen = getattr(args, "workspace", None)
    workspace = Path(chosen) if chosen else default_workspace(game)
    studio = Studio(game, workspace, follow=not chosen and mods_env() is None)
    port_ref = [0]
    httpd = ThreadingHTTPServer(("127.0.0.1", getattr(args, "port", 0) or 0), make_handler(studio, port_ref))
    port_ref[0] = httpd.server_address[1]
    url = f"http://127.0.0.1:{port_ref[0]}/?t={studio.token}"
    studio.start_index()
    ui.banner("Studio", mark=True)
    ui.ok(f"Riftstone Studio is running: {url}")
    ui.info(f"mods folder: {workspace}")
    ui.info("keep this window open while you use Studio; Ctrl+C stops it")
    if not getattr(args, "no_browser", False):
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        ui.info("Studio stopped")
    finally:
        httpd.server_close()
    return 0
