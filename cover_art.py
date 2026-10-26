"""The cover page of a client's Financial Statements: a full-page design of diagonal colour bands whose colours are taken from the
client's own logo, with the logo and name on a white corner.

How the colours are chosen (see `palette_for_client`):
  1. colours typed on the client page (Client.cover_primary / cover_accent) win, so a Partner can correct what the logo gave;
  2. otherwise the logo's two most prominent colours (greys, whites and near-blacks are ignored: they are rarely the brand colour);
     the main colour is darkened until white text on it is easy to read, the second colour is lightened or darkened until it stands out
     from the main one;
  3. a logo with one colour gets a lighter tint of it as the second colour; a black, grey or white logo - or no logo - gets the firm's
     own navy and gold.

The artwork is drawn with Pillow and returned as a PNG, sized to the page it will sit on (A4 or Letter), then placed behind the cover
text of the Word file (workpapers._cover_page). Nothing here decides anything about the accounts: it is presentation only.
"""
import colorsys
import io
import math
import os

from PIL import Image, ImageDraw, ImageFont

_HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(_HERE, "brand_assets", "fonts")
FIRM_NAVY = "0F2942"
FIRM_GOLD = "D4AF6A"
FIRM_NAME = "Neverlank"
FIRM_DESCRIPTOR = "Chartered Accountants"
MONTH_FMT = "%d %B %Y"

FRAMEWORK_LABELS = {"full_ifrs": "Full IFRS", "ifrs_for_smes": "IFRS for SMEs"}


# ------------------------------------------------------------------ colour helpers
def hex_to_rgb(h):
    h = (h or "").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        raise ValueError("not a colour")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(rgb):
    return "%02X%02X%02X" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


def valid_hex(h):
    try:
        hex_to_rgb(h)
        return True
    except (ValueError, TypeError):
        return False


