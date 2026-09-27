"""Put built archives into the game and take them out again, recoverably.

Two modes:

* overlay -- the Riftstone loader (dinput8.dll) is installed: archives go to
  <game>/riftstone/overlay/<path under nativePC>; the loader opens them
  instead of the originals.  nativePC is never touched.  Dark Arisen mods
  install only this way: the game's own files stay as Steam installed them.
* direct  -- Dragon's Dogma Online, whose client the loader has not run in yet
  (docs/runtime.md): the original archive is first copied to
  <game>/riftstone/vanilla/ and checked byte for byte, then replaced.  Dark
  Arisen archives a Riftstone before this one installed directly are still
  restored from there.

``apply`` is idempotent: it computes the whole desired state from vanilla
plus every enabled mod, writes only archives whose bytes change, and restores
archives no enabled mod touches any more.  It builds every archive and checks
every original before the first write, so a refusal changes nothing.  Every
write goes to a temporary file, is flushed, then renamed over the target, so
an interruption never leaves a half-written archive; the next apply finishes
the job.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import mod as modlib
from .arcfolder import write_file
from .errors import BuildError, RiftError
from .game import Game

STATE_SCHEMA = "riftstone.state/1"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_HASH_CACHE: dict[tuple[str, int, int], str] = {}


def sha256_cached(path: Path) -> str:
    """sha256_file, remembered while the file's size and modification time stay the same.
    For status polling; installs and restores always hash the bytes afresh."""
    st = path.stat()
    key = (str(path).lower(), st.st_size, st.st_mtime_ns)
    digest = _HASH_CACHE.get(key)
    if digest is None:
        digest = sha256_file(path)
        if len(_HASH_CACHE) > 4096:
            _HASH_CACHE.clear()
        _HASH_CACHE[key] = digest
    return digest


def _process_names() -> list[str] | None:
    """Executable names of running processes via a Toolhelp snapshot (milliseconds, no console)."""
    try:
        import ctypes
        from ctypes import wintypes

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                        ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                        ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                        ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                        ("szExeFile", ctypes.c_wchar * 260)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        snap = k32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
        if not snap or snap == wintypes.HANDLE(-1).value:
            return None
        names = []
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            ok = k32.Process32FirstW(snap, ctypes.byref(entry))
            while ok:
                names.append(entry.szExeFile)
                ok = k32.Process32NextW(snap, ctypes.byref(entry))
        finally:
            k32.CloseHandle(snap)
        return names
    except (OSError, AttributeError, ValueError):
        return None


def game_running(game: Game) -> bool:
    """True when the game's exe (DDDA.exe or DDO.exe) is running."""
    return exe_running(game.exe.name)


def exe_running(exe: str) -> bool:
    """True when a process of that executable name is running (no game folder needed)."""
    names = _process_names()
    if names is not None:
        return any(n.lower() == exe.lower() for n in names)
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe}", "/NH", "/FO", "CSV"],
                             capture_output=True, text=True, timeout=15,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return exe.lower() in out.lower()


class Lock:
    """One Riftstone writer per game install."""

    def __init__(self, game: Game):
        self.path = game.state_dir / ".lock"

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    pid = int(self.path.read_text() or 0)
                except (OSError, ValueError):
                    pid = 0
                if pid and _pid_alive(pid):
                    raise RiftError(f"another Riftstone process (pid {pid}) is changing this game; wait for it") from None
                self.path.unlink(missing_ok=True)
        raise RiftError(f"could not take {self.path}")

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)


def _pid_alive(pid: int) -> bool:
    if pid == os.getpid():
        return True
    try:
        import ctypes

        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_uint32()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259  # STILL_ACTIVE
    except Exception:  # noqa: BLE001
        return True


VANILLA_FILES = {"ddda": "data/vanilla_archives.json", "ddo": "data/vanilla_archives_ddo.json.gz"}


