"""Draws the pictures of the README that are not screenshots: the animated banner, the strip of key numbers
and the buttons (docs/readme/banner.svg, numbers.svg, btn-*.svg, go-*.svg).

    pip install fonttools brotli
    python scripts/build_readme_art.py

All lettering is drawn as outlines of the two typefaces of the viewer (web/fonts/), so the pictures look the same
on every system and need no font where they are shown. The star field is seeded: the output is the same every time.

The README names its pictures by the commit that holds them. After a change here, commit the pictures and put that
commit into the README (see the note at the top of README.md).
"""
import pathlib
import random
import re

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

REPO = pathlib.Path(__file__).resolve().parent.parent
OUT = REPO / "docs" / "readme"

VIOLET, GOLD, INK, PAPER, MUTED = "#b58afb", "#e9b452", "#15111e", "#efebe4", "#9a93a8"


class Face:
    """One weight of a typeface, as glyph outlines."""

    def __init__(self, file: str, weight: int, fallback: "Face | None" = None):
        font = instancer.instantiateVariableFont(TTFont(REPO / "web" / "fonts" / file), {"wght": weight})
        self.glyphs, self.cmap, self.upm = font.getGlyphSet(), font.getBestCmap(), font["head"].unitsPerEm
        self.fallback = fallback

    def _glyph(self, ch: str):
        if ord(ch) in self.cmap:
            return self, self.cmap[ord(ch)]
        if self.fallback and ord(ch) in self.fallback.cmap:
            return self.fallback, self.fallback.cmap[ord(ch)]
        raise KeyError(f"no glyph for {ch!r}")

    def width(self, text: str, size: float, tracking: float = 0.0) -> float:
        total = 0.0
        for ch in text:
            face, name = self._glyph(ch)
            total += face.glyphs[name].width * size / face.upm + tracking * size
        return total - tracking * size if text else 0.0

    def path(self, text: str, size: float, x: float, y: float, anchor: str = "start", tracking: float = 0.0) -> str:
        """The outline of a line of text with its baseline at y."""
        if anchor != "start":
            w = self.width(text, size, tracking)
            x -= w if anchor == "end" else w / 2
        out = []
        for ch in text:
            face, name = self._glyph(ch)
            k = size / face.upm
            pen = SVGPathPen(face.glyphs, ntos=lambda v: f"{v:.1f}".rstrip("0").rstrip("."))
            face.glyphs[name].draw(TransformPen(pen, (k, 0, 0, -k, x, y)))
            out.append(pen.getCommands())
            x += face.glyphs[name].width * k + tracking * size
        return "".join(out)


MONO = Face("GeistMono.woff2", 500)
MONO_BOLD = Face("GeistMono.woff2", 700)
SANS = Face("HankenGrotesk.woff2", 400, MONO)
SEMI = Face("HankenGrotesk.woff2", 600, MONO)
BOLD = Face("HankenGrotesk.woff2", 700, MONO_BOLD)


def text(face: Face, s: str, size: float, x: float, y: float, fill: str, anchor: str = "start", tracking: float = 0.0,
         extra: str = "") -> str:
    return f'<path d="{face.path(s, size, x, y, anchor, tracking)}" fill="{fill}"{extra}/>'


def mix(a, b, t):
    return "#%02x%02x%02x" % tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


