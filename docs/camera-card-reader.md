# Camera colour-card reader

The camera service can now store an optional pH Alert and Ammonia Alert colour-card observation beside an uploaded frame. It only runs when the pond has saved card-mask placements (migration `0032_camera_card_masks.sql`). The method samples the moved template points with small frame-scaled square patches, white-balances each card against `white_ref`, rejects glare and inconsistent dots, then compares CIELAB chroma (`a*`, `b*`) against the printed reference segments. The ratio of the runner-up chroma distance to the best distance labels confidence as clear above 2, weak above 1.3, and ambiguous otherwise.

The sample spots are drawn from saved card-mask template points, not a fixed camera mount. Individual dots are excluded if their square falls outside the image, if more than 20% of its pixels exceed 1.1 times the white-reference luminance, or if the dot differs from its patch's median by more than 25% of the median RGB magnitude. Each patch records its median RGB and used/total dot counts. A frame is marked `unusable` below half of all dots.

The result is an image-linked row in `camera_card_readings`. `GET /v1/ponds/{pond}/camera/card-reading` returns the latest result; adding `?image_id=N` selects one frame. The camera's `POST /upload` JSON is unchanged. The offline replay entry point is `python -m koi.camera.card_reader <frames dir> --pond N`; when the directory contains `pond_N_card_masks.json` with the saved `placements` and `version`, it uses those placements. Otherwise it uses the shared template's home placement. It never contacts services.

## Limits

This is a visual segment classifier, not a calibrated numeric pH or ammonia measurement. In the owner's reference evaluation, adjacent pH pairs seg2/seg3, seg4/seg5, and seg6/seg7 were not separable at the tested JPEG quality. Better JPEG detail and lower glare/haze are needed before treating those distinctions as reliable. A low confidence ratio reports ambiguity rather than resolving those pairs by chemistry assumptions.
