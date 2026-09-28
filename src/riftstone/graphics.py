"""Graphics profiles: the files a graphics tool puts in the game folder (an ENB and its preset, a ReShade set-up)
and the settings that go with it, put in and taken out as one (``riftstone graphics``), each file checked by its
SHA-256 and everything it replaces kept.

A profile is a folder in the profiles folder (:func:`profiles_folder`):

    profile.json   schema riftstone-graphics/1: the files by SHA-256, the settings, notes
    files/         the files exactly as their authors ship them.  They stay on this PC: never in git and never
                   in a package (ENBSeries forbids republishing its binaries; a preset is its author's)

profile.json::

    {"schema": "riftstone-graphics/1", "title": "...", "notes": ["..."],
     "files": {"d3d9.dll": {"sha256": "...", "from": "enbseries_dragonsdogma_v0300.zip: WrapperVersion/d3d9.dll"}},
     "ini": {"enblocal.ini": {"PROXY": {"EnableProxyLibrary": "true"}}},    keys of the profile's own .ini files
     "config": {"GRAPHICS": {"HDR": "FLOAT"}, "DISPLAY": {"VSYNC": "OFF"}},  the game's config.ini (Dark Arisen)
     "loader": {"d3d9": {"chain": ""}}}                                       riftstone_loader.ini

Every setting names a key its file already has: a misspelt key is refused, never added.  The game's settings take
only values the game reads (:data:`GAME_VALUES`, from the strings DDDA.exe matches them against).

``apply`` refuses while the game runs.  It takes the profile applied before out, moves any other file in the way
into a backup, copies the profile's files in, sets the settings and records all of it in
``<game>\\riftstone\\graphics.json``.  ``off`` puts the game back as the first ``apply`` found it: a file changed
since it was written (an ENB editor saves its settings into its .ini files) goes into the backup instead of being
deleted, and a setting changed since (in the game's options) keeps its new value.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from .arcfolder import write_file
from .errors import RiftError
from .game import Game

SCHEMA = "riftstone-graphics/1"
STATE_SCHEMA = "riftstone-graphics-state/1"
STATE_FILE = "graphics.json"                    # in <game>\riftstone
PROFILE_FILE = "profile.json"
FILES_DIR = "files"
MAX_PROFILE_JSON = 1 << 20
MAX_FILES = 4096
MAX_FILE_BYTES = 256 << 20
MAX_VALUE = 512
# What a graphics tool keeps in the game folder: its DLL (and a helper program), settings, shaders and pictures.
EXTENSIONS = frozenset({".dll", ".exe", ".ini", ".fx", ".fxh", ".hlsl", ".h", ".png", ".bmp", ".jpg", ".jpeg",
                        ".dds", ".tga", ".txt", ".conf", ".cfg", ".json"})
# Names in the game folder a profile never writes: the game, Steam's, Riftstone's own.
PROTECTED = frozenset({"ddda.exe", "ddo.exe", "dinput8.dll", "dinput8_chain.dll", "riftstone_loader.ini",
                       "steam_api.dll", "steam_appid.txt"})
PROTECTED_DIRS = frozenset({"nativepc", "riftstone"})
_PART = re.compile(r"[A-Za-z0-9 _.()\[\]{}+,'&!#@$%~=-]{1,128}")
_RESERVED = re.compile(r"(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?", re.I)
_NAME = re.compile(r"[A-Za-z0-9 _.()+,'&-]{1,64}")
_SHA = re.compile(r"[0-9a-f]{64}")
_INI_WORD = re.compile(r"[A-Za-z0-9 _.:()\-]{1,96}")

# config.ini values DDDA.exe reads (its enum names without the prefix, e.g. HDR_FLOAT is "FLOAT", which the
# options menu shows as High).  ON/OFF switches, then anything else is a plain word or number.
GAME_VALUES = {
    ("graphics", "hdr"): ("NONE", "DEFAULT", "FLOAT"),
    ("graphics", "altantialias"): ("NONE", "FXAA", "FXAA3", "FXAA3HQ"),
    ("graphics", "antialias"): ("NONE", "MSAA2X", "MSAA4X", "MSAA8X", "CSAA8X", "CSAA8XQ", "CSAA16X", "CSAA16XQ",
                                "CSAA32X"),
    ("graphics", "viewrange"): ("NORMAL", "FAR", "FARTHEST"),
    ("graphics", "texturefiltering"): ("TRILINEAR", "ANISO_X2", "ANISO_X4", "ANISO_X8", "ANISO_X16"),
    ("graphics", "grassquality"): ("LOW", "MEDIUM", "HIGH"),
}
ON_OFF = frozenset({("graphics", "doffilter"), ("graphics", "sli"), ("graphics", "stereo"), ("display", "vsync"),
                    ("display", "fullscreen"), ("display", "flush"), ("cpu", "renderingthread")})
CONFIG_SECTIONS = frozenset({"graphics", "display", "cpu"})
_PLAIN_VALUE = re.compile(r"[A-Za-z0-9_.:x+-]{1,64}")


@dataclass
class Profile:
    name: str                                   # its folder's name
    folder: Path
    title: str
    notes: list[str]
    files: dict[str, str]                       # path in the game folder ("enbseries/enbbloom.fx") -> sha256
    origins: dict[str, str]
    ini: dict[str, dict[str, dict[str, str]]]   # file -> section -> key -> value
    config: dict[str, dict[str, str]]
    loader: dict[str, dict[str, str]]

    def source(self, rel: str) -> Path:
        return self.folder / FILES_DIR / Path(*rel.split("/"))


# --- where profiles live --------------------------------------------------------------------------------------

def profiles_folder() -> Path:
    """$RIFTSTONE_PROFILES, else Riftstone's own profiles\\graphics (beside its mods folder) when it exists, else
    Documents\\Riftstone\\profiles\\graphics."""
    env = os.environ.get("RIFTSTONE_PROFILES")
    if env:
        return Path(env)
    own = Path(__file__).resolve().parents[2] / "profiles" / "graphics"
    if own.is_dir():
        return own
    return Path.home() / "Documents" / "Riftstone" / "profiles" / "graphics"


def profile_names(folder: Path | None = None) -> list[str]:
    folder = folder or profiles_folder()
    try:
        return sorted(p.name for p in folder.iterdir() if p.is_dir() and (p / PROFILE_FILE).is_file())
    except OSError:
        return []


# --- profile.json -------------------------------------------------------------------------------------------

def safe_rel(rel: object, what: str = "a file") -> str:
    """A path inside the game folder a profile may write: relative, plain names, a graphics tool's kind of file,
    nothing of the game's, Steam's or Riftstone's.  Returned with forward slashes."""
    if not isinstance(rel, str) or not rel or len(rel) > 240:
        raise RiftError(f"{what}: {rel!r} is not a path")
    parts = rel.replace("\\", "/").split("/")
    for p in parts:
        if not _PART.fullmatch(p) or p in (".", "..") or p.endswith((" ", ".")) or _RESERVED.fullmatch(p):
            raise RiftError(f"{what}: {rel!r} is not a plain relative path in the game folder")
    if parts[0].lower() in PROTECTED_DIRS:
        raise RiftError(f"{what}: {rel!r} is inside {parts[0]}\\, which a graphics profile never writes")
    if len(parts) == 1 and parts[0].lower() in PROTECTED:
        raise RiftError(f"{what}: {rel!r} is the game's, Steam's or Riftstone's own file")
    if Path(parts[-1]).suffix.lower() not in EXTENSIONS:
        raise RiftError(f"{what}: {rel!r} is not a kind of file a graphics profile holds "
                        f"({', '.join(sorted(EXTENSIONS))})")
    return "/".join(parts)


