"""Enemy skins: chosen placements of an enemy wear a different texture set (Dragon's Dogma Online's
White Chimera, your own paint), while every other one stays vanilla.

How it works (DDDA.exe build 2364871; the ``enemy_skins`` plugin, docs/enemy-skins.md):

* A skin is a folder of resources added to the enemy's own archive.  For the chimera that is
  ``rom/enemy/em5200.arc``, folder ``model\\em\\e52\\e5200\\sNN\\``: the three full-detail materials
  (``e5200_a`` body, ``e5200_00_a`` goat head, ``e5200_01_a`` snake tail) and the four albedo maps
  (``e5200_skin_BM``, ``e5200_face_BM``, ``e5200_hebi_BM``, ``e5200_eye_BM``).  The materials are the
  vanilla ones with those four textures pointed into the folder; normal maps, masks and damage
  textures stay vanilla.
* A placement wears skin NN when its magic-defence multiplier is off and the multiplier's value holds
  the bits ``0x534B00NN``.  cSetInfoPawn::applyInfo copies both into the unit whatever the flag says,
  and every reader of the value checks the flag first, so the marker changes nothing in play.
* The plugin makes a marked chimera -- and its goat head and snake tail, through their parent -- ask
  for the skin folder's material and damage textures.  Without the plugin a marked chimera is a plain
  chimera; with the plugin but without the skin's files it keeps its low-detail material.
"""
from __future__ import annotations

import json
import re
import struct
from dataclasses import dataclass
from pathlib import Path

from . import arcfolder, fsmap, lot, modfiles, mrl, tex, texcodec, typemap
from .errors import ParamError, RiftError

MARK_TAG = lot.SKIN_MARK_TAG     # "SK" in the high half of the value's bits
MARK_MASK = lot.SKIN_MARK_MASK
MAX_SKIN = 99                    # the plugin builds folders s01..s99
FLAG_FIELD = lot.SKIN_FLAG       # use the magick defence multiplier (u8)
VALUE_FIELD = lot.SKIN_VALUE     # magick defence multiplier (f32, kept as bits)
MANIFEST = "skins.json"


@dataclass(frozen=True)
class Family:
    key: str                # "chimera"
    enemy: str              # the unit name groups list
    archive: str            # the archive that loads with every one of them
    folder: str             # where its model resources live
    materials: tuple        # the full-detail materials the plugin redirects
    textures: tuple         # the albedo maps a skin replaces
    record: str             # the layout record class of its placements


FAMILIES = {
    "chimera": Family("chimera", "em5200", "rom/enemy/em5200", "model\\em\\e52\\e5200",
                      ("e5200_a", "e5200_00_a", "e5200_01_a"),
                      ("e5200_skin_BM", "e5200_face_BM", "e5200_hebi_BM", "e5200_eye_BM"),
                      "cSetInfoEnemy5200"),
}


def family(key: str) -> Family:
    k = (key or "").strip().lower()
    for fam in FAMILIES.values():
        if k in (fam.key, fam.enemy):
            return fam
    raise ParamError(f"no skin family {key!r}; skins exist for: " + ", ".join(sorted(FAMILIES)))


def family_of_enemy(em: str) -> Family | None:
    return next((f for f in FAMILIES.values() if f.enemy == em), None)


def check_number(n) -> int:
    if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= MAX_SKIN:
        raise ParamError(f"a skin number is 1 to {MAX_SKIN}")
    return n


def marker(n: int) -> int:
    """The bits a placement's magick-defence multiplier holds to wear skin n (its flag stays off)."""
    return MARK_TAG | check_number(n)


def skin_of(record: lot.Record) -> int | None:
    """The skin a placement wears, or None."""
    f = record.fields
    if f.get(FLAG_FIELD, 1) != 0 or not isinstance(f.get(VALUE_FIELD), int):
        return None
    bits = f[VALUE_FIELD]
    n = bits & ~MARK_MASK & 0xFFFFFFFF
    return n if (bits & MARK_MASK) == MARK_TAG and 1 <= n <= MAX_SKIN else None


def mark(record: lot.Record, n: int, fam: Family) -> None:
    """Make a placement of this family wear skin n."""
    if record.cls != fam.record:
        raise ParamError(f"a {record.cls} record is not a {fam.key} placement ({fam.record}); skins are per family")
    record.fields[FLAG_FIELD] = 0
    record.fields[VALUE_FIELD] = marker(n)


