# Colour-card reader: reference material

Owner's bench and first-deployment work (folder "SestingThit", 10–14 Oct 2026),
copied here unchanged as the reference for the server-side card reader.
It is not imported by the backend and is not run by CI.

- `card_match.py`: fixed-spot matcher. Each card is white-balanced on its own white
  border (von Kries), glare spots are dropped (> 20 % of pixels brighter than 1.1 × card
  white), and each sensor disc is matched to the nearest segment by CIELAB chroma
  (a*, b*), with a runner-up ratio as confidence (clear > 2, weak > 1.3, else ambiguous).
- `card_rules.py`: rule-based variant (sector sampling of the clean band, hue angle,
  fractional pH, optional flat-field).
- `METHOD.md`: how the sample spots were chosen and what the method tests showed.
- `results/`: saved outputs of the method tests and the batch evaluations.

The pixel coordinates inside both scripts belong to one old camera mount. The
backend takes positions from the pond's saved card masks instead.
