"""Studio's file tools: what each file of a mod does to the game, previews, downloads, edits, setting
files aside, exporting, and making skins -- with the choice of where produced files go.

* **What a file does.**  A file either *adds* something the game does not have (a skin's textures, a
  new layout) or *changes* a game resource (a stage's group list).  Additions never clash with other
  mods; changes can, when another mod changes the same resource.  Every file row says which, and names
  the other mods in the workspace that touch the same resource.
* **See and edit.**  Textures preview as pictures and download as ``.png`` or ``.dds`` (or the raw
  ``.tex``); an edited picture uploads back as a texture of the same kind.  YAML downloads as text and
  uploads back after it validates.  A replaced file's previous version is kept in
  ``aside/_history/`` (never overwritten), so nothing is lost.
* **Set aside.**  "Put aside" moves a file to ``<mod>/aside/`` (the build ignores it: the file is
  kept, the game does not see it); "Bring back" moves it back, swapping with a newer file at the same
  place if there is one.
* **Keep copies.**  A whole mod, or a skin's pictures, downloads as a ``.zip``.
* **Where produced files go.**  Making a skin or an encounter takes an existing mod or the name of a
  new one, so new work can stay separate from mods that change shared resources.
"""
from __future__ import annotations

import base64
import binascii
import io
import re
import shutil
import time
import zipfile
from pathlib import Path

from . import arcfolder, fsmap, mrl, params, skins, tex, texcodec, typemap, xfs
from .errors import RiftError
from .mod import MOD_FILE, Mod

ASIDE = "aside"
HISTORY = "_history"
MAX_UPLOAD = 40 * 1024 * 1024


# -- where a mod file points -------------------------------------------------------------------
def resource_of(rel: str) -> tuple[str | None, bytes, int] | None:
    """(archive or None, engine name, type id) of a mod file under files/ or archives/ (or the same
    under aside/); None for anything else (mod.json, skins.json, notes)."""
    parts = rel.split("/")
    if parts and parts[0] == ASIDE:
        parts = parts[1:]
    if len(parts) < 2 or parts[0] not in ("files", "archives"):
        return None
    arc_name = None
    rest = parts[1:]
    if parts[0] == "archives":
        cut = next((i for i, p in enumerate(rest) if p.lower().endswith(".arc")), None)
        if cut is None or cut == len(rest) - 1:
            return None
        arc_name = "/".join(rest[:cut] + [rest[cut][:-4]])
        rest = rest[cut + 1:]
    target = "/".join(rest)
    if target.lower().endswith(".yaml"):
        target = target[:-5]
    try:
        name, tid = fsmap.decode_path(target)
    except RiftError:
        return None
    return arc_name, name, tid


def _mod_files(root: Path) -> list[str]:
    out = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if rel.split("/")[0] in ("files", "archives", ASIDE) and p.name.lower() not in ("readme.txt",):
            out.append(rel)
    return out


def _mods(studio) -> list[Path]:
    """The workspace's mod folders, by name."""
    if not studio.workspace.is_dir():
        return []
    return [d for d in sorted(studio.workspace.iterdir(), key=lambda p: p.name.lower()) if (d / MOD_FILE).is_file()]


def _keys(studio) -> dict[str, dict]:
    """Every mod's active resources: {mod name: {(archive or None, name, type): rel}}."""
    out = {}
    for d in _mods(studio):
        keys = {}
        for rel in _mod_files(d):
            if rel.startswith(ASIDE + "/"):
                continue
            r = resource_of(rel)
            if r:
                keys[r] = rel
        out[d.name] = keys
    return out


def _overlaps(a: tuple, b: tuple) -> bool:
    """Two mod files touch the same game resource: same name and type, and the same archive or one of
    them under files/ (which applies to every archive holding it)."""
    return a[1:] == b[1:] and (a[0] is None or b[0] is None or a[0].lower() == b[0].lower())


def clashes(studio, mod: str | None, key: tuple, keys: dict | None = None) -> list[str]:
    """The other mods in the workspace holding the same resource (key: archive or None, name, type)."""
    keys = _keys(studio) if keys is None else keys
    return sorted(m for m, ks in keys.items() if m != mod and any(_overlaps(key, k) for k in ks))


