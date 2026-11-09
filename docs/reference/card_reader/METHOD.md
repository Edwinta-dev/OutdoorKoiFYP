# How the fixed sample spots were chosen

Run `python explore_frame.py frame.jpg` to regenerate everything in `explore_out/`
(needs `pillow` and `numpy`). `frame.jpg` is the original ESP32-CAM test shot (640×480).

| Step | File | What it shows / why |
|---|---|---|
| 1 | console output | 12×12 patch RGB/HSV stats. Saturation is only 0.08–0.28 (washed out), white is slightly green-tinted, 0.14 % of pixels clipped by glare (bottom-right of the pH card), and the frame is brighter on the right (146 → 168). This is why the matcher white-balances on each card's own white and compares chroma only. |
| 2a / 2b | `2*_enhanced_grid.png` | Contrast ×2.5, colour ×3, with a grid every 20 px (red every 100 px, original-frame coordinates). Used to find wheel centres, rims, sensor-disc size and segment dividers. |
| 3a / 3b | `3*_grey_labels.png` | Greyscale + autocontrast, ×4 zoom. The labels are too blurred to read, but their **positions** are clear. Each segment's centre angle = angle from the wheel centre to its label. Spots must avoid the text. |
| 4 | `4_first_attempt_spots.png` | First hand-placed spots. Several sat on dividers, the glare streaks or the inner ring → pH result **ambiguous** (runner-up only 1.04× further). |
| 5 | `5_final_spots.png` | Final layout: per segment, 1 spot on an inner ring (between disc and label) + 3 on an outer ring (between label and rim). Spots more than 20 % covered by glare are dropped at runtime. |
| 6 | `6_card_match_result.png`, `6_card_match_output.txt` | `card_match.py --jitter` on the frame: pH matched clearly (3.9×, 92 % stable under ±6 px shifts), NH3 clearly (2.1×, 100 %). Lime = matched segment, cyan = sensor, white = white reference. |

| 7 | `7_rules_sectors.png`, `7_rules_output.txt` | `card_rules.py` (rule-based reader): whole sectors of the clean band (r 45–68 for pH), hue angle per segment printed next to it, green = matched, cyan = sensor. |
| 8 | `8_decorrelation_boosted_view.png` | Decorrelation stretch, for viewing only. Segments pop out, but so do the 8×8 JPEG blocks: compression is destroying colour detail. |

## Method tests (`method_tests/`)

Run from inside that folder: `python exp.py`, `python exp2.py`, `python exp3.py`. Saved outputs are in the `.txt` files.

- **exp.py:** 8 processing methods with the original 4-spot sampling. pH is at most 69 % accurate per spot, whichever method is used.
- **exp2.py:** the same 8 methods with sector sampling of the clean band. pH accuracy rises to 80 %. Contrast stretch (B) = raw (A) exactly. Flat-field and decorrelation stretch give no reliable gain on pH. NH3 is 100 % with any colour-aware method.
- **exp3.py:** the hue-only rule. 16 of the 18 pH errors land on the hue-neighbour (±1 step). The radial chroma profile showed colour is stable at r ≈ 45–75 px and diluted beyond r ≈ 80 by the label text and rim blur.

**Conclusion:** sampling geometry and per-card white matter. Contrast boosting does not. The pH pairs seg6/seg7 (hue 8 vs 9°), seg2/seg3 and seg4/seg5 (about 4° apart) cannot be separated in this view by any filter. Those need cleaner pixels: higher JPEG quality, less glare and haze.

## Geometry measured (frame pixels)

- **pH wheel:** centre (262, 277), outer radius ~104, sensor disc radius ~31, labels at r ≈ 70–80.
  Segment centre angles: −112, −66, −12/−14, 26, 75, 121, 164/165, −156°.
  Sampling rings: r = 52 and r = 89 (±9°).
- **NH3 wheel:** centre (542, 280), outer radius ~100, disc radius ~31, 4 segments at 0 / 90 / 180 / −90°.
  Sampling rings: r = 50 and r = 84 (±12°).
- **Angles:** image coordinates, 0° = 3 o'clock, 90° = 6 o'clock, increasing clockwise in the image.

These coordinates are valid only for this camera position. If the mount moves, redo steps 2–3
on a new frame and update `center` / the angles in `card_match.py`, or in your lookup JSON.
