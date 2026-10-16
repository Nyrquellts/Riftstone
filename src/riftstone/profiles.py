"""Overhaul profiles (``riftstone-profile/1``): which of Riftstone's features are on and the settings a player chose,
in one small JSON file to keep or share.  Riftstone Studio, the exe (``native/studio``), writes and reads the same
files; this module is the reference its tests compare it with, and ``riftstone profile`` its command line.

A profile carries only values a check knows (``plugins.rule_for``: the plugins' and the loader's rules and the 0/1
switches), never a path, a DLL name or other free text, so a profile from someone else cannot make the loader or a
plugin open a file.  It holds no game data.

    {"schema": "riftstone-profile/1", "name": "Hordes", "made_with": "Riftstone 1.0.5",
     "features": {"enemy_cap": true, "lod_tuner": false},
     "settings": {"enemy_cap": {"enemy_cap": {"slots": "64"}}, "loader": {"overlay": {"key": "Insert"}}}}

Importing checks the whole file first and plans every move and write before it changes anything; a feature that is
not installed, a key its settings file does not have and a value its check refuses are left out and named.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import __version__, install, plugins
from .errors import RiftError
from .game import Game
from .runtime import INI_UTF16

SCHEMA = "riftstone-profile/1"
MAX_BYTES = 1 << 20
MAX_FEATURES = 128
MAX_VALUES = 4096
NAME_MAX = 80
_TOP = ("schema", "name", "made_with", "features", "settings")
_STEM = re.compile(r"[A-Za-z0-9_.\- ]{1,80}")            # plugins._check_name
_INI_NAME = re.compile(r"[\x20-\x7e]{1,64}")             # a section or key: printable ASCII


class _Obj(list):
    """A JSON object as its (key, value) pairs, in file order: duplicates are found, not silently merged."""


def _text(value, what: str, *, empty: bool = False) -> str:
    if not isinstance(value, str):
        raise RiftError(f"{what} is text")
    if not value and not empty:
        raise RiftError(f"{what} is empty")
    if len(value) > NAME_MAX:
        raise RiftError(f"{what} is longer than {NAME_MAX} characters")
    if any(ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise RiftError(f"{what} holds a control character")
    return value


def _stem(value, what: str) -> str:
    if not isinstance(value, str) or not _STEM.fullmatch(value) or value in (".", ".."):
        raise RiftError(f"{what}: {value!r} is not a feature's name")
    return value


def _ini_name(value, what: str) -> str:
    if not isinstance(value, str) or not _INI_NAME.fullmatch(value) or value != value.strip() \
            or any(c in value for c in ";=[]"):
        raise RiftError(f"{what}: {value!r} is not a section or key name of a settings file")
    return value


def _pairs(value, what: str) -> list[tuple[str, object]]:
    if not isinstance(value, _Obj):
        raise RiftError(f"{what} is an object")
    seen: set[str] = set()
    for k, _ in value:
        if k.lower() in seen:
            raise RiftError(f"{what} names {k!r} twice")
        seen.add(k.lower())
    return list(value)


def parse(raw: bytes) -> dict:
    """A profile file's bytes, every field checked: {"name", "made_with", "features": {stem: on},
    "settings": {stem: {section: {key: value}}}}, in the file's order."""
    if len(raw) > MAX_BYTES:
        raise RiftError(f"a profile is at most {MAX_BYTES} bytes")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise RiftError("a profile is UTF-8 text") from None
    try:
        doc = json.loads(text, object_pairs_hook=_Obj)
    except RecursionError:
        raise RiftError("not a profile: nested too deep") from None
    except ValueError as e:
        raise RiftError(f"not a profile: {e}") from None
    top = dict(_pairs(doc, "a profile"))
    extra = [k for k in top if k not in _TOP]
    if extra:
        raise RiftError(f"a profile has no field {extra[0]!r}")
    if top.get("schema") != SCHEMA:
        raise RiftError(f"not a {SCHEMA} file")
    name = _text(top.get("name"), "name")
    made_with = _text(top.get("made_with", ""), "made_with", empty=True)
    features: dict[str, bool] = {}
    for stem, on in _pairs(top.get("features", _Obj()), "features"):
        _stem(stem, "features")
        if not isinstance(on, bool):
            raise RiftError(f"features: {stem} is true or false")
        features[stem] = on
    if len(features) > MAX_FEATURES:
        raise RiftError(f"a profile names at most {MAX_FEATURES} features")
    settings: dict[str, dict[str, dict[str, str]]] = {}
    count = 0
    for stem, sections in _pairs(top.get("settings", _Obj()), "settings"):
        _stem(stem, "settings")
        out: dict[str, dict[str, str]] = {}
        for section, keys in _pairs(sections, f"settings of {stem}"):
            _ini_name(section, f"settings of {stem}")
            values: dict[str, str] = {}
            for key, value in _pairs(keys, f"[{section}] of {stem}"):
                _ini_name(key, f"[{section}] of {stem}")
                if not isinstance(value, str) or len(value) > 256 \
                        or any(0xD800 <= ord(c) <= 0xDFFF for c in value):
                    raise RiftError(f"{stem} [{section}] {key}: the value is a short text")
                values[key] = value
                count += 1
            out[section] = values
        settings[stem] = out
    if count > MAX_VALUES:
        raise RiftError(f"a profile holds at most {MAX_VALUES} values")
    return {"name": name, "made_with": made_with, "features": features, "settings": settings}


