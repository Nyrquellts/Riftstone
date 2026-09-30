"""Install and remove the Riftstone loader (dinput8.dll) in a game folder.

Installing keeps any other dinput8.dll (DDDA Tweak and similar) working: it is
renamed to dinput8_chain.dll and named in riftstone_loader.ini, and the
loader passes DirectInput through to it.  Removing puts it back.  Dark Arisen
mods run only through the loader (install.py): removing it takes them out of
the game and they wait for it (state.json); installing it brings them back,
and moves mods an older Riftstone installed directly into the overlay.

The loader can also give the game another Direct3D 9 ([d3d9] chain): DXVK's
32-bit d3d9.dll, kept in <game>\\riftstone\\dxvk, so nothing goes into the game
folder itself (:func:`d3d9_add`, :func:`d3d9_off`, :func:`d3d9_status`).
"""
from __future__ import annotations

import hashlib
import lzma
import os
import shutil
import tarfile
import zipfile
import zlib
from pathlib import Path

from . import install, pe
from .errors import FormatError, RiftError
from .game import Game
from .runtime import ini_bytes, ini_text

MARKER = "Riftstone loader".encode("utf-16-le")
CHAIN_NAME = "dinput8_chain.dll"
D3D9_CHAIN = "riftstone\\dxvk\\d3d9.dll"         # [d3d9] chain, as d3d9_add writes it
_DLL_LIMIT = 64 << 20
DXVK_CONF = """# DXVK settings, read because the Riftstone loader points DXVK_CONFIG_FILE at this file
# ([d3d9] chain = riftstone\\dxvk\\d3d9.dll). Every line is commented out: DXVK's defaults are
# what Riftstone measured (docs/runtime.md). DXVK's own list: dxvk.conf in its source.
#
# How much address space DXVK keeps mapped at once for the copies of the game's managed
# textures (MB; 0 = no limit). DXVK says not to change it without a very good reason.
# d3d9.textureMemory = 100
"""


def _candidates() -> list[Path]:
    here = Path(__file__).resolve()
    root = here.parents[2]
    return [root / "loader", root / "native" / "loader" / "out"]


def built_loader() -> Path:
    for d in _candidates():
        dll = d / "dinput8.dll"
        if dll.is_file() and is_ours(dll):          # is_ours swallows a locked/unreadable DLL (OSError), so a
            return d                                 # build in flight or an AV lock does not crash Studio's API
    raise RiftError("the loader is not built. Run native\\loader\\build.cmd (needs Visual Studio C++ tools), "
                    "or use a Riftstone release that includes loader\\dinput8.dll")


def is_ours(dll: Path) -> bool:
    try:
        return MARKER in dll.read_bytes()
    except OSError:
        return False


def plugins_dir(game: Game) -> Path:
    return game.state_dir / "plugins"


def list_plugins(game: Game) -> list[str]:
    d = plugins_dir(game)
    return sorted(p.name for p in d.glob("*") if p.is_file() and p.suffix.lower() in (".asi", ".dll")) if d.is_dir() else []


def add_plugin(game: Game, source: Path) -> str:
    """Copy a native plugin (.asi/.dll) into <game>\\riftstone\\plugins so the loader loads it.

    A settings file with the plugin's name (lod_tuner.ini next to lod_tuner.asi) comes along the
    first time; an ini already in the plugins folder holds the owner's settings and is kept.
    """
    source = Path(source)
    if source.suffix.lower() not in (".asi", ".dll"):
        raise RiftError("a native plugin is a .asi or .dll file")
    if not source.is_file():
        raise RiftError(f"{source} does not exist")
    d = plugins_dir(game)
    d.mkdir(parents=True, exist_ok=True)
    target = d / source.name
    shutil.copyfile(source, target)
    ini = source.with_suffix(".ini")
    if ini.is_file() and not target.with_suffix(".ini").exists():
        shutil.copyfile(ini, target.with_suffix(".ini"))
    return target.name


def remove_plugin(game: Game, name: str) -> None:
    if any(c in name for c in "\\/") or name not in list_plugins(game):
        raise RiftError(f"no plugin named {name!r} in {plugins_dir(game)}")
    (plugins_dir(game) / name).unlink()