def _text(v: object, what: str, limit: int = MAX_VALUE) -> str:
    if not isinstance(v, str) or len(v) > limit or any(c in v for c in "\r\n\0"):
        raise RiftError(f"{what} must be one line of text (at most {limit} characters)")
    try:                  # JSON's \udc80 reads as a lone surrogate, which no file can hold (fuzz: graphics_profile)
        v.encode("utf-8")
    except UnicodeEncodeError as e:
        raise RiftError(f"{what} is not text ({e.reason})") from e
    return v


def _settings(v: object, what: str) -> dict[str, dict[str, str]]:
    """{section: {key: value}} with plain section and key names and one-line values."""
    if not isinstance(v, dict):
        raise RiftError(f"{what} must be {{section: {{key: value}}}}")
    out: dict[str, dict[str, str]] = {}
    sections: set[str] = set()
    for sec, keys in v.items():
        if not isinstance(sec, str) or not _INI_WORD.fullmatch(sec) or sec.strip() != sec or not isinstance(keys, dict):
            raise RiftError(f"{what}: [{sec}] is not a section name with its keys")
        if sec.lower() in sections:
            raise RiftError(f"{what}: [{sec}] is named twice")
        sections.add(sec.lower())
        got: dict[str, str] = {}
        names: set[str] = set()
        for k, val in keys.items():
            if not isinstance(k, str) or not _INI_WORD.fullmatch(k) or k.strip() != k:
                raise RiftError(f"{what}: [{sec}] {k!r} is not a key name")
            if k.lower() in names:
                raise RiftError(f"{what}: [{sec}] {k} is named twice")
            names.add(k.lower())
            got[k] = _text(val, f"{what}: [{sec}] {k}")
        out[sec] = got
    return out


