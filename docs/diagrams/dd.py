"""Drawing primitives for the documentation diagrams.

The diagrams follow the diagram-design skill
(https://github.com/cathrynlavery/diagram-design): self-contained HTML with
inline SVG, a 4 px layout grid, orthogonal connectors with rounded corners,
masked arrow labels, one accent colour on at most two elements, and a legend
strip at the bottom. The skin is the Vanilla Sphere palette in a light and a
dark variant.

Each diagram is described once in build.py with role tokens such as {ink} or
{ink@0.10}; render() resolves them against a skin.
"""

from __future__ import annotations

import html
import math
import re

SKINS = {
    "light": {
        "paper": "#f6f3ef", "paper2": "#ece9e4", "ink": "#1b1813", "muted": "#524c45",
        "soft": "#625d55", "accent": "#bb4100", "link": "#005a9d", "backend": "#ffffff",
        "tint": 0.08, "frame": 0.02,
    },
    "dark": {
        "paper": "#0d0b08", "paper2": "#15120e", "ink": "#efebe4", "muted": "#afaaa3",
        "soft": "#908b84", "accent": "#f97c3d", "link": "#6db0f4", "backend": "#1b1814",
        "tint": 0.10, "frame": 0.04,
    },
}

FONTS = {
    "sans": "'Hanken Grotesk', system-ui, sans-serif",
    "mono": "'Geist Mono', ui-monospace, monospace",
    "serif": "'Instrument Serif', serif",
}
FONT_LINK = ("https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1"
             "&family=Hanken+Grotesk:wght@400;500;600&family=Geist+Mono:wght@400;500;600&display=swap")

SANS = 0.60   # width budget per character, in em
MONO = 0.62

# fill, stroke, tag stroke, tag text, dash
KINDS = {
    "focal":    ("{accent@tint}", "{accent}", "{accent@0.50}", "{accent}", None),
    "backend":  ("{backend}", "{ink}", "{ink@0.40}", "{ink}", None),
    "store":    ("{ink@0.05}", "{muted}", "{muted@0.50}", "{muted}", None),
    "external": ("{ink@0.03}", "{ink@0.30}", "{ink@0.22}", "{soft}", None),
    "input":    ("{muted@0.10}", "{soft}", "{soft@0.40}", "{soft}", None),
    "optional": ("{ink@0.02}", "{ink@0.20}", "{ink@0.20}", "{soft}", "4,3"),
}
KIND_NAMES = {
    "focal": "Focal", "backend": "Process", "store": "Store", "external": "External service",
    "input": "Client or input", "optional": "Deferred step",
}
EDGES = {  # stroke, marker id, width
    "muted": ("{muted}", "arrow", 1.2),
    "accent": ("{accent}", "arrow-accent", 1.4),
    "link": ("{link}", "arrow-link", 1.2),
}


def esc(text: str) -> str:
    return html.escape(text, quote=False)


def up4(value: float) -> int:
    return int(math.ceil(value / 4.0) * 4)


def sans_w(text: str, size: float = 12) -> float:
    return len(text) * size * SANS


def mono_w(text: str, size: float = 9, spacing: float = 0.0) -> float:
    return len(text) * size * (MONO + spacing)