def classify(studio, idx, mod: str, rel: str, keys: dict | None = None) -> dict:
    r = resource_of(rel)
    if r is None:
        return {"status": "other", "also": []}
    arc_name, name, tid = r
    arcs = idx.archives_with(name, tid)
    if arc_name is None:
        status = "changes" if arcs else "adds"
        where = len(arcs)
    else:
        status = "changes" if any(a.lower() == arc_name.lower() for a in arcs) else "adds"
        where = 1
    return {"status": status, "archives": where, "also": clashes(studio, mod, r, keys), "type": typemap.extension(tid),
            "resource": name.decode("latin-1"), "archive": arc_name}


def skin_of_file(rel: str) -> tuple[skins.Family, int] | None:
    """(family, skin number) when a mod file is one of a skin's resources."""
    r = resource_of(rel)
    if r is None:
        return None
    name = r[1].decode("latin-1").lower()
    for fam in skins.FAMILIES.values():
        m = re.match(re.escape(fam.folder.lower()) + r"\\s(\d\d)\\", name)
        if m:
            return fam, int(m.group(1))
    return None


def list_files(studio, mod: str) -> dict:
    root = studio.mod_root(mod)
    idx = studio.open_index()
    try:
        keys = _keys(studio)
        rows = []
        for rel in _mod_files(root):
            if rel.startswith(f"{ASIDE}/{HISTORY}/"):
                continue
            info = classify(studio, idx, mod, rel, keys)
            p = root / rel
            rows.append({"rel": rel, "size": p.stat().st_size, "aside": rel.startswith(ASIDE + "/"), **info})
    finally:
        idx.close()
    history = root / ASIDE / HISTORY
    n_hist = sum(1 for p in history.rglob("*") if p.is_file()) if history.is_dir() else 0
    return {"mod": mod, "files": rows, "history": n_hist}


# -- one file ----------------------------------------------------------------------------------
def _file(studio, mod: str, rel: str) -> Path:
    if not isinstance(rel, str) or not rel:
        raise RiftError("say which file")
    f = arcfolder.safe_member(studio.mod_root(mod), rel)
    if not f.is_file():
        raise RiftError("that file does not exist")
    return f


def _texture(data: bytes) -> tex.Tex:
    t = tex.parse(data)
    if t.is_cube:
        raise RiftError("cube maps have no picture preview")
    return t


def preview(studio, mod: str, rel: str) -> dict:
    f = _file(studio, mod, rel)
    out = {"rel": rel, "size": f.stat().st_size}
    if f.suffix.lower() == ".tex":
        t = _texture(f.read_bytes())
        png, w, h = texcodec.preview(t, 384)
        out.update(kind="texture", width=t.width, height=t.height, format=t.fmt, codec=texcodec.codec(t.fmt),
                   mips=t.mip_count, from_png=texcodec.writable(t.fmt), shown=[w, h],
                   png="data:image/png;base64," + base64.b64encode(png).decode("ascii"))
    elif f.suffix.lower() in (".yaml", ".json", ".txt"):
        out.update(kind="text", text=f.read_text(encoding="utf-8-sig", errors="replace")[:200_000])
    elif f.suffix.lower() == ".mrl":
        m = mrl.parse(f.read_bytes())
        out.update(kind="material", text=mrl.info(m))
    else:
        out.update(kind="binary", magic=f.read_bytes()[:4].decode("latin-1"))
    return out


def download(studio, mod: str, rel: str, as_: str = "raw") -> dict:
    f = _file(studio, mod, rel)
    data = f.read_bytes()
    name = f.name
    mime = "application/octet-stream"
    if as_ in ("png", "dds"):
        if f.suffix.lower() != ".tex":
            raise RiftError("only textures download as pictures")
        t = _texture(data)
        if as_ == "dds":
            data, name = tex.to_dds(t), f.stem + ".dds"
        else:
            w, h, px = texcodec.decode(t, 0)
            data, name, mime = texcodec.png(w, h, px), f.stem + ".png", "image/png"
    elif as_ != "raw":
        raise RiftError("download as raw, png or dds")
    elif f.suffix.lower() in (".yaml", ".json", ".txt"):
        mime = "text/plain; charset=utf-8"
    return {"name": name, "mime": mime, "b64": base64.b64encode(data).decode("ascii"), "bytes": len(data)}


