"""
Rule-based colour-card reader (pH Alert + Ammonia Alert) for the fixed ESP32-CAM view.

    python card_rules.py photo.jpg                          # readings + photo_rules.png overlay
    python card_rules.py photo.jpg --lookup lookup.json     # your angle -> reading table
    python card_rules.py photo.jpg --flatfield              # also compensate lighting gradient
    python card_rules.py photo.jpg --debug                  # also writes a contrast-boosted view

Pipeline (fixed rules, no training):
  1. Light compensation: each card's own white border sets per-channel gains (white -> neutral).
     --flatfield instead fits a smooth 2-D surface to ALL white card pixels per channel, so a
     light gradient across the card (e.g. sun from one side) is divided out pixel by pixel.
  2. Sampling: each segment = a SECTOR of the clean colour band (between sensor disc and the
     printed label; the outer part of the wheel is diluted by label text and rim blur).
     Glare pixels (brighter than 1.1 x card white) are excluded. Sensor = disc centre.
  3. Feature: CIELAB hue angle h = atan2(b*, a*). Brightness and overall contrast are ignored,
     which is why contrast boosting does not change the result.
  4. Decision: nearest reference segment by hue. For ordered scales (pH) the sensor's position
     between the two nearest segments is also reported (fractional reading).
  5. Confidence: 'clear' / 'between' / 'weak', plus a warning for reference pairs whose hues are
     too close to separate in this camera view (a hard limit: the information is not in the pixels).

Angle convention: degrees in the image, 0 = 3 o'clock, 90 = 6 o'clock, clockwise.
"""
import argparse
import json
import math

import numpy as np
from PIL import Image, ImageDraw

LAYOUT = {
    "pH": {
        "center": [262, 277], "band": [45, 68], "half_width": 14, "sensor_r": 20, "ordered": True,
        "card_box": [130, 40, 395, 465], "wheel_r": 112,
        "white": [[200, 110], [330, 110], [165, 160]],
        "segments": [
            {"angle": -112, "reading": "seg1 top-left"},
            {"angle": -66,  "reading": "seg2 top-right"},
            {"angle": -14,  "reading": "seg3 right"},
            {"angle": 26,   "reading": "seg4 lower-right"},
            {"angle": 75,   "reading": "seg5 bottom (yellow)"},
            {"angle": 121,  "reading": "seg6 bottom-left (magenta)"},
            {"angle": 165,  "reading": "seg7 left-lower"},
            {"angle": -156, "reading": "seg8 left-upper"},
        ],
    },
    "NH3": {
        "center": [542, 280], "band": [42, 68], "half_width": 22, "sensor_r": 20, "ordered": False,
        "card_box": [425, 40, 640, 470], "wheel_r": 112,
        "white": [[470, 420], [600, 420], [450, 150]],
        "segments": [
            {"angle": 90,  "reading": "yellow (bottom)"},
            {"angle": 0,   "reading": "green (right)"},
            {"angle": -90, "reading": "teal (top)"},
            {"angle": 180, "reading": "blue (left)"},
        ],
    },
}
GLARE_K = 1.10          # pixel brighter than 1.1 x card-white luminance = glare
MIN_PAIR_DHUE = 6.0     # reference pairs closer than this (deg) are flagged as inseparable
CLEAR_RATIO = 2.0       # runner-up hue distance / best hue distance for 'clear'


# ---------------------------------------------------------------- colour helpers
def to_lab(rgb):
    c = np.clip(np.asarray(rgb, float), 0, 255) / 255.0
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    M = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = c @ M.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 216 / 24389, np.cbrt(xyz), (24389 / 27 * xyz + 16) / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def hue(lab):
    return math.degrees(math.atan2(lab[2], lab[1]))


def dhue(a, b):
    return abs((a - b + 180) % 360 - 180)