def known_vanilla(game: Game | None = None) -> dict[str, str] | None:
    """Archive name (lower case) -> digest of the pristine build ('<sha256 hex>' for DDDA's Steam
    depot, 'crc32:<hex>' from DDO's distribution RAR), or None if not shipped."""
    kind = game.kind if game is not None else "ddda"
    try:
        import gzip
        from importlib import resources as _res

        raw = _res.files("riftstone").joinpath(VANILLA_FILES[kind]).read_bytes()
        if raw[:2] == bytes((0x1F, 0x8B)):  # gzip
            raw = gzip.decompress(raw)
        return {k.lower(): v for k, v in json.loads(raw)["archives"].items()}
    except (OSError, ValueError, KeyError):
        return None


def crc32_file(p: Path) -> str:
    import zlib

    c = 0
    with open(p, "rb") as fh:
        while b := fh.read(1 << 22):
            c = zlib.crc32(b, c)
    return f"crc32:{c:08x}"


def matches_vanilla(p: Path, want: str, sha256: str | None = None) -> bool:
    """True when file p has the recorded vanilla digest (either form)."""
    if want.startswith("crc32:"):
        return crc32_file(p) == want
    return (sha256 or sha256_file(p)) == want


def _state_problem(state) -> str | None:
    """What is wrong with the shape of a state.json that apply wrote (None when nothing is)."""
    if not isinstance(state, dict):
        return "it is not an object"
    mods = state.get("mods", [])
    if not isinstance(mods, list):
        return "its mods are not a list"
    for m in mods:
        if not (isinstance(m, dict) and all(isinstance(m.get(k), str) for k in ("path", "name", "version"))
                and m["path"] and isinstance(m.get("priority"), int)):
            return f"a mod entry is not {{path, name, version, priority}}: {json.dumps(m)[:80]}"
    if state.get("mode") not in (None, "overlay", "direct"):
        return f"its mode is {json.dumps(state['mode'])[:40]}"
    for key in ("archives", "server"):
        records = state.get(key, {})
        if not isinstance(records, dict):
            return f"its {key} are not an object"
        for name, entry in records.items():
            if not (isinstance(entry, dict) and isinstance(entry.get("sha256"), str)
                    and isinstance(entry.get("vanilla_sha256", ""), (str, type(None)))):
                return f"the record of {name} is not {{sha256, ...}}"
    if state.get("server") and not isinstance(state.get("server_assets"), str):
        return "it has server files but no server folder"
    return None


def load_state(game: Game) -> dict:
    f = game.state_dir / "state.json"
    if not f.is_file():
        return {"schema": STATE_SCHEMA, "mods": [], "archives": {}}
    try:
        state = json.loads(f.read_text(encoding="utf-8"))
    except ValueError as e:
        problem = str(e)
    else:
        if isinstance(state, dict) and state.get("schema") != STATE_SCHEMA:
            raise RiftError(f"{f} was written by an incompatible Riftstone")
        problem = _state_problem(state)
        if problem is None:
            return state
    # Without its record Riftstone cannot tell what it changed, so it cannot put it back itself.
    undo = ("restore the game's archives (ddon text restore, or re-extract them from the client RAR) and the "
            "server's files" if game.is_ddo else "verify the game files in Steam, delete riftstone\\overlay")
    raise RiftError(f"{f} is damaged ({problem}), so Riftstone cannot tell what it changed: {undo}, then delete "
                    f"state.json")