# ---------------------------------------------------------------------------------------------------- the banner
def banner() -> str:
    W, H = 1280, 400
    rnd = random.Random(20261115)
    logo = re.search(r"<svg[^>]*>(.*)</svg>", (REPO / "web" / "logo.svg").read_text(), re.S).group(1)

    stars = []
    for _ in range(120):
        x, y = rnd.uniform(16, W - 16), rnd.uniform(14, H - 60)
        r = rnd.choice([0.7, 0.8, 0.9, 1.0, 1.1, 1.3, 1.6])
        col = rnd.choice(["#ffffff", PAPER, "#d9ccff", "#ffe6b8", "#cfe0ff"])
        dur, delay = rnd.uniform(2.6, 6.5), rnd.uniform(0, 6)
        if 250 < x < 1040 and 112 < y < 262 and rnd.random() < 0.8:
            continue                                                        # keep the name readable
        if (x < 440 and y < 88) or (150 < x < 1140 and y > 316) or (900 < x and 116 < y < 150):
            continue                                                        # and the small print
        stars.append(f'<circle class="s" cx="{x:.0f}" cy="{y:.0f}" r="{r}" fill="{col}" '
                     f'style="animation-duration:{dur:.1f}s;animation-delay:-{delay:.1f}s"/>')

    # a moving object: 15 detections on a straight path, one more with every frame, as the blind search links them
    n, step, period = 15, 0.34, 9.0
    x0, y0, x1, y1 = 912, 44, 1216, 98
    dots = [f'<circle class="d" cx="{x0 + (x1 - x0) * i / (n - 1):.1f}" cy="{y0 + (y1 - y0) * i / (n - 1):.1f}" r="2.6" '
            f'style="animation-delay:{i * step:.2f}s"/>' for i in range(n)]
    moving = (n - 1) * step / period * 100

    # the 102 colours of SPHEREx, swept in wavelength
    nb, bw, gap = 102, 6, 2
    bx0 = (W - (nb * (bw + gap) - gap)) / 2
    bx1 = bx0 + nb * (bw + gap) - gap
    v0, v1, g1 = (0x6a, 0x48, 0xe0), (0xb5, 0x8a, 0xfb), (0xe9, 0xb4, 0x52)
    bars = []
    for i in range(nb):
        t = i / (nb - 1)
        col = mix(v0, v1, t / 0.45) if t < 0.45 else mix(v1, g1, (t - 0.45) / 0.55)
        bars.append(f'<rect class="b" x="{bx0 + i * (bw + gap):.0f}" y="352" width="{bw}" height="12" rx="1.5" '
                    f'fill="{col}" style="animation-delay:{i * 0.045:.3f}s"/>')

    scale = 2.3
    lx = (W - 330 * scale) / 2 - 8
    small = dict(size=14.5, fill="#8f889d", tracking=0.06)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-label="Vanilla Sphere: watch any patch of the infrared sky change over time">
