"""
Fixed-point colour-card matcher for the ESP32-CAM frame (pH Alert + Ammonia Alert cards).

    python card_match.py photo.jpg                       -> matches + photo_annotated.png
    python card_match.py photo.jpg --lookup lookup.json  -> uses your angle -> reading table
    python card_match.py photo.jpg --jitter              -> also tests robustness to small camera shifts
    python card_match.py --template                      -> writes lookup_template.json to fill in

ANGLE CONVENTION (image coordinates, degrees):
    0 = 3 o'clock (right), 90 = 6 o'clock (down), 180 / -180 = 9 o'clock, -90 = 12 o'clock (up)
    i.e. measured CLOCKWISE from the right, as seen in the camera image (which may be mirrored
    relative to the physical card - always take angles from a captured photo).

Method: every reference segment and the sensor disc are sampled at FIXED spots placed on two
rings (inside and outside the printed label, so text is never sampled). Spots hit by glare
are dropped automatically. All colours are white-balanced on the card's own white border,
converted to CIELAB, and the sensor is matched to the nearest segment by chroma distance
(a*, b* only), which ignores brightness differences across the frame.
"""
import argparse
import json
import math

import numpy as np
from PIL import Image, ImageDraw

# ---------- default layout for the current camera mount (640x480 frame) ----------
DEFAULT = {
    "pH": {
        "center": [262, 277], "r_inner": 52, "r_outer": 89, "spread": 9,
        "white": [[200, 110], [330, 110], [165, 160]],
        "sensor": [[262, 280], [254, 286], [270, 286], [262, 270]],
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
        "center": [542, 280], "r_inner": 50, "r_outer": 84, "spread": 12,
        "white": [[470, 420], [600, 420], [450, 150]],
        "sensor": [[542, 282], [534, 276], [550, 288], [542, 272]],
        "segments": [
            {"angle": 90,  "reading": "yellow (bottom)"},
            {"angle": 0,   "reading": "green (right)"},
            {"angle": -90, "reading": "teal (top)"},
            {"angle": 180, "reading": "blue (left)"},
        ],
    },
}
R = 4             # each spot is a (2R+1)^2 pixel patch
GLARE_K = 1.10    # pixel luminance above 1.10 x card-white luminance = glare
GLARE_DROP = 0.2  # drop a whole spot if more than 20% of its pixels are glare


def seg_spots(card, angle):
    cx, cy = card["center"]
    pts = []
    for r, angs in ((card["r_inner"], [angle]),
                    (card["r_outer"], [angle - card["spread"], angle, angle + card["spread"]])):
        for a in angs:
            pts.append((round(cx + r * math.cos(math.radians(a))), round(cy + r * math.sin(math.radians(a)))))
    return pts


# ---------- colour maths ----------
def srgb_to_lab(rgb):
    c = np.asarray(rgb, float) / 255.0
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    M = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = M @ c / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 216 / 24389, np.cbrt(xyz), (24389 / 27 * xyz + 16) / 116)
    return np.array([116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])])


def sample(img, spots, glare_lum=None, dx=0, dy=0):
    """Median colour over the spots; returns (rgb, n_pixels, spots_used)."""
    kept, used = [], []
    H, W, _ = img.shape
    for x, y in spots:
        x, y = x + dx, y + dy
        if not (R <= x < W - R and R <= y < H - R):
            continue
        p = img[y - R:y + R + 1, x - R:x + R + 1].reshape(-1, 3)
        if glare_lum is not None:
            bright = p.mean(1) >= glare_lum
            if bright.mean() > GLARE_DROP:
                continue
            p = p[~bright]
        kept.append(p)
        used.append((x, y))
    if not kept:
        return None, 0, []
    px = np.concatenate(kept)
    return np.median(px, 0), len(px), used