def parse_profile(data: bytes, name: str = "profile", folder: Path | None = None) -> Profile:
    """A profile.json, every field checked (the files themselves are checked by :func:`problems`)."""
    if len(data) > MAX_PROFILE_JSON:
        raise RiftError(f"{name}: profile.json is larger than {MAX_PROFILE_JSON >> 20} MB")
    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, RecursionError) as e:
        raise RiftError(f"{name}: profile.json is not JSON ({e})") from e
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise RiftError(f"{name}: profile.json is not a {SCHEMA} profile")
    unknown = set(doc) - {"schema", "title", "notes", "files", "ini", "config", "loader"}
    if unknown:
        raise RiftError(f"{name}: profile.json has fields Riftstone does not know: {', '.join(sorted(unknown))}")
    title = _text(doc.get("title", name), f"{name}: title")
    notes = doc.get("notes", [])
    if not isinstance(notes, list) or len(notes) > 200:
        raise RiftError(f"{name}: notes must be a list of lines")
    notes = [_text(n, f"{name}: a note", 2000) for n in notes]
    files_doc = doc.get("files", {})
    if not isinstance(files_doc, dict) or len(files_doc) > MAX_FILES:
        raise RiftError(f"{name}: files must be {{path: {{\"sha256\": ...}}}} (at most {MAX_FILES})")
    files: dict[str, str] = {}
    origins: dict[str, str] = {}
    lowered: dict[str, str] = {}
    for rel, meta in files_doc.items():
        norm = safe_rel(rel, f"{name}: files")
        if norm.lower() in lowered:
            raise RiftError(f"{name}: files: {rel} is named twice")
        lowered[norm.lower()] = norm
        if not isinstance(meta, dict) or not isinstance(meta.get("sha256"), str) or not _SHA.fullmatch(meta["sha256"]) \
                or set(meta) - {"sha256", "from"}:
            raise RiftError(f"{name}: files: {rel} needs its \"sha256\" (64 lower-case hex digits) and at most a \"from\"")
        files[norm] = meta["sha256"]
        if "from" in meta:
            origins[norm] = _text(meta["from"], f"{name}: files: {rel}: from")
    for low, rel in lowered.items():      # "x.fx" as a file and "x.fx/y.fx" cannot both be
        parts = low.split("/")
        for i in range(1, len(parts)):
            if "/".join(parts[:i]) in lowered:
                raise RiftError(f"{name}: files: {lowered['/'.join(parts[:i])]} is a file and a folder of {rel}")
    ini_doc = doc.get("ini", {})
    if not isinstance(ini_doc, dict):
        raise RiftError(f"{name}: ini must be {{file: {{section: {{key: value}}}}}}")
    ini: dict[str, dict[str, dict[str, str]]] = {}
    for rel, secs in ini_doc.items():
        norm = safe_rel(rel, f"{name}: ini")
        if norm.lower() not in lowered or not norm.lower().endswith(".ini"):
            raise RiftError(f"{name}: ini: {rel} is not one of the profile's own .ini files")
        if lowered[norm.lower()] in ini:
            raise RiftError(f"{name}: ini: {rel} is named twice")
        ini[lowered[norm.lower()]] = _settings(secs, f"{name}: ini: {rel}")
    config = _settings(doc.get("config", {}), f"{name}: config")
    for sec, keys in config.items():
        if sec.lower() not in CONFIG_SECTIONS:
            raise RiftError(f"{name}: config: [{sec}] is not one of the game's graphics sections "
                            f"({', '.join(s.upper() for s in sorted(CONFIG_SECTIONS))})")
        for k, v in keys.items():
            check_game_value(sec, k, v, f"{name}: config")
    loader_doc = _settings(doc.get("loader", {}), f"{name}: loader")
    return Profile(name, folder or Path(name), title, notes, files, origins, ini, config, loader_doc)


def check_game_value(section: str, key: str, value: str, what: str = "config") -> None:
    """A value DDDA.exe reads for that config.ini key."""
    pair = (section.lower(), key.lower())
    if pair in GAME_VALUES:
        if value.upper() not in GAME_VALUES[pair]:
            raise RiftError(f"{what}: [{section}] {key} = {value}: the game reads only "
                            f"{', '.join(GAME_VALUES[pair])}")
    elif pair in ON_OFF:
        if value.upper() not in ("ON", "OFF"):
            raise RiftError(f"{what}: [{section}] {key} = {value}: the game reads ON or OFF")
    elif not _PLAIN_VALUE.fullmatch(value):
        raise RiftError(f"{what}: [{section}] {key} = {value!r} is not a value the game writes (a word or a number)")