def save_state(game: Game, state: dict) -> None:
    state["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    write_file(game.state_dir / "state.json", json.dumps(state, indent=1).encode("utf-8"))


def mode_for(game: Game) -> str:
    return "overlay" if game.loader_installed() else "direct"


NEEDS_LOADER = ("Riftstone serves Dark Arisen mods through its loader, so the game's own files stay as Steam "
                "installed them: install the loader first (Riftstone.cmd loader install, or the Loader tile in "
                "Studio), then install the mods")


@dataclass
class ApplyReport:
    mode: str
    written: list[dict] = field(default_factory=list)
    restored: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    same_as_vanilla: list[str] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    dry_run: bool = False
    server_written: list[str] = field(default_factory=list)    # DDO: asset files written into the server
    server_restored: list[str] = field(default_factory=list)
    server_assets: str | None = None
    merged: list[dict] = field(default_factory=list)       # files several mods change, merged (gplmerge, servermerge)
    renumbered: list[dict] = field(default_factory=list)
    unmoved: list[dict] = field(default_factory=list)
    loose_written: list[str] = field(default_factory=list)     # loose/ files written into the overlay
    loose_removed: list[str] = field(default_factory=list)


def _target(game: Game, arc: str, mode: str) -> Path:
    live = game.arc_path(arc)
    return game.overlay_dir / live.relative_to(game.native) if mode == "overlay" else live


def _backup(game: Game, arc: str, known_vanilla: dict[str, str] | None) -> str:
    """Copy the original archive aside once, verified; returns its sha256."""
    live = game.arc_path(arc)
    backup = game.vanilla_dir / live.relative_to(game.native)
    if backup.is_file():
        return sha256_file(backup)
    digest = sha256_file(live)
    if known_vanilla is not None:
        want = known_vanilla.get(arc.lower())
        if want is not None and not matches_vanilla(live, want, digest):
            fix = ("restore the original (ddon text restore, or re-extract it from the client RAR)" if game.is_ddo
                   else "verify the game files in Steam first")
            raise BuildError(f"{arc}.arc is not the original file (another tool changed it). Riftstone will not "
                             f"back up a modified archive as 'vanilla'; {fix}.")
    backup.parent.mkdir(parents=True, exist_ok=True)
    tmp = backup.with_name(backup.name + ".riftstone-tmp")
    shutil.copyfile(live, tmp)
    if sha256_file(tmp) != digest:
        tmp.unlink(missing_ok=True)
        raise BuildError(f"backup of {arc}.arc did not verify; nothing was changed")
    os.replace(tmp, backup)
    return digest


def apply(game: Game, index, mod_roots: list[Path], dry_run: bool = False,
          known_vanilla: dict[str, str] | None = None, progress=None, mode: str | None = None) -> ApplyReport:
    """Make the game match vanilla + the given mods (in priority order).  Every archive is built, and every
    original a change replaces or puts back is checked (and kept aside) before the first file is written, so
    a refusal leaves the game as it was.  Built archives wait in <game>\\riftstone\\staging until then."""
    mode = mode or mode_for(game)
    # Overlay files may change under a running game: each write is a fresh file renamed into
    # place, and Windows refuses the rename while the game holds that archive open.  nativePC
    # files (direct mode) are never swapped under a running game.
    if not dry_run and mode == "direct" and game_running(game):
        raise RiftError("Dragon's Dogma is running. Close the game, then install again "
                        "(or install the loader: with it, mods can be updated while the game runs).")
    seen: set[str] = set()
    mods = []
    for r in mod_roots:                             # each folder once
        where = str(Path(r).resolve()).lower()
        if where not in seen:
            seen.add(where)
            mods.append(modlib.Mod.load(r))
    with Lock(game):
        state = load_state(game)
        # the numbers the last install gave groups that several mods add stay theirs (gplmerge.renumber)
        p = modlib.plan(game, index, mods, keep=state.get("renumbered"),
                        installed=[m.get("name") for m in state.get("mods", []) if isinstance(m, dict)])
        modlib.check_plan(p)
        report = ApplyReport(mode, conflicts=p.conflicts, dry_run=dry_run, merged=p.merged,
                             renumbered=p.renumbered, unmoved=p.unmoved)
        if state.get("mode") and state["mode"] != mode and state.get("archives"):
            raise RiftError(f"mods were installed in {state['mode']} mode; run 'riftstone restore' before switching to {mode}")
        installed: dict = state.setdefault("archives", {})
        staging = game.state_dir / "staging"
        shutil.rmtree(staging, ignore_errors=True)      # what an interrupted run left there
        try:
            desired: set[str] = set()
            todo: list[tuple[str, Path, Path, dict]] = []
            for arc_name in sorted(p.archives):
                built = modlib.build_archive(game, arc_name, p.archives[arc_name])
                if not built.replaced and not built.added:
                    report.same_as_vanilla.append(arc_name)   # the mod's files equal the originals here
                    continue
                desired.add(arc_name)
                digest = hashlib.sha256(built.data).hexdigest()
                target = _target(game, arc_name, mode)
                entry = {"sha256": digest, "replaced": built.replaced, "added": built.added,
                         "mods": sorted({m for c in p.archives[arc_name] for m in c.mods})}
                if target.is_file() and installed.get(arc_name, {}).get("sha256") == digest and sha256_file(target) == digest:
                    report.unchanged.append(arc_name)
                    continue
                if mode == "direct" and not game.is_ddo:
                    raise RiftError(NEEDS_LOADER)          # before anything is written
                report.written.append({"archive": arc_name, **entry, "bytes": len(built.data)})
                if progress:
                    progress.advance(1, arc_name)
                if not dry_run:
                    staged = staging / f"{len(todo)}.arc"      # on disk, not in memory: a mod can touch many
                    staged.parent.mkdir(parents=True, exist_ok=True)
                    staged.write_bytes(built.data)
                    todo.append((arc_name, target, staged, entry))
            drops = sorted(set(installed) - desired)
            report.restored += drops
            if not dry_run:
                if mode == "direct":
                    for arc_name, _, _, entry in todo:
                        entry["vanilla_sha256"] = _backup(game, arc_name, known_vanilla)
                if state.get("mode", mode) == "direct":
                    for arc_name in drops:
                        _original(game, arc_name, installed[arc_name])
            server = _plan_server(game, mods, state, report, dry_run)
            # loose files: checked now, so a refusal writes nothing; written after the archives
            _apply_loose(game, index, mods, state, report if dry_run else ApplyReport(mode), True, mode)
            if dry_run:
                return report
            for arc_name, target, staged, entry in todo:
                try:
                    write_file(target, staged.read_bytes())
                except PermissionError:
                    raise RiftError(f"{arc_name}.arc is in use by the game right now; leave that area "
                                    "(or close the game) and save again") from None
                if sha256_file(target) != entry["sha256"]:
                    raise BuildError(f"{target} did not verify after writing; run 'riftstone restore'")
                installed[arc_name] = entry
                state["mode"] = mode
                save_state(game, state)   # after every archive: an interruption loses nothing
            for arc_name in drops:
                _restore_one(game, arc_name, installed[arc_name], state.get("mode", mode))
                del installed[arc_name]
                save_state(game, state)
            if server:
                _write_server(game, state, server)
            _apply_loose(game, index, mods, state, report, False, mode)
            state["mods"] = [{"path": str(m.root), "name": m.name, "version": m.version, "priority": m.priority}
                             for m in mods]
            if p.renumbered:
                state["renumbered"] = p.renumbered
            else:
                state.pop("renumbered", None)
            if not installed:
                state.pop("mode", None)
            save_state(game, state)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    return report


def _server_target(assets: Path, rel: str) -> Path:
    parts = rel.split("/")
    if any(p in ("", ".", "..") or ":" in p for p in parts):
        raise RiftError(f"bad server file path {rel!r}")
    return assets.joinpath(*parts)


def _plan_server(game: Game, mods: list, state: dict, report: ApplyReport, dry_run: bool):
    """DDO: what makes the local server's asset folder match its originals + the mods' server/ files: (the
    folder, the files to write, the files to put back), or None when there is nothing to do.  The spawn table
    and the shops several mods change are merged against the server's own (servermerge.py); for any other file
    the later mod's copy wins.  It writes no server file; each original a file replaces for the first time is
    kept aside and verified, and each one to put back is checked."""
    wanted: dict[str, tuple[bytes, list[str]]] = {}
    copies: dict[str, dict[str, bytes]] = {}
    fights: dict[str, list[dict]] = {}
    for m in sorted(mods, key=lambda m: (m.priority, m.name.lower())):
        for rel, data in modlib.collect_server(m).items():
            prev = wanted.get(rel)
            if prev is not None and prev[0] != data:
                fights.setdefault(rel, []).append({"archive": "server", "resource": rel, "loser": prev[1][-1],
                                                   "winner": m.name})
            wanted[rel] = (data, (prev[1] if prev else []) + [m.name])
            copies.setdefault(rel, {})[m.name] = data
    done: dict = state.setdefault("server", {})
    if not wanted and not done:
        return None
    from . import ddo, servermerge

    assets = Path(state["server_assets"]) if done and state.get("server_assets") else ddo.need_assets(game)
    report.server_assets = str(assets)
    backups = game.state_dir / "server-vanilla"
    writes = []
    for rel, found in sorted(fights.items()):
        why = None
        if rel in servermerge.FILES:
            base = _server_base(game, assets, rel, done)
            try:
                if base is None:
                    raise servermerge.MergeError("the server has no such file to merge against")
                data, fs = servermerge.merge(rel, base, list(copies[rel].items()))
            except servermerge.MergeError as e:
                why = str(e)
            else:
                wanted[rel] = (data, wanted[rel][1])
                report.merged.append({"archive": "server", "resource": rel, "mods": list(copies[rel])})
                report.conflicts.extend({"archive": "server", "resource": rel, "loser": lose, "winner": win,
                                         "detail": what} for lose, win, what in fs)
                continue
        report.conflicts.extend({**f, "detail": f"not merged: {why}"} if why else f for f in found)
    for rel, (data, names) in sorted(wanted.items()):
        target = _server_target(assets, rel)
        digest = hashlib.sha256(data).hexdigest()
        if target.is_file() and done.get(rel, {}).get("sha256") == digest and sha256_file(target) == digest:
            report.unchanged.append("server/" + rel)
            continue
        report.server_written.append(rel)
        if dry_run:
            continue
        entry = {"sha256": digest, "mods": names}
        if rel in done:
            entry["vanilla_sha256"] = done[rel].get("vanilla_sha256")
        elif target.is_file():   # first change to this file: keep the original, verified
            b = _server_target(backups, rel)
            b.parent.mkdir(parents=True, exist_ok=True)
            orig = sha256_file(target)
            if not b.is_file():
                shutil.copyfile(target, b)
            if sha256_file(b) != orig:
                raise BuildError(f"the backup of server file {rel} did not verify; nothing was changed")
            entry["vanilla_sha256"] = orig
        else:
            entry["vanilla_sha256"] = None   # a new file: restore removes it
        writes.append((rel, target, data, entry))
    drops = sorted(set(done) - set(wanted))
    report.server_restored += drops
    if not dry_run:
        for rel in drops:
            _server_original(game, rel, done[rel])
    return assets, writes, drops


def _write_server(game: Game, state: dict, plan) -> None:
    """Write what _plan_server planned, recording each file as it lands."""
    assets, writes, drops = plan
    done = state["server"]
    for rel, target, data, entry in writes:
        write_file(target, data)
        if sha256_file(target) != entry["sha256"]:
            raise BuildError(f"{target} did not verify after writing; run 'riftstone restore'")
        done[rel] = entry
        state["server_assets"] = str(assets)
        save_state(game, state)
    for rel in drops:
        _restore_server_one(game, assets, rel, done[rel])
        del done[rel]
        save_state(game, state)
    if not done:
        state.pop("server_assets", None)


def _server_base(game: Game, assets: Path, rel: str, done: dict) -> bytes | None:
    """The server's own copy of a file, to merge against: the backup install keeps once it has changed the file
    (checked), else the file in the asset folder; None when the server has no such file."""
    if rel in done:
        want = done[rel].get("vanilla_sha256")
        if want is None:
            return None                     # a file the mods added
        b = _server_target(game.state_dir / "server-vanilla", rel)
        if not b.is_file() or sha256_file(b) != want:
            raise RiftError(f"the original of server file {rel} is missing or damaged in {b.parent}")
        return b.read_bytes()
    t = _server_target(assets, rel)
    return t.read_bytes() if t.is_file() else None


def _loose_target(game: Game, rel: str) -> Path:
    parts = rel.split("/")
    if any(p in ("", ".", "..") or ":" in p for p in parts):
        raise RiftError(f"bad loose file path {rel!r}")
    return game.overlay_dir.joinpath(*parts)


def _apply_loose(game: Game, index, mods: list, state: dict, report: ApplyReport, dry_run: bool, mode: str) -> None:
    """Make riftstone/overlay's loose files match the mods' loose/ folders (later mods win a path).  A loose
    resource must be one no archive of the game holds: the game only opens loose files for those."""
    from . import fsmap

    wanted: dict[str, tuple[bytes, list[str]]] = {}
    for m in sorted(mods, key=lambda m: (m.priority, m.name.lower())):
        for rel, data in modlib.collect_loose(m).items():
            if not rel.lower().endswith(".skills"):
                name, tid = fsmap.decode_path(rel)
                if index is not None and index.archives_with(name, tid):
                    raise BuildError(f"{m.name}: loose/{rel} is a resource the game's archives hold; put it under "
                                     "files/ (every archive) or archives/<archive>.arc/ instead")
            prev = wanted.get(rel)
            if prev is not None and prev[0] != data:
                report.conflicts.append({"archive": "loose", "resource": rel, "loser": prev[1][-1], "winner": m.name})
            wanted[rel] = (data, (prev[1] if prev else []) + [m.name])
    done: dict = state.setdefault("loose", {})
    if wanted and mode != "overlay":
        raise RiftError("loose files are served by the Riftstone loader: install it first (riftstone loader install)")
    for rel, (data, names) in sorted(wanted.items()):
        target = _loose_target(game, rel)
        digest = hashlib.sha256(data).hexdigest()
        if target.is_file() and done.get(rel, {}).get("sha256") == digest and sha256_file(target) == digest:
            report.unchanged.append("loose/" + rel)
            continue
        if target.is_file() and rel not in done:          # refused in the check before anything is written
            raise RiftError(f"{target} is already there and Riftstone did not put it there; move it away first")
        report.loose_written.append(rel)
        if dry_run:
            continue
        write_file(target, data)
        if sha256_file(target) != digest:
            raise BuildError(f"{target} did not verify after writing; run 'riftstone restore'")
        done[rel] = {"sha256": digest, "mods": names}
        save_state(game, state)
    for rel in sorted(set(done) - set(wanted)):
        report.loose_removed.append(rel)
        target = _loose_target(game, rel)
        if dry_run and target.is_file() and sha256_file(target) != done[rel].get("sha256"):
            raise RiftError(f"{target} changed since Riftstone wrote it; it was left in place")
        if not dry_run:
            _remove_loose_one(game, rel, done[rel])
            del done[rel]
            save_state(game, state)
    if not done:
        state.pop("loose", None)


def _remove_loose_one(game: Game, rel: str, entry: dict) -> None:
    """Remove a loose file Riftstone wrote (only if it still holds what Riftstone wrote), then the folders
    it leaves empty inside the overlay."""
    target = _loose_target(game, rel)
    if target.is_file():
        if sha256_file(target) != entry.get("sha256"):
            raise RiftError(f"{target} changed since Riftstone wrote it; it was left in place")
        try:
            target.unlink()
        except PermissionError:
            raise RiftError(f"{target} is in use by the game right now; try again later") from None
    d = target.parent
    while d != game.overlay_dir and game.overlay_dir in d.parents:
        try:
            d.rmdir()
        except OSError:
            break
        d = d.parent


def _server_original(game: Game, rel: str, entry: dict) -> Path | None:
    """The kept original of a server file, verified; None for a file a mod added (restore removes it)."""
    want = entry.get("vanilla_sha256")
    if want is None:
        return None
    backup = _server_target(game.state_dir / "server-vanilla", rel)
    if not backup.is_file() or sha256_file(backup) != want:
        raise RiftError(f"the original of server file {rel} is missing or damaged in {backup.parent}")
    return backup


def _restore_server_one(game: Game, assets: Path, rel: str, entry: dict) -> None:
    target = _server_target(assets, rel)
    backup = _server_original(game, rel, entry)
    if backup is None:
        target.unlink(missing_ok=True)
        return
    write_file(target, backup.read_bytes())
    if sha256_file(target) != entry["vanilla_sha256"]:
        raise RiftError(f"restoring server file {rel} did not verify")


def _original(game: Game, arc_name: str, entry: dict) -> tuple[Path, str]:
    """The kept original of an archive replaced in direct mode, verified: its path and sha256."""
    backup = game.vanilla_dir / game.arc_path(arc_name).relative_to(game.native)
    if not backup.is_file():
        raise RiftError(f"no vanilla backup for {arc_name}.arc; verify the game files in Steam to repair it")
    want = entry.get("vanilla_sha256") or sha256_file(backup)
    if sha256_file(backup) != want:
        raise RiftError(f"the vanilla backup of {arc_name}.arc is damaged; verify the game files in Steam")
    return backup, want


def _restore_one(game: Game, arc_name: str, entry: dict, mode: str) -> None:
    target = _target(game, arc_name, mode)
    if mode == "overlay":
        try:
            target.unlink(missing_ok=True)
        except PermissionError:
            raise RiftError(f"{arc_name}.arc is in use by the game right now; try again after leaving that area") from None
        return
    backup, want = _original(game, arc_name, entry)
    tmp = target.with_name(target.name + ".riftstone-tmp")
    shutil.copyfile(backup, tmp)
    os.replace(tmp, target)
    if sha256_file(target) != want:
        raise RiftError(f"restoring {arc_name}.arc did not verify; verify the game files in Steam")


def restore_all(game: Game) -> list[str]:
    """Return every archive Riftstone changed to its original bytes."""
    with Lock(game):
        state = load_state(game)
        mode = state.get("mode", mode_for(game))
        if mode == "direct" and state.get("archives") and game_running(game):
            raise RiftError("Dragon's Dogma is running. Close the game first.")
        done = []
        for arc_name, entry in sorted(state.get("archives", {}).items()):
            _restore_one(game, arc_name, entry, mode)
            done.append(arc_name)
        if state.get("server"):
            assets = Path(state["server_assets"])
            for rel, entry in sorted(state["server"].items()):
                _restore_server_one(game, assets, rel, entry)
                done.append("server/" + rel)
            state["server"] = {}
            state.pop("server_assets", None)
        for rel, entry in sorted(state.get("loose", {}).items()):
            _remove_loose_one(game, rel, entry)
            done.append("loose/" + rel)
        state.pop("loose", None)
        state["archives"] = {}
        state["mods"] = []
        state.pop("mode", None)
        state.pop("renumbered", None)
        save_state(game, state)
        return done


def status(game: Game) -> dict:
    state = load_state(game)
    drift = []
    mode = state.get("mode", mode_for(game))
    for arc_name, entry in state.get("archives", {}).items():
        t = _target(game, arc_name, mode)
        if not t.is_file() or sha256_cached(t) != entry["sha256"]:
            drift.append(arc_name)
    server = state.get("server", {})
    if server:
        assets = Path(state.get("server_assets", ""))
        for rel, entry in server.items():
            t = _server_target(assets, rel)
            if not t.is_file() or sha256_file(t) != entry["sha256"]:
                drift.append("server/" + rel)
    loose = state.get("loose", {})
    for rel, entry in loose.items():
        t = _loose_target(game, rel)
        if not t.is_file() or sha256_cached(t) != entry["sha256"]:
            drift.append("loose/" + rel)
    return {"mode": mode, "loader": game.loader_installed(), "mods": state.get("mods", []),
            "archives": sorted(state.get("archives", {})), "drift": drift,
            "server": sorted(server), "server_assets": state.get("server_assets"), "loose": sorted(loose)}
