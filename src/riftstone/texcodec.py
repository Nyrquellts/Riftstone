"""Pixels for textures: block (de)compression and PNG, in plain Python (standard library only).

``tex.py`` keeps a texture's pixels opaque and byte-exact; this module looks inside them, for Studio's
previews and for editing textures in any paint program:

* :func:`decode` -- one mip of a ``.tex`` as RGBA bytes.  BC1 (formats 19, 20, 25), BC3 (24, 37, 43, 47),
  BC5 normal maps (31: red and green are the two channels, blue is rebuilt) and uncompressed (40, BGRA).
* :func:`encode` -- RGBA pixels as a ``.tex`` with a full mip chain, in a template's format id and
  attributes (BC1 or BC3), so a map that came out of the game goes back as the same kind of texture.
* :func:`png` / :func:`read_png` -- 8-bit RGBA PNG out; PNG in (grey, grey+alpha, RGB, RGBA, palette, each
  with its transparency; 8 or 16 bits; not interlaced).
* :func:`preview` -- a small PNG of a texture (the largest mip that fits), for pages.

The encoder is a simple one (colour end points on the block's bounding box, indices by projection),
good enough for albedo and masks; it is not a match for a dedicated compressor.
"""
from __future__ import annotations

import struct
import zlib

from . import tex
from .errors import FormatError

BC1 = {19, 20, 25}
BC3 = {24, 37, 43, 47}
BC5 = {31}
RGBA8 = {40}
MAX_SIDE = 4096
MAX_PNG = (8 * MAX_SIDE + 1) * MAX_SIDE     # decompressed bytes a PNG may claim: 16-bit RGBA rows at the largest side


def codec(fmt: int) -> str:
    """A texture format's common name."""
    return ("BC1 (DXT1)" if fmt in BC1 else "BC3 (DXT5)" if fmt in BC3 else "BC5 (normal map)" if fmt in BC5
            else "RGBA8" if fmt in RGBA8 else f"format {fmt}")


def writable(fmt: int) -> bool:
    """A picture (PNG) can be turned back into this kind of texture."""
    return fmt in BC1 | BC3


def _mip_offset(t: tex.Tex, level: int) -> int:
    """Where mip ``level`` starts in ``t.body`` (the texture must have the standard contiguous layout)."""
    if tex._pixels_contiguous(t) is None:
        raise FormatError("tex", "only flat textures with the standard mip layout can be decoded")
    if not 0 <= level < t.mip_count:
        raise FormatError("tex", f"mip {level} does not exist (the texture has {t.mip_count})")
    return 4 * t.mip_count + sum(tex._mip_size(t.width, t.height, m, t.fmt) for m in range(level))


def _unpack565(c: int) -> tuple[int, int, int]:
    r, g, b = (c >> 11) & 31, (c >> 5) & 63, c & 31
    return (r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)