def status(game: Game) -> dict:
    dll = game.root / "dinput8.dll"
    chain = game.root / CHAIN_NAME
    logs = game.state_dir / "logs"
    return {"installed": dll.is_file() and is_ours(dll) and (game.root / "riftstone_loader.ini").is_file(),
            "other_dinput8": dll.is_file() and not is_ours(dll),
            "chained": chain.is_file(),
            "plugins": list_plugins(game),
            "crash_reports": sorted(p.name for p in logs.glob("crash-*.txt")) if logs.is_dir() else [],
            "log": (logs / "loader.log") if (logs / "loader.log").is_file() else None}


OVERLAY_KEY_DEFAULT = "Insert"      # [overlay] key: shows the in-game panel (overlay.cpp's DEFAULT_KEY)


def panel_key(game: Game) -> str:
    """The key that shows the in-game panel: [overlay] key of the game's riftstone_loader.ini (Insert without one)."""
    return _ini_values(_ini_text(game)).get("overlay", {}).get("key", "") or OVERLAY_KEY_DEFAULT


def _ini_values(text: str) -> dict[str, dict[str, str]]:
    """{section: {key: value}}, sections and keys lower-cased, first block of a section winning (as
    Windows reads it)."""
    out: dict[str, dict[str, str]] = {}
    current = None
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip().lower()
            current = None if name in seen else out.setdefault(name, {})
            seen.add(name)
        elif "=" in line and current is not None:
            k, _, v = line.partition("=")
            current.setdefault(k.strip().lower(), v.strip())
    return out


def merge_ini(template: str, existing: str, overrides: dict[str, dict[str, str]] | None = None) -> str:
    """The template's layout and comments with the owner's values kept: a key the owner set keeps its
    value, a key new in the template gets its default, a key only the owner has stays in its section.
    ``overrides`` ({section: {key: value}}) win over both."""
    mine = _ini_values(existing)
    overrides = {s.lower(): {k.lower(): v for k, v in keys.items()} for s, keys in (overrides or {}).items()}
    out: list[str] = []
    used: dict[str, set[str]] = {}
    section = None

    def close(sec):
        if sec is None:
            return
        extra = {**mine.get(sec, {}), **overrides.get(sec, {})}
        for k, v in extra.items():
            if k not in used.get(sec, set()):
                out.append(f"{k} = {v}")
                used.setdefault(sec, set()).add(k)

    for raw in template.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            close(section)
            section = line[1:-1].strip().lower()
            out.append(raw)
            continue
        if section and line and not line.startswith(";") and "=" in line:
            key = line.partition("=")[0].strip().lower()
            if key in overrides.get(section, {}):
                out.append(f"{line.partition('=')[0].strip()} = {overrides[section][key]}")
            elif key in mine.get(section, {}):
                out.append(f"{line.partition('=')[0].strip()} = {mine[section][key]}")
            else:
                out.append(raw)
            used.setdefault(section, set()).add(key)
            continue
        out.append(raw)
    close(section)
    for sec in list(mine) + [s for s in overrides if s not in mine]:
        if sec in used:
            continue
        out.append("")
        out.append(f"[{sec}]")
        used[sec] = set()
        close(sec)
    return "\n".join(out).rstrip("\n") + "\n"


def _template_ini() -> str:
    here = Path(__file__).resolve().parents[2]
    templates = [here / "loader" / "riftstone_loader.ini", here / "native" / "loader" / "riftstone_loader.ini"]
    template = next((t for t in templates if t.is_file()), None)
    return ini_text(template.read_bytes()) if template else "[loader]\noverlay = 1\n"


def _write_ini(game: Game, chain: str) -> None:
    """Write riftstone_loader.ini from the template, keeping every value the owner already set, in the code
    page the loader reads it in (runtime.ini_text)."""
    target = game.root / "riftstone_loader.ini"
    try:
        owners = target.read_bytes()
    except OSError:
        owners = b""
    existing = ini_text(owners)
    # A chain the owner set by hand (another name than dinput8_chain.dll) stays unless one was just made.
    overrides: dict[str, dict[str, str]] = {"loader": {"chain": chain}} if chain or not existing else {}
    # The panel key was F10 up to 1.0.2; the game's window treats F10 as a system key, so the default is Insert now.
    # A settings file that still holds the old default takes the new one (any other key the owner chose stays).
    if _ini_values(existing).get("overlay", {}).get("key", "").upper() == "F10":
        overrides.setdefault("overlay", {})["key"] = OVERLAY_KEY_DEFAULT
    text = merge_ini(_template_ini(), existing, overrides or None)
    tmp = target.with_name(target.name + ".riftstone-tmp")
    tmp.write_bytes(ini_bytes(text, like=owners))               # a UTF-16 file stays UTF-16, as Windows keeps it
    os.replace(tmp, target)


