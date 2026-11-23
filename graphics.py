"""Branded graphics for social media posts, drawn with Pillow (no outside service, no cost per picture).

Claude does not paint pictures; it writes the words. This module takes a small description of a graphic (a "spec": the style, the colours and the short
texts) and draws it in the firm's navy and gold with its logo, at the exact size each platform wants. The same spec always gives the same picture, so only the
spec is stored and the PNG is drawn when it is asked for.

Styles: deadline (a date to remember), quote, tip, stat (one big figure), banner (event or offer), carousel (cover, one slide per point, closing slide).
Text never overflows: each block is made as large as fits, and if even the smallest size does not fit, the picture says so (`problems`) and the post cannot be approved.
"""
import io
import re
import os
import zipfile

from PIL import Image, ImageDraw, ImageFont

_HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(_HERE, "brand_assets", "fonts")
LOGO_PATH = os.path.join(_HERE, "static", "img", "neverlank-logo.png")

NAVY = (15, 41, 66)
GOLD = (212, 175, 106)
IVORY = (246, 241, 227)
INK = (19, 32, 58)
GOLD_TEXT = (122, 95, 6)          # gold dark enough to read on ivory

THEMES = {
    "navy": dict(label="Navy and gold", bg=NAVY, text=IVORY, muted=(201, 211, 227), accent=GOLD, accent_text=NAVY, fill=GOLD, deco=GOLD),
    "ivory": dict(label="Ivory", bg=IVORY, text=NAVY, muted=(74, 88, 115), accent=GOLD_TEXT, accent_text=NAVY, fill=GOLD, deco=GOLD),
    "gold": dict(label="Gold", bg=GOLD, text=NAVY, muted=(42, 58, 85), accent=NAVY, accent_text=GOLD, fill=NAVY, deco=NAVY),
}

# key: label, width, height, area at the top and bottom that the app (Stories, Reels) covers
SIZES = {
    "linkedin": ("LinkedIn post (landscape)", 1200, 627, (0, 0)),
    "linkedin_portrait": ("LinkedIn post (portrait)", 1080, 1350, (0, 0)),
    "x": ("X (Twitter) post", 1600, 900, (0, 0)),
    "facebook": ("Facebook post", 1200, 630, (0, 0)),
    "instagram": ("Instagram post (square)", 1080, 1080, (0, 0)),
    "instagram_portrait": ("Instagram post (portrait)", 1080, 1350, (0, 0)),
    "story": ("Story, Reel cover, WhatsApp Status", 1080, 1920, (200, 300)),
    "youtube": ("YouTube thumbnail", 1280, 720, (0, 0)),
}
PLATFORM_SIZE = {"linkedin": "linkedin", "x": "x", "facebook": "facebook", "instagram": "instagram_portrait", "youtube": "youtube", "tiktok": "story", "whatsapp": "story"}

STYLES = {
    "deadline": "Deadline card (a date to remember)",
    "tip": "Tip or 'did you know'",
    "quote": "Quote",
    "stat": "One big figure",
    "banner": "Banner (event, offer, announcement)",
    "carousel": "Carousel (swipe through points)",
}
# what each style asks for: (field, label, long box?, short help)
STYLE_FIELDS = {
    "deadline": [("kicker", "Small heading", False, "e.g. COMPLIANCE REMINDER"), ("date_label", "The date", False, "e.g. 30 NOV (14 characters at most)"), ("title", "Headline", True, "what is due"),
                 ("body", "Explanation", True, "one or two short sentences"), ("cta", "Button text", False, "e.g. Get the free calendar")],
    "tip": [("kicker", "Small heading", False, "e.g. TAX TIP"), ("title", "Headline", True, ""), ("body", "Explanation", True, "or use the points below"), ("points", "Points (one per line, up to 4)", True, "")],
    "quote": [("kicker", "Small heading", False, "optional"), ("title", "The quote", True, "the exact words, as said or written"), ("attribution", "Who said it", False, "e.g. Petros Piki, Managing Partner")],
    "stat": [("kicker", "Small heading", False, ""), ("big_value", "The figure", False, "e.g. 3 in 5 (14 characters at most)"), ("caption", "What it means", True, ""), ("source", "Source", False, "where the figure comes from")],
    "banner": [("kicker", "Small heading", False, "e.g. FREE FOR BUSINESS OWNERS"), ("title", "Headline", True, ""), ("body", "Supporting line", True, ""), ("cta", "Button text", False, "")],
    "carousel": [("kicker", "Cover: small heading", False, ""), ("title", "Cover: headline", True, ""), ("body", "Cover: supporting line", True, ""), ("cta", "Last slide: headline", False, "e.g. Get your free 2027 Compliance Calendar"),
                 ("caption", "Last slide: line under it", False, "e.g. Link in our profile")],
}
LIMITS = {"kicker": 40, "title": 110, "body": 280, "date_label": 14, "cta": 60, "attribution": 80, "big_value": 14, "caption": 160, "source": 100, "footer": 60}
MAX_POINTS, MAX_SLIDES = 4, 8
SLIDE_LIMITS = {"title": 70, "body": 200}
DEFAULT_FOOTER = "www.neverlank.co.zw"
PAAB_LINE = "PAAB Firm No. Z10110"
FIRM = "NEVERLANK"
FIRM_DESC = "Chartered Accountants"


