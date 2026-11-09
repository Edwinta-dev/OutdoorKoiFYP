# 0152: Camera turbidity index and disc agreement
<!-- verify-banner:begin -->
> ⚠ Agent-written record: its claims are hypotheses to verify against the code, not ground truth. Evidence levels in Verified facts: [checked] confirmed by the tool · [tested] cited test exists and passed in this run · [claimed] not verified.
<!-- verify-banner:end -->
Status: implemented · Issue: #152 · Date: 2026-11-10

## In short

Camera card readings now compare same named patch colours with the configured clear-water frame and store a per-channel affine fit, R², an overall turbidity index, corrected disc classifications, and agreement. Three accepted unusable card readings in a row enqueue one outbox notice; a usable reading clears the latch. The upload response remains unchanged.

## How it works

1. The camera reads its configured clear-water frame's stored card result, then samples and classifies the new frame with the existing patch reader.
2. For matching usable named patches, the backend fits the observed red, green, and blue values against their clear-water values. It uses the fitted transmission and scatter terms to calculate the channel indices and their luminance-weighted overall value.
3. The same fit removes the scatter estimate from each disc colour. The corrected disc is matched to the clear frame's segment colours and compared with the ordinary current-frame segment result.
4. The frame-linked result stores fit values, agreement and alert state. A GET returns those stored fields; replay can take a reference image through `--reference-frame` or the masks JSON's `reference_frame` entry.
5. Accepted unusable readings advance the persisted streak. The third triggers one info notice. An accepted usable reading resets the streak and allows a later notice.

## Main design choice

Keep the calibration attached to the existing image-linked card result rather than adding another table or changing `imageTable`. This keeps the clear reference and the two readings in one retrieval record and leaves older rows valid through nullable/defaulted columns. Streak and latch state live on that same row so they survive camera-service restarts. In-memory-only counters would be simpler but would lose the one-notice rule on restart. A dedicated state table would separate alert state more cleanly, but would add another migration object and storage path for state that only advances with card readings.

## Traps: why the obvious fix is wrong

- Fit same named usable patches, not arbitrary current/reference patches; unrelated printed colours would distort transmission and scatter.
- Correct the disc with the fit before its second classification. Comparing raw RGB to clear-frame colours would count the water cast twice.
- A failed frame-quality gate does not advance or clear the accepted-frame unusable sequence.
- The endpoint and upload reply stay additive/internal; `POST /upload` remains frozen.

## Components

- `Backend/koi/camera/turbidity.py::analyse` — fit, index, correction
- `Backend/koi/camera/card_reader.py::compare_reference` — second segment result
- `Backend/koi/camera/camera.py::upload_image` — persistence and outbox alert
- `Backend/koi/storage/base.py::CAMERA_CARD_READING_COLUMNS` — both adapter row shape
- `Backend/koi/api/responses.py::CameraCardReadingResponse` — API fields
- `supabase/migrations/0034_camera_turbidity.sql` — additive columns
- `Backend/tests/camera/test_turbidity.py` — synthetic fit and agreement
- `docs/camera-card-reader.md`, `docs/models.md` — method and limits

## Breaks or needs follow-up

Apply migration `0034_camera_turbidity.sql` before deploying the camera and API. Existing card rows remain readable with null calibration fields and zero alert state.

## Problem and constraints

The prior reader classified discs only against segments in their own frame. It had no estimate of light scattered into the camera view and no second reading against known clear-water colours. The camera upload contract and the stored #151 card table must remain compatible with prior rows. `Backend/koi/camera/turbidity.py::analyse` implements `observed_c = a_c * clear_c + b_c` by per-channel least squares; `R²_c = 1 - SSE_c/SST_c`; `index_c = b_c / (a_c * W_c + b_c)`; `index = dot(index_channels, [0.2126, 0.7152, 0.0722])`; and `corrected_rgb = clip((observed_rgb - b) / a, 0, 255)`. The usable-patch pair gate and `a_c >= 0.05` gate run before correction.

## Alternatives considered

- Store state only in process memory — restart would restart the three-frame alert sequence.
- A new turbidity table — duplicates the existing per-image card-reading key and complicates the GET path.

## Trade-offs

The reference fit assumes the printed patches remain unchanged and share a channel-wise affine response. It estimates an optical scatter share, not NTU or a water-quality laboratory measure. Reference rows must have a card reading available.

## Key parameters

- Minimum paired patches: 6 (`Backend/koi/camera/turbidity.py::MIN_PATCHES`), issue threshold; raising it suppresses more fits, lowering it increases fit instability.
- Minimum transmission: 0.05 (`Backend/koi/camera/turbidity.py::MIN_TRANSMISSION`), issue threshold; raising it rejects more corrections, lowering it amplifies noise.
- Alert streak: 3 accepted frames (`Backend/koi/camera/camera.py::UNUSABLE_ALERT_FRAMES`), issue threshold; a higher value delays notices.
- Luminance weights: 0.2126, 0.7152, 0.0722 (`Backend/koi/camera/turbidity.py::LUMINANCE_WEIGHTS`), Rec. 709; changing them shifts overall index toward a channel.

## Assumptions

The saved reference result was captured with the same card masks and placement. Stored medians approximate the printed patch colours. If either is false, the fit and reference comparison may be biased.

## Edge cases

