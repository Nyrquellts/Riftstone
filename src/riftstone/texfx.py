"""Texture presets: Frost, Lava, Abyssal and Weathered variants of a colour (albedo) texture.

A preset recolours the largest mip and re-encodes the texture in its own format (BC1 or BC3), size and
attributes with a fresh mip chain (``texcodec.encode``), so the result drops in where the original was:
the model's material keeps pointing at the same texture name. Alpha is kept as it was. Online textures
keep their own revision (0x9D).

The looks are colour transforms driven by tileable value noise (the pattern wraps at the edges, so a
tiling texture shows no seam); everything is deterministic for a given seed. They paint into the colour
map only: a real glow (Lava) would need the material's emissive parameters, which are not decoded
(docs/formats.md, MRL). How a preset looks on a model in game: UNKNOWN until seen.

    riftstone tex preset <file.tex | engine path> --preset lava [--strength 0.8] [--seed 7] [-o out.tex]
"""
from __future__ import annotations

from . import tex, texcodec
from .errors import RiftError

PRESETS = ("frost", "lava", "abyssal", "weathered")
DESCRIPTIONS = {
    "frost": ("Icy blue-white tint with rime speckles", "氷のような青白い色調と霜の斑点"),
    "lava": ("Dark basalt with glowing cracks (painted, not emissive)", "暗い玄武岩と光るひび割れ（発光ではなく描画）"),
    "abyssal": ("Deep purple darkness with faint bioluminescent veins", "深い紫の闇とかすかに光る筋"),
    "weathered": ("Faded colours, grime and worn edges", "色あせ、汚れ、擦り切れた縁"),
}


def _hash(i: int, j: int, seed: int) -> float:
    h = (i * 374761393 + j * 668265263 + seed * 2246822519) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((h ^ (h >> 16)) & 0xFFFFFF) / 0x1000000


def _noise_field(w: int, h: int, seed: int, cells: int = 8, octaves: int = 3) -> list[float]:
    """Tileable fractal value noise in 0..1, one value per pixel (row-major)."""
    out = [0.0] * (w * h)
    amp_total = 0.0
    amp = 0.5
    for o in range(octaves):
        g = cells << o
        lattice = [[_hash(i, j, seed + 97 * o) for i in range(g)] for j in range(g)]
        sx, sy = g / w, g / h
        for y in range(h):
            fy = y * sy
            j0 = int(fy) % g
            j1 = (j0 + 1) % g
            ty = fy - int(fy)
            ty = ty * ty * (3 - 2 * ty)
            row0, row1 = lattice[j0], lattice[j1]
            base = y * w
            for x in range(w):
                fx = x * sx
                i0 = int(fx) % g
                i1 = (i0 + 1) % g
                tx = fx - int(fx)
                tx = tx * tx * (3 - 2 * tx)
                a = row0[i0] + (row0[i1] - row0[i0]) * tx
                b = row1[i0] + (row1[i1] - row1[i0]) * tx
                out[base + x] += amp * (a + (b - a) * ty)
        amp_total += amp
        amp *= 0.5
    return [v / amp_total for v in out]


def _smooth(e0: float, e1: float, x: float) -> float:
    t = min(1.0, max(0.0, (x - e0) / (e1 - e0)))
    return t * t * (3 - 2 * t)


def _mix(a, b, t):
    return tuple(p + (q - p) * t for p, q in zip(a, b))


def _clamp(c):
    return tuple(min(1.0, max(0.0, v)) for v in c)


def _pixel(preset: str, c: tuple, n: float, s: float) -> tuple:
    lum = 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    if preset == "frost":
        base = lum * 0.8 + 0.25
        c = _mix(c, (0.78 * base, 0.90 * base, 1.0 * base), 0.6 * s)
        return _mix(c, (0.95, 0.98, 1.0), 0.5 * _smooth(0.62, 0.8, n) * s)
    if preset == "lava":
        k = 0.4 + 0.6 * lum
        c = _mix(c, (0.16 * k, 0.13 * k, 0.12 * k), 0.75 * s)
        crack = 1.0 - _smooth(0.0, 0.06, abs(n - 0.5))
        glow = _mix((0.9, 0.25, 0.02), (1.0, 0.75, 0.2), crack)
        return tuple(p + g * crack * s for p, g in zip(c, glow))
    if preset == "abyssal":
        k = 0.5 + 0.8 * lum
        c = _mix(c, (0.12 * k, 0.08 * k, 0.22 * k), 0.7 * s)
        vein = 1.0 - _smooth(0.0, 0.04, abs(n - 0.5))
        return tuple(p + v * vein * 0.6 * s for p, v in zip(c, (0.05, 0.45, 0.5)))
    if preset == "weathered":
        c = _mix(c, (lum, lum, lum), 0.45 * s)
        grime = 1.0 + (0.72 + 0.28 * n - 1.0) * s
        c = tuple(p * grime * t for p, t in zip(c, _mix((1.0, 1.0, 1.0), (1.0, 0.94, 0.86), s)))
        wear = _smooth(0.78, 0.9, n) * 0.5 * s
        return _mix(c, tuple(min(1.0, p * 1.35) for p in c), wear)
    raise RiftError(f"unknown preset {preset!r} (one of {', '.join(PRESETS)})")


def apply_rgba(w: int, h: int, rgba: bytes, preset: str, strength: float = 1.0, seed: int = 1) -> bytes:
    """The preset applied to RGBA pixels (alpha unchanged)."""
    if preset not in PRESETS:
        raise RiftError(f"unknown preset {preset!r} (one of {', '.join(PRESETS)})")
    if not 0.0 <= strength <= 1.0:
        raise RiftError("strength runs from 0 to 1")
    if len(rgba) != w * h * 4:
        raise RiftError("the pixel data does not match the size")
    noise = _noise_field(w, h, seed)
    out = bytearray(rgba)
    for i, n in enumerate(noise):
        o = 4 * i
        c = _clamp(_pixel(preset, (rgba[o] / 255, rgba[o + 1] / 255, rgba[o + 2] / 255), n, strength))
        out[o] = round(c[0] * 255)
        out[o + 1] = round(c[1] * 255)
        out[o + 2] = round(c[2] * 255)
    return bytes(out)


def apply(data: bytes, preset: str, strength: float = 1.0, seed: int = 1) -> bytes:
    """A .tex with the preset applied: same format, size, attributes and revision; new mips."""
    t = tex.parse(data)
    if t.is_cube:
        raise RiftError("cube maps have no preset")
    if t.fmt in texcodec.BC5:
        raise RiftError("this is a normal map; presets recolour colour (albedo) maps only")
    if not texcodec.writable(t.fmt):
        raise RiftError(f"{texcodec.codec(t.fmt)} textures cannot be written back from pixels")
    w, h, px = texcodec.decode(t, 0)
    out = tex.parse(texcodec.encode(w, h, apply_rgba(w, h, px, preset, strength, seed), t))
    out.version = t.version                       # Online keeps 0x9D
    return tex.build(out)