def load_profile(name_or_folder: str | os.PathLike, profiles: Path | None = None) -> Profile:
    p = Path(name_or_folder)
    folder = p if (p / PROFILE_FILE).is_file() else (profiles or profiles_folder()) / str(name_or_folder)
    if not isinstance(name_or_folder, os.PathLike) and not _NAME.fullmatch(str(name_or_folder)) \
            and not (p / PROFILE_FILE).is_file():
        raise RiftError(f"{name_or_folder!r} is not a profile name")
    f = folder / PROFILE_FILE
    if not f.is_file():
        raise RiftError(f"no graphics profile {name_or_folder} (looked for {f}); riftstone graphics list shows them")
    if f.stat().st_size > MAX_PROFILE_JSON:
        raise RiftError(f"{f} is larger than {MAX_PROFILE_JSON >> 20} MB")
    return parse_profile(f.read_bytes(), folder.name, folder)


def dump_profile(p: Profile) -> bytes:
    doc = {"schema": SCHEMA, "title": p.title, "notes": p.notes,
           "files": {rel: ({"sha256": sha, "from": p.origins[rel]} if rel in p.origins else {"sha256": sha})
                     for rel, sha in p.files.items()},
           "ini": p.ini, "config": p.config, "loader": p.loader}
    return (json.dumps(doc, indent=1, ensure_ascii=False) + "\n").encode("utf-8")


# --- ini files, edited in place ----------------------------------------------------------------------------

def decode_ini(data: bytes) -> tuple[str, str]:
    """(text, encoding): UTF-16 with its byte-order mark as Windows writes it, else one byte per character, so
    every byte comes back as it was."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return data.decode("utf-16"), "utf-16"
        except UnicodeDecodeError as e:
            raise RiftError(f"a UTF-16 .ini that does not decode ({e})") from e
    return data.decode("latin-1"), "latin-1"


def encode_ini(text: str, encoding: str) -> bytes:
    try:
        return text.encode(encoding)
    except UnicodeEncodeError as e:
        raise RiftError(f"a value does not fit the file's encoding ({e})") from e


def ini_values(text: str) -> dict[str, dict[str, str]]:
    """{section: {key: value}}, lower-cased, the first block of a section and the first of a key winning, as
    Windows' GetPrivateProfileString reads them."""
    out: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith((";", "#")):
            continue
        if line.startswith("[") and line.endswith("]"):
            sec = line[1:-1].strip().lower()
            current = None if sec in seen else out.setdefault(sec, {})
            seen.add(sec)
        elif "=" in line and current is not None:
            k, _, v = line.partition("=")
            current.setdefault(k.strip().lower(), v.strip())
    return out


def set_ini(text: str, values: dict[str, dict[str, str]], what: str = "the file") -> tuple[str, dict]:
    """``text`` with each [section] key set to its value, in place: every other byte, the key's spelling and the
    spacing around its '=' kept.  Every key must already be there (in the section's first block, which is the one
    the game reads); returns (new text, {section: {key: old value}})."""
    want = {s.lower(): {k.lower(): (k, v) for k, v in keys.items()} for s, keys in values.items()}
    lines = text.splitlines(keepends=True)
    before: dict[str, dict[str, str]] = {}
    done: set[tuple[str, str]] = set()
    section = None
    seen: set[str] = set()
    for i, raw in enumerate(lines):
        body = raw.rstrip("\r\n")
        line = body.strip()
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip().lower()
            section = None if name in seen else name
            seen.add(name)
            continue
        if section not in want or not line or line.startswith((";", "#")) or "=" not in line:
            continue
        key_text, _, rest = body.partition("=")
        key = key_text.strip().lower()
        if key not in want[section] or (section, key) in done:
            continue
        done.add((section, key))
        spaces = rest[:len(rest) - len(rest.lstrip())]
        before.setdefault(section, {})[key] = rest.strip()
        lines[i] = f"{key_text}={spaces}{want[section][key][1]}{raw[len(body):]}"
    missing = [f"[{s}] {k}" for s, keys in want.items() for k, (spelt, _) in keys.items() if (s, k) not in done]
    if missing:
        raise RiftError(f"{what} has no " + ", ".join(missing) + " (a profile sets only keys the file already has)")
    return "".join(lines), before


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        return h.hexdigest()
    except OSError:
        return None


# --- checks -------------------------------------------------------------------------------------------------

def _loader_template_keys() -> dict[str, dict[str, str]]:
    from . import loader as loaderlib
    return loaderlib._ini_values(loaderlib._template_ini())


