"""Read the pond's printed colour cards from template sample points."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

from koi.camera import turbidity

PATCH_RADIUS = 4
GLARE_FRACTION = 0.20
GLARE_LUMINANCE = 1.10
DOT_DEVIATION = 0.25
LEAVE_ONE_OUT_MIN_DOTS = 4


def _lab(rgb: np.ndarray) -> np.ndarray:
    c = np.asarray(rgb, dtype=float) / 255
    c = np.where(c <= .04045, c / 12.92, ((c + .055) / 1.055) ** 2.4)
    xyz = np.array([[.4124, .3576, .1805], [.2126, .7152, .0722], [.0193, .1192, .9505]]) @ c
    xyz /= [0.95047, 1, 1.08883]
    f = np.where(xyz > 216 / 24389, np.cbrt(xyz), (24389 / 27 * xyz + 16) / 116)
    return np.array([116*f[1]-16, 500*(f[0]-f[1]), 200*(f[1]-f[2])])


def _consistent(values: np.ndarray) -> np.ndarray:
    """Which dots of one patch agree with the patch's other dots.

    A dot is kept when its relative Euclidean deviation from the median of
    the OTHER dots (leave-one-out, so a dot cannot vouch for itself) is at
    most DOT_DEVIATION of that median's magnitude. With fewer than
    LEAVE_ONE_OUT_MIN_DOTS dots the others' median is a single dot or the
    mean of two, where one bad dot would reject the good ones, so those
    patches use the median of all their dots instead (a single dot is kept).
    """
    count = len(values)
    if count < LEAVE_ONE_OUT_MIN_DOTS:
        centres = np.repeat(np.median(values, axis=0)[None, :], count, axis=0)
    else:
        centres = np.stack([np.median(np.delete(values, i, axis=0), axis=0) for i in range(count)])
    scale = np.maximum(np.linalg.norm(centres, axis=1), 1e-9)
    return np.linalg.norm(values - centres, axis=1) / scale <= DOT_DEVIATION


def _patch(img: np.ndarray, points: list, white_lum: float | None = None) -> tuple[list, list]:
    h, w = img.shape[:2]
    dots = []
    for px, py in points:
        x, y = int(round(px * w)), int(round(py * h))
        r = max(1, round(PATCH_RADIUS * w / 640))
        if x-r < 0 or y-r < 0 or x+r >= w or y+r >= h:
            continue
        pixels = img[y-r:y+r+1, x-r:x+r+1].reshape(-1, 3)
        if white_lum is not None:
            bright = pixels.mean(axis=1) >= GLARE_LUMINANCE * white_lum
            if bright.mean() > GLARE_FRACTION:
                continue
            pixels = pixels[~bright]
        if len(pixels):
            dots.append(np.median(pixels, axis=0))
    if not dots:
        return [], []
    values = np.asarray(dots)
    keep = _consistent(values)
    accepted = values[keep]
    return (np.median(accepted, axis=0).round().astype(int).tolist() if len(accepted) else [],
            [bool(v) for v in keep])


def read_frame(frame: np.ndarray, patches: list[dict], version: int | None = None) -> dict:
    """Read a BGR/RGB-independent RGB uint8 image with normalized mask points."""
    img = np.asarray(frame, dtype=float)
    by_name = {p['name']: p for p in patches}
    white, white_used = _patch(img, by_name.get('white_ref', {}).get('points', []))
    if not white:
        patch_results: dict[str, dict[str, object]] = {
            name: {'median_rgb': None, 'dots_used': 0, 'dots_total': len(patch.get('points', []))}
            for name, patch in by_name.items() if name != 'white_ref'}
        total = len(by_name.get('white_ref', {}).get('points', []))
        total += sum(len(patch.get('points', [])) for name, patch in by_name.items() if name != 'white_ref')
        return {'card_masks_version': version, 'white_rgb': None, 'patches': patch_results,
                'ph': None, 'nh3': None, 'usable_dots': 0, 'total_dots': total,
                'quality': 'unusable'}
    white_rgb = np.asarray(white, dtype=float)
    white_lum = float(white_rgb.mean())
    gain = white_lum / np.maximum(white_rgb, 1)
    totals, usable = len(by_name.get('white_ref', {}).get('points', [])), sum(white_used)
    patch_results = {}
    for name, patch in by_name.items():
        if name == 'white_ref':
            continue
        rgb, used = _patch(img, patch.get('points', []), white_lum)
        totals += len(patch.get('points', []))
        usable += sum(used)
        patch_results[name] = {'median_rgb': rgb or None, 'dots_used': sum(used),
                               'dots_total': len(patch.get('points', []))}
    cards: dict[str, object] = {}
    for prefix, output in (('ph', 'ph'), ('nh3', 'nh3')):
        reading = patch_results.get(prefix + '_reading', {}).get('median_rgb')
        if reading is None:
            cards[output] = None
            continue
        sensor_lab = _lab(np.clip(np.asarray(reading) * gain, 0, 255))
        rows = []
        for name, item in patch_results.items():
            if name.startswith(prefix + '_ref_') and item['median_rgb']:
                ref_lab = _lab(np.clip(np.asarray(item['median_rgb']) * gain, 0, 255))
                rows.append((float(np.linalg.norm(sensor_lab[1:] - ref_lab[1:])), name))
        rows.sort()
        ratio = rows[1][0] / max(rows[0][0], 1e-6) if len(rows) > 1 else 0
        cards[output] = {'segment': rows[0][1] if rows else None,
                         'distances': [{'segment': n, 'chroma': d} for d, n in rows],
                         'ratio': ratio, 'confidence': 'clear' if ratio > 2 else 'weak' if ratio > 1.3 else 'ambiguous'}
    return {'card_masks_version': version, 'white_rgb': white, 'patches': patch_results,
            **cards, 'usable_dots': usable, 'total_dots': totals,
            'quality': 'unusable' if totals == 0 or usable * 2 < totals else 'usable'}


def compare_reference(current: dict, reference: dict | None) -> dict:
    """Attach the fit and compare the two disc classifications."""
    fit = turbidity.analyse(current, reference)
    current["turbidity"] = fit
    agreements = {}
    if fit["reason"] is None and reference:
        gain = np.mean(reference["white_rgb"]) / np.maximum(np.asarray(reference["white_rgb"], float), 1)
        for prefix in ("ph", "nh3"):
            raw = current.get(prefix)
            corrected = fit["corrected"].get(prefix)
            rows = []
            if corrected:
                for name, item in (reference.get("patches") or {}).items():
                    rgb = item.get("median_rgb")
                    if name.startswith(prefix + "_ref_") and rgb:
                        rows.append((float(np.linalg.norm(_lab(np.clip(corrected, 0, 255))[1:] -
                                                           _lab(np.clip(np.asarray(rgb) * gain, 0, 255))[1:])), name))
            corrected_segment = min(rows)[1] if rows else None
            agreements[prefix] = bool(raw and raw.get("segment") and corrected_segment == raw["segment"])
            if current.get(prefix):
                current[prefix]["corrected_segment"] = corrected_segment
                current[prefix]["agreement"] = agreements[prefix]
    else:
        agreements = {"ph": False, "nh3": False}
        for prefix in agreements:
            if current.get(prefix):
                current[prefix]["corrected_segment"] = None
                current[prefix]["agreement"] = False
    current["agreement"] = agreements
    current["frame_agreement"] = "clear" if all(agreements.values()) else "disagree"
    return current


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('frames', type=Path)
    ap.add_argument('--pond', required=True, type=int)
    ap.add_argument('--reference-frame', type=Path,
                    help='clear-water reference image path; defaults to reference_frame in the pond masks JSON')
    args = ap.parse_args()
    from koi.camera.card_masks import CardPlacements, home_placements
    saved_path = args.frames / f'pond_{args.pond}_card_masks.json'
    if saved_path.exists():
        saved = json.loads(saved_path.read_text(encoding='utf-8'))
        placements = CardPlacements.validate(saved['placements'])
        patches = placements.patches()
        version = saved.get('version')
    else:
        patches = home_placements().patches()
        version = None
    reference_path = args.reference_frame or (args.frames / saved['reference_frame']
                    if saved_path.exists() and saved.get('reference_frame') else None)
    reference_result = None
    if reference_path is not None:
        reference_bgr = cv2.imread(str(reference_path))
        if reference_bgr is not None:
            reference_result = read_frame(cv2.cvtColor(reference_bgr, cv2.COLOR_BGR2RGB), patches, version=version)
    for path in sorted(args.frames.glob('*')):
        if path.suffix.lower() not in {'.jpg', '.jpeg', '.png'}:
            continue
        bgr = cv2.imread(str(path))
        if bgr is None:
            sys.stdout.write(json.dumps({'frame': path.name, 'error': 'unreadable image'}) + '\n')
            continue
        result = compare_reference(read_frame(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), patches, version=version),
                                   reference_result)
        sys.stdout.write(json.dumps({'frame': path.name, 'pond': args.pond, **result}) + '\n')


if __name__ == '__main__':
    main()