def _palette(c0: int, c1: int, four: bool) -> list:
    a, b = _unpack565(c0), _unpack565(c1)
    if four:
        return [a + (255,), b + (255,), tuple((2 * x + y) // 3 for x, y in zip(a, b)) + (255,),
                tuple((x + 2 * y) // 3 for x, y in zip(a, b)) + (255,)]
    return [a + (255,), b + (255,), tuple((x + y) // 2 for x, y in zip(a, b)) + (255,), (0, 0, 0, 0)]


def _alpha_levels(a0: int, a1: int) -> list:
    if a0 > a1:
        return [a0, a1] + [((6 - i) * a0 + (1 + i) * a1) // 7 for i in range(6)]
    return [a0, a1] + [((4 - i) * a0 + (1 + i) * a1) // 5 for i in range(4)] + [0, 255]


def decode(t: tex.Tex, level: int = 0) -> tuple[int, int, bytes]:
    """Mip ``level`` of a texture as (width, height, RGBA bytes)."""
    off = _mip_offset(t, level)
    w, h = max(1, t.width >> level), max(1, t.height >> level)
    data = t.body[off:off + tex._mip_size(t.width, t.height, level, t.fmt)]
    out = bytearray(w * h * 4)
    if t.fmt in RGBA8:
        for i in range(w * h):
            b, g, r, a = data[4 * i:4 * i + 4]
            out[4 * i:4 * i + 4] = bytes((r, g, b, a))
        return w, h, bytes(out)
    if t.fmt not in BC1 | BC3 | BC5:
        raise FormatError("tex", f"format {t.fmt} cannot be decoded")
    p = 0
    for by in range(0, h, 4):
        for bx in range(0, w, 4):
            px = [None] * 16
            if t.fmt in BC5:
                chans = []
                for c in range(2):
                    lv = _alpha_levels(data[p], data[p + 1])
                    bits = int.from_bytes(data[p + 2:p + 8], "little")
                    chans.append([lv[(bits >> (3 * i)) & 7] for i in range(16)])
                    p += 8
                for i in range(16):
                    x, y = chans[0][i] / 127.5 - 1, chans[1][i] / 127.5 - 1
                    z = max(0.0, 1 - x * x - y * y) ** 0.5
                    px[i] = (chans[0][i], chans[1][i], int(z * 127.5 + 127.5), 255)
            else:
                alpha = None
                if t.fmt in BC3:
                    lv = _alpha_levels(data[p], data[p + 1])
                    bits = int.from_bytes(data[p + 2:p + 8], "little")
                    alpha = [lv[(bits >> (3 * i)) & 7] for i in range(16)]
                    p += 8
                c0, c1, idx = struct.unpack_from("<HHI", data, p)
                p += 8
                pal = _palette(c0, c1, c0 > c1 or t.fmt in BC3)
                for i in range(16):
                    c = pal[(idx >> (2 * i)) & 3]
                    px[i] = c if alpha is None else c[:3] + (alpha[i],)
            for i in range(16):
                x, y = bx + (i & 3), by + (i >> 2)
                if x < w and y < h:
                    o = (y * w + x) * 4
                    out[o:o + 4] = bytes(px[i])
    return w, h, bytes(out)


# -- encoding -----------------------------------------------------------------------------------
def _pack565(r: int, g: int, b: int) -> int:
    return ((r * 31 + 127) // 255) << 11 | ((g * 63 + 127) // 255) << 5 | ((b * 31 + 127) // 255)


def _block(px: bytes, w: int, h: int, bx: int, by: int) -> list:
    out = []
    for i in range(16):
        x, y = min(bx + (i & 3), w - 1), min(by + (i >> 2), h - 1)
        o = (y * w + x) * 4
        out.append(px[o:o + 4])
    return out


def _color_block(blk: list, transparent: bool = False) -> bytes:
    rs = [q[0] for q in blk]
    gs = [q[1] for q in blk]
    bs = [q[2] for q in blk]
    hi, lo = (max(rs), max(gs), max(bs)), (min(rs), min(gs), min(bs))
    c0, c1 = _pack565(*hi), _pack565(*lo)
    if transparent:                         # BC1 three colours + transparent black
        if c0 > c1:
            c0, c1, hi, lo = c1, c0, lo, hi
        idx = 0
        dr, dg, db = hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]
        dd = dr * dr + dg * dg + db * db or 1
        for i, q in enumerate(blk):
            if q[3] < 128:
                k = 3
            else:
                t = ((q[0] - lo[0]) * dr + (q[1] - lo[1]) * dg + (q[2] - lo[2]) * db) / dd
                k = 1 if t < 0.25 else 2 if t < 0.75 else 0          # c1 = lo, mid, c0 = hi
            idx |= k << (2 * i)
        return struct.pack("<HHI", c0, c1, idx)
    if c0 == c1:
        return struct.pack("<HHI", c0, c1, 0)
    if c0 < c1:
        c0, c1, hi, lo = c1, c0, lo, hi
    dr, dg, db = hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]
    dd = dr * dr + dg * dg + db * db or 1
    idx = 0
    for i, q in enumerate(blk):
        t = ((q[0] - lo[0]) * dr + (q[1] - lo[1]) * dg + (q[2] - lo[2]) * db) / dd   # 0 at lo, 1 at hi
        k = 1 if t < 1 / 6 else 3 if t < 0.5 else 2 if t < 5 / 6 else 0
        idx |= k << (2 * i)
    return struct.pack("<HHI", c0, c1, idx)


def _alpha_block(blk: list) -> bytes:
    al = [q[3] for q in blk]
    a0, a1 = max(al), min(al)
    if a0 == a1:
        return bytes((a0, a1)) + bytes(6)
    lv = _alpha_levels(a0, a1)
    bits = 0
    for i, a in enumerate(al):
        k = min(range(8), key=lambda j: abs(lv[j] - a))
        bits |= k << (3 * i)
    return bytes((a0, a1)) + bits.to_bytes(6, "little")


def _compress(w: int, h: int, px: bytes, fmt: int, cutout: bool = True) -> bytes:
    out = bytearray()
    bc3 = fmt in BC3
    for by in range(0, h, 4):
        for bx in range(0, w, 4):
            blk = _block(px, w, h, bx, by)
            if bc3:
                out += _alpha_block(blk) + _color_block(blk)
            else:
                out += _color_block(blk, transparent=cutout and any(q[3] < 128 for q in blk))
    return bytes(out)


def has_cutout(t: tex.Tex) -> bool:
    """A BC1 texture uses its 1-bit alpha: some texel of its largest mip is transparent."""
    if t.fmt not in BC1:
        return False
    off = _mip_offset(t, 0)
    for c0, c1, idx in struct.iter_unpack("<HHI", t.body[off:off + tex._mip_size(t.width, t.height, 0, t.fmt)]):
        if c0 <= c1 and idx & (idx >> 1) & 0x55555555:     # three-colour block with an index 3 texel
            return True
    return False


def _half(w: int, h: int, px: bytes) -> tuple[int, int, bytes]:
    nw, nh = max(1, w // 2), max(1, h // 2)
    out = bytearray(nw * nh * 4)
    for y in range(nh):
        y0, y1 = min(2 * y, h - 1), min(2 * y + 1, h - 1)
        for x in range(nw):
            x0, x1 = min(2 * x, w - 1), min(2 * x + 1, w - 1)
            a, b, c, d = (4 * (y0 * w + x0), 4 * (y0 * w + x1), 4 * (y1 * w + x0), 4 * (y1 * w + x1))
            o = 4 * (y * nw + x)
            for k in range(4):
                out[o + k] = (px[a + k] + px[b + k] + px[c + k] + px[d + k] + 2) >> 2
    return nw, nh, bytes(out)


def _pow2(n: int) -> bool:
    return n > 0 and n & (n - 1) == 0


def encode(w: int, h: int, rgba: bytes, template: tex.Tex, cutout: bool | None = None) -> bytes:
    """RGBA pixels as a texture in the template's revision, format id and attributes (a DDO template
    gives a DDO texture), with mips down to 2 px (as the game's own maps).  BC1 and BC3 templates only;
    sides must be powers of two up to 4096.

    BC1 keeps only a 1-bit alpha (``cutout``: pixels under half alpha become transparent black).  By
    default it follows the template: a BC1 map the game draws fully opaque stays opaque, so a picture
    saved with an erased or transparent area cannot punch holes into it."""
    if template.fmt not in BC1 | BC3:
        raise FormatError("tex", f"only BC1/BC3 textures can be written from pixels, not format {template.fmt}")
    if not (_pow2(w) and _pow2(h) and w <= MAX_SIDE and h <= MAX_SIDE):
        raise FormatError("tex", f"a texture's sides are powers of two up to {MAX_SIDE} (this is {w}x{h})")
    if len(rgba) != w * h * 4:
        raise FormatError("tex", "the pixel data does not match the size")
    if cutout is None:
        cutout = template.fmt in BC1 and has_cutout(template)
    mips = max(1, max(w, h).bit_length() - 1)
    levels, cw, ch, cur = [], w, h, bytes(rgba)
    for m in range(mips):
        levels.append(_compress(cw, ch, cur, template.fmt, cutout))
        if m + 1 < mips:
            cw, ch, cur = _half(cw, ch, cur)
    offs, off = [], 16 + 4 * mips
    for lv in levels:
        offs.append(off)
        off += len(lv)
    body = struct.pack("<%dI" % mips, *offs) + b"".join(levels)
    t = tex.Tex(template.attr1, template.version, mips, w, h, 1, template.fmt, template.attr3, body)
    out = tex.build(t)
    if tex._pixels_contiguous(tex.parse(out)) is None:          # the layout the game and to_dds expect
        raise FormatError("tex", "internal: encoded texture has a bad mip layout")
    return out


# -- PNG ---------------------------------------------------------------------------------------
_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def png(w: int, h: int, rgba: bytes) -> bytes:
    """An 8-bit RGBA PNG."""
    rows = bytearray()
    stride = w * 4
    for y in range(h):
        rows.append(0)
        rows += rgba[y * stride:(y + 1) * stride]
    return (_PNG_SIG + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(bytes(rows), 6)) + _chunk(b"IEND", b""))


def read_png(data: bytes, max_pixels: int = MAX_SIDE * MAX_SIDE) -> tuple[int, int, bytes]:
    """(width, height, RGBA bytes) of a non-interlaced PNG (grey, grey+alpha, RGB, RGBA, palette; 8/16 bit)."""
    if not data.startswith(_PNG_SIG):
        raise FormatError("png", "not a PNG")
    p, idat, plte, trns, hdr = 8, bytearray(), None, None, None
    while p + 8 <= len(data):
        n, kind = struct.unpack_from(">I4s", data, p)
        if p + 12 + n > len(data):
            raise FormatError("png", "a chunk runs past the end")
        body = data[p + 8:p + 8 + n]
        if kind == b"IHDR":
            hdr = struct.unpack(">IIBBBBB", body) if n == 13 else None
        elif kind == b"PLTE":
            plte = body
        elif kind == b"tRNS":
            trns = body
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
        p += 12 + n
    if hdr is None:
        raise FormatError("png", "no header")
    w, h, depth, ctype, comp, filt, interlace = hdr
    if not (0 < w <= MAX_SIDE and 0 < h <= MAX_SIDE):
        raise FormatError("png", f"sides are 1 to {MAX_SIDE} pixels")
    if w * h > max_pixels:
        raise FormatError("png", f"a {w}x{h} image is more than this import takes ({max_pixels} pixels)")
    if interlace or comp or filt:
        raise FormatError("png", "interlaced or unusual PNGs are not supported; save it again as a plain PNG")
    chans = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ctype)
    if chans is None or depth not in (8, 16) or (ctype == 3 and depth != 8):
        raise FormatError("png", "only 8- or 16-bit grey, RGB, RGBA and 8-bit palette PNGs are supported")
    if trns is not None and ctype in (0, 2) and len(trns) != 2 * chans:
        raise FormatError("png", "its transparent colour (tRNS) does not fit the image")
    bpp = chans * depth // 8
    stride = w * bpp
    if (stride + 1) * h > MAX_PNG:
        raise FormatError("png", f"a {w}x{h} image of this kind is too large to import")
    try:
        raw = zlib.decompressobj().decompress(bytes(idat), (stride + 1) * h + 1)
    except zlib.error as e:
        raise FormatError("png", f"the image data is damaged ({e})") from None
    if len(raw) != (stride + 1) * h:
        raise FormatError("png", "the image data does not match the image's size")
    img = bytearray(stride * h)
    prev = bytearray(stride)
    for y in range(h):
        ft = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        if ft == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ft == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ft == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 0xFF
        elif ft != 0:
            raise FormatError("png", f"unknown row filter {ft}")
        img[y * stride:(y + 1) * stride] = line
        prev = line
    n = w * h
    opaque = _keyed(img, n, chans, depth, trns) if trns is not None and ctype in (0, 2) else b"\xff" * n
    if depth == 16:                          # 16-bit samples: keep the high byte
        img = img[0::2]
    out = bytearray(n * 4)
    if ctype == 6:
        out[:] = img
    elif ctype == 2:
        out[0::4], out[1::4], out[2::4], out[3::4] = img[0::3], img[1::3], img[2::3], opaque
    elif ctype == 0:
        out[0::4] = out[1::4] = out[2::4] = img
        out[3::4] = opaque
    elif ctype == 4:
        out[0::4] = out[1::4] = out[2::4] = img[0::2]
        out[3::4] = img[1::2]
    else:
        if plte is None or 3 * max(img) + 3 > len(plte):
            raise FormatError("png", "a palette index is out of range")
        table = lambda b: bytes(b[:256]).ljust(256, b"\0")
        alpha = bytes(trns[:256]).ljust(256, b"\xff") if trns else b"\xff" * 256
        out[0::4], out[1::4], out[2::4] = (img.translate(table(plte[k::3])) for k in range(3))
        out[3::4] = img.translate(alpha)
    return w, h, bytes(out)


def _keyed(img: bytes, n: int, chans: int, depth: int, trns: bytes) -> bytes:
    """Alpha for a grey or RGB PNG's colour key (tRNS: one 16-bit value per channel): 0 where every sample
    of a pixel equals the key's, at full depth, else 255."""
    width = depth // 8
    same = None                              # one byte per pixel: 1 while its samples match the key
    for c in range(chans):
        v = int.from_bytes(trns[2 * c:2 * c + 2], "big")
        if v >> depth:
            return b"\xff" * n               # no sample of this depth can equal it
        for k, b in enumerate(v.to_bytes(width, "big")):
            hit = int.from_bytes(img[c * width + k::chans * width].translate(bytes(int(i == b) for i in range(256))),
                                 "little")
            same = hit if same is None else same & hit
    return same.to_bytes(n, "little").translate(bytes([255, 0]) + bytes(254))


def preview(t: tex.Tex, side: int = 256) -> tuple[bytes, int, int]:
    """A PNG of the largest mip no bigger than ``side`` (or the smallest there is): (png, width, height)."""
    level = 0
    while level + 1 < t.mip_count and max(t.width >> level, t.height >> level) > side:
        level += 1
    w, h, px = decode(t, level)
    return png(w, h, px), w, h
