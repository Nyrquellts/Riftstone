"""rTexture (``.tex``): the MT Framework texture, Dragon's Dogma revision 0x99.  The single most
common editable asset in the game -- 73,706 of them -- and the format every armour, monster, face
and UI reskin goes through.

Header (little-endian), proved byte-exact on every ``.tex`` in the game (`check_corpus --only tex`):

  u32 magic "TEX\\0"
  u32 word1  = version:12 (0x099) | attr1:20     (attr1 is 0x20000 for a 2D texture, 0x60000 for a cube)
  u32 word2  = mipCount:6 | width:13 | height:13
  u32 word3  = depth:8 (1 flat, 6 cube) | format:8 | attr3:16 (1)
  u32 offset[mipCount * depth]                    absolute file offsets, one per mip per face
  <pixel data>                                    mips largest-first, tightly packed

The pixel payload is opaque here: parse keeps everything after the three header words verbatim, so
``build`` reproduces the original bytes for every texture, cubemaps and all.  For the 11,199 flat
textures whose mips are contiguous (all but a single oddity, plus the 21 cubemaps), :func:`to_dds`
and :func:`dds_to_tex` convert to and from a standard ``.dds`` so the texture opens in Photoshop,
GIMP or Paint.NET and comes back byte-for-byte.

Format ids were measured against the game's own pixel sizes, not guessed:

  8 bytes / 4x4 block (BC1 family):   19, 20, 25
  16 bytes / 4x4 block (BC3/BC5):     24, 31 (normal maps), 37, 43, 47
  4 bytes / pixel (uncompressed):     40

DXGI/sRGB nuance the game encodes in the format id is not expressible in a legacy ``.dds``; the block
bytes are identical regardless, and :func:`dds_to_tex` restores the exact original format id from the
``template`` texture, so a round trip is exact.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from .errors import FormatError

MAGIC = 0x00584554  # "TEX\0"
VERSION = 0x099      # Dragon's Dogma texture revision
VERSION_DDO = 0x09D  # Dragon's Dogma Online: the same header layout and format ids (measured on its
                     # 27,142 distinct textures: offsets follow the table, sizes add up); attr1 differs
                     # (0x20002 / 0x21002 / 0x22002 / 0x60002) and is kept verbatim like every other field
VERSIONS = (VERSION, VERSION_DDO)
GAME_VERSION = {"ddda": VERSION, "ddo": VERSION_DDO}
TAG = "tex/0x99"
_U = struct.Struct("<I")
_HDR = struct.Struct("<IIII")

# format id -> bytes per 4x4 block (block-compressed formats)
_BLOCK = {19: 8, 20: 8, 25: 8, 24: 16, 31: 16, 37: 16, 43: 16, 47: 16}
# format id -> bytes per pixel (uncompressed formats)
_UNCOMP = {40: 4}
# format id -> the .dds four-character code we export it as (byte layout preserved either way)
_FOURCC = {19: b"DXT1", 20: b"DXT1", 25: b"DXT1", 24: b"DXT5", 37: b"DXT5",
           43: b"DXT5", 47: b"DXT5", 31: b"ATI2"}
# a .dds coming in maps back to one of these ids when no template texture is given (best effort)
_FMT_FROM_FOURCC = {b"DXT1": 20, b"DXT5": 24, b"DXT3": 24, b"ATI2": 31}


@dataclass
class Tex:
    attr1: int          # word1 >> 12: 0x20000 flat, 0x60000 cube
    version: int        # word1 & 0xFFF: 0x099
    mip_count: int
    width: int
    height: int
    depth: int          # 1 flat, 6 cube
    fmt: int            # format id
    attr3: int          # word3 >> 16
    body: bytes         # offset table + pixel data, opaque (kept for exact rebuild)

    @property
    def is_cube(self) -> bool:
        return self.depth != 1

    @property
    def block_bytes(self) -> int | None:
        return _BLOCK.get(self.fmt)


def parse(data: bytes) -> Tex:
    if len(data) < 16:
        raise FormatError("tex", "too short for a header")
    magic, w1, w2, w3 = _HDR.unpack_from(data, 0)
    if magic != MAGIC:
        raise FormatError("tex", "not a TEX\\0 texture")
    version = w1 & 0xFFF
    if version not in VERSIONS:
        raise FormatError("tex", f"revision 0x{version:x}; Dragon's Dogma is 0x099, Online 0x09d")
    mip = w2 & 0x3F
    width = (w2 >> 6) & 0x1FFF
    height = (w2 >> 19) & 0x1FFF
    depth = w3 & 0xFF
    fmt = (w3 >> 8) & 0xFF
    attr3 = w3 >> 16
    if mip == 0:
        raise FormatError("tex", "zero mip levels")
    if depth == 0:
        raise FormatError("tex", "zero depth")
    need = 16 + 4 * mip * depth
    if len(data) < need:
        raise FormatError("tex", "truncated offset table")
    return Tex(w1 >> 12, version, mip, width, height, depth, fmt, attr3, bytes(data[16:]))


def build(t: Tex) -> bytes:
    w1 = (t.attr1 << 12) | (t.version & 0xFFF)
    w2 = (t.mip_count & 0x3F) | ((t.width & 0x1FFF) << 6) | ((t.height & 0x1FFF) << 19)
    w3 = (t.depth & 0xFF) | ((t.fmt & 0xFF) << 8) | (t.attr3 << 16)
    return _HDR.pack(MAGIC, w1, w2, w3) + t.body


def _mip_size(width: int, height: int, level: int, fmt: int) -> int:
    w = max(1, width >> level)
    h = max(1, height >> level)
    if fmt in _BLOCK:
        return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * _BLOCK[fmt]
    return w * h * _UNCOMP[fmt]


def _pixels_contiguous(t: Tex) -> bytes | None:
    """The pixel blob if this is a flat texture whose mips are the standard tight, largest-first run
    right after the offset table; ``None`` if it is a cube, an unknown format, or laid out oddly."""
    if t.is_cube or (t.fmt not in _BLOCK and t.fmt not in _UNCOMP):
        return None
    table_end = 4 * t.mip_count  # body starts after the header words, so offsets are body-relative here
    offs = struct.unpack_from("<%dI" % t.mip_count, t.body, 0)
    exp = 16 + 4 * t.mip_count  # offsets are absolute from file start
    for m in range(t.mip_count):
        if offs[m] != exp:
            return None
        exp += _mip_size(t.width, t.height, m, t.fmt)
    if exp != 16 + len(t.body):
        return None
    return t.body[table_end:]


# --- .dds interchange -------------------------------------------------------

_DDS_MAGIC = 0x20534444          # "DDS "
_DDSD = 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000   # CAPS HEIGHT WIDTH PIXELFORMAT MIPMAPCOUNT
_DDSD_LINEARSIZE = 0x80000
_DDSD_PITCH = 0x8
_DDPF_ALPHAPIXELS = 0x1
_DDPF_FOURCC = 0x4
_DDPF_RGB = 0x40
_DDSCAPS = 0x8 | 0x1000 | 0x400000           # COMPLEX TEXTURE MIPMAP
_DDS_HEADER_LEN = 128


def to_dds(t: Tex) -> bytes:
    """A standard ``.dds`` for a flat texture, or raise if it cannot be expressed as one."""
    pixels = _pixels_contiguous(t)
    if pixels is None:
        why = ("a cube map" if t.is_cube else f"format id {t.fmt}"
               if t.fmt not in _BLOCK and t.fmt not in _UNCOMP else "an unusual mip layout")
        raise FormatError("tex", f"cannot export {why} to .dds; edit it as a raw .tex instead")
    hdr = bytearray(_DDS_HEADER_LEN)
    if t.fmt in _BLOCK:
        flags = _DDSD | _DDSD_LINEARSIZE
        linear = _mip_size(t.width, t.height, 0, t.fmt)
        pf_flags, fourcc, bitcount = _DDPF_FOURCC, _FOURCC[t.fmt], 0
        masks = (0, 0, 0, 0)
    else:
        flags = _DDSD | _DDSD_PITCH
        linear = t.width * _UNCOMP[t.fmt]
        pf_flags, fourcc, bitcount = _DDPF_RGB | _DDPF_ALPHAPIXELS, b"\0\0\0\0", 32
        masks = (0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000)  # A8R8G8B8 (BGRA byte order)
    struct.pack_into("<7I", hdr, 0, _DDS_MAGIC, 124, flags, t.height, t.width, linear, 0)
    struct.pack_into("<I", hdr, 28, t.mip_count)
    struct.pack_into("<2I", hdr, 76, 32, pf_flags)
    hdr[84:88] = fourcc
    struct.pack_into("<5I", hdr, 88, bitcount, *masks)
    struct.pack_into("<I", hdr, 108, _DDSCAPS)
    return bytes(hdr) + pixels


def _read_dds(dds: bytes) -> tuple[int, int, int, bytes, bytes]:
    """(width, height, mip_count, fourcc_or_empty, pixels)."""
    if len(dds) < _DDS_HEADER_LEN or _U.unpack_from(dds, 0)[0] != _DDS_MAGIC:
        raise FormatError("dds", "not a DDS file")
    if _U.unpack_from(dds, 4)[0] != 124:
        raise FormatError("dds", "unexpected header size")
    height, width = _U.unpack_from(dds, 12)[0], _U.unpack_from(dds, 16)[0]
    mip = _U.unpack_from(dds, 28)[0] or 1
    pf_flags = _U.unpack_from(dds, 80)[0]
    fourcc = dds[84:88] if pf_flags & _DDPF_FOURCC else b""
    if fourcc == b"DX10":
        raise FormatError("dds", "DX10 extended header is not supported; save as DXT1/DXT5/ATI2 or uncompressed")
    return width, height, mip, fourcc, dds[_DDS_HEADER_LEN:]


def attr1_for(attr1: int, version: int) -> int:
    """attr1 for a texture moving to revision ``version`` from the other game.  Measured on every distinct
    texture: DDDA uses only 0x20000 (flat, 11,199), 0x60000 (cube, 21) and 0x30000 (one volume); DDO sets
    bit 1 on all 27,142 of its own (0x20002, 0x60002, and 0x21002 / 0x22002 on 403 high-memory variants,
    bits DDDA never uses).  So the shape nibble is kept and bit 1 is set exactly for DDO.  A texture that
    stays in its own game keeps its attr1 verbatim; this is only for a change of revision."""
    return (attr1 & 0xF0000) | (0x2 if version == VERSION_DDO else 0)


def dds_to_tex(dds: bytes, template: Tex | None = None, version: int = VERSION) -> Tex:
    """Turn a ``.dds`` back into a ``.tex``.  With ``template`` (the original texture) the exact
    format id and header attributes are preserved, so a texture that was exported and re-imported
    unchanged rebuilds byte-for-byte.  Without one, a fresh flat texture is synthesised."""
    width, height, mip, fourcc, pixels = _read_dds(dds)
    # a Dragon's Dogma texture holds width/height in 13 bits and mipCount in 6; reject anything that
    # would not fit (and, as importantly, would make the mip loop below run for billions of levels).
    if not 1 <= width <= 0x1FFF or not 1 <= height <= 0x1FFF:
        raise FormatError("dds", f"{width}x{height} is out of range for a texture (1..8191)")
    if not 1 <= mip <= 0x3F:
        raise FormatError("dds", f"{mip} mip levels is out of range (1..63)")
    if template is not None:
        fmt = template.fmt
        attr1, version, depth, attr3 = template.attr1, template.version, template.depth, template.attr3
        if depth != 1:
            raise FormatError("dds", "the template is a cube map; import it as a raw .tex")
    else:
        if fourcc:
            fmt = _FMT_FROM_FOURCC.get(fourcc)
            if fmt is None:
                raise FormatError("dds", f"four-cc {fourcc!r} is not a Dragon's Dogma texture format")
        else:
            fmt = 40  # uncompressed
        if version not in VERSIONS:
            raise FormatError("dds", f"texture revision 0x{version:x} is not one Riftstone writes")
        # the most common flat-texture attr1 of each game (DDDA 0x20000, 11,199 of 11,221; DDO 0x20002,
        # 26,726 of 27,142)
        attr1, depth, attr3 = (0x20002 if version == VERSION_DDO else 0x20000), 1, 1
    if fmt not in _BLOCK and fmt not in _UNCOMP:
        raise FormatError("dds", f"format id {fmt} cannot be imported")
    expect = sum(_mip_size(width, height, m, fmt) for m in range(mip))
    if len(pixels) < expect:
        raise FormatError("dds", f"pixel data is {len(pixels)} bytes, need {expect} for "
                          f"{width}x{height} with {mip} mips")
    pixels = pixels[:expect]
    table_start = 16 + 4 * mip
    offs, off = [], table_start
    for m in range(mip):
        offs.append(off)
        off += _mip_size(width, height, m, fmt)
    body = struct.pack("<%dI" % mip, *offs) + pixels
    return Tex(attr1, version, mip, width, height, depth, fmt, attr3, body)


def info(t: Tex) -> str:
    kind = "cube map" if t.is_cube else "texture"
    fam = (f"BC {t.block_bytes}-byte block" if t.block_bytes
           else "uncompressed" if t.fmt in _UNCOMP else "unknown")
    line = (f"{t.width}x{t.height} {kind}, {t.mip_count} mip level(s), "
            f"format {t.fmt} ({fam}), revision 0x{t.version:x}")
    if not t.is_cube and (t.fmt in _BLOCK or t.fmt in _UNCOMP) and _pixels_contiguous(t) is not None:
        line += "  [exports to .dds]"
    return line