<title>Vanilla Sphere</title>
<defs>
<linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#0b0912"/><stop offset="1" stop-color="#14101f"/></linearGradient>
<radialGradient id="glow" cx="50%" cy="46%" r="46%"><stop offset="0" stop-color="{VIOLET}" stop-opacity=".22"/><stop offset="1" stop-color="{VIOLET}" stop-opacity="0"/></radialGradient>
<radialGradient id="rim" cx="50%" cy="100%" r="60%"><stop offset="0" stop-color="{GOLD}" stop-opacity=".16"/><stop offset="1" stop-color="{GOLD}" stop-opacity="0"/></radialGradient>
<clipPath id="card"><rect width="{W}" height="{H}" rx="22"/></clipPath>
</defs>
<style>
.s{{animation:tw 4s ease-in-out infinite}}
@keyframes tw{{0%,100%{{opacity:.25}}50%{{opacity:1}}}}
.d{{fill:{GOLD};opacity:0;animation:det {period}s linear infinite}}
@keyframes det{{0%{{opacity:0}}1.5%{{opacity:1}}74%{{opacity:1}}82%,100%{{opacity:0}}}}
.x{{animation:hop {period}s linear infinite}}
@keyframes hop{{0%{{transform:translate(0,0);opacity:1;animation-timing-function:steps({n - 1},end)}}{moving:.2f}%{{transform:translate({x1 - x0}px,{y1 - y0}px);opacity:1}}80%{{transform:translate({x1 - x0}px,{y1 - y0}px);opacity:1}}88%,100%{{transform:translate({x1 - x0}px,{y1 - y0}px);opacity:0}}}}
.t{{opacity:0;animation:lab {period}s linear infinite}}
@keyframes lab{{0%,{moving - 6:.2f}%{{opacity:0}}{moving:.2f}%,80%{{opacity:1}}88%,100%{{opacity:0}}}}
.b{{opacity:.3;animation:sw 5.4s ease-out infinite}}
@keyframes sw{{0%,100%{{opacity:.3}}6%{{opacity:1}}34%{{opacity:.3}}}}
.g{{animation:br 7s ease-in-out infinite}}
@keyframes br{{0%,100%{{opacity:.75}}50%{{opacity:1}}}}
@media (prefers-reduced-motion:reduce){{.s,.d,.x,.t,.b,.g{{animation:none}}.d,.t{{opacity:1}}.b{{opacity:.8}}.x{{transform:translate({x1 - x0}px,{y1 - y0}px)}}}}
</style>
<g clip-path="url(#card)">
<rect width="{W}" height="{H}" fill="url(#bg)"/>
<rect class="g" width="{W}" height="{H}" fill="url(#glow)"/>
<rect width="{W}" height="{H}" fill="url(#rim)"/>
{chr(10).join(stars)}
<line x1="{x0}" y1="{y0}" x2="{x1}" y2="{y1}" stroke="{GOLD}" stroke-opacity=".22" stroke-width="1" stroke-dasharray="2 5"/>
{chr(10).join(dots)}
<g class="x"><g transform="translate({x0} {y0})" fill="none" stroke="{PAPER}" stroke-width="1.3"><circle r="9"/><path d="M-16 0h-6M16 0h6M0 -16v-6M0 16v6"/></g></g>
{text(MONO, "3I/ATLAS · 99″/h · found blind", x=x1 + 22, y=y1 + 38, anchor="end", extra=' class="t"', **small)}
{text(MONO, "RA 16h 33m · DEC −16° 59′", x=40, y=48, **small)}
{text(MONO, "NASA SPHEREx · Level 2 · DR1 + QR3", x=40, y=72, extra=' fill-opacity=".7"', **small)}
<g transform="translate({lx:.1f} 112) scale({scale})">{logo}</g>
{chr(10).join(bars)}
{text(MONO, "0.75 µm", x=bx0 - 14, y=363, anchor="end", **small)}
{text(MONO, "5.0 µm", x=bx1 + 14, y=363, **small)}
{text(MONO, "102 COLOURS OF THE INFRARED SKY", x=W / 2, y=336, anchor="middle", extra=' fill-opacity=".8"', **small)}
<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="22" fill="none" stroke="{VIOLET}" stroke-opacity=".28"/>
</g>
</svg>
'''


# ------------------------------------------------------------------------------------------- the strip of numbers
def numbers() -> str:
    cells = [
        ("102", "colours", "0.75 to 5.0 µm, the whole sky", VIOLET),
        ("99″/h", "3I/ATLAS", "found by a blind search", GOLD),
        ("279", "sigma on Pluto", "by shift-and-stack", VIOLET),
        ("~10 s", "to a new view", "under 1 s the next time", GOLD),
        ("0", "tracks by chance", "in shuffled searches", VIOLET),
    ]
    W, H = 1000, 170
    cw = W / len(cells)
    parts = []
    for i, (num, name, note, col) in enumerate(cells):
        cx = cw * i + cw / 2
        if i:
            parts.append(f'<line x1="{cw * i:.0f}" y1="36" x2="{cw * i:.0f}" y2="{H - 36}" stroke="{PAPER}" stroke-opacity=".12"/>')
        parts.append(f'<g class="c" style="animation-delay:{i * 0.18:.2f}s">'
                     + text(BOLD, num, 42, cx, 78, col, "middle", -0.01)
                     + text(SEMI, name, 17, cx, 110, PAPER, "middle")
                     + text(SANS, note, 13.5, cx, 134, MUTED, "middle") + "</g>")
    label = ("102 colours from 0.75 to 5.0 µm; 3I/ATLAS found blind at 99 arcsec per hour; Pluto at 279 sigma by "
             "shift-and-stack; a new view in about 10 seconds; 0 tracks by chance")
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-label="{label}">
<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#0d0b14"/><stop offset="1" stop-color="#15111f"/></linearGradient></defs>
<style>
.c{{animation:in 1s ease-out both}}
@keyframes in{{from{{opacity:0;transform:translateY(8px)}}to{{opacity:1;transform:none}}}}
@media (prefers-reduced-motion:reduce){{.c{{animation:none}}}}
</style>
<rect width="{W}" height="{H}" rx="18" fill="url(#bg)"/>
<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="18" fill="none" stroke="{VIOLET}" stroke-opacity=".28"/>
{chr(10).join(parts)}
</svg>
'''


