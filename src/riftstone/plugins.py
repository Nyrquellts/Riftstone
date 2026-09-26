"""The loader's plugins: what each does, on or off, its settings, and what it last wrote in its log.

A plugin is a ``.asi``/``.dll`` in ``<game>\\riftstone\\plugins``; the loader loads every one there at
start, in name order.  A plugin switched off waits in ``riftstone\\plugins\\off\\`` with its settings
(the loader reads only the folder itself, never a subfolder).  A plugin's settings are the ``.ini`` of
the same name next to it; Studio changes only the values of keys the file already has and keeps every
comment, so the file stays as readable as it was.  The loader's own settings (``riftstone_loader.ini``
next to the game) are handled the same way under the name ``loader``.

Everything here waits for the game to close before moving a file: plugins load once, at start.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from . import install
from .errors import RiftError
from .game import Game

SUFFIXES = (".asi", ".dll")
OFF = "off"
LOADER = "loader"

# What each known plugin is, in a line a player understands.  "own": written for Riftstone (its
# source is in native/plugins); anything else is shown as it is, by file name.
CATALOG: dict[str, dict] = {
    "enemy_cap": {
        "title": "Enemy cap",
        "summary": "More enemies active at once: 30 by default instead of the game's 10 (10 to 64).",
        "own": True,
    },
    "enemy_skins": {
        "title": "Enemy skins",
        "summary": "Chosen chimeras wear another look, such as Online's White and Shadow Chimeras.",
        "own": True,
    },
    "lod_tuner": {
        "title": "LOD tuner",
        "summary": "Scenery keeps its full detail until it is tiny on screen: far less pop-in.",
        "own": True,
    },
    "inclination_lock": {
        "title": "Inclination lock",
        "summary": "Your main pawn's inclinations stay where you set them.",
        "own": True,
    },
    "save_backup": {
        "title": "Save backup",
        "summary": "Copies your save while you play, so one bad session can be undone.",
        "own": True,
    },
    "compat": {
        "title": "Online skills (experimental)",
        "summary": "Runs Dragon's Dogma Online skills (the Alchemist's) in place of the skills you equip; "
                   "their files are converted on your computer from your own Online client.",
        "own": True,
    },
    "free_sprint": {
        "title": "Free sprint",
        "summary": "Sprinting costs no stamina while you are out of battle.",
        "own": True,
    },
    "draw_distance": {
        "title": "Draw distance",
        "summary": "Objects and grass show farther out, and enemies can stay active farther, at any View Range.",
        "own": True,
    },
    "six_skill_warrior": {
        "title": "Six-skill Warrior",
        "summary": "A Warrior gets six skills like a staff: three more on the secondary-weapon skill button.",
        "own": True,
    },
    LOADER: {
        "title": "Riftstone loader",
        "summary": "Serves your mods without touching the game's files, reports crashes, loads the plugins.",
        "own": True,
    },
}

# Checks for values Studio lets a person type, by (file stem, section, key).  A key not listed takes a
# short line without ';' '=' '[' or a line break.
_RULES: dict[tuple[str, str, str], tuple] = {
    ("enemy_cap", "enemy_cap", "slots"): ("int", 10, 64),
    ("enemy_cap", "enemy_cap", "record"): ("int", 0, 1),
    ("lod_tuner", "lod", "enabled"): ("int", 0, 1),
    ("lod_tuner", "lod", "poppixels"): ("int", 0, 512),
    ("lod_tuner", "lod", "scale"): ("auto_float", 0.25, 8.0),
    ("lod_tuner", "lod", "characters"): ("float", 0.25, 8.0),
    ("lod_tuner", "lod", "maxdistance"): ("int", 100, 100000),
    ("lod_tuner", "lod", "screenheight"): ("auto_int", 240, 8640),
    ("lod_tuner", "lod", "fieldofview"): ("auto_float", 10.0, 170.0),
    ("compat", "levels", "unlearned"): ("int", 1, 10),
    ("compat", "levels", "learned"): ("int", 1, 10),
    ("compat", "levels", "level2"): ("int", 1, 10),
    ("compat", "levels", "level3"): ("int", 1, 10),
    ("compat", "test", "level"): ("int", 1, 10),
    ("free_sprint", "sprint", "mode"): ("choice", "out_of_battle", "always", "off"),
    ("free_sprint", "sprint", "who"): ("choice", "party", "arisen"),
    ("draw_distance", "draw", "enabled"): ("int", 0, 1),
    ("draw_distance", "draw", "objects"): ("float_or_zero", 1.0, 20.0),
    ("draw_distance", "draw", "grass"): ("float_or_zero", 1.0, 3.0),
    ("draw_distance", "draw", "enemies"): ("float_or_zero", 1.0, 20.0),
    ("draw_distance", "draw", "humanenemies"): ("int_or_zero", 100, 2000),
    ("draw_distance", "draw", "objectsneverhide"): ("int", 0, 1),
    ("draw_distance", "draw", "enemiesalwaysactive"): ("int", 0, 1),
    ("six_skill_warrior", "warrior", "mode"): ("choice", "six", "off"),
    # the plugins read these as their ini describes; anything else they ignore or clamp
    ("inclination_lock", "lock", "mode"): ("choice", "freeze", "commands", "off"),
    ("save_backup", "backup", "keep"): ("int", 1, 1000),
    ("save_backup", "backup", "keepsessions"): ("int", 0, 1000),
    ("save_backup", "backup", "checkseconds"): ("float", 0.05, 3600.0),
    (LOADER, "fps", "max_fps"): ("int_or_zero", 30, 360),
    (LOADER, "render", "shadow_map_size"): ("shadow", 1024, 8192),
    (LOADER, "live", "hang_seconds"): ("int", 5, 600),
    (LOADER, "saves", "keep"): ("int", 1, 500),
    (LOADER, "loader", "keep_reports"): ("int", 1, 100),
    (LOADER, "overlay", "key"): ("fkey",),
    (LOADER, "overlay", "position"): ("choice", "top-right", "top-left", "bottom-right", "bottom-left"),
    (LOADER, "overlay", "scale"): ("auto_float", 0.75, 3.0),
}
_BOOL_KEYS = re.compile(r"^(enabled|overlay|log_redirects|crash_reports|minidump|plugins|safe_mode|safe_mode_notice|"
                        r"fatal_hint|missing_textures|frame_stats|hang_reports|borderless|borderless_fill|"
                        r"background_run|backup|show_at_start|record|trace)$")


def plugins_dir(game: Game) -> Path:
    return game.state_dir / "plugins"


def off_dir(game: Game) -> Path:
    return plugins_dir(game) / OFF


def _files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in SUFFIXES),
                  key=lambda p: p.name.lower())


def built() -> dict[str, Path]:
    """Plugins this Riftstone can install: a release's ``plugins\\`` folder, else the native builds."""
    root = Path(__file__).resolve().parents[2]
    found: dict[str, Path] = {}
    for p in _files(root / "plugins"):
        found.setdefault(p.stem.lower(), p)
    base = root / "native" / "plugins"
    if base.is_dir():
        for d in sorted(base.iterdir()):
            for p in _files(d / "out"):
                if p.stem.lower() == d.name.lower():
                    found.setdefault(p.stem.lower(), p)
    return found


