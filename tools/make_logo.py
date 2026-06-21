"""Logo tooling for the ZHA Tools integration.

Builds the logo SVGs and renders the brand PNGs that ship with the integration:

* ``icons/logo.svg`` -- the light logo, with the "ZHA" / "TOOLS" wordmark baked
  into vector paths (extracted from a bold sans-serif TTF) so it renders
  identically regardless of which fonts a viewer has installed;
* ``icons/logo-dark.svg`` -- the dark-theme variant (a deeper gradient with a
  light edge ring), derived from the light logo;
* ``custom_components/zha_tools/brand/{,dark_}{icon,logo}{,@2x}.png`` -- the
  brand images, which Home Assistant 2026.3.0+ serves directly from the
  ``brand/`` subdirectory.

Run it with uv so the optional build dependencies are available::

    uv run --with resvg-py python tools/make_logo.py            # SVGs + PNGs
    uv run --with fonttools --with resvg-py \
        python tools/make_logo.py --wordmark                    # also rebuild the
                                                                 # wordmark in logo.svg

The wordmark is already baked into the committed ``icons/logo.svg``, so the font
is only needed with ``--wordmark``. The font is resolved from ``$ZHA_LOGO_FONT``,
then a few common system locations, then matplotlib's bundled DejaVuSans-Bold if
matplotlib is importable.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOGO_SVG = REPO_ROOT / "icons" / "logo.svg"
DARK_SVG = REPO_ROOT / "icons" / "logo-dark.svg"
BRAND_DIR = REPO_ROOT / "custom_components" / "zha_tools" / "brand"

# Badge gradient stops, shared by the wordmark builder and the dark derivation.
LIGHT_STOPS = ("#7C3AED", "#EC4899", "#F97316")
DARK_STOPS = ("#5B21B6", "#BE185D", "#C2410C")

# Sizes rendered for each SVG: (output basename, pixel size).
RENDER_SIZES = (("icon", 256), ("icon@2x", 512), ("logo", 512), ("logo@2x", 1024))


# --- font resolution --------------------------------------------------------


def _find_font() -> str:
    """Return the path to a bold sans-serif TTF, or raise if none is found."""
    env = os.environ.get("ZHA_LOGO_FONT")
    candidates = [env] if env else []
    candidates += [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    try:
        import matplotlib

        bundled = Path(matplotlib.get_data_path()) / "fonts/ttf/DejaVuSans-Bold.ttf"
        if bundled.is_file():
            return str(bundled)
    except ImportError:
        pass
    raise SystemExit(
        "No bold TTF font found. Set $ZHA_LOGO_FONT to a DejaVuSans-Bold.ttf "
        "(or similar) and re-run with --wordmark."
    )


# --- light logo (wordmark baked to paths) -----------------------------------


def build_wordmark() -> str:
    """Build the light ``logo.svg`` markup with the wordmark as vector paths."""
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.ttLib import TTFont

    font = TTFont(_find_font())
    upm = font["head"].unitsPerEm
    cap = getattr(font["OS/2"], "sCapHeight", 0) or 1493
    cmap = font.getBestCmap()
    hmtx = font["hmtx"]
    glyph_set = font.getGlyphSet()

    def word_group(text, font_size, cx, cy, ls_px, opacity=1.0):
        s = font_size / upm
        ls_units = ls_px / s
        x = 0.0
        parts = []
        for ch in text:
            gname = cmap[ord(ch)]
            pen = SVGPathPen(glyph_set)
            glyph_set[gname].draw(pen)
            d = pen.getCommands()
            if d:
                parts.append(f'<path transform="translate({x:.1f},0)" d="{d}"/>')
            x += hmtx[gname][0] + ls_units
        total = x - ls_units
        tx = cx - (total * s) / 2
        ty = cy + (cap * s) / 2
        op = f' fill-opacity="{opacity}"' if opacity != 1.0 else ""
        return (
            f'<g transform="translate({tx:.1f},{ty:.1f}) '
            f'scale({s:.5f},{-s:.5f})"{op}>{"".join(parts)}</g>'
        )

    zha = word_group("ZHA", 176, 256, 238, 8)
    tools = word_group("TOOLS", 56, 256, 350, 22, opacity=0.92)
    c0, c1, c2 = LIGHT_STOPS

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512" role="img" aria-label="ZHA Tools logo">
  <title>ZHA Tools</title>
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{c0}"/>
      <stop offset="0.5" stop-color="{c1}"/>
      <stop offset="1" stop-color="{c2}"/>
    </linearGradient>
    <linearGradient id="sheen" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#ffffff" stop-opacity="0.28"/>
      <stop offset="0.5" stop-color="#ffffff" stop-opacity="0"/>
    </linearGradient>
    <linearGradient id="mesh" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#22D3EE"/>
      <stop offset="1" stop-color="#A3E635"/>
    </linearGradient>
    <filter id="shadow" x="-20%" y="-20%" width="140%" height="140%">
      <feDropShadow dx="0" dy="5" stdDeviation="7" flood-color="#000000" flood-opacity="0.30"/>
    </filter>
  </defs>

  <rect x="16" y="16" width="480" height="480" rx="104" fill="url(#bg)"/>
  <rect x="16" y="16" width="480" height="480" rx="104" fill="url(#sheen)"/>

  <g stroke="url(#mesh)" stroke-width="7" fill="none" opacity="0.55" stroke-linejoin="round">
    <polygon points="256,86 403,171 403,341 256,426 109,341 109,171"/>
  </g>
  <g fill="url(#mesh)" opacity="0.7">
    <circle cx="256" cy="86" r="14"/>
    <circle cx="403" cy="171" r="14"/>
    <circle cx="403" cy="341" r="14"/>
    <circle cx="256" cy="426" r="14"/>
    <circle cx="109" cy="341" r="14"/>
    <circle cx="109" cy="171" r="14"/>
  </g>

  <g filter="url(#shadow)" fill="#ffffff">
    {zha}
    {tools}
  </g>
</svg>
"""