def _upload(body: dict) -> bytes:
    raw = body.get("b64")
    if not isinstance(raw, str) or not raw:
        raise RiftError("send the file's contents (b64)")
    if len(raw) > MAX_UPLOAD * 4 // 3 + 4:
        raise RiftError(f"the file is larger than {MAX_UPLOAD // (1024 * 1024)} MB")
    try:
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise RiftError("the file's contents are not valid base64") from None


def _keep_history(root: Path, rel: str, data: bytes) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = root / ASIDE / HISTORY / f"{rel}.{stamp}"
    n = 1
    while out.exists():
        n += 1
        out = root / ASIDE / HISTORY / f"{rel}.{stamp}-{n}"
    arcfolder.write_file(out, data)
    return out


def _yaml_kind(text: str, source: str) -> tuple[str, str | None]:
    """A YAML file's kind: its tag's format and, for parameter files (XFS), the root class (None when
    the text does not compile)."""
    kind = params.yaml_tag(text).split("/")[0]
    if kind != params.TAG.split("/")[0]:
        return kind, None
    try:
        return kind, xfs.parse(params.yaml_to_resource(text, source)).root_class.name
    except RiftError:
        return kind, None


def replace(studio, body: dict) -> dict:
    """Put an edited file in place of a mod file; the old one is kept in aside/_history."""
    mod, rel = body.get("mod"), body.get("rel")
    f = _file(studio, mod, rel)
    new = _upload(body)
    old = f.read_bytes()
    suffix = f.suffix.lower()
    if suffix == ".tex":
        new = skins.texture_like(new, old, keep_revision=True)   # a DDO mod's texture stays DDO's
        t = tex.parse(new)
        note = f"texture {t.width}x{t.height}, {texcodec.codec(t.fmt)}, {t.mip_count} mips"
    elif suffix == ".yaml":
        try:
            text = new.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise RiftError("a .yaml file is UTF-8 text") from None
        res = studio.validate(text, f.name)
        if not res["ok"]:
            raise RiftError(f"not saved: {res['error']}")
        was, now = _yaml_kind(old.decode("utf-8-sig", "replace"), f.name), _yaml_kind(text, f.name)
        if was[0] != now[0] or None not in (was[1], now[1]) and was[1] != now[1]:
            raise RiftError(f"not saved: this file holds {'/'.join(filter(None, was))} data, the new one is "
                            f"{'/'.join(filter(None, now))}")
        new = text.encode("utf-8")
        note = res.get("summary", "valid")
    elif suffix == ".mrl":
        try:
            m = mrl.parse(new)
        except RiftError as e:
            raise RiftError(f"not a material list: {e}") from None
        note = f"{len(m.materials)} materials, {len(m.textures)} textures"
    else:
        if new[:4] != old[:4]:
            raise RiftError(f"the new file is a different kind (it starts {new[:4]!r}, the old one {old[:4]!r})")
        note = f"{len(new)} bytes"
    root = studio.mod_root(mod)
    kept = _keep_history(root, rel, old)
    arcfolder.write_file(f, new)
    studio.log("ok", f"Replaced {rel} in {mod} ({note}); the old one is in {kept.relative_to(root).as_posix()}")
    return {"ok": True, "note": note, "kept": kept.relative_to(root).as_posix()}