def problems(p: Profile, game: Game | None = None, config: Path | None = None) -> list[str]:
    """What stops the profile from applying: its files missing or not the bytes it names, an .ini setting its file
    lacks, a config.ini or loader key that does not exist, an ENB proxy that is not a Direct3D 9 DLL here."""
    out = []
    texts: dict[str, str] = {}
    for rel, sha in p.files.items():
        src = p.source(rel)
        if not src.is_file():
            out.append(f"{rel} is missing from {p.folder / FILES_DIR}")
            continue
        if src.stat().st_size > MAX_FILE_BYTES:
            out.append(f"{rel} is larger than {MAX_FILE_BYTES >> 20} MB")
            continue
        if _file_sha(src) != sha:
            out.append(f"{rel} is not the file profile.json names (its SHA-256 differs)")
            continue
        if rel in p.ini:
            texts[rel] = decode_ini(src.read_bytes())[0]
        if rel.lower() == "d3d9.dll":          # the game loads it at start: a 64-bit one stops it there
            from . import loader as loaderlib
            try:
                loaderlib.check_d3d9_dll(src.read_bytes(), rel)
            except (OSError, RiftError) as e:
                out.append(str(e))
    for rel, secs in p.ini.items():
        if rel in texts:
            try:
                set_ini(texts[rel], secs, rel)
            except RiftError as e:
                out.append(str(e))
    have = _loader_template_keys()
    for sec, keys in p.loader.items():
        for k in keys:
            if k.lower() not in have.get(sec.lower(), {}):
                out.append(f"riftstone_loader.ini has no [{sec}] {k}")
    if p.config and config is not None:
        try:
            current = ini_values(decode_ini(config.read_bytes())[0])
        except OSError:
            current = None
            out.append(f"the game's settings file {config} cannot be read (start the game once to make it)")
        if current is not None:
            for sec, keys in p.config.items():
                for k in keys:
                    if k.lower() not in current.get(sec.lower(), {}):
                        out.append(f"the game's config.ini has no [{sec}] {k}")
    if game is not None:
        out += _proxy_problems(p, game)
        folder_dll = any(rel.lower() == "d3d9.dll" for rel in p.files)
        chain = {s.lower(): {k.lower(): v for k, v in ks.items()} for s, ks in p.loader.items()}.get("d3d9", {})
        if folder_dll and chain.get("chain", None) not in ("",):
            out.append("the profile puts d3d9.dll in the game folder, which stays in charge of Direct3D 9: it must "
                       "also empty the loader's [d3d9] chain (\"loader\": {\"d3d9\": {\"chain\": \"\"}})")
    return out


def _proxy_problems(p: Profile, game: Game) -> list[str]:
    """An ENB's [PROXY] ProxyLibrary (the next Direct3D 9, such as DXVK's) must be a 32-bit Direct3D 9 DLL inside
    the game folder, and not the game folder's d3d9.dll itself (ENB would load itself)."""
    from . import loader as loaderlib
    out = []
    for rel, secs in p.ini.items():
        proxy = {s.lower(): {k.lower(): v for k, v in ks.items()} for s, ks in secs.items()}.get("proxy")
        if not proxy or proxy.get("enableproxylibrary", "").lower() != "true":
            continue
        lib = proxy.get("proxylibrary", "")
        if not lib:
            try:
                lib = ini_values(decode_ini(p.source(rel).read_bytes())[0]).get("proxy", {}).get("proxylibrary", "")
            except OSError:
                lib = ""
        if not lib:
            out.append(f"{rel}: [PROXY] EnableProxyLibrary is on and ProxyLibrary names nothing")
            continue
        if ":" in lib or lib.startswith(("\\", "/")) or ".." in lib.replace("\\", "/").split("/"):
            out.append(f"{rel}: [PROXY] ProxyLibrary = {lib} is not a path inside the game folder")
            continue
        target = game.root / Path(*lib.replace("\\", "/").split("/"))
        if target.parent == game.root and target.name.lower() == "d3d9.dll":
            out.append(f"{rel}: [PROXY] ProxyLibrary = {lib} is the game folder's d3d9.dll: ENB would load itself")
            continue
        if not target.is_file():
            out.append(f"{rel}: [PROXY] ProxyLibrary = {lib} does not exist in the game folder"
                       + (" (riftstone loader d3d9 add <DXVK release> puts DXVK there)" if "dxvk" in lib.lower()
                          else ""))
            continue
        try:
            loaderlib.check_d3d9_dll(target.read_bytes()[:64 << 20], lib)
        except (OSError, RiftError) as e:
            out.append(f"{rel}: [PROXY] ProxyLibrary: {e}")
    return out


# --- applied state ------------------------------------------------------------------------------------------

def state_path(game: Game) -> Path:
    return game.state_dir / STATE_FILE