# ---------------------------------------------------------------- step 1: light compensation
def white_pixels_mask(img, card):
    H, W, _ = img.shape
    yy, xx = np.mgrid[0:H, 0:W]
    x0, y0, x1, y1 = card["card_box"]
    m = np.zeros((H, W), bool)
    m[y0:y1, x0:x1] = True
    m &= np.hypot(xx - card["center"][0], yy - card["center"][1]) > card["wheel_r"]
    mx, mn = img.max(2), img.min(2)
    m &= (mx - mn) / np.maximum(mx, 1) < 0.07                 # near-neutral
    v = img.mean(2)
    ref = np.median(v[m])
    return m & (v > 0.85 * ref) & (v < 1.10 * ref)            # drop printed text and glare


def compensate(img, card, flatfield):
    """Returns the image rescaled so this card's white reads (200,200,200), plus the white level."""
    if not flatfield:
        w = np.median(np.concatenate([img[y - 4:y + 5, x - 4:x + 5].reshape(-1, 3) for x, y in card["white"]]), 0)
        return img * (200.0 / w), w
    H, W, _ = img.shape
    m = white_pixels_mask(img, card)
    yy, xx = np.mgrid[0:H, 0:W]
    X, Y = xx / W, yy / H
    terms = [np.ones_like(X), X, Y, X * X, Y * Y, X * Y]       # smooth quadratic light field
    A = np.stack([t[m] for t in terms], 1)
    out = np.empty_like(img)
    for ch in range(3):
        coef, *_ = np.linalg.lstsq(A, img[..., ch][m], rcond=None)
        out[..., ch] = img[..., ch] / sum(k * t for k, t in zip(coef, terms)) * 200.0
    return out, np.median(img[m], 0)


# ---------------------------------------------------------------- step 2-5
def read_card(img, card, flatfield=False):
    comp, white = compensate(img, card, flatfield)
    H, W, _ = img.shape
    yy, xx = np.mgrid[0:H, 0:W]
    cx, cy = card["center"]
    rr = np.hypot(xx - cx, yy - cy)
    th = np.degrees(np.arctan2(yy - cy, xx - cx))
    not_glare = img.mean(2) < GLARE_K * white.mean()
    r0, r1 = card["band"]

    refs = []
    for seg in card["segments"]:
        m = (rr >= r0) & (rr <= r1) & (np.abs((th - seg["angle"] + 180) % 360 - 180) <= card["half_width"]) & not_glare
        lab = to_lab(np.median(comp[m], 0))
        refs.append({**seg, "lab": lab, "hue": hue(lab), "chroma": float(np.hypot(lab[1], lab[2])),
                     "px": int(m.sum()), "mask": m})
    sm = (rr <= card["sensor_r"]) & not_glare
    s_lab = to_lab(np.median(comp[sm], 0))
    s_hue = hue(s_lab)

    for r in refs:
        r["dhue"] = dhue(s_hue, r["hue"])
    ranked = sorted(refs, key=lambda r: r["dhue"])
    best, second = ranked[0], ranked[1]
    ratio = second["dhue"] / max(best["dhue"], 1e-6)

    # inseparable reference pairs in this view
    warn = [(a["reading"], b["reading"], dhue(a["hue"], b["hue"]))
            for i, a in enumerate(refs) for b in refs[i + 1:] if dhue(a["hue"], b["hue"]) < MIN_PAIR_DHUE]

    result = {"reading": best["reading"], "sensor_hue": round(s_hue, 1), "best_dhue": round(best["dhue"], 1),
              "runner_up": second["reading"], "ratio": round(ratio, 2)}
    if card.get("ordered"):
        # position along the hue-ordered scale: is the sensor between best and its hue-neighbour?
        order = sorted(refs, key=lambda r: r["hue"])
        i = order.index(best)
        lo = order[i - 1] if i > 0 else None
        hi = order[i + 1] if i < len(order) - 1 else None
        nb = None
        if lo is not None and lo["hue"] <= s_hue <= best["hue"]:
            nb = lo
        elif hi is not None and best["hue"] <= s_hue <= hi["hue"]:
            nb = hi
        if nb is not None:
            frac = dhue(s_hue, best["hue"]) / max(dhue(nb["hue"], best["hue"]), 1e-6)
            result["between"] = [best["reading"], nb["reading"]]
            result["fraction_toward_second"] = round(frac, 2)
            try:   # numeric readings -> interpolate
                result["interpolated"] = round(float(best["reading"]) + frac * (float(nb["reading"]) - float(best["reading"])), 2)
            except (TypeError, ValueError):
                pass
            result["confidence"] = "between" if frac > 0.35 else ("clear" if ratio >= CLEAR_RATIO else "weak")
        else:
            result["confidence"] = "clear" if ratio >= CLEAR_RATIO else "weak"
    else:
        result["confidence"] = "clear" if ratio >= CLEAR_RATIO else "weak"
    if any(best["reading"] in (a, b) for a, b, _ in warn):
        result["confidence"] += " (inseparable pair involved)"
    return result, refs, s_lab, sm, warn