# --- dark variant (derived from the light logo) -----------------------------


def build_dark(light_svg: str) -> str:
    """Derive the dark-theme variant markup from the light logo markup."""
    dark = light_svg
    for light, deep in zip(LIGHT_STOPS, DARK_STOPS):
        dark = dark.replace(light, deep)

    sheen = '  <rect x="16" y="16" width="480" height="480" rx="104" fill="url(#sheen)"/>\n'
    if sheen not in dark:
        raise SystemExit("sheen rect anchor not found in logo.svg")
    ring = (
        '  <rect x="17.5" y="17.5" width="477" height="477" rx="102.5" '
        'fill="none" stroke="#ffffff" stroke-opacity="0.22" stroke-width="3"/>\n'
    )
    dark = dark.replace(sheen, sheen + ring)
    return dark.replace(
        'aria-label="ZHA Tools logo"', 'aria-label="ZHA Tools logo (dark)"'
    ).replace("<title>ZHA Tools</title>", "<title>ZHA Tools (dark)</title>")


# --- rendering --------------------------------------------------------------


def render_pngs(svg: str, prefix: str) -> None:
    """Render ``svg`` to the brand PNGs, names optionally given a ``prefix``."""
    import resvg_py

    BRAND_DIR.mkdir(parents=True, exist_ok=True)
    for name, size in RENDER_SIZES:
        png = resvg_py.svg_to_bytes(svg_string=svg, width=size, height=size)
        out = BRAND_DIR / f"{prefix}{name}.png"
        out.write_bytes(bytes(png))
        print(f"wrote {out.relative_to(REPO_ROOT)} ({size}x{size})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the ZHA Tools logo assets.")
    parser.add_argument(
        "--wordmark",
        action="store_true",
        help="rebuild the wordmark in icons/logo.svg from a font (needs fonttools)",
    )
    args = parser.parse_args()

    if args.wordmark:
        light = build_wordmark()
        LOGO_SVG.write_text(light, encoding="utf-8")
        print(f"wrote {LOGO_SVG.relative_to(REPO_ROOT)}")
    else:
        light = LOGO_SVG.read_text(encoding="utf-8")

    dark = build_dark(light)
    DARK_SVG.write_text(dark, encoding="utf-8")
    print(f"wrote {DARK_SVG.relative_to(REPO_ROOT)}")

    render_pngs(light, "")
    render_pngs(dark, "dark_")


if __name__ == "__main__":
    main()