def _safe_dir(rel: object, what: str) -> str:
    if not isinstance(rel, str) or not rel or len(rel) > 240:
        raise RiftError(f"{what}: {rel!r} is not a folder in the game folder")
    parts = rel.split("/")
    if any(not _PART.fullmatch(x) or x in (".", "..") or x.endswith((" ", ".")) or _RESERVED.fullmatch(x)
           for x in parts) or parts[0].lower() in PROTECTED_DIRS:
        raise RiftError(f"{what}: {rel!r} is not a folder a graphics profile makes")
    return rel


def _pairs(block: object) -> bool:
    """{section: {key: [before, set]}}, every value a string."""
    return isinstance(block, dict) and all(
        isinstance(ks, dict) and all(isinstance(v, list) and len(v) == 2 and all(isinstance(x, str) for x in v)
                                     for v in ks.values()) for ks in block.values())


def load_state(game: Game) -> dict | None:
    """<game>\\riftstone\\graphics.json: the profile in the game folder, the files it wrote (SHA-256 as written),
    the folders it made, the files it moved aside and the settings it changed ({section: {key: [before, set]}})."""
    f = state_path(game)
    if not f.is_file():
        return None
    try:
        doc = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as e:
        raise RiftError(f"{f} cannot be read ({e}); it records which graphics profile is in the game folder") from e
    if not isinstance(doc, dict) or doc.get("schema") != STATE_SCHEMA or not isinstance(doc.get("files"), dict):
        raise RiftError(f"{f} was not written by this Riftstone")
    for rel, sha in doc["files"].items():
        safe_rel(rel, str(f))
        if not isinstance(sha, str) or not _SHA.fullmatch(sha):
            raise RiftError(f"{f}: {rel} has no SHA-256")
    if not isinstance(doc.get("aside", []), list) or not isinstance(doc.get("dirs", []), list):
        raise RiftError(f"{f}: aside and dirs must be lists")
    for rel in doc.get("aside", []):
        safe_rel(rel, str(f))
    for rel in doc.get("dirs", []):
        _safe_dir(rel, str(f))
    for key in ("config", "loader"):
        if not _pairs(doc.get(key) or {}):
            raise RiftError(f"{f}: {key} is not {{section: {{key: [before, set]}}}}")
    for key in ("backup", "config_file"):
        if doc.get(key) is not None and not isinstance(doc[key], str):
            raise RiftError(f"{f}: {key} is not a path")
    return doc


def _save_state(game: Game, doc: dict) -> None:
    write_file(state_path(game), json.dumps(doc, indent=1, ensure_ascii=False).encode("utf-8"))


def _backups_home() -> Path:
    from .index import home
    return home() / "backups"


def _new_backup() -> Path:
    base = _backups_home() / f"graphics-{time.strftime('%Y%m%d-%H%M%S')}"
    folder, n = base, 1
    while folder.exists():
        n += 1
        folder = base.with_name(f"{base.name}-{n}")
    folder.mkdir(parents=True)
    return folder


def default_config(game: Game) -> Path | None:
    """The game's own settings file (Dark Arisen: %LOCALAPPDATA%\\CAPCOM\\DRAGONS DOGMA DARK ARISEN\\config.ini)."""
    if game.kind != "ddda":
        return None
    from . import playtest
    return playtest.game_config()