def match_card(img, card, dx=0, dy=0):
    white, _, _ = sample(img, card["white"], dx=dx, dy=dy)
    glare = GLARE_K * white.mean()
    gain = white.mean() / white                       # von Kries: card white -> neutral
    s_raw, n, s_used = sample(img, card["sensor"], glare, dx, dy)
    s_lab = srgb_to_lab(np.clip(s_raw * gain, 0, 255))
    rows = []
    for seg in card["segments"]:
        r_raw, _, used = sample(img, seg_spots(card, seg["angle"]), glare, dx, dy)
        if r_raw is None:
            continue
        r_lab = srgb_to_lab(np.clip(r_raw * gain, 0, 255))
        rows.append({"reading": seg["reading"], "angle": seg["angle"], "rgb": r_raw.round().astype(int).tolist(),
                     "dC": float(np.linalg.norm(s_lab[1:] - r_lab[1:])),
                     "dE": float(np.linalg.norm(s_lab - r_lab)), "spots": used})
    rows.sort(key=lambda r: r["dC"])
    ratio = rows[1]["dC"] / max(rows[0]["dC"], 1e-6)
    conf = "clear" if ratio > 2 else ("weak" if ratio > 1.3 else "AMBIGUOUS")
    return {"white": white, "sensor": s_raw, "sensor_px": n, "sensor_spots": s_used,
            "rows": rows, "ratio": ratio, "confidence": conf}


def load_layout(lookup_path):
    layout = json.loads(json.dumps(DEFAULT))
    if lookup_path:
        with open(lookup_path) as f:
            user = json.load(f)
        for name, cfg in user.items():
            layout.setdefault(name, {}).update(cfg)   # any key (segments, center, radii...) can be overridden
    return layout


def run(path, layout, jitter=False):
    im = Image.open(path).convert("RGB")
    img = np.asarray(im).astype(float)
    ann = im.copy()
    d = ImageDraw.Draw(ann)
    summary = {}
    for name, card in layout.items():
        res = match_card(img, card)
        best = res["rows"][0]
        print(f"\n== {name} ==  card white RGB {res['white'].round().astype(int).tolist()}   "
              f"sensor RGB {res['sensor'].round().astype(int).tolist()} ({res['sensor_px']} px)")
        print(f"  {'reading':28s} {'angle':>6s} {'RGB':>16s} {'dChroma':>8s} {'dE76':>6s}")
        for r in res["rows"]:
            print(f"  {str(r['reading']):28s} {r['angle']:6.0f} {str(r['rgb']):>16s} {r['dC']:8.1f} {r['dE']:6.1f}")
        print(f"  -> {best['reading']}   (runner-up is {res['ratio']:.1f}x further: {res['confidence']})")
        summary[name] = {"reading": best["reading"], "ratio": round(res["ratio"], 2), "confidence": res["confidence"]}

        if jitter:
            votes = {}
            for dx in range(-6, 7, 3):
                for dy in range(-6, 7, 3):
                    b = match_card(img, card, dx, dy)["rows"][0]["reading"]
                    votes[b] = votes.get(b, 0) + 1
            total = sum(votes.values())
            print(f"  jitter +/-6 px ({total} shifts): " +
                  ", ".join(f"{k} {v / total:.0%}" for k, v in sorted(votes.items(), key=lambda kv: -kv[1])))
            summary[name]["jitter_agreement"] = round(votes.get(best["reading"], 0) / total, 2)

        for x, y in card["white"]:
            d.rectangle([x - R, y - R, x + R, y + R], outline="white")
        for x, y in res["sensor_spots"]:
            d.rectangle([x - R, y - R, x + R, y + R], outline="cyan")
        for r in res["rows"]:
            col = "lime" if r is best else "yellow"
            for x, y in r["spots"]:
                d.rectangle([x - R, y - R, x + R, y + R], outline=col)
    out = path.rsplit(".", 1)[0] + "_annotated.png"
    ann.save(out)
    print(f"\nAnnotated spots: {out}   (lime = matched segment, cyan = sensor, white = white ref)")
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("photo", nargs="?")
    ap.add_argument("--lookup", help="JSON with per-card segments [{angle, reading}] and optional overrides")
    ap.add_argument("--jitter", action="store_true", help="re-run with the spots shifted up to 6 px")
    ap.add_argument("--template", action="store_true", help="write lookup_template.json")
    a = ap.parse_args()
    if a.template:
        tpl = {k: {"segments": [{"angle": s["angle"], "reading": None} for s in v["segments"]]} for k, v in DEFAULT.items()}
        with open("lookup_template.json", "w") as f:
            json.dump(tpl, f, indent=2)
        print("Wrote lookup_template.json - fill in each 'reading' (angles: 0=right, 90=down, clockwise in the image)")
        return
    if not a.photo:
        ap.error("photo required")
    summary = run(a.photo, load_layout(a.lookup), a.jitter)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