def _check_name(name: str) -> str:
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.\- ]{1,80}", name) or name in (".", ".."):
        raise RiftError(f"bad plugin name {name!r}")
    return name


# -- ini files, comments kept -------------------------------------------------------------------------

def read_settings(path: Path) -> list[dict]:
    """Every ``key = value`` in an ini, in file order, with its section and the comment lines above it."""
    if not path.is_file():
        return []
    out, section, notes, above_section, first = [], "", [], [], False
    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            notes = []
            continue
        if line.startswith(";"):
            notes.append(line.lstrip(";").strip())
            continue
        if line.startswith("[") and line.endswith("]"):
            # comments right above a section describe its first key when that key has none of its own
            section, above_section, notes, first = line[1:-1].strip(), notes, [], True
            continue
        if "=" in line:
            k, _, v = line.partition("=")      # no inline comments: Windows reads the rest of the line
            out.append({"section": section, "key": k.strip(), "value": v.strip(),
                        "help": " ".join(notes or (above_section if first else []))})
            first = False
        notes = []
    return out


def _valid(stem: str, section: str, key: str, value) -> str:
    text = str(value).strip() if value is not None else ""
    if not text or len(text) > 64 or any(c in text for c in ";=[]\r\n\x00"):
        raise RiftError(f"{key}: a short value without ; = [ ] or line breaks")
    rule = _RULES.get((stem.lower(), section.lower(), key.lower()))
    if rule is None and _BOOL_KEYS.match(key.lower()):
        rule = ("int", 0, 1)
    if rule is None:
        return text
    kind = rule[0]

    def number(conv, lo, hi):
        try:
            n = conv(text)
        except ValueError:
            raise RiftError(f"{key} is a number from {lo} to {hi}") from None
        if not (lo <= n <= hi):
            raise RiftError(f"{key} is a number from {lo} to {hi}")
        return str(n)

    if kind == "int":
        return number(int, rule[1], rule[2])
    if kind == "float":
        return number(float, rule[1], rule[2])
    if kind in ("auto_float", "auto_int"):
        return "auto" if text.lower() == "auto" else number(float if kind == "auto_float" else int, rule[1], rule[2])
    if kind == "int_or_zero":
        return "0" if text == "0" else number(int, rule[1], rule[2])
    if kind == "float_or_zero":   # 0 = "leave it to the game", else a multiplier in range; 3, not 3.0
        try:
            if float(text) == 0:
                return "0"
        except ValueError:
            pass
        v = float(number(float, rule[1], rule[2]))
        return str(int(v)) if v.is_integer() else str(v)
    if kind == "shadow":
        if text == "0":
            return "0"
        n = int(number(int, rule[1], rule[2]))
        if n % 32:
            raise RiftError(f"{key} is a multiple of 32 (or 0 to leave it)")
        return str(n)
    if kind == "fkey":
        if not re.fullmatch(r"[Ff](?:[1-9]|1[0-2])", text):
            raise RiftError(f"{key} is one of F1 to F12")
        return text.upper()
    if kind == "choice":
        if text.lower() not in rule[1:]:
            raise RiftError(f"{key} is one of: {', '.join(rule[1:])}")
        return text.lower()
    return text


