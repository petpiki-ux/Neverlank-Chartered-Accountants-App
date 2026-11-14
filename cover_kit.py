"""Cover pages, the design library. Pure drawing (Pillow): no Flask, no database, so it can be tested on its own.

A cover is made from three things:
  * a DESIGN (one of DESIGNS: bands, minimal, blocks, framed, geometric, arcs, waves, header),
  * a PALETTE (cover_art.build_palette: primary, accent, dark; from a theme or the client's logo),
  * a CONTENT dictionary (title, subtitle, period, client, logo, reg no, tag, detail lines, status, kicker, footer).
Every design adapts to portrait and landscape pages and to long or short text; nothing is cut off and nothing runs off the page.

`render(design, pal, content, width_px, aspect)` returns a PNG (BytesIO). `aspect` is page height / page width: 1.4142 for A4 portrait,
0.7071 for A4 landscape. cover_pages.py decides the content for each kind of document and where the picture goes.
"""
import io
import math
import random
import functools
import zlib

from PIL import Image, ImageDraw

import cover_art as CA
from cover_art import FIRM_GOLD, FIRM_NAVY, _clip, _font, _mix, build_palette, contrast, hex_to_rgb, rgb_to_hex

WHITE = (255, 255, 255)
INK = (31, 41, 51)
MUTED = (100, 112, 128)

# ----------------------------------------------------------------------------- themes
THEMES = [
    ("client", "Client logo colours", None),
    ("navy_gold", "Navy and gold", ("0F2942", "D4AF6A")),
    ("emerald", "Emerald and amber", ("0B5D4B", "E3B23C")),
    ("burgundy", "Burgundy and champagne", ("6D1F32", "D9B26F")),
    ("slate", "Slate and teal", ("2F3E52", "5FB3B3")),
    ("ocean", "Ocean and sky", ("0E4C7A", "57C1E8")),
    ("violet", "Violet and sun", ("4B2E83", "F2B544")),
    ("charcoal", "Charcoal and copper", ("23272E", "D9822B")),
]
THEME_KEYS = [t[0] for t in THEMES]


def theme_label(key):
    return {t[0]: t[1] for t in THEMES}.get(key, "Navy and gold")


def palette_for_theme(key, client_palette=None):
    """The three cover colours for a theme. 'client' (or an unknown key) uses the client's own palette, falling back to navy and gold."""
    for k, _label, cols in THEMES:
        if k == key and cols:
            return build_palette(hex_to_rgb(cols[0]), hex_to_rgb(cols[1]), "theme")
    return client_palette or build_palette()


# ----------------------------------------------------------------------------- content
def normalise_content(c):
    c = dict(c or {})
    for k in ("kicker", "title", "subtitle", "big", "client", "reg_no", "firm_name", "firm_descriptor", "tag_label", "tag_value", "status", "footer"):
        c[k] = (c.get(k) or "").strip() if isinstance(c.get(k), str) or c.get(k) is None else str(c.get(k)).strip()
    c.setdefault("logo_path", None)
    lines = []
    for item in c.get("lines") or []:
        try:
            label, value = item
        except (TypeError, ValueError):
            continue
        label, value = str(label or "").strip(), str(value or "").strip()
        if value:
            lines.append((label, value))
    c["lines"] = lines[:8]
    c["firm_name"] = c["firm_name"] or CA.FIRM_NAME
    c["firm_descriptor"] = c["firm_descriptor"] or CA.FIRM_DESCRIPTOR
    return c


# ----------------------------------------------------------------------------- drawing context
@functools.lru_cache(maxsize=256)
def _cached_font(name, size):
    return CA._font(name, size)