def skin_name(fam: Family, n: int, base: str) -> str:
    return f"{fam.folder}\\s{check_number(n):02d}\\{base}"


def skin_file(mod_root: Path, fam: Family, n: int, base: str, type_id: int) -> Path:
    """Where a mod keeps one of skin n's resources: archives/<family archive>.arc/<resource path>."""
    rel = fsmap.encode_name(skin_name(fam, n, base).encode("latin-1"), type_id)
    return Path(mod_root) / "archives" / (fam.archive + ".arc") / rel


# -- textures -------------------------------------------------------------------------------------
def game_texture(data: bytes, version: int = tex.VERSION) -> bytes:
    """A flat texture of revision ``version`` (DDDA 0x99 or DDO 0x9D): that game's own as is, the other
    game's (the same header and pixel layout; measured on the chimera maps, every word but the first
    equal) with its revision and attribute bits rewritten (``tex.attr1_for``), or a .dds
    (DXT1/DXT5; ``tex.dds_to_tex`` refuses DXT3 and asks for DXT5)."""
    if version not in tex.VERSIONS:
        raise ParamError(f"texture revision 0x{version:x} is not one Riftstone writes")
    if data[:4] == b"DDS ":
        return tex.build(tex.dds_to_tex(data, version=version))
    if len(data) < 16 or data[:4] != b"TEX\0":
        raise ParamError("not a texture (.tex or .dds)")
    w1 = struct.unpack_from("<I", data, 4)[0]
    if w1 & 0xFFF != version and w1 & 0xFFF in tex.VERSIONS:
        data = data[:4] + struct.pack("<I", (tex.attr1_for(w1 >> 12, version) << 12) | version) + data[8:]
    t = tex.parse(data)                                  # checks the revision and header
    if tex.build(t) != bytes(data):
        raise ParamError("the texture does not rebuild byte for byte; refusing it")
    # The game reads every mip the header announces: the pixels must be exactly that long.
    if t.is_cube or tex._pixels_contiguous(t) is None:
        raise ParamError(f"a replaceable texture is a flat texture whose {t.mip_count} mip(s) all follow the "
                         f"header ({t.width}x{t.height}, format {t.fmt}, {len(data)} bytes does not fit)")
    return bytes(data)


def ddda_texture(data: bytes) -> bytes:
    """A texture DDDA can load (see ``game_texture``)."""
    return game_texture(data, tex.VERSION)


def texture_like(data: bytes, template: bytes, keep_revision: bool = False) -> bytes:
    """Any picture offered for a texture -- a .png (from any paint program), a .dds or a .tex from
    either game -- as a texture of the template's kind (format id and attributes): a DDDA texture
    (enemy skins), or with ``keep_revision`` one of the template's own game (a DDO mod's texture stays
    a DDO texture)."""
    version = tex.VERSION
    if keep_revision and len(template) >= 8 and template[:4] == b"TEX\0":
        version = struct.unpack_from("<I", template, 4)[0] & 0xFFF
    t = tex.parse(game_texture(template, version))
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h, px = texcodec.read_png(data)
        return game_texture(texcodec.encode(w, h, px, t), version)
    if data[:4] == b"DDS ":
        return game_texture(tex.build(tex.dds_to_tex(data, template=t)), version)
    return game_texture(data, version)


# -- a skin's resources ---------------------------------------------------------------------------
def resources(game, idx, fam: Family, n: int, textures: dict[str, bytes]) -> dict[tuple[str, int], bytes]:
    """Everything skin n adds to the family's archive: its four albedo maps (``textures`` by base name;
    a missing one is a copy of the vanilla map) and the three materials pointed at them."""
    TEX, MRL = typemap.BY_EXT["tex"], typemap.BY_EXT["mrl"]
    check_number(n)
    unknown = set(textures) - set(fam.textures)
    if unknown:
        raise ParamError(f"a {fam.key} skin replaces {', '.join(fam.textures)}; not {', '.join(sorted(unknown))}")
    out: dict[tuple[str, int], bytes] = {}
    for base in fam.textures:
        vanilla, _ = modfiles.load(game, idx, None, f"{fam.folder}\\{base}".encode("latin-1"), TEX)
        data = textures.get(base)
        out[(skin_name(fam, n, base), TEX)] = ddda_texture(vanilla) if data is None else texture_like(data, vanilla)
    prefix = fam.folder.lower() + "\\"
    for base in fam.materials:
        data, _ = modfiles.load(game, idx, None, f"{fam.folder}\\{base}".encode("latin-1"), MRL)
        m = mrl.parse(data)
        for t in m.textures:
            name = t.name
            if name.lower().startswith(prefix) and name.rsplit("\\", 1)[-1] in fam.textures:
                t.set_name(skin_name(fam, n, name.rsplit("\\", 1)[-1]))
        built = mrl.build(m)
        mrl.parse(built)
        out[(skin_name(fam, n, base), MRL)] = built
    return out


