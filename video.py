"""Short captioned videos for TikTok and YouTube Shorts, drawn by the app in the firm's navy and gold (no outside video service, no cost per video).

What a video is: a hook (the opening line), two to five short scenes, and a closing call to action. Each scene is a still frame in the same style as the graphics
(logo, footer, the colours) with big captions that fade in and out; a thin gold bar along the top shows the progress. The video is silent with words on the screen, as most
business short videos are (music or a voice can be added in TikTok or YouTube). It is 1080 x 1920, 24 frames a second, H.264 with a silent sound track, 12 to 45 seconds,
which both TikTok and YouTube Shorts accept.

How it is made: the description of the video (the words and the colours) is the only thing stored in the database. The MP4 is made on request, in the background, and kept
on the server's disk. The picture of each scene is drawn once and the frames in between are made by fading, so a 30 second video takes only a short while.

The AI (see social_video.py) writes only the words; it never draws.
"""
import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile

from PIL import Image, ImageChops, ImageDraw

import graphics as G

W, H = G.SIZES["story"][1:3]            # 1080 x 1920
FPS = 24
LIMITS = {"kicker": 40, "hook": 80, "cta": 60, "caption": 120, "footer": 60}
SCENE_LIMITS = {"title": 60, "body": 140}
MIN_SCENES, MAX_SCENES = 2, 5
FADE_IN, FADE_OUT = 0.45, 0.3           # seconds


# ------------------------------------------------------------------ the description

def clean_spec(raw):
    raw = raw if isinstance(raw, dict) else {}
    spec = {"theme": raw.get("theme") if raw.get("theme") in G.THEMES else "navy"}
    for field, limit in LIMITS.items():
        spec[field] = (G._para if field in ("hook", "caption") else G._line)(raw.get(field), limit)
    scenes = []
    for s in (raw.get("scenes") or [])[:MAX_SCENES]:
        if isinstance(s, dict):
            title, body = G._line(s.get("title"), SCENE_LIMITS["title"]), G._para(s.get("body"), SCENE_LIMITS["body"])
            if title or body:
                scenes.append({"title": title, "body": body})
    spec["scenes"] = scenes
    spec["paab"] = bool(raw.get("paab", True))
    spec["footer"] = spec["footer"] or G.DEFAULT_FOOTER
    return spec


def text_of(spec):
    parts = [spec.get("kicker"), spec.get("hook")]
    for s in spec.get("scenes") or []:
        parts += [s.get("title"), s.get("body")]
    parts += [spec.get("cta"), spec.get("caption")]
    return "\n".join(p for p in parts if p)


def is_blank(spec):
    return not text_of(spec).strip()


def alt_text(spec):
    return ("Neverlank video: " + (spec.get("hook") or (spec["scenes"][0]["title"] if spec.get("scenes") else "")))[:300]


def key_of(spec):
    """A fingerprint of everything that changes what the video looks like (also the version of the drawing code)."""
    return hashlib.sha256((json.dumps(clean_spec(spec), sort_keys=True) + f"|{W}x{H}|v1").encode()).hexdigest()[:32]


# ------------------------------------------------------------------ the scenes and their timing

def _seconds(text, low, high):
    words = len(text.split())
    return max(low, min(high, 1.6 + words / 2.8))          # about three words a second, plus a moment to take it in


def scenes_of(spec):
    """[{kind, title, body, seconds}] in order: the hook, the scenes, the closing."""
    s = clean_spec(spec)
    out = []
    if s["hook"] or s["kicker"]:
        out.append({"kind": "hook", "title": s["hook"], "body": "", "seconds": _seconds(s["hook"], 2.5, 4.5)})
    n = len(s["scenes"])
    for i, sc in enumerate(s["scenes"]):
        out.append({"kind": "step", "title": sc["title"], "body": sc["body"], "n": i + 1, "of": n, "seconds": _seconds(sc["title"] + " " + sc["body"], 3.0, 7.0)})
    if s["cta"] or s["caption"]:
        out.append({"kind": "cta", "title": s["cta"], "body": s["caption"], "seconds": _seconds(s["cta"] + " " + s["caption"], 3.0, 5.0)})
    return out


def total_seconds(spec):
    return round(sum(sc["seconds"] for sc in scenes_of(spec)), 1)


def problems(spec):
    s = clean_spec(spec)
    found = []
    if is_blank(s):
        found.append("The video has no text yet.")
    if len(s["scenes"]) < MIN_SCENES:
        found.append(f"A video needs at least {MIN_SCENES} scenes.")
    total = total_seconds(s)
    if s["scenes"] and total < 12:
        found.append("The video would be under 12 seconds. Add words or a scene.")
    for i, sc in enumerate(scenes_of(s)):
        base, full, box, probs = _scene_images(s, sc)
        found += [p for p in probs if p not in found]
    return found


# ------------------------------------------------------------------ drawing one scene

def _canvas(s):
    gspec = G.clean_spec({"style": "tip", "theme": s["theme"], "size": "story", "paab": s["paab"], "footer": s["footer"]})
    return G.Canvas(gspec, 0)