def write_setting(path: Path, section: str, key: str, value: str) -> str:
    """Set one existing key; every other line (comments, spacing, other keys) stays as it was."""
    if not path.is_file():
        raise RiftError(f"{path.name} does not exist")
    lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    current, done = "", False
    for i, raw in enumerate(lines):
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            continue
        if done or line.startswith(";") or "=" not in line or current.lower() != section.lower():
            continue
        k = line.partition("=")[0].strip()
        if k.lower() == key.lower():
            indent = raw[: len(raw) - len(raw.lstrip())]
            lines[i] = f"{indent}{k} = {value}"
            done = True
    if not done:
        raise RiftError(f"{path.name} has no {key} in [{section}]")
    tmp = path.with_name(path.name + ".riftstone-tmp")
    # bytes, not write_text: text mode would turn each "\r\n" into "\r\r\n" on Windows (fuzz finding, plugin_ini)
    tmp.write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-8"))
    os.replace(tmp, path)
    return value


def _log_tail(game: Game, stem: str, n: int = 12) -> list[str]:
    log = game.state_dir / "logs" / f"{stem}.log"
    try:
        return log.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    except OSError:
        return []


# -- the list ----------------------------------------------------------------------------------------

def _live_states() -> dict[str, str]:
    """{plugin file name (lower case): loaded|failed|quarantined|skipped} from the running game."""
    try:
        from . import runtime

        live = runtime.read_live()
    except Exception:  # noqa: BLE001 - no game, no block: nothing live to say
        return {}
    if not live or live.get("exited"):
        return {}
    out = {}
    for item in str(live.get("plugins") or "").split(";"):
        name, _, state = item.partition("=")
        if name:
            out[name.strip().lower()] = state.strip()
    return out


