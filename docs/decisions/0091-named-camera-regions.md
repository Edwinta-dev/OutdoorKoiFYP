# 0091: Named camera regions: water gap, rim, plants and a colour reference
Status: implemented · Issue: #91 · Date: 2026-10-10

## In short
A pond's camera config can now hold named regions as well as the single
water mask: `water_gap`, `rim`, `plants` and an optional `reference`
patch. When a pond has regions, every uploaded frame stores per-region
colour metrics in a new `imageTable.regions` column. That includes
whether plants have drifted over the water gap. A config with only a
mask behaves exactly as before.

## Problem and constraints
Pond 455's view is mostly floating plants, the concrete rim and loose
objects, so a whole-frame greenness value measures plant colour, not
algae. The owner wants to watch a fixed spot of open water, the rim's
film, and plant cover separately, and to cancel colour drift with a
grey patch. Constraints: `POST /upload` must not change; rows written
before the change must still load; schema changes go in a new
migration; the app editor (#60) comes later, so for now regions are set
through the existing mask API or a documented SQL call.

## Approach
- `Backend/koi/camera/mask.py::validate_regions` checks the regions
  object. `water_gap`, `rim` and `plants` are required, `reference` is
  optional, and nothing else is allowed. Each polygon goes through
  `validate_polygon`, and errors name the region.
- Migration `0023_camera_regions.sql` adds `regions` (jsonb) to
  `camera_config` and `camera_mask_version`, checked by
  `camera_regions_are_valid`. `mask` becomes nullable, but a row still
  needs a mask or regions. `save_camera_mask` gains `p_regions default
  null`, so the two-argument call still works. Saved versions stay
  immutable, now including their regions. `imageTable.regions` is added
  as nullable jsonb.
- Regions are saved in the same `mask_version` as the mask. Any region
  change is therefore a new version, and
  `Backend/koi/camera/camera.py::upload_image` already writes
  `baseline_reset = 'mask_changed'` when the version changes.
- `Backend/koi/camera/hsvEngine.py::region_metrics` measures each region
  separately and ignores pixels outside every region. With a reference
  patch, every pixel's RGB is divided by the patch's channel means, then
  multiplied by the patch's mean brightness. The scaling leaves GCC
  unchanged and keeps values near 0..1. For `water_gap` it also counts
  plant pixels: saturated, lit green (`PLANT_LOWER`..`PLANT_UPPER` in
  HSV, after correction). It reports `plant_fraction` and
  `open_fraction`, takes its colour means over open-water pixels only,
  and sets `usable: false` (`plants_over_gap`) when plants cover more
  than `GAP_MAX_PLANT_FRACTION` = 0.3.
- `camera.py::get_camera_config` reads mask, regions and version in one
  go. `measure_regions` stores the metrics, or nothing (logged as
  `region_metrics_failed`) if they cannot be computed. The green ratio,
  quality gate and state machine still use the mask, or the whole frame
  when there is none.
- `PUT /v1/ponds/{pond}/camera/mask` (`Backend/koi/api/routes.py::put_camera_mask`)
  accepts `regions`. Leaving it out keeps the regions in force, so the
  app's current mask-only editor cannot wipe them. `regions: null`
  removes them. `polygon: null` (no mask) is allowed only with regions
  in the body.

## Alternatives considered
| Option | Why not chosen |
|---|---|
| Keep the partial attempt's `region_metrics` in `quality.py` | Outside the issue's scope, and it counted any green hue in the gap as plant, so green water would have read as "drifted over". |
| Keep `mask` required and ask for a full-frame polygon alongside regions | It would force a fake mask that is a different version from "no mask", with no benefit. |
| Make every PUT a full replacement (omitted regions = none) | The existing app editor sends only `polygon` and would silently delete the owner's regions. |

## Trade-offs
- The plant classifier is a fixed HSV threshold set against synthetic
  colours. Pale or shaded leaves may count as water, and strongly green
  water as plant. This will matter as soon as real frames are reviewed.
  The thresholds are stored in each row's `thresholds`, so stored rows
  can be judged again later.
- Keeping the current regions on a polygon-only PUT means a read and
  then a write in the route. Two concurrent saves could drop one side's
  regions.

## Assumptions
- The reference patch is matte, neutral and not blown out. A patch with
  no unclipped pixels gives `reference.applied: false` and no
  correction. A tinted patch would tint every corrected reading.
- Regions may overlap. Each region is measured on its own.

## Edge cases
- Handled: clipped pixels are left out of the colour means; a gap with
  no unclipped open-water pixels is `usable: false` (`no_usable_pixels`);
  a blown reference is not applied; a failed metrics computation stores
  the frame without regions.
- Not handled: a camera that moves while the regions stay put. The gap
  then reads whatever is now there, and only the plant check may catch
  it.

## Changes outside the scope
`Backend/koi/storage/{base,memory,supabase_storage}.py` (regions column
and save parameter), `Backend/koi/api/schemas.py` and `responses.py`
(request and response models), `docs/api/openapi.yaml` (regenerated).
Existing tests were extended for the new `regions` key and the
three-argument function signature. Without these the route and the
camera could not read or write regions.

## How it was verified
New tests: `tests/camera/test_camera_regions.py` covers validation,
known region colours, the cast frame brought back to GCC 1/3, a
drifted-over gap, the legacy mask-only config and the region-change
reset. Regions API cases were added to `tests/api/test_camera_mask_api.py`,
storage cases to `tests/storage/test_camera_mask_storage.py`, and the
SQL tests on the local stack are in `tests/sql/test_sql_camera_regions.py`.
`python tools/check.py all` and `python tools/check.py database` were
run after `supabase db reset`. Not checked: real ESP32-CAM frames of
pond 455.

## To change this
Plant thresholds and `GAP_MAX_PLANT_FRACTION` are in `hsvEngine.py`.
The allowed region names are in `mask.py` and must match
`camera_regions_are_valid`. A new metrics shape needs `REGIONS_VERSION`
bumped.

## Rollback
Reverting the code leaves `regions` columns unused, and old code still
reads configs that have a mask. A config saved with `mask = null` would
make the old camera analyse the whole frame. The old API would also
reject such a row in its response model. Check `camera_config` for null
masks before reverting. Owner action: apply 0023 before deploying the
backend, because the new code selects `regions`.

<!-- verified-facts:begin (generated by integrity record; do not edit) -->
## Verified facts

Generated 2026-10-10 02:52 UTC for issue #91 attempt 9; compared HEAD..working tree (base 7b7e611).

**Changed components**

| Component | Change | Lines +/- | Tags | Description |
|---|---|---|---|---|
| `Backend/koi/api/responses.py::CameraMaskVersion` | modified | +2/-1 |  |  |
| `Backend/koi/api/responses.py::CameraMaskResponse` | modified | +2/-0 |  |  |
| `Backend/koi/api/routes.py::_camera_mask_response` | modified | +1/-0 |  |  |
| `Backend/koi/api/routes.py::put_camera_mask` | modified | +9/-3 |  | Saves the body's polygon, and its regions or the ones in force, |
| `Backend/koi/api/routes.py::<module>` | added | +6/-0 |  |  |
| `Backend/koi/api/schemas.py::CameraMaskUpdate` | modified | +9/-2 |  | PUT /v1/ponds/{pond}/camera/mask: the pond's water mask, a polygon |
| `Backend/koi/api/schemas.py::CameraMaskUpdate._valid_polygon` | modified | +3/-1 |  |  |
| `Backend/koi/api/schemas.py::CameraMaskUpdate._valid_regions` | added | +9/-0 |  |  |
| `Backend/koi/api/schemas.py::CameraMaskUpdate._mask_or_regions` | added | +5/-0 |  |  |
| `Backend/koi/camera/camera.py::get_camera_config` | added | +8/-0 |  | (polygon, regions, mask_version) from one read of the pond's |
| `Backend/koi/camera/camera.py::get_mask` | modified | +2/-4 |  | (polygon, mask_version) from one read of the pond's camera_config |
| `Backend/koi/camera/camera.py::measure_regions` | added | +10/-0 |  | hsvEngine.region_metrics for the pond's regions, or None when it has |
| `Backend/koi/camera/camera.py::push_current_data` | modified | +2/-2 |  | Raises StorageError when the row cannot be written. |
| `Backend/koi/camera/camera.py::upload_image` | modified | +4/-2 |  |  |
| `Backend/koi/camera/camera.py::<module>` | added | +14/-0 |  |  |
| `Backend/koi/camera/hsvEngine.py::_polygon_mask` | added | +8/-0 |  | Boolean mask of the pixels inside a normalised polygon, filled as |
| `Backend/koi/camera/hsvEngine.py::_rgb_means` | added | +10/-0 |  | gcc, exg, r, g, b means of an (n, 3) array of 0..1 RGB, as |
| `Backend/koi/camera/hsvEngine.py::region_metrics` | added | +62/-0 |  | Per-region colour metrics of a decoded BGR frame, stored in |
| `Backend/koi/camera/hsvEngine.py::REGIONS_VERSION` | added | +1/-0 |  |  |
| `Backend/koi/camera/hsvEngine.py::PLANT_LOWER` | added | +1/-0 |  |  |
| `Backend/koi/camera/hsvEngine.py::PLANT_UPPER` | added | +1/-0 |  |  |
| `Backend/koi/camera/hsvEngine.py::GAP_MAX_PLANT_FRACTION` | added | +1/-0 |  |  |
| `Backend/koi/camera/hsvEngine.py::GAP_PLANTS_OVER` | added | +1/-0 |  |  |
| `Backend/koi/camera/hsvEngine.py::GAP_NO_PIXELS` | added | +1/-0 |  |  |
| `Backend/koi/camera/hsvEngine.py::<module>` | added | +24/-0 |  |  |
| `Backend/koi/camera/mask.py::validate_regions` | added | +20/-0 |  | value as {name: polygon}: water_gap, rim and plants, optionally |
| `Backend/koi/camera/mask.py::REQUIRED_REGIONS` | added | +1/-0 |  |  |
| `Backend/koi/camera/mask.py::OPTIONAL_REGIONS` | added | +1/-0 |  |  |
| `Backend/koi/camera/mask.py::REGION_NAMES` | added | +1/-0 |  |  |
| `Backend/koi/camera/mask.py::__all__` | modified | +2/-1 |  |  |
| `Backend/koi/camera/mask.py::<module>` | added | +7/-0 |  |  |
| `Backend/koi/storage/base.py::Storage.insert_image` | modified | +4/-3 |  | mask_version and baseline_reset (migration 0010), quality and |
| `Backend/koi/storage/base.py::Storage.fetch_camera_mask` | modified | +3/-2 |  | The pond's camera_config row {pond_id, mask, regions, |
| `Backend/koi/storage/base.py::Storage.fetch_camera_mask_versions` | modified | +1/-1 |  | Every saved mask of the pond {pond_id, mask_version, mask, |
| `Backend/koi/storage/base.py::Storage.save_camera_mask` | modified | +3/-2 |  | Stores mask (a validated polygon, koi/camera/mask.py, or None) |
| `Backend/koi/storage/base.py::IMAGE_COLUMNS` | modified | +1/-1 |  |  |
| `Backend/koi/storage/base.py::CAMERA_CONFIG_COLUMNS` | modified | +1/-1 |  |  |
| `Backend/koi/storage/base.py::CAMERA_MASK_VERSION_COLUMNS` | modified | +1/-1 |  |  |
| `Backend/koi/storage/base.py::<module>` | modified | +2/-2 |  |  |
| `Backend/koi/storage/memory.py::MemoryStorage.insert_image` | modified | +3/-2 |  |  |
| `Backend/koi/storage/memory.py::MemoryStorage.save_camera_mask` | modified | +5/-3 |  |  |
| `Backend/koi/storage/memory.py::TABLE_COLUMNS` | modified | +1/-1 |  |  |
| `Backend/koi/storage/supabase_storage.py::SupabaseStorage.insert_image` | modified | +3/-1 |  |  |
| `Backend/koi/storage/supabase_storage.py::SupabaseStorage.save_camera_mask` | modified | +7/-2 |  |  |
| `supabase/migrations/0023_camera_regions.sql` | new file | +191/-0 |  |  |

Plus 6 test file(s), 1 doc file(s).

**Removed symbols**

None.

**Scope**

Declared: `Backend/koi/camera/mask.py::validate_polygon`, `Backend/koi/camera/hsvEngine.py`, `Backend/koi/camera/camera.py`, `Backend/koi/api/routes.py`, `supabase/migrations/**`. Verdict: violation.
- out of scope: `Backend/koi/api/responses.py::CameraMaskVersion` (modified); explained in the record: no
- out of scope: `Backend/koi/api/responses.py::CameraMaskResponse` (modified); explained in the record: no
- out of scope: `Backend/koi/api/schemas.py::CameraMaskUpdate` (modified); explained in the record: yes
- out of scope: `Backend/koi/api/schemas.py::CameraMaskUpdate._valid_polygon` (modified); explained in the record: yes
- out of scope: `Backend/koi/api/schemas.py::CameraMaskUpdate._valid_regions` (added); explained in the record: yes
- out of scope: `Backend/koi/api/schemas.py::CameraMaskUpdate._mask_or_regions` (added); explained in the record: yes
- out of scope: `Backend/koi/camera/mask.py::__all__` (modified); explained in the record: no
- out of scope: `Backend/koi/camera/mask.py::<module>` (added); explained in the record: no
- out of scope: `Backend/koi/storage/base.py::Storage.insert_image` (modified); explained in the record: no
- out of scope: `Backend/koi/storage/base.py::Storage.fetch_camera_mask` (modified); explained in the record: no
- out of scope: `Backend/koi/storage/base.py::Storage.fetch_camera_mask_versions` (modified); explained in the record: no
- out of scope: `Backend/koi/storage/base.py::Storage.save_camera_mask` (modified); explained in the record: no
- out of scope: `Backend/koi/storage/base.py::IMAGE_COLUMNS` (modified); explained in the record: no
- out of scope: `Backend/koi/storage/base.py::CAMERA_CONFIG_COLUMNS` (modified); explained in the record: no
- out of scope: `Backend/koi/storage/base.py::CAMERA_MASK_VERSION_COLUMNS` (modified); explained in the record: no
- out of scope: `Backend/koi/storage/base.py::<module>` (modified); explained in the record: no
- out of scope: `Backend/koi/storage/memory.py::MemoryStorage.insert_image` (modified); explained in the record: no
- out of scope: `Backend/koi/storage/memory.py::MemoryStorage.save_camera_mask` (modified); explained in the record: no
- out of scope: `Backend/koi/storage/memory.py::TABLE_COLUMNS` (modified); explained in the record: no
- out of scope: `Backend/koi/storage/supabase_storage.py::SupabaseStorage.insert_image` (modified); explained in the record: no
- out of scope: `Backend/koi/storage/supabase_storage.py::SupabaseStorage.save_camera_mask` (modified); explained in the record: no

**Supervisor checks passed**

whitespace/conflict-marker check, apply pending migrations to the local Supabase stack, OutdoorKoi host test suites (layout-aware), database suite (migration rehearsal, SQL tests, local profile), scope check, dangling reference check

**Consistency**

0 error(s), 55 warning(s) between the rationale above and the change.
- WARN: 'How it was verified' names a test file that does not exist: tests/camera/test_camera_regions.py
- WARN: 'How it was verified' names a test file that does not exist: tests/api/test_camera_mask_api.py
- WARN: 'How it was verified' names a test file that does not exist: tests/storage/test_camera_mask_storage.py
- WARN: 'How it was verified' names a test file that does not exist: tests/sql/test_sql_camera_regions.py
- WARN: references something that doesn't exist: camera.py::get_camera_config (no such file)
- WARN: references something that doesn't exist: 0023_camera_regions.sql
- WARN: references something that doesn't exist: quality.py
- WARN: references something that doesn't exist: responses.py
- WARN: references something that doesn't exist: hsvEngine.py
- WARN: references something that doesn't exist: mask.py
- WARN: Backend/koi/api/responses.py::CameraMaskVersion (modified) changed but not mentioned in the record
- WARN: Backend/koi/api/responses.py::CameraMaskResponse (modified) changed but not mentioned in the record
- WARN: Backend/koi/api/routes.py::_camera_mask_response (modified) changed but not mentioned in the record
- WARN: Backend/koi/camera/camera.py::get_camera_config (added) changed but not mentioned in the record
- WARN: Backend/koi/camera/camera.py::get_mask (modified) changed but not mentioned in the record
- WARN: Backend/koi/camera/camera.py::push_current_data (modified) changed but not mentioned in the record
- WARN: Backend/koi/camera/hsvEngine.py::_polygon_mask (added) changed but not mentioned in the record
- WARN: Backend/koi/camera/hsvEngine.py::_rgb_means (added) changed but not mentioned in the record
- WARN: Backend/koi/camera/hsvEngine.py::GAP_PLANTS_OVER (added) changed but not mentioned in the record
- WARN: Backend/koi/camera/hsvEngine.py::GAP_NO_PIXELS (added) changed but not mentioned in the record
- WARN: Backend/koi/camera/mask.py::REQUIRED_REGIONS (added) changed but not mentioned in the record
- WARN: Backend/koi/camera/mask.py::OPTIONAL_REGIONS (added) changed but not mentioned in the record
- WARN: Backend/koi/camera/mask.py::REGION_NAMES (added) changed but not mentioned in the record
- WARN: Backend/koi/camera/mask.py::__all__ (modified) changed but not mentioned in the record
- WARN: Backend/koi/storage/base.py::Storage.insert_image (modified) changed but not mentioned in the record
- WARN: Backend/koi/storage/base.py::Storage.fetch_camera_mask (modified) changed but not mentioned in the record
- WARN: Backend/koi/storage/base.py::Storage.fetch_camera_mask_versions (modified) changed but not mentioned in the record
- WARN: Backend/koi/storage/base.py::Storage.save_camera_mask (modified) changed but not mentioned in the record
- WARN: Backend/koi/storage/base.py::IMAGE_COLUMNS (modified) changed but not mentioned in the record
- WARN: Backend/koi/storage/base.py::CAMERA_CONFIG_COLUMNS (modified) changed but not mentioned in the record
- WARN: Backend/koi/storage/base.py::CAMERA_MASK_VERSION_COLUMNS (modified) changed but not mentioned in the record
- WARN: Backend/koi/storage/base.py::<module> (modified) changed but not mentioned in the record
- WARN: Backend/koi/storage/memory.py::MemoryStorage.insert_image (modified) changed but not mentioned in the record
- WARN: Backend/koi/storage/memory.py::MemoryStorage.save_camera_mask (modified) changed but not mentioned in the record
- WARN: Backend/koi/storage/memory.py::TABLE_COLUMNS (modified) changed but not mentioned in the record
- WARN: Backend/koi/storage/supabase_storage.py::SupabaseStorage.insert_image (modified) changed but not mentioned in the record
- WARN: Backend/koi/storage/supabase_storage.py::SupabaseStorage.save_camera_mask (modified) changed but not mentioned in the record
- WARN: supabase/migrations/0023_camera_regions.sql (new file) changed but not mentioned in the record
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/api/responses.py::CameraMaskVersion
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/api/responses.py::CameraMaskResponse
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/camera/mask.py::__all__
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/camera/mask.py::<module>
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/storage/base.py::Storage.insert_image
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/storage/base.py::Storage.fetch_camera_mask
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/storage/base.py::Storage.fetch_camera_mask_versions
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/storage/base.py::Storage.save_camera_mask
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/storage/base.py::IMAGE_COLUMNS
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/storage/base.py::CAMERA_CONFIG_COLUMNS
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/storage/base.py::CAMERA_MASK_VERSION_COLUMNS
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/storage/base.py::<module>
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/storage/memory.py::MemoryStorage.insert_image
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/storage/memory.py::MemoryStorage.save_camera_mask
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/storage/memory.py::TABLE_COLUMNS
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/storage/supabase_storage.py::SupabaseStorage.insert_image
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/storage/supabase_storage.py::SupabaseStorage.save_camera_mask

**Commit**

This record is committed with the change it describes; `git log -1 -- docs/decisions/0091-named-camera-regions.md` gives the commit.
<!-- verified-facts:end -->