def _items(c, s, sc):
    t = c.theme
    if sc["kind"] == "hook":
        return G._items(G._kicker(c, s["kicker"]),
                        {"kind": "text", "text": sc["title"], "size": 92, "min": 40, "weight": "Bold", "color": t["text"], "gap": 20, "leading": 1.14} if sc["title"] else None)
    if sc["kind"] == "step":
        return G._items({"kind": "pill", "text": f"{sc['n']} / {sc['of']}", "size": 34, "min": 18, "weight": "Bold", "color": t["accent_text"], "fill": t["fill"], "gap": 34},
                        {"kind": "text", "text": sc["title"], "size": 80, "min": 36, "weight": "Bold", "color": t["text"], "gap": 30, "leading": 1.14} if sc["title"] else None,
                        {"kind": "text", "text": sc["body"], "size": 48, "min": 24, "weight": "Regular", "color": t["muted"], "gap": 0, "leading": 1.3} if sc["body"] else None)
    return G._items({"kind": "text", "text": sc["title"], "size": 84, "min": 36, "weight": "Bold", "color": t["text"], "gap": 28, "leading": 1.14} if sc["title"] else None,
                    {"kind": "text", "text": sc["body"], "size": 44, "min": 22, "weight": "Regular", "color": t["muted"], "gap": 40, "leading": 1.3} if sc["body"] else None,
                    G._pill(c, s["footer"]))


def _scene_images(s, sc):
    """(the picture without the captions, the picture with them, the box where they differ, problems)."""
    cb = _canvas(s)
    cb.footer()
    base = cb.img.copy()
    cf = _canvas(s)
    cf.stack(_items(cf, s, sc))
    full = cf.img.copy()
    box = ImageChops.difference(base, full).getbbox()
    if box:
        pad = 8
        box = (max(0, box[0] - pad), max(0, box[1] - pad), min(W, box[2] + pad), min(H, box[3] + pad))
    return base, full, box, cf.problems


def still(spec, index=0):
    """PNG bytes of one scene as it looks at its best (for the preview), and the problems."""
    s = clean_spec(spec)
    scs = scenes_of(s)
    if not scs:
        c = _canvas(s)
        c.footer()
        out = io.BytesIO()
        c.img.save(out, format="PNG")
        return out.getvalue(), ["The video has no text yet."]
    index = max(0, min(int(index or 0), len(scs) - 1))
    base, full, box, probs = _scene_images(s, scs[index])
    out = io.BytesIO()
    full.save(out, format="PNG", optimize=True)
    return out.getvalue(), probs


# ------------------------------------------------------------------ making the file

def ffmpeg_path():
    path = shutil.which("ffmpeg")
    if path:
        return path
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _ease(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def render(spec, out_path, progress=None):
    """Makes the MP4 at out_path. Returns (True, '') or (False, reason). progress(done_fraction) is called now and then."""
    s = clean_spec(spec)
    scs = scenes_of(s)
    if is_blank(s) or len(s["scenes"]) < MIN_SCENES:
        return False, "Write the words first: a video needs at least two scenes."
    exe = ffmpeg_path()
    if not exe:
        return False, "The video maker (ffmpeg) is not installed on this server."
    total_frames = sum(max(1, round(sc["seconds"] * FPS)) for sc in scs)
    cmd = [exe, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "64k", "-shortest", "-movflags", "+faststart", out_path]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    done = 0
    bar_h = 8
    x0, x1 = int(W * 0.07), W - int(W * 0.07)
    bar_y = 170
    gold = G.THEMES[s["theme"]]["fill"]
    bg = G.THEMES[s["theme"]]["bg"]
    track_fill = tuple(int(c * 0.35 + b * 0.65) for c, b in zip(G.THEMES[s["theme"]]["muted"], bg))
    try:
        for sc in scs:
            base, full, box, _probs = _scene_images(s, sc)
            frames = max(1, round(sc["seconds"] * FPS))
            fi, fo = int(FADE_IN * FPS), int(FADE_OUT * FPS)
            last = sc is scs[-1]
            base_crop = base.crop(box) if box else None
            full_crop = full.crop(box) if box else None
            for i in range(frames):
                if i < fi:
                    a = _ease((i + 1) / fi)
                elif not last and i >= frames - fo:
                    a = _ease((frames - i) / fo)
                else:
                    a = 1.0
                frame = base.copy()
                if box:
                    frame.paste(full_crop if a >= 0.999 else Image.blend(base_crop, full_crop, a), (box[0], box[1]))
                d = ImageDraw.Draw(frame)
                d.rectangle((x0, bar_y, x1, bar_y + bar_h), fill=track_fill)
                d.rectangle((x0, bar_y, x0 + int((x1 - x0) * (done + i + 1) / total_frames), bar_y + bar_h), fill=gold)
                try:
                    proc.stdin.write(frame.tobytes())
                except BrokenPipeError:
                    raise RuntimeError("the video encoder stopped")
            done += frames
            if progress:
                progress(done / total_frames)
        proc.stdin.close()
        err = proc.stderr.read().decode("utf-8", "ignore")[-300:]
        if proc.wait(timeout=120) != 0:
            return False, "The video encoder failed. " + err.strip()
    except Exception as exc:                                        # noqa: BLE001 - report, never crash the page
        try:
            proc.kill()
        except Exception:
            pass
        return False, f"The video could not be made: {exc}"
    if not os.path.exists(out_path) or os.path.getsize(out_path) < 1000:
        return False, "The video file was not made."
    return True, ""


def render_to_temp(spec, folder, final_name, progress=None):
    """Renders into a temporary file in `folder` and moves it to final_name when complete, so a half-made file is never served."""
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".mp4", dir=folder)
    os.close(fd)
    ok, err = render(spec, tmp, progress)
    if not ok:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False, err
    os.replace(tmp, os.path.join(folder, final_name))
    return True, ""