- Handled: missing reference, fewer than 6 pairs, low transmission, absent reference white, absent disc readings, and old rows with null new columns.
- Not handled: drift in the printed card itself, spatially varying glare, or a reference image that has never been processed into a card reading.

## Not verified

The migration was not run against the local database because it is still at the schema before 0034. No real pond capture batch or hosted database was available.

## Changes outside the scope

None.

## How it was verified

- `python tools/check.py all`: Ruff, mypy, backend pytest (1,996 passed, 2 expected skips), tools pytest (39 passed), all 11 backtest metrics, firmware host tests (296 passed), all 3 ESP32 compiles, Flutter analysis, and Flutter tests passed. The SQL migration test skipped because the local DB predates 0034; the dev-profile SQL check skipped because the local Supabase stack is unavailable.
- Focused camera/API tests: 33 passed, 1 migration skip; synthetic tests cover fit recovery, index, correction, clear index and both agreement outcomes; upload test checks the alert fires once and re-arms after a usable frame.

## To change this

To require more calibration evidence, raise `Backend/koi/camera/turbidity.py::MIN_PATCHES`; cost: more frames have no index. To notify after a longer unusable sequence, change the streak comparison in `Backend/koi/camera/camera.py::upload_image`; cost: delayed obstruction notice.

## Rollback

Revert the camera fit/read paths and API fields, then leave migration 0034's additive columns unused. Do not edit migration 0034 after application.

<!-- verified-facts:begin (generated by integrity record; do not edit) -->
## Verified facts

Generated 2026-11-09 18:39 UTC for issue #152 attempt 7; compared HEAD..working tree (base 5927f06). Records are claims to check against the code, not proof.

**Change summary**

- `Backend/koi/`: 5 file(s), 7 component(s), +136/-4 lines
- `supabase/migrations/`: 1 file(s), 0 component(s), +13/-0 lines
- plus 3 test file(s), 3 doc file(s)
- new files: `Backend/koi/camera/turbidity.py`, `supabase/migrations/0034_camera_turbidity.sql`

**Removed symbols**

None.

**Scope**

Declared: `Backend/koi/camera/**`, `Backend/koi/api/**`, `Backend/koi/storage/**`, `Backend/koi/notifications.py`, `Backend/tests/**`, `supabase/migrations/**`, `supabase/tests/**`, `docs/**`. Verdict: pass.

**Supervisor checks passed**

whitespace/conflict-marker check, apply pending migrations to the local Supabase stack, OutdoorKoi host test suites (layout-aware), database suite (migration rehearsal, SQL tests, local profile), scope check, dangling reference check

**Evidence**

9 checked by the tool, 0 tested in this run, 14 claimed only (of 23 claims). [checked] = the named code exists and changed, or the cited value matches; [tested] = the cited test exists and the test gate passed in this run; [claimed] = not verified.
- Components: 6 checked, 0 tested, 2 claimed
- Key parameters: 3 checked, 0 tested, 1 claimed
- Edge cases: 0 checked, 0 tested, 1 claimed
- How it was verified: 0 checked, 0 tested, 2 claimed
- Traps: 0 checked, 0 tested, 4 claimed
- Assumptions: 0 checked, 0 tested, 1 claimed
- Trade-offs: 0 checked, 0 tested, 1 claimed
- Not verified: 0 checked, 0 tested, 1 claimed
- Breaks or needs follow-up: 0 checked, 0 tested, 1 claimed
  - [checked] Components: `Backend/koi/camera/turbidity.py::analyse` — fit, index, correction (named code exists and changed)
  - [checked] Key parameters: Minimum paired patches: 6 (`Backend/koi/camera/turbidity.py::MIN_PATCHES`), issue thres... (value matches the code: MIN_PATCHES = 6)
  - [checked] Components: `Backend/koi/camera/card_reader.py::compare_reference` — second segment result (named code exists and changed)
  - [checked] Key parameters: Minimum transmission: 0.05 (`Backend/koi/camera/turbidity.py::MIN_TRANSMISSION`), issue... (value matches the code: MIN_TRANSMISSION = 0.05)
  - [checked] Components: `Backend/koi/camera/camera.py::upload_image` — persistence and outbox alert (named code exists and changed)
  - [checked] Key parameters: Alert streak: 3 accepted frames (`Backend/koi/camera/camera.py::UNUSABLE_ALERT_FRAMES`)... (value matches the code: UNUSABLE_ALERT_FRAMES = 3)
  - [checked] Components: `Backend/koi/storage/base.py::CAMERA_CARD_READING_COLUMNS` — both adapter row shape (named code exists and changed)
  - [checked] Components: `Backend/koi/api/responses.py::CameraCardReadingResponse` — API fields (named code exists and changed)
  - [checked] Components: `supabase/migrations/0034_camera_turbidity.sql` — additive columns (named code exists and changed)
  - [claimed] Components: `Backend/tests/camera/test_turbidity.py` — synthetic fit and agreement (names no code)
  - full per-claim list (23) in the JSON record

**Consistency**

0 error(s), 1 warning(s) between the rationale above and the change.
- WARN: Backend/koi/camera/card_reader.py::main (modified) changed but not mentioned in the record

**Commit**

This record is committed with the change it describes; `git log -1 -- docs/decisions/0152-camera-turbidity.md` gives the commit.
<!-- verified-facts:end -->