class Ctx:
    SS = 2

    def __init__(self, width_px, aspect, pal, content):
        self.W = int(width_px)
        self.H = int(round(self.W * aspect))
        self.land = self.W > self.H
        self.u = min(self.W, self.H) / 100.0
        self.pal = pal
        self.primary, self.accent, self.dark = (hex_to_rgb(pal[k]) for k in ("primary", "accent", "dark"))
        self.c = content
        self.big = Image.new("RGB", (self.W * self.SS, self.H * self.SS), WHITE)
        self.img = None
        self.draw = None
        self.seed = zlib.crc32((content.get("client", "") + "|" + content.get("title", "")).encode("utf-8"))

    # --- art (drawn supersampled, so edges are smooth)
    def fill_all(self, colour):
        ImageDraw.Draw(self.big).rectangle([0, 0, self.W * self.SS, self.H * self.SS], fill=colour)

    def poly(self, pts, colour, alpha=1.0):
        if self.big is None:      # after finish_art: small details are drawn straight onto the picture
            if alpha >= 1.0:
                self.draw.polygon(pts, fill=colour)
            return
        pts = [(x * self.SS, y * self.SS) for x, y in pts]
        if len(pts) < 3:
            return
        if alpha >= 1.0:
            ImageDraw.Draw(self.big).polygon(pts, fill=colour)
            return
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        x0, y0 = max(0, int(min(xs)) - 1), max(0, int(min(ys)) - 1)
        x1, y1 = min(self.big.width, int(max(xs)) + 2), min(self.big.height, int(max(ys)) + 2)
        if x1 <= x0 or y1 <= y0:
            return
        mask = Image.new("L", (x1 - x0, y1 - y0), 0)
        ImageDraw.Draw(mask).polygon([(x - x0, y - y0) for x, y in pts], fill=int(255 * alpha))
        self.big.paste(Image.new("RGB", mask.size, colour), (x0, y0), mask)

    def rect(self, x0, y0, x1, y1, colour, alpha=1.0):
        self.poly([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], colour, alpha)

    def circle(self, cx, cy, r, colour, alpha=1.0, ring=0):
        """A filled disc, or (ring > 0) an outline of that thickness."""
        n = 160
        outer = [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]
        if not ring:
            self.poly(outer, colour, alpha)
            return
        r2 = max(0.0, r - ring)
        inner = [(cx + r2 * math.cos(2 * math.pi * i / n), cy + r2 * math.sin(2 * math.pi * i / n)) for i in range(n)]
        xs, ys = [p[0] * self.SS for p in outer], [p[1] * self.SS for p in outer]
        x0, y0 = max(0, int(min(xs)) - 1), max(0, int(min(ys)) - 1)
        x1, y1 = min(self.big.width, int(max(xs)) + 2), min(self.big.height, int(max(ys)) + 2)
        if x1 <= x0 or y1 <= y0:
            return
        mask = Image.new("L", (x1 - x0, y1 - y0), 0)
        d = ImageDraw.Draw(mask)
        d.polygon([(x * self.SS - x0, y * self.SS - y0) for x, y in outer], fill=int(255 * alpha))
        d.polygon([(x * self.SS - x0, y * self.SS - y0) for x, y in inner], fill=0)
        self.big.paste(Image.new("RGB", mask.size, colour), (x0, y0), mask)

    def line(self, x0, y0, x1, y1, colour, width):
        ImageDraw.Draw(self.big).line([(x0 * self.SS, y0 * self.SS), (x1 * self.SS, y1 * self.SS)], fill=colour, width=max(1, int(width * self.SS)))

    def frame(self, x0, y0, x1, y1, colour, width):
        w = max(1, int(width * self.SS))
        ImageDraw.Draw(self.big).rectangle([x0 * self.SS, y0 * self.SS, x1 * self.SS, y1 * self.SS], outline=colour, width=w)

    def finish_art(self):
        self.img = self.big.resize((self.W, self.H), Image.LANCZOS)
        self.big = None
        self.draw = ImageDraw.Draw(self.img)

    # --- text
    def font(self, name, size):
        return _cached_font(name, max(8, int(size)))

    def smaller(self, name, f, limit, too_big):
        """Steps `f` down a point at a time while too_big(f) is true and it is still above `limit` (stops when the font cannot shrink any more)."""
        while f.size > limit and too_big(f):
            g = self.font(name, f.size - 1)
            if g.size >= f.size:
                break
            f = g
        return f

    def tw(self, s, font):
        try:
            return font.getlength(s)
        except Exception:
            return self.draw.textlength(s, font=font) if self.draw else len(s) * font.size * 0.55

    def text(self, x, y_top, s, font, fill, anchor="l"):
        """Draw one line with its CAPITAL TOP at y_top (so blocks line up whatever the letters are). anchor: l, r or c."""
        if not s:
            return
        base = y_top + 0.72 * font.size
        a = {"l": "ls", "r": "rs", "c": "ms"}[anchor]
        self.draw.text((x, base), s, font=font, fill=fill, anchor=a)

    def spaced(self, x, y_top, s, font, fill, track, anchor="l"):
        """Letter-spaced text (small caps look)."""
        total = sum(self.tw(ch, font) for ch in s) + track * max(0, len(s) - 1)
        cx = x if anchor == "l" else (x - total if anchor == "r" else x - total / 2.0)
        base = y_top + 0.72 * font.size
        for ch in s:
            self.draw.text((cx, base), ch, font=font, fill=fill, anchor="ls")
            cx += self.tw(ch, font) + track

    def wrap(self, s, font, max_w, max_lines=None):
        words, lines, cur = (s or "").split(), [], ""
        for w in words:
            trial = (cur + " " + w).strip()
            if self.tw(trial, font) <= max_w or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        if max_lines and len(lines) > max_lines:
            lines = lines[:max_lines]
            last = lines[-1]
            while last and self.tw(last + "...", font) > max_w:
                last = last[:-1]
            lines[-1] = last.rstrip() + "..."
        return lines

    def para(self, x, y, s, font, fill, max_w, anchor="l", pitch=1.42, max_lines=None):
        """A wrapped paragraph; returns the y just below it."""
        for ln in self.wrap(s, font, max_w, max_lines):
            self.text(x, y, ln, font, fill, anchor)
            y += font.size * pitch
        return y

    def title_split(self, text, max_w, size_max, min_lines=2, max_lines=3):
        """Best way to set the title: balanced lines, the largest type that fits `max_w`. Returns (lines, font)."""
        words = text.split()
        if not words:
            return [], self.font("Bold", size_max)
        ref = self.font("Bold", 100)

        def width(parts):
            return max(self.tw(" ".join(p), ref) for p in parts)

        def best_split(n):
            # all ways of cutting the words into n runs (n <= 3 keeps this tiny); choose the narrowest widest-line
            if n == 1:
                return [words]
            best = None
            idx = range(1, len(words))
            if n == 2:
                cuts = [(i,) for i in idx]
            else:
                cuts = [(i, j) for i in idx for j in range(i + 1, len(words))]
            for cut in cuts:
                bounds = (0,) + cut + (len(words),)
                parts = [words[bounds[k]:bounds[k + 1]] for k in range(len(bounds) - 1)]
                w = width(parts)
                if best is None or w < best[0]:
                    best = (w, parts)
            return best[1] if best else [words]

        lo = min(max(1, min_lines), len(words))
        chosen = None
        for n in range(lo, min(max_lines, len(words)) + 1):
            parts = best_split(n)
            size = min(size_max, 100.0 * max_w / max(1.0, width(parts)))
            chosen = (parts, size)
            if size >= 0.62 * size_max:
                break
        parts, size = chosen
        f = self.font("Bold", max(10, int(size)))
        return [" ".join(p) for p in parts], f

    def title_block(self, x, y, max_w, size_max, colours, anchor="l", min_lines=2, pitch=1.12):
        """The document title in big bold type. Returns (y below the block, font)."""
        lines, f = self.title_split(self.c["title"].upper(), max_w, size_max, min_lines=min_lines)
        for i, ln in enumerate(lines):
            self.text(x, y, ln, f, colours[min(i, len(colours) - 1)], anchor)
            y += f.size * pitch
        return y, f

    def fit(self, s, name, max_w, max_size, min_size=9):
        size = max_size
        while size > min_size:
            f = self.font(name, size)
            if self.tw(s, f) <= max_w:
                return f
            size -= 1
        return self.font(name, min_size)

    # --- recurring pieces
    def logo(self, x, y, max_w, max_h, anchor="l"):
        """Paste the client's logo (transparent edges kept). Returns (w, h), or (0, 0) with no usable logo."""
        p = self.c.get("logo_path")
        if not p:
            return 0, 0
        try:
            lg = Image.open(p).convert("RGBA")
            ratio = min(max_w / lg.width, max_h / lg.height)
            lg = lg.resize((max(1, int(lg.width * ratio)), max(1, int(lg.height * ratio))), Image.LANCZOS)
            px = x if anchor == "l" else (x - lg.width if anchor == "r" else x - lg.width // 2)
            self.img.paste(lg, (int(px), int(y)), lg)
            return lg.width, lg.height
        except Exception:
            return 0, 0

    def has_logo(self):
        p = self.c.get("logo_path")
        if not p:
            return False
        try:
            Image.open(p).verify()
            return True
        except Exception:
            return False

    def badge(self, x, y, s, fill, fg, anchor="l", size=None):
        """A rounded status label ('DRAFT', 'FINAL'); returns (w, h) or (0, 0)."""
        if not s:
            return 0, 0
        f = self.font("Bold", size or 1.9 * self.u)
        label = s.upper()
        tw = self.tw(label, f)
        padx, h = 2.2 * self.u, f.size * 1.95
        w = tw + 2 * padx + 0.12 * f.size * len(label) * 0.4
        x0 = x if anchor == "l" else (x - w if anchor == "r" else x - w / 2.0)
        self.draw.rounded_rectangle([x0, y, x0 + w, y + h], radius=h / 2.0, fill=fill)
        self.draw.text((x0 + w / 2.0, y + h / 2.0), label, font=f, fill=fg, anchor="mm")
        return w, h

    def details(self, x, y, max_w, label_fill, value_fill, anchor="l", cols=1, size=None, skip=()):
        """The particulars (Prepared for, Prepared by, Date, Reference ...) as small label-over-value cells. Returns y below."""
        items = [(a, b) for a, b in self.c["lines"] if a not in skip]
        if not items:
            return y
        size = size or 1.55 * self.u
        f_l, f_v = self.font("Light", size * 0.82), self.font("Medium", size)
        gap = 2.2 * self.u
        cols = max(1, min(cols, len(items)))
        cw = (max_w - gap * (cols - 1)) / cols
        rows = [items[i:i + cols] for i in range(0, len(items), cols)]
        for row in rows:
            row_h = 0
            for k, (label, value) in enumerate(row):
                if anchor == "l":
                    cx = x + k * (cw + gap)
                elif anchor == "r":
                    cx = x - max_w + (k + 1) * cw + k * gap
                else:
                    cx = x - max_w / 2.0 + k * (cw + gap) + cw / 2.0
                yy = y
                if label:
                    self.text(cx, yy, label.upper() if anchor != "c" else label.upper(), f_l, label_fill, anchor)
                    yy += f_l.size * 1.5
                yy = self.para(cx, yy, value, f_v, value_fill, cw, anchor, pitch=1.3, max_lines=3)
                row_h = max(row_h, yy - y)
            y += row_h + 1.2 * self.u
        return y

    def finish(self):
        out = io.BytesIO()
        self.img.save(out, "PNG", optimize=True)
        out.seek(0)
        return out


# ----------------------------------------------------------------------------- the designs
def _firm_mark(ctx, x, y, fill, sub_fill, anchor="l", rule=True):
    c = ctx.c
    f1 = ctx.font("Bold", 3.4 * ctx.u)
    ctx.text(x, y, c["firm_name"], f1, fill, anchor)
    yy = y + f1.size * 1.0
    if rule:
        wlen = 12 * ctx.u
        x0 = x if anchor == "l" else (x - wlen if anchor == "r" else x - wlen / 2.0)
        ctx.draw.line([(x0, yy + 0.6 * ctx.u), (x0 + wlen, yy + 0.6 * ctx.u)], fill=fill, width=max(2, int(0.18 * ctx.u)))
        yy += 1.6 * ctx.u
    ctx.text(x, yy + 0.4 * ctx.u, c["firm_descriptor"], ctx.font("Regular", 1.9 * ctx.u), sub_fill, anchor)
    return yy + 3 * ctx.u


def _tag(ctx, x_right, y, label_fill, value_fill):
    c = ctx.c
    if not c["tag_value"]:
        return
    f1, f2 = ctx.font("Light", 1.7 * ctx.u), ctx.font("Light", 3.0 * ctx.u)
    if c["tag_label"]:
        ctx.text(x_right, y, c["tag_label"], f1, label_fill, "r")
    f2 = ctx.fit(c["tag_value"], "Light", 45 * ctx.u, 3.0 * ctx.u)
    ctx.text(x_right, y + 2.9 * ctx.u, c["tag_value"], f2, value_fill, "r")


def _kicker(ctx, x, y, fill, anchor="l"):
    if ctx.c["kicker"]:
        ctx.spaced(x, y, ctx.c["kicker"].upper(), ctx.font("Medium", 1.45 * ctx.u), fill, 0.28 * ctx.u, anchor)
        return y + 3.2 * ctx.u
    return y


def _client_block(ctx, x, y, max_w, name_fill, sub_fill, bullet_fill, logo_h, logo_w=None, anchor="l", name_size=3.6, name_lines=4):
    """Logo (if any), client name, registration number; for white areas. Returns y below."""
    u = ctx.u
    lw, lh = ctx.logo(x, y, logo_w or min(max_w, 36 * u), logo_h, anchor)
    if lh:
        y += lh + 2.6 * u
    f = ctx.font("Bold", name_size * u)
    # shrink for long names so the whole name stays visible
    f = ctx.smaller("Bold", f, 1.8 * u, lambda g: len(ctx.wrap(ctx.c["client"], g, max_w)) > name_lines)
    y = ctx.para(x, y, ctx.c["client"], f, name_fill, max_w, anchor, pitch=1.28, max_lines=name_lines)
    if ctx.c["reg_no"]:
        y += 0.8 * u
        f2 = ctx.font("Regular", 1.8 * u)
        ctx.text(x, y, "Registration no. " + ctx.c["reg_no"], f2, sub_fill, anchor)
        y += f2.size * 1.6
    return y


def _footer(ctx, y, fill, anchor="c", x=None):
    if ctx.c["footer"]:
        px = x if x is not None else ctx.W / 2.0
        room = (ctx.W - 2 * 9 * ctx.u) if anchor == "c" else (ctx.W - px - 9 * ctx.u)
        f = ctx.fit(ctx.c["footer"], "Light", room, 1.45 * ctx.u, 7)
        ctx.text(px, y, ctx.c["footer"], f, fill, anchor)


# ---- 1. bands (the Financial Statements design, diagonal colour bands)
def _band(W, H, u0, u1):
    poly = [(0, 0), (W, 0), (W, H), (0, H)]
    poly = _clip(poly, 1, 1, u1)
    poly = _clip(poly, -1, -1, -u0)
    return poly


def design_bands(ctx):
    W, H, u = ctx.W, ctx.H, ctx.u
    T = W + H
    ctx.fill_all(ctx.primary)
    for u0, u1, al in ((0.45, 0.545, 0.12), (0.58, 0.70, 0.07), (0.74, 0.79, 0.10)):
        ctx.poly(_band(W, H, u0 * T, u1 * T), WHITE, al)
    ctx.poly(_band(W, H, -1, 0.22 * T), ctx.dark)
    ctx.poly(_band(W, H, 0.22 * T, 0.31 * T), ctx.accent)
    for uu, x0, x1 in ((0.185 * T, 0.03 * W, 0.19 * W), (0.36 * T, 0.50 * W, 0.70 * W)):
        ctx.line(x0, uu - x0, x1, uu - x1, WHITE, 3 if not ctx.land else 2.5)
    d0 = (0.40 if not ctx.land else 0.26) * H
    ctx.poly([(0, d0), (0, H), (H - d0, H)], WHITE)
    ctx.finish_art()
    c = ctx.c
    right = W - 7 * u
    soft = _mix(WHITE, ctx.primary, 0.25)
    _firm_mark(ctx, 7 * u, 5.5 * u, WHITE, WHITE)
    _tag(ctx, right, 5.5 * u, soft, soft)
    max_w = (56 if not ctx.land else 70) * u
    y = (0.385 if not ctx.land else 0.27) * H
    if c["kicker"]:
        ctx.spaced(right, y - 4.4 * u, c["kicker"].upper(), ctx.font("Medium", 1.45 * u), ctx.accent, 0.28 * u, "r")
    y, f = ctx.title_block(right, y, max_w, 9.4 * u, (ctx.accent, WHITE, WHITE), "r")
    y += 0.4 * u
    ctx.draw.line([(right - max_w, y), (right, y)], fill=WHITE, width=max(2, int(0.14 * u)))
    y += 2.4 * u
    if c["big"]:
        fb = ctx.fit(c["big"], "Light", max_w, 17 * u)
        ctx.text(right, y, c["big"], fb, WHITE, "r")
        y += fb.size * 0.72 + 3.0 * u
    if c["subtitle"]:
        y = ctx.para(right, y, c["subtitle"], ctx.fit(c["subtitle"], "Regular", max_w, 2.6 * u, 14), WHITE, max_w, "r", pitch=1.35, max_lines=3)
    if c["status"]:
        ctx.badge(right, y + 1.6 * u, c["status"], ctx.accent, ctx.dark, "r")
    # the white corner: logo, client, particulars
    x0 = 7 * u
    gap = 3.5 * u

    def bound(yy):
        return max(8 * u, yy - d0 - gap)

    y = d0 + ((0.40 if ctx.has_logo() else 0.46) if not ctx.land else (0.34 if ctx.has_logo() else 0.40)) * (H - d0)
    max_cw = bound(y) - x0
    y = _client_block(ctx, x0, y, max(14 * u, min(max_cw, 40 * u)), ctx.primary, (70, 80, 95), ctx.accent, 0.12 * H if not ctx.land else 0.10 * H, name_size=3.2 if not ctx.land else 2.6)
    y += 1.0 * u
    f_l, f_v = ctx.font("Light", 1.35 * u), ctx.font("Regular", 1.65 * u)
    for label, value in ctx.c["lines"]:
        if y > H - 5 * u:
            break
        mw = max(10 * u, bound(y) - x0 - 2 * u)
        ctx.text(x0, y, (label + "  ").upper() if label else "", f_l, MUTED)
        off = ctx.tw((label + "  ").upper(), f_l) if label else 0
        for k, ln in enumerate(ctx.wrap(value, f_v, max(8 * u, mw - off), 2)):
            ctx.text(x0 + off, y - (0.15 * u if k == 0 else 0), ln, f_v, (60, 70, 85))
            y += f_v.size * 1.42
        y += 0.5 * u


# ---- 2. minimal
def design_minimal(ctx):
    W, H, u = ctx.W, ctx.H, ctx.u
    ctx.fill_all(WHITE)
    strip_h = (0.17 if not ctx.land else 0.24) * H
    ctx.rect(0, H - strip_h, W, H, ctx.primary)
    ctx.rect(0, H - strip_h - 0.9 * u, W, H - strip_h, ctx.accent)
    ctx.rect(0, 0, 2.2 * u, H - strip_h - 0.9 * u, ctx.primary)
    ctx.finish_art()
    c = ctx.c
    left, right = 10 * u, W - 9 * u
    _firm_mark(ctx, left, 7 * u, ctx.primary, MUTED, rule=False)
    _tag(ctx, right, 7 * u, MUTED, ctx.primary)
    y = (0.27 if not ctx.land else 0.22) * H
    y = _kicker(ctx, left, y, ctx.accent)
    max_w = (W - left - 14 * u) * 0.9 if not ctx.land else W * 0.58 - left
    y, f = ctx.title_block(left, y, max_w, 9.0 * u, (ctx.primary,), "l", pitch=1.14)
    y += 1.4 * u
    ctx.rect(left, y, left + 16 * u, y + 0.9 * u, ctx.accent)
    y += 4.2 * u
    if c["big"]:
        fb = ctx.fit(c["big"], "Light", max_w, 15 * u if not ctx.land else 10 * u)
        ctx.text(left - 0.5 * u, y, c["big"], fb, _mix(ctx.accent, ctx.primary, 0.15), "l")
        y += fb.size * 0.72 + 3.2 * u
    if c["subtitle"]:
        y = ctx.para(left, y, c["subtitle"], ctx.fit(c["subtitle"], "Regular", max_w, 2.7 * u, 14), MUTED, max_w, "l", pitch=1.35, max_lines=3)
    if c["status"]:
        ctx.badge(left, y + 2.0 * u, c["status"], ctx.primary, WHITE, "l")
    # client above the strip (landscape: in a column on the right)
    cy_end = H - strip_h - 0.9 * u - 4 * u
    block_w = (W - left - 14 * u) * (0.62 if not ctx.land else 0.30)
    if ctx.land:
        f_name = ctx.font("Bold", 3.2 * u)
        _client_block(ctx, W * 0.64, H * 0.24, block_w, ctx.primary, MUTED, ctx.accent, 0.16 * H, logo_w=34 * u, name_size=3.2)
        sy = H - strip_h + 3.2 * u
        ctx.details(left, sy, W - left - 9 * u, _mix(WHITE, ctx.primary, 0.35), WHITE, "l", cols=min(5, max(1, len(c["lines"]))), size=1.5 * u)
        _footer(ctx, H - 4.5 * u, _mix(WHITE, ctx.primary, 0.35), "l", left)
        return
    # measure first, then place the block so it ends above the strip
    f_name = ctx.font("Bold", 3.4 * u)
    f_name = ctx.smaller("Bold", f_name, 2.2 * u, lambda g: len(ctx.wrap(c["client"], g, block_w)) > 3)
    n_lines = min(3, len(ctx.wrap(c["client"], f_name, block_w)))
    lh = 0.13 * H if ctx.has_logo() else 0
    total = (lh + 2.6 * u if lh else 0) + n_lines * f_name.size * 1.28 + (f_name.size * 0.9 if c["reg_no"] else 0)
    _client_block(ctx, left, cy_end - total, block_w, ctx.primary, MUTED, ctx.accent, lh or 1, logo_w=34 * u, name_size=f_name.size / u)
    # particulars on the strip, in white
    sy = H - strip_h + 3.2 * u
    cols = 3 if len(c["lines"]) > 2 else max(1, len(c["lines"]))
    ctx.details(left, sy, W - left - 9 * u, _mix(WHITE, ctx.primary, 0.35), WHITE, "l", cols=cols, size=1.6 * u)
    _footer(ctx, H - 4.5 * u, _mix(WHITE, ctx.primary, 0.35), "l", left)


# ---- 3. blocks
def design_blocks(ctx):
    W, H, u = ctx.W, ctx.H, ctx.u
    pw = (0.36 if not ctx.land else 0.30) * W
    ctx.fill_all(WHITE)
    ctx.rect(0, 0, pw, H, ctx.primary)
    ctx.rect(pw, 0, pw + 1.4 * u, H, ctx.accent)
    ctx.poly([(0, H), (pw, H), (0, H - 0.34 * pw)], ctx.dark)
    ctx.poly([(0, 0), (0.55 * pw, 0), (0, 0.55 * pw)], ctx.dark, 0.55)
    ctx.finish_art()
    c = ctx.c
    px = 5.5 * u
    pmax = pw - 2 * px
    ctx.text(px, 8 * u, c["firm_name"], ctx.fit(c["firm_name"], "Bold", pmax, 3.3 * u), WHITE)
    ctx.text(px, 12.6 * u, c["firm_descriptor"], ctx.fit(c["firm_descriptor"], "Regular", pmax, 1.8 * u, 9), _mix(WHITE, ctx.primary, 0.2))
    y = H * 0.52
    if c["tag_value"]:
        ctx.text(px, y, c["tag_label"], ctx.font("Light", 1.5 * u), _mix(WHITE, ctx.primary, 0.3))
        y += 2.3 * u
        y = ctx.para(px, y, c["tag_value"], ctx.font("Medium", 2.3 * u), WHITE, pmax, "l", pitch=1.3, max_lines=2) + 2.4 * u
    ctx.details(px, y, pmax, _mix(WHITE, ctx.primary, 0.35), WHITE, "l", cols=1, size=1.55 * u)
    left = pw + 9 * u
    right = W - 8 * u
    max_w = right - left
    y = (0.2 if not ctx.land else 0.17) * H
    y = _kicker(ctx, left, y, ctx.accent)
    y, f = ctx.title_block(left, y, max_w, 8.6 * u, (ctx.primary,), "l", pitch=1.14)
    y += 1.6 * u
    ctx.rect(left, y, left + 14 * u, y + 0.9 * u, ctx.accent)
    y += 4.4 * u
    if c["big"]:
        fb = ctx.fit(c["big"], "Light", max_w, 15 * u)
        ctx.text(left - 0.4 * u, y, c["big"], fb, ctx.accent if contrast(ctx.accent, WHITE) >= 1.8 else ctx.primary)
        y += fb.size * 0.72 + 3.2 * u
    if c["subtitle"]:
        y = ctx.para(left, y, c["subtitle"], ctx.fit(c["subtitle"], "Regular", max_w, 2.7 * u, 14), MUTED, max_w, "l", pitch=1.35, max_lines=3)
    if c["status"]:
        ctx.badge(left, y + 2 * u, c["status"], ctx.accent, ctx.dark, "l")
    f_name = ctx.font("Bold", 3.5 * u)
    lh = (0.12 if not ctx.land else 0.15) * H if ctx.has_logo() else 0
    n_lines = min(3, len(ctx.wrap(c["client"], f_name, max_w)))
    total = (lh + 2.6 * u if lh else 0) + n_lines * f_name.size * 1.28 + (f_name.size * 0.9 if c["reg_no"] else 0)
    _client_block(ctx, left, H - 9 * u - total, max_w, ctx.primary, MUTED, ctx.accent, lh or 1, logo_w=36 * u)
    _footer(ctx, H - 5 * u, MUTED, "l", left)


# ---- 4. framed (classic)
def _ornament(ctx, cx, y, width, colour):
    u = ctx.u
    ctx.draw.line([(cx - width / 2, y), (cx - 2.2 * u, y)], fill=colour, width=max(1, int(0.18 * u)))
    ctx.draw.line([(cx + 2.2 * u, y), (cx + width / 2, y)], fill=colour, width=max(1, int(0.18 * u)))
    d = 0.9 * u
    ctx.draw.polygon([(cx, y - d), (cx + d, y), (cx, y + d), (cx - d, y)], fill=colour)


def design_framed(ctx):
    W, H, u = ctx.W, ctx.H, ctx.u
    ctx.fill_all((252, 250, 245))
    ctx.frame(4.5 * u, 4.5 * u, W - 4.5 * u, H - 4.5 * u, ctx.accent, 0.5 * u)
    ctx.frame(6.2 * u, 6.2 * u, W - 6.2 * u, H - 6.2 * u, ctx.primary, 0.22 * u)
    cs = 3.2 * u
    for (cx, cy, sx, sy) in ((6.2 * u, 6.2 * u, 1, 1), (W - 6.2 * u, 6.2 * u, -1, 1), (6.2 * u, H - 6.2 * u, 1, -1), (W - 6.2 * u, H - 6.2 * u, -1, -1)):
        ctx.poly([(cx, cy), (cx + sx * cs, cy), (cx, cy + sy * cs)], ctx.primary)
    ctx.finish_art()
    c = ctx.c
    cx = W / 2.0
    max_w = W - 34 * u if not ctx.land else W * 0.62
    y = 13 * u if not ctx.land else 11 * u
    ctx.spaced(cx, y, c["firm_name"].upper(), ctx.font("Bold", 2.6 * u), ctx.primary, 0.9 * u, "c")
    y += 4.2 * u
    ctx.spaced(cx, y, c["firm_descriptor"].upper(), ctx.font("Regular", 1.35 * u), MUTED, 0.5 * u, "c")
    y += 5.4 * u
    _ornament(ctx, cx, y, 30 * u, ctx.accent)
    y += 6 * u
    lh = 0.11 * H if not ctx.land else 0.10 * H
    lw, lhh = ctx.logo(cx, y, 34 * u, lh, "c")
    if lhh:
        y += lhh + 4 * u
    y = _kicker(ctx, cx, y, ctx.accent, "c")
    y, f = ctx.title_block(cx, y, max_w, 7.6 * u, (ctx.primary,), "c", min_lines=2 if not ctx.land else 1, pitch=1.16)
    y += 2.4 * u
    _ornament(ctx, cx, y, 24 * u, ctx.accent)
    y += 5.6 * u
    if c["subtitle"]:
        y = ctx.para(cx, y, c["subtitle"], ctx.fit(c["subtitle"], "Regular", max_w, 2.5 * u, 13), MUTED, max_w, "c", pitch=1.35, max_lines=3)
        y += 2 * u
    if c["big"] and not ctx.land:
        fb = ctx.fit(c["big"], "Light", max_w, 12 * u)
        ctx.text(cx, y, c["big"], fb, ctx.accent if contrast(ctx.accent, (252, 250, 245)) >= 1.8 else ctx.primary, "c")
        y += fb.size * 0.72 + 4 * u
    f_name = ctx.font("Bold", 3.6 * u if not ctx.land else 3.0 * u)
    f_name = ctx.smaller("Bold", f_name, 2.2 * u, lambda g: len(ctx.wrap(c["client"], g, max_w)) > 3)
    y = ctx.para(cx, y + 1 * u, c["client"], f_name, ctx.primary, max_w, "c", pitch=1.28, max_lines=3)
    if c["reg_no"]:
        ctx.text(cx, y + 0.6 * u, "Registration no. " + c["reg_no"], ctx.font("Regular", 1.7 * u), MUTED, "c")
        y += 4 * u
    if c["status"]:
        ctx.spaced(cx, y + 2.4 * u, "[ " + c["status"].upper() + " ]", ctx.font("Bold", 1.8 * u), ctx.accent if contrast(ctx.accent, (252, 250, 245)) >= 1.8 else ctx.primary, 0.5 * u, "c")
    n = len(c["lines"])
    if n:
        cols = min(n, 3 if not ctx.land else 4)
        rows = -(-n // cols)
        by = max(y + 5 * u, H - 11 * u - rows * 7.2 * u) if not ctx.land else min(max(y + 3 * u, H - 22 * u - rows * 5 * u), H - 14 * u - rows * 7.2 * u + 4 * u)
        by = max(by, y + 3 * u)
        ctx.details(cx, by, W - 30 * u if not ctx.land else W * 0.74, MUTED, INK, "c", cols=cols, size=1.5 * u)
    if not (ctx.land and y > H * 0.62):
        _footer(ctx, H - 10 * u, MUTED, "c")


# ---- 5. geometric (mosaic that is different for every client)
def design_geometric(ctx):
    W, H, u = ctx.W, ctx.H, ctx.u
    rnd = random.Random(ctx.seed)
    ctx.fill_all(WHITE)
    zone = (0.62 if not ctx.land else 0.66) * H
    cell = 9.5 * u
    cols, rows = int(W / cell) + 2, int(zone / cell) + 2
    shades = [ctx.primary, ctx.dark, _mix(ctx.primary, WHITE, 0.14), _mix(ctx.primary, WHITE, 0.30), ctx.accent]
    weights = [30, 24, 20, 12, 5]
    for r in range(rows):
        for q in range(cols):
            x0, y0 = q * cell, r * cell
            fade = max(0.0, 1.0 - (y0 / zone) ** 1.6)
            for tri in (((x0, y0), (x0 + cell, y0), (x0, y0 + cell)), ((x0 + cell, y0), (x0 + cell, y0 + cell), (x0, y0 + cell))):
                if r == 0 or rnd.random() < fade:
                    col = rnd.choices(shades, weights)[0]
                    ctx.poly(tri, col, 1.0 if r < 2 else 0.35 + 0.65 * fade)
    # solid title panel across the mosaic
    py0 = 0.30 * zone
    py1 = min(zone * 0.97, py0 + 44 * u)
    ctx.rect(0, py0, W * (0.84 if not ctx.land else 0.80), py1, ctx.dark, 0.94)
    ctx.rect(0, py0, W * (0.84 if not ctx.land else 0.80), py0 + 0.9 * u, ctx.accent)
    ctx.finish_art()
    c = ctx.c
    _firm_mark(ctx, 8 * u, 6 * u, WHITE, WHITE)
    # the tag sits on the mosaic: put it on a small plate so it always reads
    if c["tag_value"]:
        f2 = ctx.fit(c["tag_value"], "Medium", 40 * u, 2.4 * u)
        tw = max(ctx.tw(c["tag_value"], f2), ctx.tw(c["tag_label"], ctx.font("Light", 1.4 * u))) + 5 * u
        ctx.draw.rectangle([W - 8 * u - tw, 5.2 * u, W - 8 * u, 5.2 * u + 7.6 * u], fill=ctx.dark)
        ctx.text(W - 10.5 * u, 6.2 * u, c["tag_label"], ctx.font("Light", 1.4 * u), _mix(WHITE, ctx.primary, 0.3), "r")
        ctx.text(W - 10.5 * u, 8.9 * u, c["tag_value"], f2, WHITE, "r")
    x = 8 * u
    max_w = W * (0.84 if not ctx.land else 0.80) - x - 6 * u
    y = py0 + 5.4 * u
    y = _kicker(ctx, x, y, ctx.accent)
    y, f = ctx.title_block(x, y, max_w, 8.2 * u, (WHITE, ctx.accent if contrast(ctx.accent, ctx.dark) >= 2.5 else WHITE), "l", pitch=1.13)
    y += 1.2 * u
    if c["subtitle"]:
        y = ctx.para(x, y, c["subtitle"] if not c["big"] else (c["big"] + " | " + c["subtitle"]), ctx.fit(c["subtitle"], "Regular", max_w, 2.5 * u, 13), WHITE, max_w, "l", pitch=1.3, max_lines=2)
    elif c["big"]:
        ctx.text(x, y, c["big"], ctx.fit(c["big"], "Light", max_w, 6 * u), WHITE)
    # lower white area
    ly = zone + 6 * u
    lh = 0.09 * H if not ctx.land else 0.13 * H
    half = (W - 16 * u) * (0.55 if not ctx.land else 0.45)
    f_name = ctx.font("Bold", 3.4 * u)
    yy = _client_block(ctx, x, ly, half, ctx.primary, MUTED, ctx.accent, lh, logo_w=34 * u, name_size=3.3)
    if c["status"]:
        ctx.badge(x, yy + 1.4 * u, c["status"], ctx.primary, WHITE, "l")
    ctx.details(W - 8 * u, ly, (W - 16 * u) - half - 6 * u, MUTED, INK, "r", cols=1, size=1.5 * u)
    _footer(ctx, H - 6 * u, MUTED, "l", x)


# ---- 6. arcs
def design_arcs(ctx):
    W, H, u = ctx.W, ctx.H, ctx.u
    ctx.fill_all(ctx.primary)
    cx, cy = W * 1.0, H * 1.0
    R = max(W, H) * 0.95
    for i, k in enumerate((1.0, 0.82, 0.64, 0.46)):
        if i % 2 == 0:
            ctx.circle(cx, cy, R * k, WHITE, 0.045)
        else:
            ctx.circle(cx, cy, R * k, WHITE, 0.10, ring=0.35 * u)
    ctx.circle(cx, cy, R * 0.26, ctx.dark, 0.85)
    ctx.circle(cx, cy, R * 0.16, ctx.accent)
    ctx.circle(0, 0, 0.30 * min(W, H), ctx.dark, 0.5)
    ctx.finish_art()
    c = ctx.c
    soft = _mix(WHITE, ctx.primary, 0.25)
    left = 9 * u
    _firm_mark(ctx, left, 7 * u, WHITE, WHITE)
    if c["tag_value"]:
        if ctx.land:
            _tag(ctx, W - 8 * u, 7 * u, soft, WHITE)
        else:
            ctx.text(left, 20 * u, c["tag_label"], ctx.font("Light", 1.5 * u), soft)
            ctx.text(left, 22.6 * u, c["tag_value"], ctx.fit(c["tag_value"], "Medium", 40 * u, 2.4 * u), WHITE)
    max_w = (W - left - 12 * u) * 0.86 if not ctx.land else W * 0.46 - left
    y = (0.31 if not ctx.land else 0.30) * H
    y = _kicker(ctx, left, y, ctx.accent)
    y, f = ctx.title_block(left, y, max_w, 9.2 * u, (WHITE, ctx.accent if contrast(ctx.accent, ctx.primary) >= 2 else WHITE, WHITE), "l", pitch=1.12)
    y += 1.8 * u
    ctx.draw.line([(left, y), (left + 20 * u, y)], fill=ctx.accent, width=max(2, int(0.4 * u)))
    y += 3.6 * u
    if c["big"]:
        fb = ctx.fit(c["big"], "Light", max_w, 13 * u)
        ctx.text(left - 0.4 * u, y, c["big"], fb, WHITE)
        y += fb.size * 0.72 + 3 * u
    if c["subtitle"]:
        y = ctx.para(left, y, c["subtitle"], ctx.fit(c["subtitle"], "Regular", max_w, 2.6 * u, 13), WHITE, max_w, "l", pitch=1.35, max_lines=3)
    if c["status"]:
        ctx.badge(left, y + 1.8 * u, c["status"], ctx.accent, ctx.dark, "l")
    # client and particulars on a white card, bottom-left
    card_w = (0.62 if not ctx.land else 0.46) * W
    f_name = ctx.font("Bold", 3.2 * u)
    inner = card_w - 9 * u
    n_lines = min(3, len(ctx.wrap(c["client"], f_name, inner)))
    lh = 0.08 * H if ctx.has_logo() else 0
    n = len(c["lines"])
    dcols = 2 if n > 1 else 1
    drows = -(-n // dcols) if n else 0
    card_h = 6 * u + (lh + 2.4 * u if lh else 0) + n_lines * f_name.size * 1.28 + (f_name.size * 1.2 if c["reg_no"] else 0) + (drows * 7.3 * u + 3 * u if n else 0)
    cy0 = H - 10 * u - card_h
    cx0 = left
    if ctx.land:
        cx0 = W - 8 * u - card_w
        cy0 = max(22 * u, (H - card_h) / 2.0 + 6 * u)
    ctx.draw.rounded_rectangle([cx0, cy0, cx0 + card_w, cy0 + card_h], radius=1.2 * u, fill=WHITE)
    yy = _client_block(ctx, cx0 + 4.5 * u, cy0 + 3.4 * u, inner, ctx.primary, MUTED, ctx.accent, lh or 1, logo_w=30 * u, name_size=3.2)
    if n:
        ctx.draw.line([(cx0 + 4.5 * u, yy + 1.2 * u), (cx0 + card_w - 4.5 * u, yy + 1.2 * u)], fill=(225, 230, 236), width=max(1, int(0.12 * u)))
        ctx.details(cx0 + 4.5 * u, yy + 3.4 * u, inner, MUTED, INK, "l", cols=dcols, size=1.4 * u)


# ---- 7. waves
def _wave_poly(W, H, base, amp, wl, phase, steps=140):
    pts = [(W * i / steps, base + amp * math.sin(2 * math.pi * (W * i / steps) / wl + phase)) for i in range(steps + 1)]
    return pts + [(W, H), (0, H)]


def design_waves(ctx):
    W, H, u = ctx.W, ctx.H, ctx.u
    ctx.fill_all(WHITE)
    top = (0.64 if not ctx.land else 0.62) * H
    amp = 3.2 * u if not ctx.land else 2.4 * u
    ctx.poly(_wave_poly(W, H, top - 4 * u, amp, W * 0.9, 0.2), ctx.accent, 0.55)
    ctx.poly(_wave_poly(W, H, top, amp * 1.15, W * 1.1, 2.0), ctx.primary)
    ctx.poly(_wave_poly(W, H, top + 8 * u, amp * 1.2, W * 0.8, 4.2), ctx.dark)
    ctx.finish_art()
    c = ctx.c
    left, right = 9 * u, W - 9 * u
    _firm_mark(ctx, left, 7 * u, ctx.primary, MUTED, rule=False)
    lw, lh = ctx.logo(right, 6 * u, 34 * u, 9 * u, "r")
    if not lh:
        _tag(ctx, right, 6.5 * u, MUTED, ctx.primary)
    y = (0.22 if not ctx.land else 0.20) * H
    y = _kicker(ctx, left, y, ctx.accent)
    max_w = (W - left - 14 * u) * (0.9 if not ctx.land else 0.7)
    y, f = ctx.title_block(left, y, max_w, 9.0 * u, (ctx.primary,), "l", pitch=1.13)
    y += 1.6 * u
    ctx.draw.line([(left, y), (left + 16 * u, y)], fill=ctx.accent, width=max(2, int(0.4 * u)))
    y += 3.6 * u
    if c["big"] and not ctx.land:
        fb = ctx.fit(c["big"], "Light", max_w, 12 * u)
        ctx.text(left - 0.4 * u, y, c["big"], fb, ctx.accent if contrast(ctx.accent, WHITE) >= 1.8 else ctx.primary)
        y += fb.size * 0.72 + 3 * u
    elif c["big"]:
        fb = ctx.fit(c["big"], "Light", W * 0.3, 24 * u)
        ctx.text(right, 14 * u if not lh else 18 * u, c["big"], fb, _mix(ctx.accent, WHITE, 0.25), "r")
    if c["subtitle"]:
        y = ctx.para(left, y, c["subtitle"], ctx.fit(c["subtitle"], "Regular", max_w, 2.6 * u, 13), MUTED, max_w, "l", pitch=1.35, max_lines=3)
    if c["status"]:
        ctx.badge(left, y + 1.8 * u, c["status"], ctx.primary, WHITE, "l")
    # client on the front wave, in white
    by = top + (15 if not ctx.land else 12) * u
    avail = H - by - 6 * u
    f_name = ctx.font("Bold", 3.3 * u if not ctx.land else 3.0 * u)
    half = (W - 2 * left) * (0.5 if not ctx.land else 0.32)
    y2 = ctx.para(left, by, c["client"], f_name, WHITE, half, "l", pitch=1.28, max_lines=3)
    if c["reg_no"]:
        ctx.text(left, y2 + 0.6 * u, "Registration no. " + c["reg_no"], ctx.font("Regular", 1.6 * u), _mix(WHITE, ctx.dark, 0.25))
    ctx.details(right, by, (W - 2 * left) - half - 5 * u, _mix(WHITE, ctx.dark, 0.35), WHITE, "r", cols=1 if not ctx.land else 3, size=1.4 * u)
    if lh and c["tag_value"]:
        _tag(ctx, right, top - 9 * u, MUTED, ctx.primary)


# ---- 8. header (colour block on top, white below)
def design_header(ctx):
    W, H, u = ctx.W, ctx.H, ctx.u
    ctx.fill_all(WHITE)
    hb = (0.44 if not ctx.land else 0.54) * H
    ctx.rect(0, 0, W, hb, ctx.primary)
    for k in range(-6, 14):
        x0 = k * 11 * u
        ctx.poly([(x0, hb), (x0 + 5.5 * u, hb), (x0 + 5.5 * u + hb, 0), (x0 + hb, 0)], WHITE, 0.045)
    ctx.poly([(W, 0), (W, 0.5 * hb), (W - 0.5 * hb, 0)], ctx.dark)
    ctx.rect(0, hb, W, hb + 1.6 * u, ctx.accent)
    ctx.finish_art()
    c = ctx.c
    left, right = 9 * u, W - 9 * u
    soft = _mix(WHITE, ctx.primary, 0.25)
    _firm_mark(ctx, left, 7 * u, WHITE, WHITE)
    if c["tag_value"]:
        if ctx.land:
            _tag(ctx, right, 6 * u, soft, WHITE)
        else:
            ctx.text(left, 19.6 * u, c["tag_label"], ctx.font("Light", 1.5 * u), soft)
            ctx.text(left, 22.2 * u, c["tag_value"], ctx.fit(c["tag_value"], "Medium", 40 * u, 2.2 * u), WHITE)
    max_w = (W - left - 14 * u) * (0.92 if not ctx.land else 0.7)
    lines, f = ctx.title_split(c["title"].upper(), max_w, 8.8 * u)
    blk = len(lines) * f.size * 1.12
    ty = hb - 7 * u - blk
    if c["kicker"]:
        ctx.spaced(left, ty - 4.4 * u, c["kicker"].upper(), ctx.font("Medium", 1.45 * u), ctx.accent, 0.28 * u)
    for i, ln in enumerate(lines):
        ctx.text(left, ty, ln, f, ctx.accent if (i == 0 and len(lines) > 1 and contrast(ctx.accent, ctx.primary) >= 2.2) else WHITE)
        ty += f.size * 1.12
    y = hb + 1.6 * u + 7 * u
    if c["big"]:
        fb = ctx.fit(c["big"], "Light", max_w if not ctx.land else W * 0.4, 8 * u)
        ctx.text(left, y, c["big"], fb, ctx.primary)
        y += fb.size * 0.72 + 2.4 * u
    if c["subtitle"]:
        y = ctx.para(left, y, c["subtitle"], ctx.fit(c["subtitle"], "Regular", max_w if not ctx.land else W * 0.4, 2.6 * u, 13), MUTED, max_w if not ctx.land else W * 0.4, "l", pitch=1.35, max_lines=3)
    if c["status"]:
        ctx.badge(left, y + 1.2 * u, c["status"], ctx.primary, WHITE, "l")
    if ctx.land:
        xr = W * 0.52
        wr = right - xr
        yy = _client_block(ctx, xr, hb + 8 * u, wr, ctx.primary, MUTED, ctx.accent, 0.1 * H, logo_w=30 * u, name_size=3.0, name_lines=2)
        ctx.details(xr, yy + 2.2 * u, wr, MUTED, INK, "l", cols=min(3, max(1, len(c["lines"]))), size=1.4 * u)
        ctx.draw.line([(left, H - 9 * u), (right, H - 9 * u)], fill=(225, 230, 236), width=max(1, int(0.12 * u)))
        _footer(ctx, H - 7 * u, MUTED, "l", left)
        return
    # client and particulars
    f_name = ctx.font("Bold", 3.4 * u)
    avail_w = (W - 2 * left) * (0.5 if not ctx.land else 0.42)
    lh = 0.085 * H if not ctx.land else 0.15 * H
    n_lines = min(3, len(ctx.wrap(c["client"], f_name, avail_w)))
    total = (lh + 2.6 * u if ctx.has_logo() else 0) + n_lines * f_name.size * 1.28 + (f_name.size * 0.9 if c["reg_no"] else 0)
    cy = H - 14 * u - total
    cy = max(cy, y + 8 * u)
    _client_block(ctx, left, cy, avail_w, ctx.primary, MUTED, ctx.accent, lh, logo_w=34 * u)
    n = len(c["lines"])
    dcols = 2 if n > 3 else 1
    est = (-(-n // dcols)) * 7.4 * u
    dy = max(y + 8 * u, min(cy, H - 14 * u - est))
    ctx.details(right, dy, (W - 2 * left) - avail_w - 6 * u, MUTED, INK, "r", cols=dcols, size=1.4 * u)
    ctx.draw.line([(left, H - 9 * u), (right, H - 9 * u)], fill=(225, 230, 236), width=max(1, int(0.12 * u)))
    _footer(ctx, H - 7 * u, MUTED, "l", left)


DESIGNS = [
    ("bands", "Diagonal bands", "Bold diagonal colour bands with a white corner for the client. The Financial Statements design.", design_bands),
    ("minimal", "Clean minimal", "White page, strong left rule, big type and a colour strip carrying the particulars.", design_minimal),
    ("blocks", "Colour block", "A deep colour panel on the left for the firm and particulars; the title on white.", design_blocks),
    ("framed", "Classic framed", "A double border, centred type and ornaments. Formal and traditional.", design_framed),
    ("geometric", "Geometric mosaic", "A triangle mosaic that is different for every client, with a solid title panel.", design_geometric),
    ("arcs", "Concentric arcs", "Colour field with sweeping arcs from the corner; the client on a white card.", design_arcs),
    ("waves", "Waves", "Light page, the title on top and layered waves carrying the client and particulars.", design_waves),
    ("header", "Executive header", "A colour header block with the title, a gold rule, then white for the details.", design_header),
]
DESIGN_KEYS = [d[0] for d in DESIGNS]
_FUNCS = {d[0]: d[3] for d in DESIGNS}


def design_label(key):
    return {d[0]: d[1] for d in DESIGNS}.get(key, "Diagonal bands")


def render(design, pal, content, width_px=1654, aspect=1.4142):
    """The cover as a PNG (BytesIO)."""
    fn = _FUNCS.get(design) or design_bands
    ctx = Ctx(width_px, aspect, pal, normalise_content(content))
    fn(ctx)
    return ctx.finish()