# ------------------------------------------------------------------ the description of a graphic

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _line(value, limit):
    value = _CONTROL.sub("", str(value or ""))
    return " ".join(value.replace("\r", " ").replace("\n", " ").split())[:limit]


def _para(value, limit):
    value = _CONTROL.sub("", str(value or ""))
    text = "\n".join(" ".join(part.split()) for part in value.replace("\r", "\n").split("\n"))
    return text.strip()[:limit]


def clean_spec(raw, size_key=None):
    """A safe, complete spec from whatever was typed or drafted: unknown styles, themes and sizes fall back to the defaults, every text is cut to its limit."""
    raw = raw if isinstance(raw, dict) else {}
    spec = {"style": raw.get("style") if raw.get("style") in STYLES else "tip", "theme": raw.get("theme") if raw.get("theme") in THEMES else "navy",
            "size": size_key if size_key in SIZES else (raw.get("size") if raw.get("size") in SIZES else "linkedin")}
    for field, limit in LIMITS.items():
        spec[field] = (_para if field in ("body", "caption", "title") else _line)(raw.get(field), limit)
    points = raw.get("points") or []
    if isinstance(points, str):
        points = points.split("\n")
    spec["points"] = [_line(p, 100) for p in points if _line(p, 100)][:MAX_POINTS]
    slides = []
    for s in (raw.get("slides") or [])[:MAX_SLIDES]:
        if isinstance(s, dict) and (_line(s.get("title"), 70) or _para(s.get("body"), 200)):
            slides.append({"title": _line(s.get("title"), SLIDE_LIMITS["title"]), "body": _para(s.get("body"), SLIDE_LIMITS["body"])})
    spec["slides"] = slides
    spec["paab"] = bool(raw.get("paab", True))
    spec["footer"] = spec["footer"] or DEFAULT_FOOTER
    return spec


def slide_count(spec):
    if spec.get("style") != "carousel":
        return 1
    return 1 + len(spec.get("slides") or []) + (1 if spec.get("cta") else 0)


def text_of(spec):
    """All the words a person will read on the graphic (what the automatic checks and the approver look at)."""
    parts = [spec.get(f) for f in ("kicker", "date_label", "title", "body", "cta", "attribution", "big_value", "caption", "source")] + list(spec.get("points") or [])
    for s in spec.get("slides") or []:
        parts += [s.get("title"), s.get("body")]
    return "\n".join(p for p in parts if p)


def alt_text(spec):
    bits = [spec.get("title") or spec.get("big_value") or "", spec.get("body") or spec.get("caption") or ""]
    return ("Neverlank graphic: " + ". ".join(b for b in bits if b))[:300]


def is_blank(spec):
    return not text_of(spec).strip()


# ------------------------------------------------------------------ drawing helpers

