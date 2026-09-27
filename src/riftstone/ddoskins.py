"""Dragon's Dogma Online's chimera variants as DDDA skin textures, made from the player's own Online client.

    riftstone monster convert "White Chimera" --into chimera --as-skin N --mod <mod>
    python tools/ddo_skins.py white <out folder>      # the four maps as files, for skin make --textures

DDO's chimeras are DDDA's chimera model with other maps (the UV layout is the same; checked on the
skin, snake and face sheets).  The White Chimera is a plain recolour, so its four albedo maps are
copied as they are (``riftstone skin make`` rewrites their revision, 0x9D -> 0x99).  The Shadow and
Blaze chimeras get their look from DDO's "curse" material: a mottled mask (``em015203_*_d_MM``)
blends a dark curse texture, and for Blaze an ember glow, over the normal chimera's colours in the
shader.  DDDA has no such shader, so ``bake`` blends it into the albedo maps with DDO's own masks:
the pattern is DDO's, the colours are chosen here (DDO's material constants are unlabeled), and
nothing animates.  They are an approximation; the White Chimera is exact.

Everything here is deterministic, so a mod package carries the recipe (``ddo-skin``: family, number,
variant; sources.py) and never the textures: the player's Riftstone makes them again from the
player's own client (package.py).

Reads the client through Riftstone's own ARCC reader (the client ``find_game('ddo')`` finds), else through
the ``ddon`` toolkit (``<path>``, or $DDON_HOME\\src).  Standard library only; the block
(de)compression below is plain Python.
"""
from __future__ import annotations

import os
import re
import struct
import sys
from pathlib import Path

from . import tex
from .errors import RiftError

TEX_TYPE = 0x241F5DEB        # rTexture (JAMCRC of the class name, as in both games)

VARIANTS = {
    "white": ("EM015202", "Dragon's Dogma Online White Chimera (em015202)"),
    "shadow": ("EM015203", "Dragon's Dogma Online Shadow Chimera (em015203), curse baked in"),
    "blaze": ("EM015204", "Dragon's Dogma Online Blaze Chimera (em015204), curse and embers baked in"),
}
BASE = "obj\\em\\em015200\\model\\em015200_"      # the plain DDO chimera, shared by every variant archive


def variant_of(source: str) -> str | None:
    """The variant a skin's recorded source names (skins.json "source": this module's title, or any text
    naming the variant's enemy id, as "Dragon's Dogma Online, em015202" does), or None."""
    if not isinstance(source, str):
        return None
    low = source.lower()
    return next((v for v, (archive, title) in VARIANTS.items() if source == title or archive.lower() in low), None)


def names_online(source: str) -> bool:
    """A skin source that says it came from Dragon's Dogma Online (by name, abbreviation or an em01 id)."""
    low = source.lower() if isinstance(source, str) else ""
    return "online" in low or "ddo" in low or bool(re.search(r"em01\d{4}", low))


def ddon_arc():
    home = Path(os.environ.get("DDON_HOME", r"<path>"))
    if str(home / "src") not in sys.path:
        sys.path.insert(0, str(home / "src"))
    try:
        from ddon import arc as darc  # type: ignore
        from ddon import paths as dpaths  # type: ignore
    except ImportError:
        raise RiftError(f"Dragon's Dogma Online's client was not found (set RIFTSTONE_DDO to its folder); the ddon "
                        f"toolkit is not under {home}\\src either") from None
    return darc, dpaths


def client_rom() -> Path:
    _, dpaths = ddon_arc()
    try:
        return Path(dpaths.rom_root())
    except Exception as e:  # noqa: BLE001 -- ddon explains what it looked for
        raise RiftError(f"the DDO client was not found: {e}") from None


def read_textures(archive: str) -> dict[str, bytes]:
    """Every texture of a DDO enemy archive, by name: through Riftstone's own ARCC reader when the client is
    found (find_game('ddo'); the original archive if an install replaced it), else through the ddon toolkit."""
    from . import arc as rarc
    from .game import find_game

    try:
        game = find_game("ddo")
    except RiftError:
        game = None
    if game is not None and game.arc_path(f"rom/EM/{archive}").is_file():
        a = rarc.Archive.read(game.vanilla_arc(f"rom/EM/{archive}"))
        return {e.name.decode("latin-1"): e.data() for e in a.entries if e.type_id == TEX_TYPE}
    darc, _ = ddon_arc()
    a = darc.Arc.load(client_rom() / "EM" / f"{archive}.arc")
    out = {}
    for e in a.entries:
        if e.type_id == TEX_TYPE:
            out[e.name] = a.data(e)
    return out