def _lin(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb):
    r, g, b = (_lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _hsv(rgb):
    return colorsys.rgb_to_hsv(*(c / 255.0 for c in rgb))


def _from_hsv(h, s, v):
    return tuple(c * 255 for c in colorsys.hsv_to_rgb(h % 1.0, max(0, min(1, s)), max(0, min(1, v))))


def _mix(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def darken_until(rgb, against, ratio):
    """Make `rgb` darker (keeping its hue) until it reads against `against` at the given contrast ratio."""
    h, s, v = _hsv(rgb)
    for _ in range(40):
        cur = _from_hsv(h, s, v)
        if contrast(cur, against) >= ratio:
            return cur
        v -= 0.025
        if v <= 0.05:
            break
    return _from_hsv(h, s, max(v, 0.05))


def lighten_until(rgb, against, ratio):
    """Make `rgb` lighter until it stands out from `against` at the given contrast ratio."""
    h, s, v = _hsv(rgb)
    for _ in range(60):
        cur = _from_hsv(h, s, v)
        if contrast(cur, against) >= ratio:
            return cur
        if v < 1.0:
            v = min(1.0, v + 0.03)
        else:
            s = max(0.0, s - 0.03)
        if v >= 1.0 and s <= 0.0:
            break
    return _from_hsv(h, s, v)


# ------------------------------------------------------------------ colours from a logo
def logo_colours(path):
    """(main, second_or_None) as RGB tuples from a logo file, or (None, None) when the logo has no real colour
    (black, grey or white artwork), cannot be read, or is empty."""
    try:
        img = Image.open(path).convert("RGBA")
    except Exception:
        return None, None
    img.thumbnail((140, 140))
    px = list(img.getdata())
    opaque = [p for p in px if p[3] >= 128]
    if not opaque:
        return None, None
    groups = {}          # 15-degree hue bucket -> [weight, r, g, b] weighted sums
    coloured = 0
    for r, g, b, a in opaque:
        h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        if s < 0.22 or v < 0.18 or (v > 0.93 and s < 0.3):
            continue
        coloured += 1
        w = s * v
        k = int(h * 24) % 24
        g_ = groups.setdefault(k, [0.0, 0.0, 0.0, 0.0])
        g_[0] += w
        g_[1] += r * w
        g_[2] += g * w
        g_[3] += b * w
    if coloured < max(12, 0.02 * len(opaque)):
        return None, None

    def hue_gap(k1, k2):
        d = abs(k1 - k2) % 24
        return min(d, 24 - d)

    def merged(centre):
        tot = [0.0, 0.0, 0.0, 0.0]
        for k, g_ in groups.items():
            if hue_gap(k, centre) <= 1:
                for i in range(4):
                    tot[i] += g_[i]
        return tot

    # a bucket's strength includes its two neighbours, so one colour that straddles a boundary is not split in two
    strength = {k: merged(k)[0] for k in groups}
    main_k = max(strength, key=strength.get)
    m = merged(main_k)
    main = (m[1] / m[0], m[2] / m[0], m[3] / m[0])
    others = {k: v for k, v in strength.items() if hue_gap(k, main_k) >= 3}
    second = None
    if others:
        k2 = max(others, key=others.get)
        if others[k2] >= 0.12 * strength[main_k]:
            m2 = merged(k2)
            second = (m2[1] / m2[0], m2[2] / m2[0], m2[3] / m2[0])
    return main, second


def build_palette(main=None, second=None, source="logo"):
    """Turn up to two brand colours into the colours the cover uses. With no colour at all, the firm's own navy and gold."""
    white = (255, 255, 255)
    if main is None:
        main, second, source = hex_to_rgb(FIRM_NAVY), hex_to_rgb(FIRM_GOLD), "default"
    primary = darken_until(main, white, 4.8)
    if second is None:
        accent = lighten_until(_mix(primary, (255, 255, 255), 0.6), primary, 3.6)
    else:
        accent = second
        if contrast(accent, primary) < 2.6:
            # first give the accent room by deepening the main colour (white text still reads), then lighten the accent only if needed
            primary = darken_until(primary, accent, 2.6) if luminance(primary) < luminance(accent) else primary
            primary = darken_until(primary, white, 4.8)
        if contrast(accent, primary) < 2.6:
            lighter = lighten_until(accent, primary, 2.6)
            accent = lighter if contrast(lighter, primary) >= 2.6 else darken_until(accent, primary, 2.6)
    h, s, v = _hsv(primary)
    dark = _from_hsv(h, s * 0.85, v * 0.62)
    if contrast(dark, primary) < 1.25:
        dark = _from_hsv(h, s * 0.85, v * 0.45)
    return {"primary": rgb_to_hex(primary), "accent": rgb_to_hex(accent), "dark": rgb_to_hex(dark), "source": source}


def logo_path_for(client):
    from config import Config
    if client is not None and getattr(client, "logo_filename", None):
        cand = os.path.join(Config.CLIENT_LOGOS_DATA_DIR, client.logo_filename)
        if os.path.exists(cand):
            return cand
    return None


def palette_for_client(client):
    """The cover's colours for a client: colours typed on the client page first, then the logo, then the firm's own."""
    typed_main = getattr(client, "cover_primary", None)
    typed_second = getattr(client, "cover_accent", None)
    logo = logo_path_for(client)
    if valid_hex(typed_main):
        main = hex_to_rgb(typed_main)
        second = hex_to_rgb(typed_second) if valid_hex(typed_second) else (logo_colours(logo)[1] if logo else None)
        pal = build_palette(main, second, "typed")
    elif logo:
        main, second = logo_colours(logo)
        pal = build_palette(main, second, "logo") if main else build_palette()
        if valid_hex(typed_second) and main:
            pal = build_palette(main, hex_to_rgb(typed_second), "logo")
    else:
        pal = build_palette()
    # the colour the logo gave, before it was darkened for reading: shown next to the typed boxes on the client page
    return pal


# ------------------------------------------------------------------ geometry
def _clip(poly, a, b, c):
    """Keep the part of a polygon where a*x + b*y <= c (Sutherland-Hodgman, one edge)."""
    out = []
    n = len(poly)
    for i in range(n):
        p, q = poly[i], poly[(i + 1) % n]
        fp, fq = a * p[0] + b * p[1] - c, a * q[0] + b * q[1] - c
        if fp <= 0:
            out.append(p)
        if (fp < 0 < fq) or (fq < 0 < fp):
            t = fp / (fp - fq)
            out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
    return out


def _band(W, H, u0, u1):
    """The part of the page where u0 <= x + y <= u1 (a diagonal band running from lower-left to upper-right)."""
    poly = [(0, 0), (W, 0), (W, H), (0, H)]
    poly = _clip(poly, 1, 1, u1)
    poly = _clip(poly, -1, -1, -u0)
    return poly


def _font(name, size):
    for cand in (os.path.join(FONT_DIR, f"Poppins-{name}.ttf"), "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if name in ("Bold", "Medium") else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(cand, max(8, int(size)))
        except Exception:
            continue
    return ImageFont.load_default()


def _w(draw, text, font):
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def _fit(draw, text, name, max_w, max_size, min_size=10):
    size = max_size
    while size > min_size:
        f = _font(name, size)
        if _w(draw, text, f) <= max_w:
            return f
        size -= 2
    return _font(name, min_size)


def _wrap(draw, text, font, width_for_line, y, line_h):
    """Word-wrap into lines; `width_for_line(y_top)` says how wide a line starting at y may be (the white corner widens downwards)."""
    words, lines, cur = text.split(), [], ""
    cy = y
    for word in words:
        trial = (cur + " " + word).strip()
        if _w(draw, trial, font) <= width_for_line(cy) or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
            cy += line_h
    if cur:
        lines.append(cur)
    return lines


# ------------------------------------------------------------------ the page
def render_cover(client_name, period_end, pal, logo_path=None, reg_no=None, framework=None, firm_name=FIRM_NAME,
                 firm_descriptor=FIRM_DESCRIPTOR, width_px=1654, aspect=1.4142):
    """The cover as a PNG (BytesIO). `aspect` is page height / page width: 1.4142 for A4, 1.2941 for Letter."""
    W = int(width_px)
    H = int(round(W * aspect))
    SS = 2
    primary, accent, dark = (hex_to_rgb(pal[k]) for k in ("primary", "accent", "dark"))
    T = W + H
    big = Image.new("RGB", (W * SS, H * SS), primary)

    def poly_fill(poly, colour, alpha=1.0):
        pts = [(x * SS, y * SS) for x, y in poly]
        if len(pts) < 3:
            return
        if alpha >= 1.0:
            ImageDraw.Draw(big).polygon(pts, fill=colour)
            return
        mask = Image.new("L", big.size, 0)
        ImageDraw.Draw(mask).polygon(pts, fill=int(255 * alpha))
        big.paste(Image.new("RGB", big.size, colour), (0, 0), mask)

    # soft lighter stripes across the main field
    for u0, u1, al in ((0.45, 0.545, 0.12), (0.58, 0.70, 0.07), (0.74, 0.79, 0.10)):
        poly_fill(_band(W, H, u0 * T, u1 * T), (255, 255, 255), al)
    # the darker corner and the accent band
    poly_fill(_band(W, H, -1, 0.22 * T), dark)
    poly_fill(_band(W, H, 0.22 * T, 0.31 * T), accent)
    # thin white lines
    def seg(u, x0, x1, thick=3):
        y0, y1 = u - x0, u - x1
        d = ImageDraw.Draw(big)
        d.line([(x0 * SS, y0 * SS), (x1 * SS, y1 * SS)], fill=(255, 255, 255), width=thick * SS)
    seg(0.185 * T, 0.03 * W, 0.19 * W)
    seg(0.36 * T, 0.50 * W, 0.70 * W)
    # the white corner
    d0 = 0.40 * H
    poly_fill([(0, d0), (0, H), (H - d0, H)], (255, 255, 255))
    img = big.resize((W, H), Image.LANCZOS)
    del big
    draw = ImageDraw.Draw(img)
    u = W / 100.0
    white = (255, 255, 255)
    right = W - 7 * u

    # top left: the firm
    draw.text((7 * u, 5.5 * u), firm_name, font=_font("Bold", 3.4 * u), fill=white)
    draw.line([(7 * u, 10.2 * u), (7 * u + 12 * u, 10.2 * u)], fill=white, width=max(2, int(0.18 * u)))
    draw.text((7 * u, 11.4 * u), firm_descriptor, font=_font("Regular", 1.9 * u), fill=white)

    # top right: the framework
    if framework:
        f1, f2 = _font("Light", 1.7 * u), _font("Light", 3.0 * u)
        label = "Reporting framework"
        draw.text((right - _w(draw, label, f1), 5.5 * u), label, font=f1, fill=_mix(white, primary, 0.25))
        draw.text((right - _w(draw, framework, f2), 8.4 * u), framework, font=f2, fill=_mix(white, primary, 0.25))

    # the title block, right-aligned
    max_w = 56 * u
    y = 0.385 * H
    f_fin = _fit(draw, "FINANCIAL", "Bold", max_w, 9.4 * u)
    f_sta = _fit(draw, "STATEMENTS", "Bold", max_w, 9.4 * u)
    size = min(f_fin.size, f_sta.size)
    f_fin, f_sta = _font("Bold", size), _font("Bold", size)
    for text, font, colour in (("FINANCIAL", f_fin, accent), ("STATEMENTS", f_sta, white)):
        bb = draw.textbbox((0, 0), text, font=font)
        draw.text((right - (bb[2] - bb[0]) - bb[0], y - bb[1]), text, font=font, fill=colour)
        y += (bb[3] - bb[1]) + 2.2 * u
    y += 1.2 * u
    draw.line([(right - max_w, y), (right, y)], fill=white, width=max(2, int(0.14 * u)))
    y += 2.4 * u
    year = str(period_end.year) if period_end else ""
    if year:
        f_year = _fit(draw, year, "Light", max_w, 17 * u)
        bb = draw.textbbox((0, 0), year, font=f_year)
        draw.text((right - (bb[2] - bb[0]) - bb[0], y - bb[1]), year, font=f_year, fill=white)
        y += (bb[3] - bb[1]) + 3.2 * u
    sub = f"for the year ended {period_end.strftime(MONTH_FMT)}" if period_end else "for the year ended ____________"
    f_sub = _fit(draw, sub, "Regular", max_w, 2.6 * u)
    draw.text((right - _w(draw, sub, f_sub), y), sub, font=f_sub, fill=white)

    # the white corner: logo, name, particulars
    x0 = 7 * u
    gap = 3.5 * u

    def bound(yy):
        return max(8 * u, yy - d0 - gap)        # the corner's hypotenuse, less a margin, at row yy

    y = d0 + (0.40 if logo_path else 0.50) * (H - d0)
    if logo_path:
        try:
            lg = Image.open(logo_path).convert("RGBA")
            box_w = min(38 * u, bound(y) - x0)
            box_h = 0.13 * H
            ratio = min(box_w / lg.width, box_h / lg.height)
            lg = lg.resize((max(1, int(lg.width * ratio)), max(1, int(lg.height * ratio))), Image.LANCZOS)
            img.paste(lg, (int(x0), int(y)), lg)
            y += lg.height + 3.0 * u
        except Exception:
            pass
    name = (client_name or "").strip()
    f_name = _font("Bold", (3.4 if logo_path else 3.9) * u)
    line_h = f_name.size * 1.28
    for line in _wrap(draw, name, f_name, lambda yy: bound(yy) - x0, y, line_h)[:4]:
        draw.text((x0, y), line, font=f_name, fill=primary)
        y += line_h
    y += 1.6 * u
    f_det = _font("Regular", 1.85 * u)
    det_h = f_det.size * 1.55
    particulars = []
    if reg_no:
        particulars.append(f"Registration no. {reg_no}")
    for text in particulars:
        for line in _wrap(draw, text, f_det, lambda yy: bound(yy) - x0 - 2.2 * u, y, det_h):
            draw.text((x0, y), "•", font=f_det, fill=accent if contrast(accent, (255, 255, 255)) >= 2 else primary)
            draw.text((x0 + 2.2 * u, y), line, font=f_det, fill=(70, 80, 95))
            y += det_h
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    out.seek(0)
    return out


def render_for_engagement(engagement, aspect=1.4142, width_px=1654):
    client = engagement.client
    pal = palette_for_client(client)
    return render_cover(
        client.name if client else "", engagement.period_end, pal, logo_path=logo_path_for(client),
        reg_no=(client.company_number if client else None),
        framework=FRAMEWORK_LABELS.get(getattr(engagement, "reporting_framework", None)),
        aspect=aspect, width_px=width_px,
    ), pal