def load_layout(path):
    layout = json.loads(json.dumps(LAYOUT))
    if path:
        with open(path) as f:
            for k, v in json.load(f).items():
                layout.setdefault(k, {}).update(v)
    return layout


def decorrelation_stretch(img):
    """For human viewing only: spreads the colour axes so segment differences are visible."""
    px = img.reshape(-1, 3)
    mu = px.mean(0)
    w, V = np.linalg.eigh(np.cov((px - mu).T))
    T = V @ np.diag(50.0 / np.sqrt(w)) @ V.T
    return np.clip((px - mu) @ T.T + 128, 0, 255).reshape(img.shape).astype(np.uint8)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("photo")
    ap.add_argument("--lookup")
    ap.add_argument("--flatfield", action="store_true")
    ap.add_argument("--debug", action="store_true")
    a = ap.parse_args()

    im = Image.open(a.photo).convert("RGB")
    img = np.asarray(im).astype(float)
    layout = load_layout(a.lookup)
    ov = im.copy()
    d = ImageDraw.Draw(ov)
    out = {}
    for name, card in layout.items():
        res, refs, s_lab, sm, warn = read_card(img, card, a.flatfield)
        out[name] = res
        print(f"\n== {name} ==  sensor hue {res['sensor_hue']} deg")
        print(f"  {'reading':28s} {'angle':>6s} {'hue':>6s} {'chroma':>6s} {'dHue':>6s} {'px':>5s}")
        for r in sorted(refs, key=lambda r: r["hue"]):
            print(f"  {str(r['reading']):28s} {r['angle']:6.0f} {r['hue']:6.1f} {r['chroma']:6.1f} {r['dhue']:6.1f} {r['px']:5d}")
        for p, q, dh in warn:
            print(f"  ! {p} and {q} differ by only {dh:.1f} deg hue in this view - cannot be told apart reliably")
        print("  -> " + json.dumps(res))
        cx, cy = card["center"]
        for r in refs:
            ys, xs = np.where(r["mask"])
            col = (0, 255, 0) if r["reading"] == res["reading"] else (255, 255, 0)
            for x, y in zip(xs[::3], ys[::3]):
                d.point((int(x), int(y)), fill=col)
            tx = cx + (card["band"][1] + 26) * math.cos(math.radians(r["angle"]))
            ty = cy + (card["band"][1] + 26) * math.sin(math.radians(r["angle"]))
            d.text((tx - 6, ty - 5), f"{r['hue']:.0f}", fill=(0, 0, 0))
        ys, xs = np.where(sm)
        for x, y in zip(xs[::3], ys[::3]):
            d.point((int(x), int(y)), fill=(0, 255, 255))
    base = a.photo.rsplit(".", 1)[0]
    ov.resize((ov.width * 2, ov.height * 2), Image.NEAREST).save(base + "_rules.png")
    if a.debug:
        Image.fromarray(decorrelation_stretch(img)).resize((im.width * 2, im.height * 2)).save(base + "_boosted.png")
    print("\n" + json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