# -- block (de)compression ---------------------------------------------------------------------
def _565(c):
    return ((c >> 11) & 31) * 255 // 31, ((c >> 5) & 63) * 255 // 63, (c & 31) * 255 // 31


def _colors(c0, c1, four):
    a, b = _565(c0), _565(c1)
    if four:
        return [a, b, tuple((2 * x + y) // 3 for x, y in zip(a, b)), tuple((x + 2 * y) // 3 for x, y in zip(a, b))]
    return [a, b, tuple((x + y) // 2 for x, y in zip(a, b)), (0, 0, 0)]


def decode(t: tex.Tex) -> tuple[int, int, bytearray]:
    """The largest mip as RGBA bytes (DXT1-family 8-byte blocks, or DXT5 16-byte blocks)."""
    w, h = t.width, t.height
    block = tex._BLOCK.get(t.fmt)
    if block is None:
        raise RiftError(f"texture format {t.fmt} is not block-compressed")
    data = tex.to_dds(t)[128:]
    out = bytearray(w * h * 4)
    p = 0
    for by in range(0, h, 4):
        for bx in range(0, w, 4):
            alpha = [255] * 16
            if block == 16:
                a0, a1 = data[p], data[p + 1]
                bits = int.from_bytes(data[p + 2:p + 8], "little")
                al = [a0, a1] + ([((6 - i) * a0 + (1 + i) * a1) // 7 for i in range(6)] if a0 > a1
                                 else [((4 - i) * a0 + (1 + i) * a1) // 5 for i in range(4)] + [0, 255])
                alpha = [al[(bits >> (3 * i)) & 7] for i in range(16)]
                p += 8
            c0, c1, idx = struct.unpack_from("<HHI", data, p)
            p += 8
            pal = _colors(c0, c1, c0 > c1 or block == 16)
            for i in range(16):
                x, y = bx + (i & 3), by + (i >> 2)
                if x < w and y < h:
                    r, g, b = pal[(idx >> (2 * i)) & 3]
                    o = (y * w + x) * 4
                    out[o:o + 4] = bytes((r, g, b, alpha[i]))
    return w, h, out


def _to565(r, g, b):
    return ((r * 31 + 127) // 255) << 11 | ((g * 63 + 127) // 255) << 5 | ((b * 31 + 127) // 255)


def encode_dxt5(w: int, h: int, px: bytearray) -> bytes:
    """A straightforward DXT5 encoder: colour endpoints at the block's extremes along its main axis
    (approximated by luminance), four-colour indices by nearest palette entry, 8-level alpha."""
    out = bytearray()
    for by in range(0, h, 4):
        for bx in range(0, w, 4):
            blk = []
            for i in range(16):
                x, y = min(bx + (i & 3), w - 1), min(by + (i >> 2), h - 1)
                o = (y * w + x) * 4
                blk.append(px[o:o + 4])
            al = [q[3] for q in blk]
            a0, a1 = max(al), min(al)
            if a0 == a1:
                out += bytes((a0, a1)) + b"\0" * 6
            else:
                levels = [a0, a1] + [((6 - i) * a0 + (1 + i) * a1) // 7 for i in range(6)]
                bits = 0
                for i, a in enumerate(al):
                    k = min(range(8), key=lambda j: abs(levels[j] - a))
                    bits |= k << (3 * i)
                out += bytes((a0, a1)) + bits.to_bytes(6, "little")
            lum = [q[0] * 299 + q[1] * 587 + q[2] * 114 for q in blk]
            hi, lo = blk[lum.index(max(lum))], blk[lum.index(min(lum))]
            c0, c1 = _to565(*hi[:3]), _to565(*lo[:3])
            if c0 < c1:
                c0, c1 = c1, c0
            if c0 == c1:
                out += struct.pack("<HHI", c0, c1, 0)
                continue
            pal = _colors(c0, c1, True)
            idx = 0
            for i, q in enumerate(blk):
                k = min(range(4), key=lambda j: (pal[j][0] - q[0]) ** 2 + (pal[j][1] - q[1]) ** 2 + (pal[j][2] - q[2]) ** 2)
                idx |= k << (2 * i)
            out += struct.pack("<HHI", c0, c1, idx)
    return bytes(out)


def _half(w, h, px):
    nw, nh = max(1, w // 2), max(1, h // 2)
    out = bytearray(nw * nh * 4)
    for y in range(nh):
        for x in range(nw):
            acc = [0, 0, 0, 0]
            for dy in (0, 1):
                for dx in (0, 1):
                    o = ((min(2 * y + dy, h - 1)) * w + min(2 * x + dx, w - 1)) * 4
                    for c in range(4):
                        acc[c] += px[o + c]
            out[(y * nw + x) * 4:(y * nw + x) * 4 + 4] = bytes(v // 4 for v in acc)
    return nw, nh, out


def build_tex(w: int, h: int, px: bytearray, template: tex.Tex) -> bytes:
    """A DDDA texture (the template's format id and attributes) with a full DXT5 mip chain."""
    mips, cw, ch, cur, body = 0, w, h, px, bytearray()
    while True:
        body += encode_dxt5(cw, ch, cur)
        mips += 1
        if cw == 1 and ch == 1:
            break
        cw, ch, cur = _half(cw, ch, cur)
    hdr = bytearray(128)
    linear = max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * 16
    struct.pack_into("<4sI", hdr, 0, b"DDS ", 124)
    struct.pack_into("<IIIIII", hdr, 8, 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000 | 0x80000, h, w, linear, 0, mips)
    struct.pack_into("<II4s", hdr, 76, 32, 0x4, b"DXT5")
    struct.pack_into("<I", hdr, 108, 0x1000 | 0x400000 | 0x8)
    t = tex.dds_to_tex(bytes(hdr) + bytes(body), template=template)
    return tex.build(t)


# -- the variants -------------------------------------------------------------------------------
def _mask(texs, name):
    t = tex.parse(_ddda(texs[name]))
    return decode(t)


def _ddda(data: bytes) -> bytes:
    w1 = struct.unpack_from("<I", data, 4)[0]
    if w1 & 0xFFF == 0x09D:
        data = data[:4] + struct.pack("<I", (((w1 >> 12) & ~0x2) << 12) | tex.VERSION) + data[8:]
    return data


def _sample(w, h, px, x, y, W, H):
    """px (w x h) sampled at the position of (x, y) in a W x H image (nearest)."""
    sx, sy = min(w - 1, x * w // W), min(h - 1, y * h // H)
    o = (sy * w + sx) * 4
    return px[o], px[o + 1], px[o + 2], px[o + 3]


def bake(base: bytes, mask: tuple, style: str) -> bytes:
    t = tex.parse(_ddda(base))
    w, h, px = decode(t)
    mw, mh, mpx = mask
    out = bytearray(px)
    for y in range(h):
        for x in range(w):
            o = (y * w + x) * 4
            m = _sample(mw, mh, mpx, x, y, w, h)[0] / 255.0
            r, g, b = px[o], px[o + 1], px[o + 2]
            if style == "shadow":
                k = 0.25 + 0.65 * m                         # the curse darkens everything, deeply where masked
                dr, dg, db = 26, 22, 44                     # a dark violet-blue, like DDO's curse texture
                r, g, b = (int(c * (1 - k) + d * k) for c, d in ((r, dr), (g, dg), (b, db)))
            else:                                           # blaze: scorched fur, embers in the brightest cracks
                r, g, b = int(r * 0.55), int(g * 0.45), int(b * 0.40)
                e = max(0.0, min(1.0, (m - 0.72) / 0.23)) ** 2 * 0.8
                r, g, b = r + int(235 * e), g + int(95 * e), b + int(15 * e)   # ember light added, fur kept
            out[o:o + 3] = bytes((max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))))
    return build_tex(w, h, out, t)


NAMES = {"e5200_skin_BM": "skin_BM", "e5200_face_BM": "face_BM", "e5200_hebi_BM": "hebi_BM", "e5200_eye_BM": "eye_BM"}


def build(variant: str, echo=lambda s: None) -> dict[str, bytes]:
    """A variant's four albedo maps by DDDA name (DDO textures as they are for White; baked for the
    others).  Used by main() and by Studio's Skins panel."""
    archive, _ = VARIANTS[variant]
    texs = read_textures(archive)
    own = f"obj\\em\\{archive.lower()}\\model\\{archive.lower()}_"
    out = {}
    if variant == "white":
        for dd, part in NAMES.items():
            out[dd] = texs[own + part]
            echo(f"{dd}.tex  <- {own + part}")
        return out
    masks = {part: _mask(texs, f"obj\\em\\em015203\\model\\em015203_{part.split('_')[0]}_d_MM")
             for part in ("skin_BM", "face_BM", "hebi_BM")}
    for dd, part in NAMES.items():
        if part == "eye_BM":
            out[dd] = texs.get(own + part) or texs[BASE + part]
            echo(f"{dd}.tex  <- {own + part if own + part in texs else BASE + part}")
            continue
        base = texs[own + part] if (variant == "shadow" and own + part in texs) else texs[BASE + part]
        echo(f"{dd}.tex  <- baking {variant} over {'DDO ' + part} ...")
        out[dd] = bake(base, masks[part], variant)
    return out