def describe(game: Game) -> list[dict]:
    """Every plugin in the game folder (on or off) and every one this Riftstone could install."""
    live = _live_states()
    have = built()
    rows, seen = [], set()
    for folder, enabled in ((plugins_dir(game), True), (off_dir(game), False)):
        for p in _files(folder):
            stem = p.stem
            if stem.lower() in seen:
                continue
            seen.add(stem.lower())
            info = CATALOG.get(stem.lower(), {})
            src = have.get(stem.lower())
            rows.append({
                "name": stem, "file": p.name, "installed": True, "enabled": enabled,
                "title": info.get("title", stem), "summary": info.get("summary", ""), "own": info.get("own", False),
                "size": p.stat().st_size, "live": live.get(p.name.lower()),
                "update": bool(src and src.read_bytes() != p.read_bytes()),
                "settings": read_settings(p.with_suffix(".ini")), "log": _log_tail(game, stem),
            })
    for stem, src in sorted(have.items()):
        if stem in seen:
            continue
        info = CATALOG.get(stem, {})
        rows.append({"name": src.stem, "file": src.name, "installed": False, "enabled": False,
                     "title": info.get("title", src.stem), "summary": info.get("summary", ""),
                     "own": info.get("own", False), "size": src.stat().st_size, "live": None, "update": False,
                     "settings": read_settings(src.with_suffix(".ini")), "log": []})
    return rows


def loader_settings(game: Game) -> list[dict]:
    return read_settings(game.root / "riftstone_loader.ini")


def _locate(game: Game, name: str) -> tuple[Path, bool]:
    _check_name(name)
    for folder, enabled in ((plugins_dir(game), True), (off_dir(game), False)):
        for p in _files(folder):
            if p.stem.lower() == name.lower():
                return p, enabled
    raise RiftError(f"no plugin named {name!r} in {plugins_dir(game)}")


def _closed(game: Game) -> None:
    if install.game_running(game):
        raise RiftError(f"{game.title} is running: plugins load at start, so close the game first")


def set_value(game: Game, name: str, section: str, key: str, value) -> dict:
    """Change one setting of a plugin (or of the loader); the game reads it at its next start."""
    if name == LOADER:
        path = game.root / "riftstone_loader.ini"
    else:
        path = _locate(game, name)[0].with_suffix(".ini")
    if not isinstance(section, str) or not isinstance(key, str):
        raise RiftError("send the section and the key")
    stem = LOADER if name == LOADER else path.stem
    clean = _valid(stem, section, key, value)
    write_setting(path, section, key, clean)
    return {"ok": True, "file": path.name, "section": section, "key": key, "value": clean,
            "note": "the game reads it at its next start" if install.game_running(game) else "saved"}


def toggle(game: Game, name: str, on: bool) -> dict:
    """Switch a plugin on (into plugins\\) or off (into plugins\\off\\), its .ini along."""
    _closed(game)
    p, enabled = _locate(game, name)
    if enabled == on:
        return {"ok": True, "enabled": on, "changed": False}
    dest_dir = plugins_dir(game) if on else off_dir(game)
    dest_dir.mkdir(parents=True, exist_ok=True)
    for f in (p, p.with_suffix(".ini")):
        if f.is_file():
            target = dest_dir / f.name
            if target.exists():
                raise RiftError(f"{target} already exists; sort the two copies out first")
            os.replace(f, target)
    return {"ok": True, "enabled": on, "changed": True}


def add(game: Game, name: str) -> dict:
    """Install (or update) a plugin this Riftstone built; an ini already there keeps its values."""
    from .loader import merge_ini

    _closed(game)
    src = built().get(_check_name(name).lower())
    if src is None:
        raise RiftError(f"{name} is not built here (native\\plugins\\{name}\\build.cmd builds it)")
    try:
        cur, enabled = _locate(game, src.stem)
        folder = cur.parent
    except RiftError:
        folder, enabled = plugins_dir(game), True
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / (src.name + ".riftstone-tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, folder / src.name)
    ini_src, ini_dst = src.with_suffix(".ini"), folder / (src.stem + ".ini")
    if ini_src.is_file():
        template = ini_src.read_text(encoding="utf-8-sig", errors="replace")
        mine = ini_dst.read_text(encoding="utf-8-sig", errors="replace") if ini_dst.is_file() else ""
        ini_dst.write_text(merge_ini(template, mine) if mine else template, encoding="utf-8")
    return {"ok": True, "file": src.name, "enabled": enabled}
