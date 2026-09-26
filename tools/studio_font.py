"""Make Studio's display font: Riftstone Blade cut down to the characters Studio shows in it.

The full bilingual face is about 3.5 MB (every JIS X 0213 character). Studio sets only its wordmark
and short headings in it, so this keeps printable ASCII, Latin-1, the usual typographic punctuation and
the Japanese characters inside the page's display-font elements (class "display", "brand", "padtitle",
"gtitle", "h1" or "jp"), and writes one small WOFF2 next to the page. Run it again after changing those
headings. Needs fontTools with Brotli (pip install fonttools brotli) -- a build step only; Studio itself
stays standard library.

    py -3 tools/studio_font.py --source C:\\path\\to\\RiftstoneBlade-Regular.ttf

The font is under the SIL Open Font License 1.1 (no Reserved Font Name), so a subset may keep its
name; src/riftstone/studio/fonts/OFL.txt travels with it.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "src" / "riftstone" / "studio" / "index.html"
OUT = ROOT / "src" / "riftstone" / "studio" / "fonts" / "riftstone-blade.woff2"
DISPLAY_CLASSES = ("display", "brand", "padtitle", "gtitle", "jp")
PUNCT = "\u2013\u2014\u2018\u2019\u201c\u201d\u2022\u2026\u00b7\u2192\u2190\u2039\u203a\u00d7\u30fb"


def display_text(html: str) -> str:
    """Text inside elements whose class list names a display-font class (nested tags ignored)."""
    out = []
    cls = "|".join(DISPLAY_CLASSES)
    for m in re.finditer(r'<(\w+)[^>]*class="[^"]*\b(?:%s)\b[^"]*"[^>]*>(.*?)</\1>' % cls, html, re.S):
        out.append(re.sub(r"<[^>]+>", "", m.group(2)))
    # strings the script puts into display elements, marked /*display*/"..."
    out += re.findall(r'/\*display\*/\s*"([^"]*)"', html)
    return "".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", required=True, help="RiftstoneBlade-Regular.ttf (the bilingual face)")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args(argv)
    try:
        from fontTools import subset
        from fontTools.ttLib import TTFont
    except ImportError:
        print("needs fontTools: py -3 -m pip install fonttools brotli", file=sys.stderr)
        return 2
    text = display_text(PAGE.read_text(encoding="utf-8"))
    chars = {chr(c) for c in range(0x20, 0x7F)} | {chr(c) for c in range(0xA0, 0x100)} | set(PUNCT)
    chars |= {c for c in text if ord(c) > 0x2FFF}
    font = TTFont(a.source)
    cmap = font.getBestCmap()
    missing = sorted(c for c in chars if ord(c) not in cmap and not c.isspace())
    opts = subset.Options()
    opts.flavor = "woff2"
    opts.layout_features = ["kern", "liga", "calt", "locl", "palt", "vert"]
    opts.name_IDs = ["*"]
    opts.name_languages = ["*"]
    opts.notdef_outline = True
    opts.hinting = False
    sub = subset.Subsetter(opts)
    sub.populate(unicodes=[ord(c) for c in chars if ord(c) in cmap])
    sub.subset(font)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    font.flavor = "woff2"
    font.save(out)
    ja = sorted(c for c in chars if ord(c) > 0x2FFF)
    print(f"{out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}: {out.stat().st_size:,} bytes, "
          f"{len(chars)} characters ({len(ja)} Japanese)" + (f"; not in the font: {''.join(missing)}" if missing else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