def _set_config(config: Path, values: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    """Set config.ini keys in place; {section: {key: old value}} (lower-case names)."""
    text, enc = decode_ini(config.read_bytes())
    new, before = set_ini(text, values, str(config))
    if new != text:
        write_file(config, encode_ini(new, enc))
    return before


def _loader_now(game: Game) -> dict[str, dict[str, str]]:
    from . import loader as loaderlib
    return loaderlib._ini_values(loaderlib._ini_text(game))


def _set_loader(game: Game, values: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    """Set riftstone_loader.ini keys (the loader's own writer: its template, the owner's values kept); {section:
    {key: old value}} (lower-case names)."""
    from . import loader as loaderlib
    now = _loader_now(game)
    before = {s.lower(): {k.lower(): now.get(s.lower(), {}).get(k.lower(), "") for k in keys}
              for s, keys in values.items()}
    if values:
        loaderlib._set_ini(game, values)
    return before


def _running(game: Game) -> bool:
    from . import install
    return install.game_running(game)


def _remove_empty_dirs(root: Path, rels: list[str]) -> None:
    """The folders a profile made, deepest first, when nothing else is left in them."""
    for rel in sorted(rels, key=lambda r: -r.count("/")):
        try:
            (root / Path(*rel.split("/"))).rmdir()
        except OSError:
            pass


def _get(values: dict[str, dict[str, str]], section: str, key: str) -> str:
    for s, keys in values.items():
        if s.lower() == section.lower():
            for k, v in keys.items():
                if k.lower() == key.lower():
                    return v
    return ""


@dataclass
class Result:
    profile: str = ""
    wrote: list[str] = field(default_factory=list)
    moved_aside: list[str] = field(default_factory=list)       # files in the way, now in the backup
    removed: list[str] = field(default_factory=list)
    kept_changed: list[str] = field(default_factory=list)      # changed since written: moved into the backup
    restored: list[str] = field(default_factory=list)          # files that were in the way, put back
    lost: list[str] = field(default_factory=list)              # were in the way, but gone from the backup
    config: dict = field(default_factory=dict)                 # "[SEC] Key": (before, now)
    config_kept: dict = field(default_factory=dict)            # changed since in the game's options: left alone
    loader: dict = field(default_factory=dict)
    backup: Path | None = None
    took_out: "Result | None" = None


def apply(game: Game, name: str | os.PathLike, profiles: Path | None = None, config: Path | None = None) -> Result:
    """Put a profile in the game folder (taking out the one there first) and set its settings.  Everything it will
    write is made and checked before anything is touched; if a step fails, the game folder and both settings files
    go back as they were."""
    from . import install
    p = load_profile(name, profiles)
    config = config if config is not None else default_config(game)
    if p.config and config is None:
        raise RiftError(f"{p.name} sets the game's config.ini, which only Dark Arisen has")
    if _running(game):
        raise RiftError("Dragon's Dogma is running. Close it first (its Direct3D DLL cannot be swapped while it is "
                        "loaded, and it writes config.ini when it closes).")
    bad = problems(p, game, config)
    if bad:
        raise RiftError(f"graphics profile {p.name} cannot be applied:\n  " + "\n  ".join(bad))
    data: dict[str, bytes] = {}
    for rel in p.files:
        b = p.source(rel).read_bytes()
        if rel in p.ini:
            text, enc = decode_ini(b)
            b = encode_ini(set_ini(text, p.ini[rel], rel)[0], enc)
        data[rel] = b
    with install.Lock(game):
        took_out = _off(game, config) if load_state(game) else None
        backup = _new_backup()
        saved_cfg = backup / "config.ini"
        saved_loader = backup / "riftstone_loader.ini"
        loader_ini = game.root / "riftstone_loader.ini"
        if config is not None and config.is_file():
            shutil.copyfile(config, saved_cfg)
        if loader_ini.is_file():
            shutil.copyfile(loader_ini, saved_loader)
        made_dirs: list[str] = []
        for rel in p.files:
            parts = rel.split("/")
            for i in range(1, len(parts)):
                sub = "/".join(parts[:i])
                if sub not in made_dirs and not (game.root / Path(*parts[:i])).exists():
                    made_dirs.append(sub)
        written: dict[str, str] = {}
        aside: list[str] = []
        cfg_before: dict = {}
        loader_before: dict = {}
        try:
            for rel, b in data.items():
                target = game.root / Path(*rel.split("/"))
                if target.is_dir():
                    raise RiftError(f"{target} is a folder where the profile puts a file")
                if target.exists():
                    keep = backup / "files" / Path(*rel.split("/"))
                    keep.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(target), str(keep))
                    aside.append(rel)
                write_file(target, b)
                written[rel] = _sha(b)
            if p.config:
                cfg_before = _set_config(config, p.config)
            loader_before = _set_loader(game, p.loader)
        except BaseException:
            for rel in written:
                (game.root / Path(*rel.split("/"))).unlink(missing_ok=True)
            for rel in aside:
                keep = backup / "files" / Path(*rel.split("/"))
                if keep.is_file():
                    shutil.move(str(keep), str(game.root / Path(*rel.split("/"))))
            _remove_empty_dirs(game.root, made_dirs)
            if cfg_before and saved_cfg.is_file():
                shutil.copyfile(saved_cfg, config)
            if saved_loader.is_file() and loader_ini.is_file() and loader_ini.read_bytes() != saved_loader.read_bytes():
                shutil.copyfile(saved_loader, loader_ini)
            raise
        _save_state(game, {"schema": STATE_SCHEMA, "profile": p.name, "title": p.title, "folder": str(p.folder),
                           "applied": time.strftime("%Y-%m-%dT%H:%M:%S"), "backup": str(backup),
                           "files": written, "dirs": made_dirs, "aside": aside,
                           "config_file": str(config) if (config is not None and p.config) else None,
                           # as the profile spells them: {section: {key: [before, set]}}
                           "config": {s: {k: [cfg_before.get(s.lower(), {}).get(k.lower(), ""), v]
                                          for k, v in keys.items()} for s, keys in p.config.items()},
                           "loader": {s: {k: [loader_before.get(s.lower(), {}).get(k.lower(), ""), v]
                                          for k, v in keys.items()} for s, keys in p.loader.items()}})
    return Result(profile=p.name, wrote=list(written), moved_aside=aside, backup=backup, took_out=took_out,
                  config={f"[{s}] {k}": (cfg_before.get(s.lower(), {}).get(k.lower(), ""), v)
                          for s, keys in p.config.items() for k, v in keys.items()},
                  loader={f"[{s}] {k}": (loader_before.get(s.lower(), {}).get(k.lower(), ""), v)
                          for s, keys in p.loader.items() for k, v in keys.items()})


def off(game: Game, config: Path | None = None) -> Result:
    """Take the applied profile out and put back what it replaced."""
    from . import install
    if _running(game):
        raise RiftError("Dragon's Dogma is running. Close it first.")
    if not load_state(game):
        raise RiftError("no graphics profile is applied")
    with install.Lock(game):
        return _off(game, config)


def _off(game: Game, config: Path | None) -> Result:
    doc = load_state(game)
    backup = Path(doc["backup"]) if doc.get("backup") else _new_backup()
    backup.mkdir(parents=True, exist_ok=True)
    res = Result(profile=str(doc.get("profile", "")), backup=backup)
    for rel, sha in doc["files"].items():
        target = game.root / Path(*rel.split("/"))
        if not target.is_file():
            continue
        if _file_sha(target) == sha:
            target.unlink()
            res.removed.append(rel)
        else:                                      # changed since (an ENB editor saves into its .ini files)
            keep = backup / "changed" / Path(*rel.split("/"))
            keep.parent.mkdir(parents=True, exist_ok=True)
            keep.unlink(missing_ok=True)
            shutil.move(str(target), str(keep))
            res.kept_changed.append(rel)
    for rel in doc.get("aside", []):
        kept = backup / "files" / Path(*rel.split("/"))
        target = game.root / Path(*rel.split("/"))
        if kept.is_file() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(kept), str(target))
            res.restored.append(rel)
        elif not target.exists():
            res.lost.append(rel)
    _remove_empty_dirs(game.root, list(doc.get("dirs", [])))
    cfg = config if config is not None else (Path(doc["config_file"]) if doc.get("config_file") else None)
    back, kept = _restorable(doc.get("config") or {}, cfg)
    if back:
        _set_config(cfg, back)
    res.config = {f"[{s}] {k}": v for s, keys in back.items() for k, v in keys.items()}
    res.config_kept = kept
    now = _loader_now(game)
    lback: dict[str, dict[str, str]] = {}
    for s, keys in (doc.get("loader") or {}).items():
        for k, (old, was_set) in keys.items():
            if now.get(s.lower(), {}).get(k.lower(), "") == was_set:
                lback.setdefault(s, {})[k] = old
    if lback:
        from . import loader as loaderlib
        loaderlib._set_ini(game, lback)
    res.loader = {f"[{s}] {k}": v for s, keys in lback.items() for k, v in keys.items()}
    state_path(game).unlink()
    return res