def _font(weight, size):
    for cand in (os.path.join(FONT_DIR, f"Poppins-{weight}.ttf"),
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if weight in ("Bold", "Medium") else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(cand, max(8, int(size)))
        except Exception:
            continue
    return ImageFont.load_default()


def _wrap(draw, text, font, max_w):
    lines = []
    for para in str(text).split("\n"):
        words, cur = para.split(), ""
        for word in words:
            trial = (cur + " " + word).strip()
            if draw.textlength(trial, font=font) <= max_w:
                cur = trial
                continue
            if cur:
                lines.append(cur)
            while draw.textlength(word, font=font) > max_w and len(word) > 1:      # a word wider than the line is broken
                cut = len(word) - 1
                while cut > 1 and draw.textlength(word[:cut], font=font) > max_w:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            cur = word
        lines.append(cur)
    return lines or [""]


def _spaced(draw, xy, text, font, fill, spacing):
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + spacing
    return x


def _spaced_width(draw, text, font, spacing):
    return sum(draw.textlength(ch, font=font) + spacing for ch in text) - spacing if text else 0


def _logo(size):
    try:
        im = Image.open(LOGO_PATH).convert("RGBA")
        return im.resize((size, size), Image.LANCZOS)
    except Exception:
        return None


class Canvas:
    def __init__(self, spec, slide=0):
        self.spec = spec
        label, self.W, self.H, (self.safe_top, self.safe_bottom) = SIZES[spec["size"]]
        self.theme = THEMES[spec["theme"]]
        self.u = (self.W * self.H) ** 0.5 / 1080.0
        if self.H / self.W > 1.5:
            self.u *= 1.2                       # tall formats (Stories) get bigger type
        self.m = int(self.W * 0.07) if self.W <= self.H else int(self.H * 0.1)
        self.img = Image.new("RGB", (self.W, self.H), self.theme["bg"])
        self.d = ImageDraw.Draw(self.img)
        self.problems = []
        self.slide = slide
        self._decorate()

    # --- decoration and the fixed parts
    def _decorate(self):
        layer = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        r = int(min(self.W, self.H) * 0.62)
        cx, cy = self.W - int(self.W * 0.04), int(self.safe_top + self.H * 0.08)
        deco = self.theme["deco"]
        for k, a in ((1.0, 34), (0.78, 26), (0.56, 18)):
            rr = int(r * k)
            ld.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), outline=deco + (a,), width=max(2, int(3 * self.u)))
        self.img.paste(layer, (0, 0), layer)
        self.d = ImageDraw.Draw(self.img)
        self.d.rectangle((self.m, self.safe_top + int(self.m * 0.55), self.m + int(96 * self.u), self.safe_top + int(self.m * 0.55) + max(5, int(9 * self.u))), fill=self.theme["fill"])

    def footer(self):
        """Logo, the firm's name, the website and (optionally) the PAAB number along the bottom. Returns the y where the content must stop."""
        fh = int(min(self.W, self.H) * 0.125)
        y0 = self.H - self.safe_bottom - self.m // 2 - fh
        self.d.line((self.m, y0, self.W - self.m, y0), fill=self.theme["fill"], width=max(2, int(3 * self.u)))
        ls = int(fh * 0.8)
        logo = _logo(ls)
        ty = y0 + (fh - ls) // 2 + int(fh * 0.1)
        x = self.m
        if logo is not None:
            self.img.paste(logo, (x, ty), logo)
            x += ls + int(16 * self.u)
        f1, f2 = _font("Bold", 30 * self.u), _font("Regular", 22 * self.u)
        self._spaced_text(x, ty + int(ls * 0.12), FIRM, f1, self.theme["text"], 4 * self.u)
        self.d.text((x, ty + int(ls * 0.12) + 38 * self.u), FIRM_DESC, font=f2, fill=self.theme["muted"])
        right = self.W - self.m
        f3 = _font("Medium", 24 * self.u)
        site = self.spec.get("footer") or DEFAULT_FOOTER
        self.d.text((right, ty + int(ls * 0.12)), site, font=f3, fill=self.theme["text"], anchor="ra")
        if self.spec.get("paab"):
            self.d.text((right, ty + int(ls * 0.12) + 36 * self.u), PAAB_LINE, font=_font("Regular", 20 * self.u), fill=self.theme["muted"], anchor="ra")
        return y0 - int(self.m * 0.35)

    def _spaced_text(self, x, y, text, font, fill, spacing):
        _spaced(self.d, (x, y), text, font, fill, spacing)

    def area(self):
        top = self.safe_top + int(self.m * 0.55) + int(24 * self.u) + max(5, int(9 * self.u))
        return top, self.footer()

    # --- a stack of blocks, centred in the free space, every text as large as fits
    def stack(self, items, x0=None, width=None, align="left", top=None, bottom=None, centre=True):
        a_top, a_bottom = self.area() if top is None else (top, bottom)
        x0 = self.m if x0 is None else x0
        width = (self.W - 2 * self.m) if width is None else width
        avail = a_bottom - a_top
        for scale in [1.0 - 0.04 * i for i in range(0, 16)]:
            prepared, total, ok = [], 0, True
            for it in items:
                p = self._prepare(it, width, scale)
                prepared.append(p)
                total += p["h"] + p.get("gap", 0)
            total -= prepared[-1].get("gap", 0) if prepared else 0
            if total <= avail:
                break
        else:
            ok = False
        if not ok:
            self.problems.append("The text is too long to fit on this graphic. Shorten it.")
        y = a_top + (max(0, (avail - total) // 2) if centre else 0)
        y = max(a_top, y - int(avail * 0.04)) if centre else y
        for p in prepared:
            self._draw(p, y, x0, width, align)
            y += p["h"] + p.get("gap", 0)

    def _prepare(self, it, width, scale):
        kind = it["kind"]
        u = self.u
        if kind == "spacer":
            return {"kind": kind, "h": int(it["h"] * u * scale), "gap": 0}
        text = it.get("text", "")
        size = max(it.get("min", 18) * u, it["size"] * u * scale)
        weight = it.get("weight", "Regular")
        gap = int(it.get("gap", 20) * u * scale)
        if kind == "pill":
            font = _font(weight, size)
            tw = self.d.textlength(text, font=font)
            padx, pady = int(size * 0.9), int(size * 0.55)
            lines = _wrap(self.d, text, font, int(width - 2 * padx))
            w = int(min(width, max(self.d.textlength(l, font=font) for l in lines) + 2 * padx + 2))
            lh = int(size * 1.25)
            return {"kind": kind, "font": font, "lines": lines, "w": w, "h": lh * len(lines) + 2 * pady, "lh": lh, "padx": padx, "pady": pady, "gap": gap, "color": it["color"], "fill": it["fill"]}
        if kind == "bullets":
            font = _font(weight, size)
            ind = int(size * 1.1)
            rows, h = [], 0
            lh = int(size * 1.3)
            for t in it["items"]:
                lines = _wrap(self.d, t, font, width - ind)
                rows.append(lines)
                h += lh * len(lines) + int(size * 0.45)
            return {"kind": kind, "font": font, "rows": rows, "h": h - int(size * 0.45), "lh": lh, "ind": ind, "size": size, "gap": gap, "color": it["color"], "mark": it["mark"]}
        font = _font(weight, size)
        spacing = it.get("spacing", 0) * u
        if spacing:
            lines = [text]
            w = _spaced_width(self.d, text, font, spacing)
            if w > width:
                lines = _wrap(self.d, text, font, width)
        else:
            lines = _wrap(self.d, text, font, width)
        lh = int(size * it.get("leading", 1.28))
        return {"kind": "text", "font": font, "lines": lines, "h": lh * len(lines), "lh": lh, "gap": gap, "color": it["color"], "spacing": spacing, "size": size}

    def _draw(self, p, y, x0, width, align):
        d = self.d
        if p["kind"] == "spacer":
            return
        if p["kind"] == "text":
            for i, line in enumerate(p["lines"]):
                lw = _spaced_width(d, line, p["font"], p["spacing"]) if p["spacing"] else d.textlength(line, font=p["font"])
                x = x0 if align == "left" else x0 + (width - lw) / 2
                ty = y + i * p["lh"]
                if p["spacing"]:
                    _spaced(d, (x, ty), line, p["font"], p["color"], p["spacing"])
                else:
                    d.text((x, ty), line, font=p["font"], fill=p["color"])
        elif p["kind"] == "pill":
            x = x0 if align == "left" else x0 + (width - p["w"]) / 2
            d.rounded_rectangle((x, y, x + p["w"], y + p["h"]), radius=p["h"] // 2, fill=p["fill"])
            for i, line in enumerate(p["lines"]):
                lw = d.textlength(line, font=p["font"])
                d.text((x + (p["w"] - lw) / 2, y + p["pady"] + i * p["lh"]), line, font=p["font"], fill=p["color"])
        elif p["kind"] == "bullets":
            yy = y
            sq = int(p["size"] * 0.42)
            for lines in p["rows"]:
                d.rectangle((x0, yy + int(p["lh"] * 0.34), x0 + sq, yy + int(p["lh"] * 0.34) + sq), fill=p["mark"])
                for i, line in enumerate(lines):
                    d.text((x0 + p["ind"], yy + i * p["lh"]), line, font=p["font"], fill=p["color"])
                yy += p["lh"] * len(lines) + int(p["size"] * 0.45)

    def png(self):
        out = io.BytesIO()
        self.img.save(out, format="PNG", optimize=True)
        return out.getvalue()


# ------------------------------------------------------------------ the styles

def _kicker(c, text, gap=26):
    return {"kind": "text", "text": text.upper(), "size": 28, "min": 16, "weight": "Bold", "color": c.theme["accent"], "gap": gap, "spacing": 3.5} if text else None


def _pill(c, text):
    return {"kind": "pill", "text": text, "size": 34, "min": 18, "weight": "Bold", "color": c.theme["accent_text"], "fill": c.theme["fill"], "gap": 0} if text else None


def _items(*items):
    return [i for i in items if i]


def _deadline(c, s):
    t = c.theme
    items = _items(_kicker(c, s["kicker"]),
                   {"kind": "pill", "text": s["date_label"].upper(), "size": 74, "min": 30, "weight": "Bold", "color": t["accent_text"], "fill": t["fill"], "gap": 30} if s["date_label"] else None,
                   {"kind": "text", "text": s["title"], "size": 80, "min": 34, "weight": "Bold", "color": t["text"], "gap": 24, "leading": 1.18} if s["title"] else None,
                   {"kind": "text", "text": s["body"], "size": 38, "min": 20, "weight": "Regular", "color": t["muted"], "gap": 34} if s["body"] else None,
                   _pill(c, s["cta"]))
    c.stack(items)


def _tip(c, s):
    t = c.theme
    items = _items(_kicker(c, s["kicker"]),
                   {"kind": "text", "text": s["title"], "size": 72, "min": 32, "weight": "Bold", "color": t["text"], "gap": 28, "leading": 1.18} if s["title"] else None,
                   {"kind": "text", "text": s["body"], "size": 40, "min": 20, "weight": "Regular", "color": t["muted"], "gap": 26} if s["body"] else None,
                   {"kind": "bullets", "items": s["points"], "size": 40, "min": 20, "weight": "Medium", "color": t["text"], "mark": t["fill"], "gap": 0} if s["points"] else None)
    c.stack(items)


def _quote(c, s):
    t = c.theme
    items = _items(_kicker(c, s["kicker"], gap=10),
                   {"kind": "text", "text": "“", "size": 150, "min": 60, "weight": "Bold", "color": t["fill"], "gap": -20, "leading": 0.9},
                   {"kind": "text", "text": s["title"], "size": 64, "min": 28, "weight": "Medium", "color": t["text"], "gap": 36, "leading": 1.3} if s["title"] else None,
                   {"kind": "text", "text": "— " + s["attribution"], "size": 32, "min": 18, "weight": "Bold", "color": t["accent"], "gap": 0} if s["attribution"] else None)
    c.stack(items)


def _stat(c, s):
    t = c.theme
    items = _items(_kicker(c, s["kicker"]),
                   {"kind": "text", "text": s["big_value"], "size": 230, "min": 70, "weight": "Bold", "color": t["accent"], "gap": 14, "leading": 1.0} if s["big_value"] else None,
                   {"kind": "text", "text": s["caption"], "size": 52, "min": 24, "weight": "Medium", "color": t["text"], "gap": 24, "leading": 1.22} if s["caption"] else None,
                   {"kind": "text", "text": ("Source: " + s["source"]) if s["source"] else "", "size": 26, "min": 16, "weight": "Light", "color": t["muted"], "gap": 0} if s["source"] else None)
    c.stack(items)


def _banner(c, s):
    t = c.theme
    landscape = c.W / c.H > 1.3
    items = _items(_kicker(c, s["kicker"]),
                   {"kind": "text", "text": s["title"], "size": 76, "min": 32, "weight": "Bold", "color": t["text"], "gap": 24, "leading": 1.16} if s["title"] else None,
                   {"kind": "text", "text": s["body"], "size": 38, "min": 20, "weight": "Regular", "color": t["muted"], "gap": 32} if s["body"] else None,
                   _pill(c, s["cta"]))
    if landscape:
        top, bottom = c.area()
        ls = int(min(c.H * 0.46, c.W * 0.24))
        logo = _logo(ls)
        text_w = c.W - 2 * c.m - ls - int(c.m * 0.8)
        c.stack(items, width=text_w, top=top, bottom=bottom)
        if logo is not None:
            c.img.paste(logo, (c.W - c.m - ls, top + (bottom - top - ls) // 2), logo)
    else:
        c.stack(items)


def _carousel(c, s):
    t = c.theme
    n_slides = len(s["slides"])
    i = c.slide
    if i == 0:
        c.stack(_items(_kicker(c, s["kicker"]),
                       {"kind": "text", "text": s["title"], "size": 84, "min": 34, "weight": "Bold", "color": t["text"], "gap": 26, "leading": 1.16} if s["title"] else None,
                       {"kind": "text", "text": s["body"], "size": 40, "min": 20, "weight": "Regular", "color": t["muted"], "gap": 34} if s["body"] else None,
                       {"kind": "text", "text": "SWIPE  >>", "size": 30, "min": 18, "weight": "Bold", "color": t["accent"], "gap": 0, "spacing": 4} if n_slides else None))
    elif i <= n_slides:
        sl = s["slides"][i - 1]
        c.stack(_items({"kind": "text", "text": f"{i:02d}", "size": 150, "min": 60, "weight": "Bold", "color": t["accent"], "gap": 10, "leading": 1.0},
                       {"kind": "text", "text": sl["title"], "size": 64, "min": 30, "weight": "Bold", "color": t["text"], "gap": 24, "leading": 1.16} if sl["title"] else None,
                       {"kind": "text", "text": sl["body"], "size": 42, "min": 20, "weight": "Regular", "color": t["muted"], "gap": 0} if sl["body"] else None))
    else:
        c.stack(_items(_kicker(c, s["kicker"]),
                       {"kind": "text", "text": s["cta"], "size": 74, "min": 32, "weight": "Bold", "color": t["text"], "gap": 28, "leading": 1.16},
                       {"kind": "text", "text": s["caption"], "size": 40, "min": 20, "weight": "Medium", "color": t["accent"], "gap": 0} if s["caption"] else None))


_DRAW = {"deadline": _deadline, "tip": _tip, "quote": _quote, "stat": _stat, "banner": _banner, "carousel": _carousel}


def render(spec, slide=0):
    """(PNG bytes, problems) for one picture of the graphic. `slide` counts from 0 and only matters for a carousel."""
    s = clean_spec(spec)
    slide = max(0, min(int(slide or 0), slide_count(s) - 1))
    c = Canvas(s, slide)
    _DRAW[s["style"]](c, s)
    return c.png(), c.problems


_PROBLEM_CACHE = {}


def problems(spec):
    """Everything that is wrong with the graphic, for all its slides (remembered for the same spec, because checking means drawing every slide)."""
    import json
    s = clean_spec(spec)
    key = json.dumps(s, sort_keys=True)
    if key not in _PROBLEM_CACHE:
        if len(_PROBLEM_CACHE) > 300:
            _PROBLEM_CACHE.clear()
        _PROBLEM_CACHE[key] = _problems(s)
    return list(_PROBLEM_CACHE[key])


def _problems(s):
    found = []
    if is_blank(s):
        found.append("The graphic has no text yet.")
    if s["style"] == "carousel" and not s["slides"]:
        found.append("A carousel needs at least one slide.")
    for i in range(slide_count(s)):
        found += [p for p in render(s, i)[1] if p not in found]
    return found


def at_size(spec, size_key):
    """The same graphic drawn at another platform's size (the words and colours stay; the layout refits)."""
    return clean_spec(dict(spec, size=size_key if size_key in SIZES else spec.get("size")))


def size_name(size_key):
    return SIZES[size_key][0].split(" (")[0]


def zip_all_sizes(spec, base="neverlank", sizes=None):
    """One zip with the graphic at every platform size (or the given sizes): neverlank-<name>-<size>[-NN].png"""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for key in (sizes or list(SIZES)):
            s = at_size(spec, key)
            n = slide_count(s)
            for i in range(n):
                z.writestr(f"{base}-{key.replace('_', '-')}" + (f"-{i + 1:02d}" if n > 1 else "") + ".png", render(s, i)[0])
    return out.getvalue()


def zip_of(spec, base="neverlank"):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for i in range(slide_count(spec)):
            z.writestr(f"{base}-{i + 1:02d}.png", render(spec, i)[0])
    return out.getvalue()


def contrast(fg, bg):
    def lum(rgb):
        out = []
        for ch in rgb:
            ch = ch / 255.0
            out.append(ch / 12.92 if ch <= 0.03928 else ((ch + 0.055) / 1.055) ** 2.4)
        return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]
    a, b = lum(fg), lum(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)