def mod_paths(fam: Family, res) -> list[str]:
    """Where a mod keeps a skin's resources, as its own paths."""
    return sorted(f"archives/{fam.archive}.arc/" + fsmap.encode_name(name.encode("latin-1"), tid) for name, tid in res)


def is_other_game_texture(data: bytes) -> bool:
    """A texture of Dragon's Dogma Online's revision (0x9D): the other game's content."""
    return len(data) >= 8 and data[:4] == b"TEX\0" and struct.unpack_from("<I", data, 4)[0] & 0xFFF == tex.VERSION_DDO


def record_ddo(mod_root: Path, fam: Family, n: int, variant: str, res) -> None:
    """Skin n was made from Online's chimera variant (ddoskins.py): the mod's package carries that recipe, and
    the player's Riftstone makes the textures from the player's own Online client (sources.py)."""
    from . import sources
    sources.record(mod_root, "ddo-skin", {"family": fam.key, "skin": n, "variant": variant}, mod_paths(fam, res),
                   foreign="Dragon's Dogma Online")


def mark_other_game(mod_root: Path, fam: Family, n: int, inputs: dict[str, bytes]) -> list[str]:
    """Mark the maps of skin n that were made from Online textures (a package refuses to carry them); returns
    their base names."""
    from . import sources
    TEX = typemap.BY_EXT["tex"]
    theirs = sorted(base for base, data in inputs.items() if is_other_game_texture(data))
    sources.mark_foreign(mod_root, mod_paths(fam, [(skin_name(fam, n, b), TEX) for b in theirs]),
                         "Dragon's Dogma Online")
    return theirs