def _restorable(recorded: dict, cfg: Path | None) -> tuple[dict, dict]:
    """The config.ini keys to put back ({section: {key: before}}): those still at the value the profile set.  The
    others were changed since in the game's options and keep their value ({"[SECTION] Key": value now})."""
    if cfg is None or not cfg.is_file():
        return {}, {}
    now = ini_values(decode_ini(cfg.read_bytes())[0])
    back: dict[str, dict[str, str]] = {}
    kept: dict[str, str] = {}
    for s, keys in recorded.items():
        for k, (old, was_set) in keys.items():
            cur = now.get(s.lower(), {}).get(k.lower())
            if cur is None:
                continue
            if cur.upper() == was_set.upper():
                back.setdefault(s, {})[k] = old
            else:
                kept[f"[{s}] {k}"] = cur
    return back, kept


def status(game: Game, profiles: Path | None = None, config: Path | None = None) -> dict:
    """The applied profile and each of its files (as written / changed since / missing), and every profile there
    is with what would stop it applying."""
    doc = load_state(game)
    out: dict = {"applied": None, "files": {}, "profiles": {}}
    if doc:
        out["applied"] = {k: doc.get(k) for k in ("profile", "title", "applied", "backup", "folder")}
        for rel, sha in doc["files"].items():
            cur = _file_sha(game.root / Path(*rel.split("/")))
            out["files"][rel] = "missing" if cur is None else ("as written" if cur == sha else "changed since")
    cfg = config if config is not None else default_config(game)
    for name in profile_names(profiles):
        try:
            p = load_profile(name, profiles)
            out["profiles"][name] = {"title": p.title, "problems": problems(p, game, cfg), "files": len(p.files)}
        except RiftError as e:
            out["profiles"][name] = {"title": name, "problems": [str(e)], "files": 0}
    return out