# ---------------------------------------------------------------------------------------------------- the buttons
ICONS = {  # on a 20 x 20 square; {c} is the colour
    "play": '<path d="M6 3.6v12.8L16.6 10z" fill="{c}"/>',
    "found": '<g fill="none" stroke="{c}" stroke-width="1.7" stroke-linecap="round"><circle cx="10" cy="10" r="4.6"/>'
             '<path d="M10 1.6v3M10 15.4v3M1.6 10h3M15.4 10h3"/></g><circle cx="10" cy="10" r="1.5" fill="{c}"/>',
    "api": '<path d="M7 5.2 2.4 10 7 14.8M13 5.2l4.6 4.8L13 14.8" fill="none" stroke="{c}" stroke-width="1.8" '
           'stroke-linecap="round" stroke-linejoin="round"/>',
    "docs": '<path d="M10 5.2C7.8 3.8 5.2 3.6 2.8 4.4v10.8c2.4-.8 5-.6 7.2.8 2.2-1.4 4.8-1.6 7.2-.8V4.4'
            'c-2.4-.8-5-.6-7.2.8zM10 5.2v10.8" fill="none" stroke="{c}" stroke-width="1.6" stroke-linejoin="round"/>',
    "science": '<circle cx="10" cy="10" r="2.3" fill="{c}"/><g fill="none" stroke="{c}" stroke-width="1.4">'
               '<ellipse cx="10" cy="10" rx="8.6" ry="3.6" transform="rotate(-35 10 10)"/>'
               '<ellipse cx="10" cy="10" rx="8.6" ry="3.6" transform="rotate(35 10 10)"/></g>',
}


def button(label: str, icon: str, kind: str) -> str:
    """A call to action, 44 high. kind: violet, gold or line."""
    H, size = 44, 15.5
    tw = BOLD.width(label, size, 0.005)
    W = round(18 + 20 + 9 + tw + 20)
    if kind == "line":
        ink, face = PAPER, SEMI
        body = (f'<rect x=".75" y=".75" width="{W - 1.5}" height="{H - 1.5}" rx="10.25" fill="{INK}" stroke="{VIOLET}" '
                f'stroke-opacity=".55" stroke-width="1.5"/>')
        icon_col, shine = VIOLET, ""
    else:
        a, b, ink = ("#c9abff", "#a77cf6", "#170f26") if kind == "violet" else ("#f4cf80", "#dfa439", "#1e1604")
        face, icon_col = BOLD, ink
        body = (f'<rect width="{W}" height="{H}" rx="11" fill="url(#f)"/>'
                f'<rect x=".75" y=".75" width="{W - 1.5}" height="{H - 1.5}" rx="10.25" fill="none" stroke="#fff" stroke-opacity=".28" stroke-width="1.5"/>')
        shine = (f'<g clip-path="url(#k)"><rect class="h" x="-60" y="-10" width="46" height="{H + 20}" fill="#fff" '
                 f'fill-opacity=".38" transform="skewX(-20)"/></g>')
    defs = "" if kind == "line" else (
        f'<defs><linearGradient id="f" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{a}"/>'
        f'<stop offset="1" stop-color="{b}"/></linearGradient><clipPath id="k"><rect width="{W}" height="{H}" rx="11"/></clipPath></defs>'
        f'<style>.h{{animation:sh 5s ease-in-out infinite}}@keyframes sh{{0%,55%{{transform:skewX(-20deg) translateX(0)}}'
        f'85%,100%{{transform:skewX(-20deg) translateX({W + 140}px)}}}}@media (prefers-reduced-motion:reduce){{.h{{animation:none}}}}</style>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-label="{label}">'
            f'<title>{label}</title>{defs}{body}{shine}'
            f'<g transform="translate(18 12)">{ICONS[icon].format(c=icon_col)}</g>'
            f'{text(face, label, size, 47, 27.5, ink, tracking=0.005)}</svg>\n')