WAITING = "waiting_for_loader"     # state.json: the mods that were on when the loader was removed


def _switch_mods(game: Game, index, to_mode: str) -> list[str]:
    """Take mods out in the old mode, then put them back in the new one (with the mods that waited for the
    loader since it was removed)."""
    state = install.load_state(game)
    roots = [Path(m["path"]) for m in state.get("mods", [])]
    roots += [p for p in map(Path, state.get(WAITING, [])) if p not in roots and p.is_dir()]
    if state.get("archives"):
        install.restore_all(game)
    if roots:
        install.apply(game, index, roots, mode=to_mode)
    if WAITING in install.load_state(game):
        with install.Lock(game):
            st = install.load_state(game)
            st.pop(WAITING, None)
            install.save_state(game, st)
    return [r.name for r in roots]


def _mods_in_overlay(game: Game) -> bool:
    """Every archive the installed mods changed is served from riftstone\\overlay already."""
    state = install.load_state(game)
    return all((game.overlay_dir / (arc + ".arc")).is_file() for arc in state.get("archives", {}))


def install_loader(game: Game, index) -> dict:
    """Put the loader in place, or update it.  An update keeps the settings and leaves the installed
    mods as they are (they are in the overlay already); a first install moves them there."""
    if game.kind != "ddda":
        raise RiftError(f"'loader install' is for DDDA.exe; {game.title} mods install directly (originals kept in "
                        "riftstone\\vanilla), and the runtime goes into its client with the DDO toolkit "
                        "(ddon.cmd runtime install --yes, docs/runtime.md)")
    if install.game_running(game):
        raise RiftError("Dragon's Dogma is running. Close it first.")
    src = built_loader()
    dll = game.root / "dinput8.dll"
    update = game.loader_installed() and _mods_in_overlay(game)
    chain = ""
    if dll.is_file() and not is_ours(dll):
        if (game.root / CHAIN_NAME).exists():
            raise RiftError(f"both dinput8.dll and {CHAIN_NAME} exist and neither is Riftstone's; sort them out first")
        os.replace(dll, game.root / CHAIN_NAME)
        chain = CHAIN_NAME
    elif (game.root / CHAIN_NAME).is_file():
        chain = CHAIN_NAME
    tmp = dll.with_name("dinput8.dll.riftstone-tmp")
    shutil.copyfile(src / "dinput8.dll", tmp)
    os.replace(tmp, dll)
    _write_ini(game, chain)
    moved = [] if update else _switch_mods(game, index, "overlay")
    return {"chained": chain or None, "mods_moved_to_overlay": moved, "updated": update}


def _ini_text(game: Game) -> str:
    try:
        return (game.root / "riftstone_loader.ini").read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""