def write(mod_root: Path, fam: Family, n: int, res: dict[tuple[str, int], bytes], title: str = "",
          source: str = "") -> list[Path]:
    """Put a skin into a mod (archives/<family archive>.arc/...) and record it in the mod's skins.json.  A manifest
    it cannot add to, or a resource outside the skin's folder, is refused before any file is written.  Whatever
    made the skin's files before no longer counts (sources.forget); a caller that knows where the new maps came
    from says so after (record_ddo, mark_other_game)."""
    from . import sources
    check_number(n)
    man = read_manifest(mod_root)
    outs = []
    for (name, type_id), data in sorted(res.items()):
        if not name.startswith(fam.folder + f"\\s{n:02d}\\"):
            raise ParamError(f"{name} is not in skin {n}'s folder")
        rel = fsmap.encode_name(name.encode("latin-1"), type_id)
        outs.append((mod_root / "archives" / (fam.archive + ".arc") / rel, data))
    written = []
    for out, data in outs:
        arcfolder.write_file(out, data)
        written.append(out)
    sources.forget(mod_root, mod_paths(fam, res))
    man.setdefault(fam.key, {})[str(n)] = {"title": title or f"{fam.key} skin {n}", "source": source,
                                           "textures": list(fam.textures), "materials": list(fam.materials)}
    (mod_root / MANIFEST).write_text(json.dumps(man, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    written.append(mod_root / MANIFEST)
    return written


def read_manifest(mod_root: Path) -> dict:
    """A mod's skins.json: {family: {number: {"title": ..., ...}}}.  Each family Riftstone knows must be an object
    (write adds to it); anything else is kept as it is."""
    p = Path(mod_root) / MANIFEST
    if not p.is_file():
        return {}
    try:
        man = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as e:       # RecursionError: nesting deeper than the decoder goes
        raise RiftError(f"{p}: not a skins manifest ({e})") from None
    if not isinstance(man, dict):
        raise RiftError(f"{p}: not a skins manifest")
    for key in FAMILIES:
        if key in man and not isinstance(man[key], dict):
            raise RiftError(f'{p}: "{key}" is not an object of skins by number ({{"1": {{"title": ...}}}})')
    return man


def owners(workspace: Path, fam: Family) -> dict[int, str]:
    """{skin number: the mod that has it} for one family, over the mods in a folder of mods.  Numbers are
    shared by every mod: the game sees one ``sNN`` folder, whichever mod added it."""
    from .mod import MOD_FILE

    out = {}
    workspace = Path(workspace)
    for d in sorted(workspace.iterdir(), key=lambda p: p.name.lower()) if workspace.is_dir() else ():
        if not (d / MOD_FILE).is_file():
            continue
        try:
            entries = read_manifest(d).get(fam.key) or {}
        except RiftError:
            continue
        for n in entries if isinstance(entries, dict) else ():
            if re.fullmatch(r"[0-9]{1,9}", str(n)):      # a hand-edited key such as '²' passed isdigit()
                out.setdefault(int(n), d.name)
    return out


def check_free(mod_root: Path, fam: Family, n: int) -> None:
    """Refuse skin n for a mod when another mod next to it has that number already."""
    owner = owners(Path(mod_root).parent, fam).get(n)
    if owner is not None and owner != Path(mod_root).name:
        raise RiftError(f"{fam.key} skin {n} is already in {owner}: every mod shares one set of skin numbers (the "
                        f"game sees one s{n:02d} folder), so pick another number or remake it in {owner}")


PICTURES = (".png", ".dds", ".tex")


def ddo_variant_of_folder(folder: Path) -> str | None:
    """The Online chimera variant a folder of maps came from, when tools/ddo_skins.py wrote it (its
    riftstone-source.json), else None."""
    from . import ddoskins
    try:
        mark = json.loads((Path(folder) / "riftstone-source.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    variant = mark.get("variant") if isinstance(mark, dict) and mark.get("kind") == "ddo-skin" else None
    return variant if variant in ddoskins.VARIANTS else None


def textures_from_folder(folder: Path, fam: Family) -> dict[str, bytes]:
    """The albedo maps in a folder, by base name: <base>.png, <base>.dds or <base>.tex (either game's).
    They are converted in :func:`resources`, with the vanilla map as the template."""
    folder = Path(folder)
    if not folder.is_dir():
        raise RiftError(f"{folder} is not a folder")
    out = {}
    for base in fam.textures:
        for ext in PICTURES:
            p = folder / (base + ext)
            if p.is_file():
                out[base] = p.read_bytes()
                break
    if not out:
        raise RiftError(f"{folder} holds none of {', '.join(b + '.png/.dds/.tex' for b in fam.textures)}")
    return out


def export(mod_root: Path, fam: Family, n: int, folder: Path, kind: str = "png") -> list[Path]:
    """Skin n's albedo maps as editable pictures (png or dds) in a folder, named so ``skin make`` takes
    them back: edit them in any paint program, then ``riftstone skin make <family> <n> --textures``."""
    TEX = typemap.BY_EXT["tex"]
    if kind not in ("png", "dds"):
        raise ParamError("export as png or dds")
    folder = Path(folder)
    if folder.exists() and not folder.is_dir():
        raise RiftError(f"{folder} is a file; export writes the pictures into a folder")
    pictures = []                  # every picture first: a refusal leaves no folder behind
    for base in fam.textures:
        src = skin_file(mod_root, fam, n, base, TEX)
        if not src.is_file():
            raise RiftError(f"{Path(mod_root).name} has no {fam.key} skin {n} ({src.name} is missing)")
        t = tex.parse(src.read_bytes())
        if kind == "dds":
            out = tex.to_dds(t)
        else:
            w, h, px = texcodec.decode(t, 0)
            out = texcodec.png(w, h, px)
        pictures.append((folder / f"{base}.{kind}", out))
    try:
        folder.mkdir(parents=True, exist_ok=True)
        for p, out in pictures:
            p.write_bytes(out)
    except OSError as e:
        raise RiftError(f"cannot write the pictures into {folder}: {e.strerror or e}") from None
    return [p for p, _ in pictures]