def to_json(profile: dict) -> str:
    """The file's text: two-space JSON, keys in the profile's order, a line end at the end."""
    doc = {"schema": SCHEMA, "name": profile["name"], "made_with": profile.get("made_with", ""),
           "features": profile["features"], "settings": profile["settings"]}
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def installed(game: Game) -> list[tuple[str, Path, bool]]:
    """(stem, file, on) of every plugin in riftstone\\plugins (on) and plugins\\off (off), by name; a plugin in both
    counts where it runs, as plugins.describe shows it.  A plugin named like the loader's settings, or with a name a
    profile cannot hold, is left out."""
    rows, seen = [], set()
    for folder, on in ((plugins.plugins_dir(game), True), (plugins.off_dir(game), False)):
        for p in plugins._files(folder):
            if p.stem.lower() in seen or p.stem.lower() == plugins.LOADER or not _STEM.fullmatch(p.stem) \
                    or p.stem in (".", ".."):
                continue
            seen.add(p.stem.lower())
            rows.append((p.stem, p, on))
    return sorted(rows, key=lambda r: r[0].lower())


def _carried(stem: str, ini: Path) -> dict[str, dict[str, str]]:
    """The values of an ini a profile carries: keys with a check, their first appearance, values the check passes."""
    out: dict[str, dict[str, str]] = {}
    spelled: dict[str, str] = {}
    seen: set[tuple[str, str]] = set()
    for s in plugins.read_settings(ini):
        section, key = s["section"], s["key"]
        where = (section.lower(), key.lower())
        if where in seen:
            continue
        seen.add(where)
        try:
            _ini_name(section, "section")
            _ini_name(key, "key")
        except RiftError:
            continue
        if plugins.rule_for(stem, section, key) is None:
            continue
        try:
            clean = plugins._valid(stem, section, key, s["value"])
        except RiftError:
            continue
        out.setdefault(spelled.setdefault(section.lower(), section), {})[key] = clean
    return out


def export(game: Game, name: str) -> dict:
    """This game folder's features and the values a profile carries, under a name."""
    profile = {"name": _text(name, "name"), "made_with": f"Riftstone {__version__}", "features": {}, "settings": {}}
    for stem, path, on in installed(game):
        profile["features"][stem] = on
        values = _carried(path.stem, path.with_suffix(".ini"))
        if values:
            profile["settings"][stem] = values
    loader = _carried(plugins.LOADER, game.root / "riftstone_loader.ini")
    if loader:
        profile["settings"][plugins.LOADER] = loader
    return profile