def _set_ini(game: Game, overrides: dict[str, dict[str, str]]) -> None:
    """riftstone_loader.ini from the template, the owner's values kept, ``overrides`` set."""
    target = game.root / "riftstone_loader.ini"
    text = merge_ini(_template_ini(), _ini_text(game), overrides)
    tmp = target.with_name(target.name + ".riftstone-tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)


def check_d3d9_dll(data: bytes, name: str = "the DLL") -> None:
    """A DLL the loader can chain for the game's Direct3D 9: 32-bit, a DLL, exports Direct3DCreate9."""
    try:
        h = pe.header(data)
        names = pe.exports(data)
    except FormatError as e:
        raise RiftError(f"{name} is not a Windows DLL ({e})") from e
    if h.machine == 0x8664:
        raise RiftError(f"{name} is a 64-bit DLL, and Dark Arisen is 32-bit: use the d3d9.dll from DXVK's x32 folder")
    if h.machine != 0x014C or h.pe32plus:
        raise RiftError(f"{name} is not a 32-bit Windows DLL ({h.machine_name})")
    if not h.is_dll:
        raise RiftError(f"{name} is a program, not a DLL")
    if "Direct3DCreate9" not in names:
        raise RiftError(f"{name} has no Direct3DCreate9: it is not a Direct3D 9 runtime")


def _is_dxvk(data: bytes) -> bool:
    """DXVK's DLLs read their settings from DXVK_CONFIG_FILE and name it in their bytes (3.1.1: once)."""
    return b"DXVK_CONFIG_FILE" in data


_MAX_MEMBERS = 4096                             # a DXVK release holds about a dozen files


def _tar_names(tar: tarfile.TarFile) -> list[str]:
    """The member names, reading no further than _MAX_MEMBERS headers (each header costs decompression)."""
    names = []
    for member in tar:
        names.append(member.name)
        if len(names) > _MAX_MEMBERS:
            raise RiftError(f"more than {_MAX_MEMBERS} files: not a DXVK release")
    return names


def _x32_member(names: list[str]) -> str:
    """The one x32/d3d9.dll in a release archive's member names."""
    if len(names) > _MAX_MEMBERS:
        raise RiftError(f"more than {_MAX_MEMBERS} files: not a DXVK release")
    hits = [n for n in names if n.replace("\\", "/").lower().rstrip("/").endswith("x32/d3d9.dll")]
    if len(hits) != 1:
        raise RiftError("the archive has " + ("no" if not hits else f"{len(hits)}") + " x32/d3d9.dll (a DXVK release "
                        "holds exactly one, in its x32 folder)")
    return hits[0]


def find_d3d9_dll(source: Path) -> tuple[bytes, str]:
    """(DLL bytes, where they came from) for a 32-bit d3d9.dll given as the DLL itself, a folder (its x32\\d3d9.dll
    or d3d9.dll), or a DXVK release archive (.tar.gz, .tgz, .tar or .zip: its x32/d3d9.dll).  Checked with
    check_d3d9_dll; nothing is extracted to disk."""
    source = Path(source)
    if source.is_dir():
        for cand in (source / "x32" / "d3d9.dll", source / "d3d9.dll"):
            if cand.is_file():
                source = cand
                break
        else:
            raise RiftError(f"{source} has neither x32\\d3d9.dll nor d3d9.dll")
    if not source.is_file():
        raise RiftError(f"{source} does not exist")
    if source.stat().st_size > (512 << 20):
        raise RiftError(f"{source} is too large for a DLL or a DXVK release")
    low = source.name.lower()
    try:
        if low.endswith((".tar.gz", ".tgz", ".tar")):
            with tarfile.open(source, "r:*") as tar:
                member = tar.getmember(_x32_member(_tar_names(tar)))
                if not member.isfile() or member.size > _DLL_LIMIT:
                    raise RiftError(f"{member.name} in {source.name} is not a DLL-sized file")
                f = tar.extractfile(member)
                data = f.read(_DLL_LIMIT + 1) if f else b""
                origin = f"{source.name}: {member.name}"
        elif low.endswith(".zip"):
            with zipfile.ZipFile(source) as z:
                info = z.getinfo(_x32_member(z.namelist()))
                if info.file_size > _DLL_LIMIT:
                    raise RiftError(f"{info.filename} in {source.name} is not a DLL-sized file")
                data = z.read(info)
                origin = f"{source.name}: {info.filename}"
        else:
            data = source.read_bytes()
            origin = str(source)
    # zipfile refuses a newer format or an encrypted entry with NotImplementedError / RuntimeError, and damaged
    # compressed data surfaces as zlib's or lzma's own error (fuzz: d3d9_source).
    except (tarfile.TarError, zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, OSError, KeyError, ValueError,
            NotImplementedError, RuntimeError, zlib.error, lzma.LZMAError) as e:
        raise RiftError(f"{source} cannot be read as a DLL or a DXVK release ({e})") from e
    if len(data) > _DLL_LIMIT:
        raise RiftError(f"{origin} is too large for a DLL")
    check_d3d9_dll(data, origin)
    return data, origin


def d3d9_status(game: Game) -> dict:
    """What [d3d9] chain names and whether the loader would take it; any d3d9.dll put straight into the game
    folder (DXVK or another wrapper installed the classic way)."""
    setting = _ini_values(_ini_text(game)).get("d3d9", {}).get("chain", "").split(";", 1)[0].strip()
    out = {"setting": setting, "path": None, "problem": None, "dxvk": False, "sha256": None, "conf": None,
           "game_folder_d3d9": None, "game_folder_dxvk": False}
    folder_dll = game.root / "d3d9.dll"
    if folder_dll.is_file():
        out["game_folder_d3d9"] = folder_dll
        try:
            out["game_folder_dxvk"] = _is_dxvk(folder_dll.read_bytes()[:_DLL_LIMIT])
        except OSError:
            pass
    if not setting:
        return out
    target = game.root / setting
    out["path"] = target
    try:
        inside = target.resolve().is_relative_to(game.root.resolve())
    except OSError:
        inside = False
    if ":" in setting or setting.startswith(("\\", "/")) or not inside:
        out["problem"] = "is not inside the game folder"
    elif not target.is_file():
        out["problem"] = "does not exist"
    else:
        try:
            data = target.read_bytes()
            check_d3d9_dll(data, setting)
            out["dxvk"] = _is_dxvk(data)
            out["sha256"] = hashlib.sha256(data).hexdigest()
        except (OSError, RiftError) as e:
            out["problem"] = str(e)
        conf = target.parent / "dxvk.conf"
        out["conf"] = conf if conf.is_file() else None
    if out["game_folder_d3d9"] and not out["problem"]:
        out["problem"] = "is not loaded: the game folder's own d3d9.dll stays in charge (take one of the two out)"
    return out


def d3d9_add(game: Game, source: Path) -> dict:
    """Put a 32-bit d3d9.dll (DXVK's, from its release archive, its folder or the DLL) in
    <game>\\riftstone\\dxvk and name it in [d3d9] chain; a dxvk.conf with DXVK's defaults (all commented out)
    goes beside it unless one is there.  Nothing goes into the game folder itself."""
    if install.game_running(game):
        raise RiftError("Dragon's Dogma is running. Close it first (the DLL cannot be replaced while it is loaded).")
    data, origin = find_d3d9_dll(source)
    folder = game.state_dir / "dxvk"
    folder.mkdir(parents=True, exist_ok=True)
    dll = folder / "d3d9.dll"
    tmp = dll.with_name("d3d9.dll.riftstone-tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dll)
    conf = folder / "dxvk.conf"
    wrote_conf = not conf.exists()
    if wrote_conf:
        conf.write_text(DXVK_CONF, encoding="utf-8")
    _set_ini(game, {"d3d9": {"chain": D3D9_CHAIN}})
    return {"path": dll, "origin": origin, "sha256": hashlib.sha256(data).hexdigest(), "dxvk": _is_dxvk(data),
            "conf": conf, "wrote_conf": wrote_conf, "loader_installed": game.loader_installed(),
            "game_folder_d3d9": (game.root / "d3d9.dll") if (game.root / "d3d9.dll").is_file() else None}


def d3d9_off(game: Game) -> bool:
    """[d3d9] chain emptied: the game gets Windows' own Direct3D 9 again (the DLL stays in riftstone\\dxvk).
    Returns whether a chain was set."""
    was = bool(d3d9_status(game)["setting"])
    if was:
        _set_ini(game, {"d3d9": {"chain": ""}})
    return was


def remove_loader(game: Game, index) -> dict:
    if game.kind != "ddda":
        raise RiftError(f"{game.title}'s runtime comes and goes with the DDO toolkit (ddon.cmd runtime remove --yes)")
    if install.game_running(game):
        raise RiftError("Dragon's Dogma is running. Close it first.")
    dll = game.root / "dinput8.dll"
    if dll.is_file() and not is_ours(dll):
        raise RiftError("the dinput8.dll in the game folder is not Riftstone's loader; nothing removed")
    state = install.load_state(game)
    roots = [Path(m["path"]) for m in state.get("mods", [])]
    if state.get("archives"):
        install.restore_all(game)
    dll.unlink(missing_ok=True)
    (game.root / "riftstone_loader.ini").unlink(missing_ok=True)
    chain = game.root / CHAIN_NAME
    restored_chain = False
    if chain.is_file():
        os.replace(chain, dll)
        restored_chain = True
    if roots:           # Dark Arisen mods run only through the loader: they wait for it to come back
        with install.Lock(game):
            st = install.load_state(game)
            st[WAITING] = [str(r) for r in roots]
            install.save_state(game, st)
    return {"restored_other_dinput8": restored_chain, "mods_waiting": [r.name for r in roots]}