class Diagram:
    def __init__(self, slug: str, title: str, desc: str, eyebrow: str, width: int, height: int):
        assert width % 4 == 0 and height % 4 == 0
        self.slug, self.title, self.desc, self.eyebrow = slug, title, desc, eyebrow
        self.w, self.h = width, height
        self.zones: list[str] = []
        self.edges: list[str] = []
        self.labels: list[str] = []
        self.nodes: list[str] = []
        self.top: list[str] = []
        self.extra_defs: list[str] = []

    # ------------------------------------------------------------------ nodes
    def node(self, x, y, w, h, name, sub=None, tag=None, kind="backend", sub2=None,
             rx=6, name_size=12, badge=None):
        for v in (x, y, w, h):
            assert v % 4 == 0, (name, v)
        fill, stroke, tag_stroke, tag_text, dash = KINDS[kind]
        need = max(sans_w(name, name_size), mono_w(sub or ""), mono_w(sub2 or "")) + 16
        assert need <= w + 0.5, f"{self.slug}: '{name}' needs {need:.0f} px, box is {w}"
        cx = x + w / 2
        out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{{paper}}"/>']
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
                   f'stroke="{stroke}" stroke-width="1"{dash_attr}/>')
        if tag:
            tw = max(24, up4(mono_w(tag, 7, 0.08) + 10))
            out.append(f'<rect x="{x + 8}" y="{y + 8}" width="{tw}" height="12" rx="2" fill="transparent" '
                       f'stroke="{tag_stroke}" stroke-width="0.8"/>')
            out.append(f'<text x="{x + 8 + tw / 2}" y="{y + 17}" fill="{tag_text}" font-size="7" '
                       f'font-family="{{mono}}" text-anchor="middle" letter-spacing="0.08em">{esc(tag)}</text>')
        if badge:
            bw = max(24, up4(mono_w(badge, 8, 0.08) + 10))
            out.append(f'<rect x="{x + w - 8 - bw}" y="{y + 8}" width="{bw}" height="12" rx="2" '
                       f'fill="{{paper}}" stroke="{tag_stroke}" stroke-width="0.8"/>')
            out.append(f'<text x="{x + w - 8 - bw / 2}" y="{y + 17}" fill="{tag_text}" font-size="8" '
                       f'font-family="{{mono}}" text-anchor="middle" letter-spacing="0.08em">{esc(badge)}</text>')
        c = y + h / 2 + (6 if (tag or badge) else 0)
        lines = 1 + bool(sub) + bool(sub2)
        base = {1: c + 4, 2: c - 2, 3: c - 8}[lines]
        out.append(f'<text x="{cx}" y="{base}" fill="{{ink}}" font-size="{name_size}" font-weight="600" '
                   f'font-family="{{sans}}" text-anchor="middle">{esc(name)}</text>')
        if sub:
            out.append(f'<text x="{cx}" y="{base + 16}" fill="{{muted}}" font-size="9" '
                       f'font-family="{{mono}}" text-anchor="middle">{esc(sub)}</text>')
        if sub2:
            out.append(f'<text x="{cx}" y="{base + 29}" fill="{{muted}}" font-size="9" '
                       f'font-family="{{mono}}" text-anchor="middle">{esc(sub2)}</text>')
        self.nodes.append("\n".join(out))

    def diamond(self, cx, cy, half_w, half_h, text, sub=None):
        pts = f"{cx},{cy - half_h} {cx + half_w},{cy} {cx},{cy + half_h} {cx - half_w},{cy}"
        out = [f'<polygon points="{pts}" fill="{{paper}}"/>',
               f'<polygon points="{pts}" fill="{{backend}}" stroke="{{ink}}" stroke-width="1"/>',
               f'<text x="{cx}" y="{cy + (0 if sub else 4)}" fill="{{ink}}" font-size="12" font-weight="600" '
               f'font-family="{{sans}}" text-anchor="middle">{esc(text)}</text>']
        if sub:
            out.append(f'<text x="{cx}" y="{cy + 14}" fill="{{muted}}" font-size="9" font-family="{{mono}}" '
                       f'text-anchor="middle">{esc(sub)}</text>')
        self.nodes.append("\n".join(out))

    def dot(self, cx, cy, r=6, ring=False):
        if ring:
            self.nodes.append(f'<circle cx="{cx}" cy="{cy}" r="8" fill="{{paper}}" stroke="{{ink}}" stroke-width="1"/>'
                              f'<circle cx="{cx}" cy="{cy}" r="5" fill="{{ink}}"/>')
        else:
            self.nodes.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{{ink}}"/>')

    # ------------------------------------------------------------------ zones
    def zone(self, x, y, w, h, label, dashed=False, label_dx=12):
        for v in (x, y, w, h):
            assert v % 4 == 0, (label, v)
        lw = up4(mono_w(label, 7, 0.14) + 12)
        dash = ' stroke-dasharray="4,4"' if dashed else ""
        stroke = "{ink@0.20}" if dashed else "{ink@0.10}"
        self.zones.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{{ink@0.02}}" '
            f'stroke="{stroke}" stroke-width="0.8"{dash}/>\n'
            f'<rect x="{x + label_dx}" y="{y + 4}" width="{lw}" height="12" rx="2" fill="{{paper}}"/>\n'
            f'<text x="{x + label_dx + lw / 2}" y="{y + 13}" fill="{{soft}}" font-size="7" font-family="{{mono}}" '
            f'text-anchor="middle" letter-spacing="0.14em">{esc(label)}</text>')

    # ------------------------------------------------------------- connectors
    def edge(self, points, kind="muted", dashed=False, marker=True, r=8, hops=()):
        """Orthogonal polyline with rounded corners. hops: points on a segment to bridge."""
        stroke, mid, width = EDGES[kind]
        for (x1, y1), (x2, y2) in zip(points, points[1:]):
            assert x1 == x2 or y1 == y2, f"{self.slug}: diagonal segment {(x1, y1)} -> {(x2, y2)}"
        attrs = f'fill="none" stroke="{stroke}" stroke-width="{1 if dashed else width}"'
        if dashed:
            attrs += f' stroke-dasharray="{dashed if isinstance(dashed, str) else "5,4"}"'
        if marker:
            attrs += f' marker-end="url(#{mid})"'
        if len(points) == 2 and not hops:
            (x1, y1), (x2, y2) = points
            self.edges.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" {attrs}/>')
            return
        d = [f"M {points[0][0]},{points[0][1]}"]
        hops = list(hops)
        for i in range(1, len(points)):
            px, py = points[i - 1]
            x, y = points[i]
            last = i == len(points) - 1
            # end of this segment, shortened by r when a corner follows
            ex, ey = x, y
            if not last:
                if px == x:
                    ey = y - r if y > py else y + r
                else:
                    ex = x - r if x > px else x + r
            # bridge any hop on this segment
            for hx, hy in hops:
                if px == x == hx and min(py, y) < hy < max(py, y):
                    s = 1 if y > py else -1
                    d.append(f"V {hy - 8 * s} a 8,8 0 0,{0 if s > 0 else 1} 0,{16 * s}")
                elif py == y == hy and min(px, x) < hx < max(px, x):
                    s = 1 if x > px else -1
                    d.append(f"H {hx - 8 * s} a 8,8 0 0,{1 if s > 0 else 0} {16 * s},0")
            d.append(f"V {ey}" if px == x else f"H {ex}")
            if not last:
                nx, ny = points[i + 1]
                if px == x:   # vertical then horizontal
                    d.append(f"Q {x},{y} {x + (r if nx > x else -r)},{y}")
                else:
                    d.append(f"Q {x},{y} {x},{y + (r if ny > y else -r)}")
        self.edges.append(f'<path d="{" ".join(d)}" {attrs}/>')

    def label(self, cx, top, text, kind="muted", anchor="middle"):
        """Masked arrow label; top is the top of the 12 px mask."""
        w = up4(mono_w(text, 8, 0.06) + 8)
        color = {"muted": "{muted}", "accent": "{accent}", "link": "{link}", "soft": "{soft}"}[kind]
        if anchor == "start":
            x = cx
        elif anchor == "end":
            x = cx - w
        else:
            x = round(cx - w / 2)
        self.labels.append(
            f'<rect x="{x}" y="{top}" width="{w}" height="12" rx="2" fill="{{paper}}"/>\n'
            f'<text x="{x + w / 2}" y="{top + 9}" fill="{color}" font-size="8" font-family="{{mono}}" '
            f'text-anchor="middle" letter-spacing="0.06em">{esc(text)}</text>')
        return w

    def hlabel(self, x1, x2, y, text, kind="muted", below=False):
        """Label centred on a horizontal segment, 8 px clear of the stroke."""
        self.label((x1 + x2) / 2, y + 8 if below else y - 20, text, kind)

    def vlabel(self, x, y1, y2, text, kind="muted", side="right"):
        """Label beside a vertical segment, 8 px clear of the stroke."""
        top = round((y1 + y2) / 2 - 6)
        if side == "right":
            self.label(x + 8, top, text, kind, anchor="start")
        else:
            self.label(x - 8, top, text, kind, anchor="end")

    # ----------------------------------------------------------------- legend
    def legend(self, items, y=None):
        """items: node kinds, edge kinds ('muted', 'link', 'accent', 'dashed') or (kind, text)."""
        y = y if y is not None else self.h - 60
        out = [f'<line x1="40" y1="{y}" x2="{self.w - 40}" y2="{y}" stroke="{{ink@0.10}}" stroke-width="0.8"/>',
               f'<text x="40" y="{y + 16}" fill="{{muted}}" font-size="8" font-family="{{mono}}" '
               f'letter-spacing="0.18em">LEGEND</text>']
        x = 40
        ky = y + 32
        edge_names = {"muted": "Internal call", "link": "HTTP request", "accent": "Primary path",
                      "dashed": "Return or deferred"}
        for item in items:
            kind, text = item if isinstance(item, tuple) else (item, None)
            if kind in KINDS:
                fill, stroke, _, _, dash = KINDS[kind]
                text = text or KIND_NAMES[kind]
                dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
                out.append(f'<rect x="{x}" y="{ky}" width="14" height="10" rx="2" fill="{fill}" stroke="{stroke}" '
                           f'stroke-width="1"{dash_attr}/>')
                tx = x + 20
            else:
                text = text or edge_names[kind]
                base = "muted" if kind == "dashed" else kind
                stroke, mid, width = EDGES[base]
                dash_attr = ' stroke-dasharray="5,4"' if kind == "dashed" else ""
                out.append(f'<line x1="{x}" y1="{ky + 6}" x2="{x + 28}" y2="{ky + 6}" stroke="{stroke}" '
                           f'stroke-width="{width}"{dash_attr} marker-end="url(#{mid})"/>')
                tx = x + 36
            out.append(f'<text x="{tx}" y="{ky + 8}" fill="{{muted}}" font-size="8.5" '
                       f'font-family="{{sans}}">{esc(text)}</text>')
            x = up4(tx + len(text) * 8.5 * 0.56 + 28)
        assert x <= self.w - 12, f"{self.slug}: legend too wide ({x})"
        self.top.append("\n".join(out))

    # --------------------------------------------------------------- sequence
    def actor(self, cx, y, w, name, sub=None, kind="backend", tag=None, h=56):
        self.node(int(cx - w / 2), y, w, h, name, sub, tag, kind)

    def lifeline(self, cx, y1, y2):
        self.zones.append(f'<line x1="{cx}" y1="{y1}" x2="{cx}" y2="{y2}" stroke="{{ink@0.20}}" '
                          f'stroke-width="1" stroke-dasharray="3,3"/>')

    def activation(self, cx, y1, y2):
        self.edges.insert(0, f'<rect x="{cx - 4}" y="{y1}" width="8" height="{y2 - y1}" fill="{{paper}}"/>'
                             f'<rect x="{cx - 4}" y="{y1}" width="8" height="{y2 - y1}" fill="{{ink@0.06}}" '
                             f'stroke="{{muted}}" stroke-width="0.8"/>')

    def msg(self, x1, x2, y, text, kind="muted", ret=False, label_cx=None):
        """Message between lifelines at x1 -> x2; the ends stop at the activation bars."""
        s = 1 if x2 > x1 else -1
        self.edge([(x1 + 4 * s, y), (x2 - 4 * s, y)], kind, dashed=ret)
        self.label(label_cx if label_cx is not None else (x1 + x2) / 2, y - 20, text, kind)

    def self_msg(self, cx, y, text, kind="muted"):
        self.edge([(cx + 4, y), (cx + 36, y), (cx + 36, y + 24), (cx + 4, y + 24)], kind, r=6)
        self.label(cx + 44, y + 6, text, kind, anchor="start")

    def frame(self, x, y, w, h, op, guard=None, guard_x=None):
        """Combined fragment. guard_x is where the guard text starts (clear of lifelines)."""
        self.zones.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="4" fill="{{ink@frame}}" '
            f'stroke="{{ink@0.22}}" stroke-width="1"/>\n'
            f'<rect x="{x}" y="{y}" width="40" height="16" rx="2" fill="{{paper}}" stroke="{{ink@0.22}}" stroke-width="1"/>\n'
            f'<text x="{x + 20}" y="{y + 12}" fill="{{muted}}" font-size="8" font-family="{{mono}}" '
            f'text-anchor="middle" letter-spacing="0.12em">{esc(op)}</text>')
        if guard:
            self.guard(guard_x if guard_x is not None else x + 12, y + 32, guard)

    def guard(self, x, y, text):
        self.zones.append(f'<text x="{x}" y="{y}" fill="{{muted}}" font-size="8" font-family="{{mono}}" '
                          f'letter-spacing="0.04em">{esc(text)}</text>')

    def divider(self, x, y, w):
        self.zones.append(f'<line x1="{x + 8}" y1="{y}" x2="{x + w - 8}" y2="{y}" stroke="{{ink@0.20}}" '
                          f'stroke-width="1" stroke-dasharray="4,3"/>')

    # --------------------------------------------------------------------- ER
    def entity(self, x, y, w, name, fields, focal=False, tag="ENTITY"):
        """fields: list of strings; a leading '#' marks the key, '>' a foreign key, '+' a muted note."""
        h = 40 + 12 + len(fields) * 16 + 4
        h = up4(h)
        stroke = "{accent}" if focal else "{ink}"
        fill = "{accent@tint}" if focal else "{backend}"
        head = "{accent@0.06}" if focal else "{ink@0.04}"
        out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{{paper}}"/>',
               f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{fill}" stroke="{stroke}" stroke-width="1"/>',
               f'<path d="M {x},{y + 40} V {y + 6} Q {x},{y} {x + 6},{y} H {x + w - 6} Q {x + w},{y} {x + w},{y + 6} V {y + 40} Z" fill="{head}"/>',
               f'<line x1="{x}" y1="{y + 40}" x2="{x + w}" y2="{y + 40}" stroke="{{ink@0.22}}" stroke-width="1"/>',
               f'<text x="{x + 16}" y="{y + 16}" fill="{"{accent}" if focal else "{muted}"}" font-size="8" '
               f'font-family="{{mono}}" letter-spacing="0.14em">{esc(tag)}</text>',
               f'<text x="{x + 16}" y="{y + 32}" fill="{{ink}}" font-size="14" font-weight="600" '
               f'font-family="{{sans}}">{esc(name)}</text>']
        fy = y + 60
        for field in fields:
            color, text = "{ink}", field
            if field.startswith("+"):
                color, text = "{soft}", field[1:].strip()
            elif field.startswith(">"):
                text = "→ " + field[1:].strip()
            assert mono_w(text, 10) + 32 <= w, f"{self.slug}: field '{text}' too wide"
            out.append(f'<text x="{x + 16}" y="{fy}" fill="{color}" font-size="10" font-family="{{mono}}">{esc(text)}</text>')
            fy += 16
        self.nodes.append("\n".join(out))
        return h

    def card(self, cx, cy, text):
        """Cardinality chip centred at (cx, cy)."""
        w = up4(mono_w(text, 10) + 6)
        self.labels.append(
            f'<rect x="{round(cx - w / 2)}" y="{cy - 6}" width="{w}" height="12" rx="2" fill="{{paper}}"/>'
            f'<text x="{cx}" y="{cy + 4}" fill="{{muted}}" font-size="10" font-weight="600" '
            f'font-family="{{mono}}" text-anchor="middle">{esc(text)}</text>')

    # ----------------------------------------------------------------- layers
    def band(self, x, y, w, h, index, name, note, focal=False):
        fill = "{accent@tint}" if focal else "{backend}"
        stroke = "{accent}" if focal else "{ink@0.30}"
        self.nodes.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{{paper}}"/>\n'
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}" stroke="{stroke}" stroke-width="1"/>\n'
            f'<text x="{x + 24}" y="{y + h / 2 + 3}" fill="{"{accent}" if focal else "{soft}"}" font-size="8" '
            f'font-family="{{mono}}" letter-spacing="0.14em">{esc(index)}</text>\n'
            f'<text x="{x + 88}" y="{y + h / 2 + 5}" fill="{{ink}}" font-size="14" font-weight="600" '
            f'font-family="{{sans}}">{esc(name)}</text>\n'
            f'<text x="{x + w - 24}" y="{y + h / 2 + 3}" fill="{{muted}}" font-size="9" font-family="{{mono}}" '
            f'text-anchor="end">{esc(note)}</text>')

    def text(self, x, y, text, role="eyebrow", anchor="start", color="{soft}"):
        if role == "eyebrow":
            self.top.append(f'<text x="{x}" y="{y}" fill="{color}" font-size="8" font-family="{{mono}}" '
                            f'text-anchor="{anchor}" letter-spacing="0.14em">{esc(text)}</text>')
        elif role == "callout":
            self.top.append(f'<text x="{x}" y="{y}" fill="{color}" font-size="14" font-style="italic" '
                            f'font-family="{{serif}}" text-anchor="{anchor}">{esc(text)}</text>')
        else:
            self.top.append(f'<text x="{x}" y="{y}" fill="{color}" font-size="9" font-family="{{mono}}" '
                            f'text-anchor="{anchor}">{esc(text)}</text>')

    def raw(self, layer, svg):
        getattr(self, layer).append(svg)

    # ----------------------------------------------------------------- render
    def render(self, skin_name: str) -> str:
        skin = SKINS[skin_name]
        slug = self.slug if skin_name == "light" else f"{self.slug}-dark"

        def rgba(hex_color, alpha):
            r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
            return f"rgba({r},{g},{b},{alpha})"

        def resolve(match):
            token = match.group(1)
            if token in FONTS:
                return FONTS[token]
            if "@" in token:
                name, alpha = token.split("@")
                alpha = skin[alpha] if alpha in skin else float(alpha)
                return rgba(skin[name], f"{alpha:.2f}")
            return skin[token]

        body = "\n\n".join(
            f"        <!-- {name} -->\n" + "\n".join("        " + line for part in parts for line in part.split("\n"))
            for name, parts in (("Zones and lifelines", self.zones), ("Connectors (behind the nodes)", self.edges),
                                ("Connector labels", self.labels), ("Nodes", self.nodes), ("Legend and notes", self.top))
            if parts)
        defs = "\n".join(
            f'          <marker id="{mid}" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">'
            f'<polygon points="0 0, 8 3, 0 6" fill="{color}"/></marker>'
            for mid, color in (("arrow", "{muted}"), ("arrow-accent", "{accent}"), ("arrow-link", "{link}")))
        defs += "".join("\n          " + d for d in self.extra_defs)
        page = TEMPLATE.format(
            title=esc(self.title), desc=esc(self.desc), eyebrow=esc(self.eyebrow), slug=slug,
            w=self.w, h=self.h, defs=defs, body=body, font_link=FONT_LINK.replace("&", "&amp;"),
            scheme=skin_name)
        return re.sub(r"\{([a-z0-9]+(?:@[a-z0-9.]+)?)\}", resolve, page)


TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} · Vanilla Sphere</title>
  <link href="{font_link}" rel="stylesheet">
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    :root {{
      --color-paper:   {{paper}};
      --color-ink:     {{ink}};
      --color-muted:   {{muted}};
      --color-accent:  {{accent}};
      --font-sans:     {{sans}};
      --font-serif:    {{serif}};
      --font-mono:     {{mono}};
      color-scheme: {scheme};
    }}

    body {{
      font-family: var(--font-sans);
      background: var(--color-paper);
      color: var(--color-ink);
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 3rem 2rem;
    }}

    .frame {{ max-width: 1200px; width: 100%; }}
    .diagram-container {{ width: 100%; overflow-x: auto; }}

    .eyebrow {{
      font-family: var(--font-mono);
      font-size: 0.66rem;
      font-weight: 500;
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--color-muted);
      margin-bottom: 0.5rem;
    }}

    h1 {{
      font-family: var(--font-serif);
      font-size: clamp(1.5rem, 2.4vw + 0.75rem, 2rem);
      font-weight: 400;
      letter-spacing: -0.02em;
      line-height: 1.15;
      color: var(--color-ink);
      margin-bottom: 1.5rem;
    }}

    svg {{ width: 100%; min-width: {w}px; display: block; }}
    @media print {{
      .diagram-container {{ overflow-x: visible; }}
      svg {{ min-width: 0; }}
    }}
  </style>
</head>
<body>
  <div class="frame">
    <p class="eyebrow">{eyebrow} · Vanilla Sphere</p>
    <h1>{title}</h1>

    <div class="diagram-container">
      <svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" role="img" aria-labelledby="{slug}-title {slug}-desc">
        <title id="{slug}-title">{title}</title>
        <desc id="{slug}-desc">{desc}</desc>
        <defs>
{defs}
        </defs>

        <rect width="100%" height="100%" fill="{{paper}}"/>

{body}
      </svg>
    </div>
  </div>
</body>
</html>
"""