def _decodes(ini: Path) -> bool:
    raw = ini.read_bytes()
    if not raw.startswith(INI_UTF16):
        return True
    try:
        raw[2:].decode("utf-16-le")
        return True
    except UnicodeDecodeError:
        return False


def apply(game: Game, profile: dict, dry_run: bool = False) -> dict:
    """Switch the features and write the values a profile names.  Everything is planned first: a move whose
    target is taken refuses the whole profile and changes nothing.  Returns what was (or with ``dry_run`` would be)
    done: {"switched", "set", "unchanged", "skipped": [one line each], "writes": [(file, section, key, value)]}."""
    if install.game_running(game):
        raise RiftError(f"{game.title} is running: close the game first, then import the profile")
    where = {stem.lower(): (path, on) for stem, path, on in installed(game)}
    moves: list[tuple[Path, Path]] = []
    skipped: list[str] = []
    switched = unchanged = 0
    later: dict[str, Path] = {}                    # stem -> where its settings file will be
    for stem, on in profile["features"].items():
        cur = where.get(stem.lower())
        if cur is None:
            skipped.append(f"{stem}: not installed here")
            continue
        path, was = cur
        if was == on:
            unchanged += 1
            continue
        dest = plugins.plugins_dir(game) if on else plugins.off_dir(game)
        for f in (path, path.with_suffix(".ini")):
            if f.is_file():
                target = dest / f.name
                if target.exists() or any(t == target for _, t in moves):
                    raise RiftError(f"{target} already exists; sort the two copies out first")
                moves.append((f, target))
        switched += 1
        later[stem.lower()] = dest / path.with_suffix(".ini").name
    writes: list[tuple[Path, str, str, str]] = []
    for stem, sections in profile["settings"].items():
        if stem.lower() == plugins.LOADER:
            now = after = game.root / "riftstone_loader.ini"
            rule_stem = plugins.LOADER
        else:
            cur = where.get(stem.lower())
            if cur is None:
                skipped.append(f"{stem}: not installed here")
                continue
            now = cur[0].with_suffix(".ini")
            after = later.get(stem.lower(), now)
            rule_stem = cur[0].stem
        if not now.is_file():
            skipped.append(f"{stem}: has no settings file")
            continue
        if not _decodes(now):
            skipped.append(f"{stem}: {now.name} starts with a UTF-16 mark but does not read as UTF-16")
            continue
        current: dict[tuple[str, str], str] = {}
        for s in plugins.read_settings(now):
            current.setdefault((s["section"].lower(), s["key"].lower()), s["value"])
        for section, keys in sections.items():
            for key, value in keys.items():
                named = f"{stem} [{section}] {key}"
                have = current.get((section.lower(), key.lower()))
                if have is None:
                    skipped.append(f"{named}: not in {now.name}")
                    continue
                if plugins.rule_for(rule_stem, section, key) is None:
                    skipped.append(f"{named}: not a setting a profile carries")
                    continue
                try:
                    clean = plugins._valid(rule_stem, section, key, value)
                except RiftError as e:
                    skipped.append(f"{named}: {e}")
                    continue
                if have == clean:
                    unchanged += 1
                    continue
                writes.append((after, section, key, clean))
    if not dry_run:
        for _, target in moves:
            target.parent.mkdir(parents=True, exist_ok=True)
        for f, target in moves:
            os.replace(f, target)
        for path, section, key, clean in writes:
            plugins.write_setting(path, section, key, clean)
    return {"switched": switched, "set": len(writes), "unchanged": unchanged, "skipped": skipped,
            "writes": [(p.name, s, k, v) for p, s, k, v in writes], "dry_run": dry_run}