def preset(studio, body: dict) -> dict:
    """Recolour a mod's texture with a preset (Frost, Lava, Abyssal, Weathered) in place; the old one is
    kept in aside/_history. The material keeps pointing at the same texture, so the model picks it up."""
    from . import texfx

    mod, rel = body.get("mod"), body.get("rel")
    f = _file(studio, mod, rel)
    if f.suffix.lower() != ".tex":
        raise RiftError("presets apply to textures (.tex)")
    strength = body.get("strength", 1.0)
    if isinstance(strength, bool) or not isinstance(strength, (int, float)):
        raise RiftError("strength is a number from 0 to 1")
    old = f.read_bytes()
    new = texfx.apply(old, str(body.get("preset", "")), float(strength))
    root = studio.mod_root(mod)
    kept = _keep_history(root, rel, old)
    arcfolder.write_file(f, new)
    studio.log("ok", f"{body.get('preset')} preset on {rel} in {mod}; the old one is in {kept.relative_to(root).as_posix()}")
    return {"ok": True, "preset": body.get("preset"), "kept": kept.relative_to(root).as_posix()}


def aside(studio, body: dict) -> dict:
    """Move a file to aside/ (back=False) or back from it (back=True; swaps with a newer file there)."""
    mod, rel = body.get("mod"), body.get("rel")
    root = studio.mod_root(mod)
    f = _file(studio, mod, rel)
    if body.get("back"):
        if not rel.startswith(ASIDE + "/") or rel.startswith(f"{ASIDE}/{HISTORY}/"):
            raise RiftError("only a file that was put aside comes back")
        target = arcfolder.safe_member(root, rel[len(ASIDE) + 1:])
        if target.exists():                                # swap: the newer file goes aside in its place
            tmp = f.with_name(f.name + ".swap")
            f.replace(tmp)
            target.replace(f)
            tmp.replace(target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            f.replace(target)
        studio.log("ok", f"Brought back {rel[len(ASIDE) + 1:]} in {mod}")
        return {"ok": True, "rel": rel[len(ASIDE) + 1:]}
    if rel.split("/")[0] not in ("files", "archives"):
        raise RiftError("only files under files/ or archives/ can be put aside")
    target = arcfolder.safe_member(root, f"{ASIDE}/{rel}")
    if target.exists():
        raise RiftError("a copy of that file is already set aside; bring it back first (that swaps them)")
    target.parent.mkdir(parents=True, exist_ok=True)
    f.replace(target)
    studio.log("ok", f"Put aside {rel} in {mod}: kept, not built")
    out = {"ok": True, "rel": f"{ASIDE}/{rel}"}
    skin = skin_of_file(rel)
    if skin:
        out["warn"] = (f"placements wearing {skin[0].key} skin {skin[1]} look for this file; while it is set aside, "
                       f"how the game draws them is UNKNOWN (bring it back before installing if they should wear it)")
    return out


def export_zip(studio, mod: str) -> dict:
    """The whole mod folder (not its build output) as a .zip, to keep a copy somewhere else."""
    root = studio.mod_root(mod)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(root.rglob("*")):
            if p.is_file():
                rel = p.relative_to(root).as_posix()
                if rel.split("/")[0] == "build" or any(part.startswith(".") for part in rel.split("/")):
                    continue
                z.write(p, f"{root.name}/{rel}")
    data = buf.getvalue()
    return {"name": f"{root.name}-{time.strftime('%Y%m%d-%H%M')}.zip", "mime": "application/zip",
            "b64": base64.b64encode(data).decode("ascii"), "bytes": len(data)}


# -- where produced files go ---------------------------------------------------------------------
_BAD = re.compile(r'[\\/:*?"<>|]')


def destination(studio, body: dict) -> tuple[Path, str | None]:
    """The mod that gets produced files: ``mod`` (an existing one) or ``new_mod`` (the name of a new one,
    checked here and created by ``make_mod`` once the work is ready, so a step that fails leaves no
    empty mod behind).  Returns (its folder, the new mod's name or None)."""
    new = body.get("new_mod")
    if new in (None, ""):
        return studio.mod_root(body.get("mod")), None
    if not isinstance(new, str) or not new.strip() or new != new.strip() or _BAD.search(new) or new in (".", ".."):
        raise RiftError("give the new mod a name without \\ / : * ? \" < > |")
    root = studio.workspace / new
    if (root / MOD_FILE).is_file():
        raise RiftError(f"a mod named {new!r} already exists; pick it as the destination instead")
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise RiftError(f"{root} exists and is not empty")
    return root, new


def make_mod(studio, root: Path, new: str | None) -> None:
    """Create the new mod ``destination`` checked (nothing to do for an existing one)."""
    if new is not None:
        Mod.create(root, new)
        studio.log("ok", f"Created mod {new}")


# -- skins ---------------------------------------------------------------------------------------
def _thumb(p: Path, side: int = 128) -> str | None:
    try:
        png, _, _ = texcodec.preview(tex.parse(p.read_bytes()), side)
    except (RiftError, OSError, ValueError):
        return None
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def ddo_tool():
    """tools/ddo_skins.py (the Riftstone checkout's), or None when it is not here."""
    import importlib.util
    path = Path(__file__).resolve().parents[2] / "tools" / "ddo_skins.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("riftstone_ddo_skins", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def skins_list(studio) -> dict:
    TEX = typemap.BY_EXT["tex"]
    out = []
    for d in _mods(studio):
        try:
            man = skins.read_manifest(d)
        except RiftError as e:
            out.append({"mod": d.name, "error": str(e)})
            continue
        for key, entries in sorted(man.items()):
            fam = skins.FAMILIES.get(key)
            if fam is None or not isinstance(entries, dict):
                continue
            for n, e in sorted(entries.items(),
                               key=lambda kv: int(kv[0]) if re.fullmatch(r"[0-9]{1,9}", str(kv[0])) else 0):
                if not re.fullmatch(r"[0-9]{1,9}", str(n)) or not 1 <= int(n) <= skins.MAX_SKIN:   # '²', a hand edit
                    continue
                thumbs = {b: _thumb(skins.skin_file(d, fam, int(n), b, TEX)) for b in fam.textures}
                e = e if isinstance(e, dict) else {}                 # a hand edit: "1": "White"
                out.append({"mod": d.name, "family": key, "number": int(n), "title": e.get("title", ""),
                            "source": e.get("source", ""), "thumbs": thumbs})
    fams = [{"key": f.key, "enemy": f.enemy, "textures": list(f.textures)} for f in skins.FAMILIES.values()]
    tool = ddo_tool()
    ddo = sorted(tool.VARIANTS) if tool is not None else []
    return {"skins": out, "families": fams, "ddo": ddo}


def skin_owners(studio, fam: skins.Family) -> dict[int, str]:
    """{skin number: the mod that has it} for one family, over every mod in the workspace."""
    return skins.owners(studio.workspace, fam)


def next_number(studio, fam: skins.Family) -> int:
    used = skin_owners(studio, fam)
    for n in range(1, skins.MAX_SKIN + 1):
        if n not in used:
            return n
    raise RiftError(f"every {fam.key} skin number (1..{skins.MAX_SKIN}) is taken by a mod in {studio.workspace}")


def skin_make(studio, body: dict) -> dict:
    """Make a skin from uploaded pictures (any of the family's albedo maps; the rest vanilla) or from a
    Dragon's Dogma Online variant, into an existing mod or a new one.  Making a skin again (same number,
    same mod) replaces it; the files it replaces are kept in aside/_history."""
    fam = skins.family(str(body.get("family", "chimera")))
    game = studio.need_game()
    root, new = destination(studio, body)
    n = body.get("number")
    if n in (None, ""):
        n = next_number(studio, fam)
    if not isinstance(n, int) or isinstance(n, bool):
        raise RiftError("number is a skin number, 1..99")
    skins.check_number(n)
    skins.check_free(root, fam, n)
    textures = {}
    ups = body.get("textures")
    ups = {} if ups is None else ups
    if not isinstance(ups, dict):
        raise RiftError("textures is {map name: {b64}}")
    for base, up in ups.items():
        if base not in fam.textures:
            raise RiftError(f"a {fam.key} skin replaces {', '.join(fam.textures)}; not {base}")
        if not isinstance(up, dict):
            raise RiftError(f"{base}: send {{b64}}")
        textures[base] = _upload(up)
    ddo = body.get("ddo")
    title, source = str(body.get("title") or ""), str(body.get("source") or "")
    if ddo:
        tool = ddo_tool()
        if not isinstance(ddo, str) or tool is None or ddo not in getattr(tool, "VARIANTS", {}):
            raise RiftError("Dragon's Dogma Online import needs tools/ddo_skins.py, the ddon toolkit and one of its "
                            "variants")
        if textures:
            raise RiftError("either upload pictures or import from Dragon's Dogma Online, not both")
        textures = tool.build(ddo)
        title = title or tool.VARIANTS[ddo][1].split(" (")[0]
        source = source or tool.VARIANTS[ddo][1]
    idx = studio.open_index()
    try:
        res = skins.resources(game, idx, fam, n, textures)
    finally:
        idx.close()
    make_mod(studio, root, new)
    kept = []
    for (name, tid), data in sorted(res.items()):
        rel = f"archives/{fam.archive}.arc/" + fsmap.encode_name(name.encode("latin-1"), tid)
        f = root / rel
        if f.is_file() and f.read_bytes() != data:
            kept.append(_keep_history(root, rel, f.read_bytes()).relative_to(root).as_posix())
    files = skins.write(root, fam, n, res, title, source)
    studio.log("ok", f"{fam.key} skin {n} ({title or 'untitled'}) -> {root.name}: {len(textures)} map(s) of "
                     f"{len(fam.textures)} replaced" + (f"; {len(kept)} old file(s) kept in aside/_history" if kept else ""))
    return {"ok": True, "mod": root.name, "number": n, "written": [p.relative_to(root).as_posix() for p in files],
            "kept": kept, "note": "a skin only adds files, so it never clashes with other mods"}


def skin_export(studio, q: dict) -> dict:
    """A skin's albedo maps as pictures in a .zip (named so they upload straight back)."""
    fam = skins.family(str(q.get("family", "chimera")))
    try:
        n = int(q.get("number", ""))
    except ValueError:
        raise RiftError("number is a skin number") from None
    root = studio.mod_root(q.get("mod"))
    kind = q.get("as", "png")
    tmp = studio.workspace / f".export-{n}-{time.time_ns()}"
    try:
        files = skins.export(root, fam, n, tmp, kind)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for p in files:
                z.write(p, f"{fam.key}-skin-{n:02d}/{p.name}")
            z.writestr(f"{fam.key}-skin-{n:02d}/README.txt",
                       f"{fam.key} skin {n} from {root.name}: edit these in any paint program, then upload them "
                       f"back in Riftstone Studio (Skins) or run\n  riftstone skin make {fam.key} {n} --textures "
                       f"<this folder> --mod \"{root.name}\"\n")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    data = buf.getvalue()
    return {"name": f"{root.name}-{fam.key}-skin-{n:02d}.zip", "mime": "application/zip",
            "b64": base64.b64encode(data).decode("ascii"), "bytes": len(data)}


# -- the routes -------------------------------------------------------------------------------------
def api(studio, method: str, route: str, q: dict, body: dict) -> dict | None:
    """Studio's routes for these tools; None when the route is not one of them."""
    if route == "files/list" and method == "GET":
        return list_files(studio, q.get("mod"))
    if route == "files/preview" and method == "GET":
        return preview(studio, q.get("mod"), q.get("rel", ""))
    if route == "files/download" and method == "GET":
        return download(studio, q.get("mod"), q.get("rel", ""), q.get("as", "raw"))
    if route == "files/replace" and method == "POST":
        return replace(studio, body)
    if route == "files/aside" and method == "POST":
        return aside(studio, body)
    if route == "files/preset" and method == "POST":
        return preset(studio, body)
    if route == "files/export" and method == "GET":
        return export_zip(studio, q.get("mod"))
    if route == "skins" and method == "GET":
        return skins_list(studio)
    if route == "skins/make" and method == "POST":
        return skin_make(studio, body)
    if route == "skins/export" and method == "GET":
        return skin_export(studio, q)
    return None


__all__ = ["api", "clashes", "classify", "destination", "make_mod", "resource_of", "skin_owners"]