def chip(name: str, kind: str, colour: str) -> str:
    """A link to one live view, 38 high: a beating dot, the object and what it shows."""
    H = 38
    nw, kw = SEMI.width(name, 14.5), SANS.width(kind, 13)
    xk = 30 + nw + 9
    xa = xk + kw + 10
    W = round(xa + 11 + 15)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-label="{name}, {kind}">'
            f'<title>{name}, {kind}</title>'
            f'<style>.r{{transform-origin:18px 19px;animation:p 2.6s ease-out infinite}}@keyframes p{{0%{{transform:scale(1);opacity:.7}}'
            f'70%,100%{{transform:scale(2.6);opacity:0}}}}@media (prefers-reduced-motion:reduce){{.r{{animation:none;opacity:0}}}}</style>'
            f'<rect x=".75" y=".75" width="{W - 1.5}" height="{H - 1.5}" rx="18.25" fill="{INK}" stroke="{colour}" stroke-opacity=".5" stroke-width="1.5"/>'
            f'<circle class="r" cx="18" cy="19" r="4" fill="{colour}"/><circle cx="18" cy="19" r="4" fill="{colour}"/>'
            f'{text(SEMI, name, 14.5, 30, 24, PAPER)}{text(SANS, kind, 13, xk, 24, MUTED)}'
            f'<path d="M{xa:.1f} 24l8-8m-6.5 0h6.5v6.5" fill="none" stroke="{colour}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>'
            f'</svg>\n')


FILES = {
    "banner.svg": banner,
    "numbers.svg": numbers,
    "btn-live.svg": lambda: button("Open the live site", "play", "violet"),
    "btn-found.svg": lambda: button("See what it found", "found", "gold"),
    "btn-api.svg": lambda: button("API explorer", "api", "line"),
    "btn-docs.svg": lambda: button("Documentation", "docs", "line"),
    "btn-science.svg": lambda: button("Science", "science", "line"),
    "go-3i-atlas.svg": lambda: chip("3I/ATLAS", "interstellar comet", GOLD),
    "go-pluto.svg": lambda: chip("Pluto", "found by stacking", VIOLET),
    "go-makemake.svg": lambda: chip("Makemake", "dwarf planet", GOLD),
    "go-barnard.svg": lambda: chip("Barnard's Star", "fastest star in the sky", VIOLET),
    "go-eps-indi-b.svg": lambda: chip("Epsilon Indi B", "brown dwarf spectrum", GOLD),
    "go-r-dra.svg": lambda: chip("R Draconis", "a star that faded", VIOLET),
    "go-quasar.svg": lambda: chip("SDSS J1528+3637", "quasar redshift", GOLD),
    "go-wise-0855.svg": lambda: chip("WISE 0855", "coldest brown dwarf", VIOLET),
}

if __name__ == "__main__":
    for name, draw in FILES.items():
        (OUT / name).write_text(draw())
        print(f"{name:20s} {(OUT / name).stat().st_size:7d} bytes")
