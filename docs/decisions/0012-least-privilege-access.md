# 0012: Least-privilege data access: device tokens and secure sensor ingest
Status: implemented · Issue: #12 · Date: 2026-10-11

## In short

The sensor node no longer needs write access to `SensorData`. It calls a new database function, `ingest_readings`, with a per-device token. The database works out the pond from that token and checks every reading before storing it. A second migration takes away every table, sequence and function grant that no app or backend path uses, fixes the default privileges so that new objects start closed, and moves the `http` extension out of the API schema. Everything the app does while signed out keeps working. The role/object matrix for #116 is in `docs/access-matrix.md`.

## Problem and constraints

The live database gave `anon` and `authenticated` every privilege on every table, sequence and function, including `http_get`. Anyone holding the publishable key could therefore write readings for any pond, change backend state, run maintenance functions and make the database send HTTP requests. The node inserted into `SensorData` with that same key.

Constraints:
- `supabase db push` applies all pending migrations at once, so after each migration the app must still work signed out. RLS on pond tables and the app-facing revocations are left to #116.
- The camera's `POST /upload` and Storage are frozen (#86).
- Schema changes go in new migrations only, and existing rows and readers must keep working.
- Tests may only use the local stack.

## Approach

- **`supabase/migrations/0025_device_tokens.sql`**
  - Extends `devices`, keeping its key `(pond_id, kind)`. New columns: `device_id` (uuid), `revoked_at`, and two rate-limit window columns.
  - Adds `device_tokens`, which stores only the sha256 of each token.
  - `issue_device_token` (owner only) creates the sensor device if needed and returns a 64-hex random token. Tokens that are still valid get `expires_at = now() + overlap`, so a rotation keeps the old token working until the node is reflashed.
  - `revoke_device_token` revokes one token. Setting `devices.revoked_at` refuses every token of that device.
  - `ingest_readings(device_token, rows)` is security definer and executable by `anon`. It checks the token, then the per-device rate limit, then the batch shape: an array of 1 to 32 objects holding only `sensor_type` and `data1`. Any other key, such as `userID`, rejects the whole batch. Each row is then accepted or rejected on its own (unknown channel, not a number, out of range, duplicate). Accepted rows are inserted with the device's pond, and the reply lists what was rejected.
- **`supabase/migrations/0026_least_privilege.sql`**
  - Revokes the default privileges of `postgres` on tables, sequences and functions in `public` from `anon` and `authenticated`, and revokes global `PUBLIC` execute on new functions.
  - Drops and recreates `http` in `extensions`. That schema is not exposed by PostgREST, and the extension does not support `SET SCHEMA`.
  - Rewrites the client-reachable and definer functions with qualified names and `search_path = ''`. `get_bundled_dashboard_payload` and `get_pond_telemetry_history` become invoker and backend only. `get_historical_graph_payload` stays definer, because it refreshes `daily_sensor_averages`, and now refuses `p_days` outside 1..366.
  - Revokes everything in `public` from `PUBLIC`, `anon` and `authenticated`, then grants back exactly the app's calls (see the matrix). The #8 functions are explicitly granted to `service_role`.
- **Firmware.** `Embedded/sensor_node/sensor_node.ino` and `Embedded/full_sketch/full_sketch.ino` (the build on the board) now POST to `/rest/v1/rpc/ingest_readings` with the core's `HTTPClient`. They send `SENSOR_DEVICE_TOKEN` and no pond id. A failed lux read is now left out instead of being sent as -1. `Embedded/libraries/koi_sensing/src/ingest_reply.h::ingestAccepted` and `ingestStatusText` read the reply. The installed ESPSupabase `rpc()` posts to `<url>/rpc/...`, which is missing `/rest/v1`, so it could not be used.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Token hash columns on `devices` (current and previous) | Allows only one overlapping token, and revocation history would be lost; a token table keeps `devices` columns and readers unchanged. |
| Reject the whole batch on any invalid value | One bad probe (an uncalibrated pH) would drop the good temperature and TDS rows of that cycle. Shape and ownership problems still reject the whole batch. |
| Revoke `http` grants in place | `postgres` is not the grantor (`supabase_admin` is), so `REVOKE` only warns; tested locally. |
| Change `supabase_admin` default privileges | Permission denied to `postgres`; documented instead (extensions go in `extensions`). |
| ESPSupabase `rpc()` | Wrong URL in the installed version (see above). |

## Trade-offs

- `get_historical_graph_payload` still serves any pond to anyone with the publishable key until #116.
- The rate limit counts only accepted calls. Floods of wrong tokens are not limited in the database, and each one costs a hash and an index lookup.
- TLS on the node is still not certificate-checked (as before), so someone on the node's network path could read the token. Pinning a root CA is a follow-up.
- Readings that miss their range are dropped with a reason in the reply, but the node does not log them anywhere persistent.

## Key parameters

- **Batch size, 32 rows.** The node sends at most 6 rows per cycle, so 32 leaves room for a few buffered cycles. Raising it allows bigger catch-up uploads; lowering it below 6 breaks the node.
- **Rate limit, 6 accepted calls per device per 60 s window.** `sensor_node` uploads at most about 2-3 times a minute; `full_sketch` uploads every 5 min, plus manual service-mode uploads. Lowering it risks refusing retries; raising it weakens flood protection.
- **Rotation overlap, 24 hours by default, 0 to 30 days.** This is the time allowed to reflash the node after issuing a new token.
- **Token, 32 random bytes as hex.** Accepted length is 32 to 256 characters.
- **Channel ranges.** temp -10..60 C (rejects -127 and 85 sensor faults), pH 0..14, TDS 0..5000 ppm, LUX 0..200000 lx, battery_mv 0..20000 mV, reset_reason integer 0..255. These are plausibility bounds chosen here; they are not model constants.
- **`p_days`, 1..366.** The app asks for 7 or 30.

## Assumptions

- Live migrations run as `postgres`, and live matches the local stack in grantors (`supabase_admin` for `http`). If live allows the in-place revoke, the move is still correct.
- `drop extension http` is allowed on live and nothing outside the schema depends on it. The rehearsal on the dump passed; live itself was not checked.
- The app's only direct Supabase calls are the ones in `MobileUI/mobile_app/lib/data/pond_data_source.dart` and `lib/utils/pond_camera_storage.dart` (found by grep), plus Storage, which is unchanged.
- Trigger functions need no `EXECUTE` grant to fire (confirmed by the anon onboarding test).

## Edge cases

- Handled:
  - wrong, empty, too-long, expired or revoked token, and revoked device: all return HTTP 401 with the same message;
  - forged `userID`/`pond_id`/`id`/`created_at`: 400, nothing stored;
  - 33 or more rows: 413;
  - empty or non-array input: 400;
  - NaN, which ArduinoJson writes as null: rejected `not_a_number`;
  - `1e400`: `out_of_range`;
  - duplicate channel: the first row is kept;
  - rotating the token does not reset the rate limit.
- Not handled:
  - GY-33 rows (`UPLOAD_GY33`) are rejected as unknown channels until they are added to the allowed list.
  - Deleting a pond from the app fails when it has a `pond_profile` row (0008, unchanged by this issue).

## Changes outside the scope

None beyond the issue's areas. Two test-file changes were needed:
- `Backend/tests/storage/test_schema.py::firmware_refs` now recognises `/rest/v1/rpc/<name>` calls, so the scan still covers the sensor sketch.
- Five existing SQL tests that pinned the old open grants now assert the new ones: `test_sql_dashboard`, `test_sql_device_health`, `test_sql_sensor_ingest` and `test_sql_evaluation_provenance` (`test_provenance_tables_are_backend_only`).

## How it was verified

- `Backend/tests/sql/test_sql_least_privilege.py`, 80 cases on the local stack as anon, authenticated, service_role and postgres. They cover:
  - the token cases and rotation;
  - the batch, sample and range checks, and the rate limit;
  - the exact anon and authenticated grant sets;
  - every signed-out app call;
  - the shared weather read;
  - refused internal functions;
  - the #8 calls as service_role;
  - fixed search paths;
  - the `http` move;
  - new objects created by `postgres`.
- `tools/db_rehearsal.py`: the dump-upgrade and fresh paths converge, and every legacy row is kept.
- A manual run against the local PostgREST with the anon key gave 200, 401, 400, 413 and 429 as designed, refused direct inserts, and returned 404 for `rpc/http_get`.
- Firmware: host tests, plus ESP32 compiles of `sensor_node` and `full_sketch`.
- Not verified: anything on the board or on live (flashing, the OneMap call, live grantors).

## To change this

- To allow GY-33 rows, add `GY33_R/G/B/C` to the channel list and ranges in `ingest_readings`, in a new migration. The cost is that those rows become stored readings.
- To accept #17 sample times, add `sampled_at` to the accepted keys (a new column, nullable). When every node sends it, make it required: this is the v1 sunset.
- To favour catch-up after outages, raise `c_max_rows` from 32 to 128. The cost is bigger requests on the node.
- Likely bug sources:
  - a new app call without a grant: it fails with 401/42501 as anon;
  - a new migration that relies on the old defaults: its objects are invisible to the app.

## Rollback

`ingest_readings` and the token tables depend on 0025, and the firmware depends on `ingest_readings`.
- To reopen the node's direct path, grant `insert on "SensorData"` (and sequence usage) to `anon` in a new migration.
- Reverting 0026 means re-granting the old privileges and recreating `http` in `public`.
- After any revert, check the app signed out, the node's rows and the worker's `/ready`.

<!-- verified-facts:begin (generated by integrity record; do not edit) -->
## Verified facts

Generated 2026-10-11 09:23 UTC for issue #12 attempt 1; compared HEAD..working tree (base c6950be).

**Changed components**

| Component | Change | Lines +/- | Tags | Description |
|---|---|---|---|---|
| `Embedded/full_sketch/full_sketch.ino` | modified | +34/-23 |  |  |
| `Embedded/full_sketch/secrets.h.example` | modified | +6/-0 |  |  |
| `Embedded/libraries/koi_sensing/src/ingest_reply.h` | new file | +37/-0 |  |  |
| `Embedded/libraries/koi_sensing/src/koi_sensing.h` | modified | +1/-0 |  |  |
| `Embedded/sensor_node/secrets.h.example` | modified | +6/-0 |  |  |
| `Embedded/sensor_node/sensor_node.ino` | modified | +53/-24 |  |  |
| `supabase/migrations/0025_device_tokens.sql` | new file | +318/-0 |  |  |
| `supabase/migrations/0026_least_privilege.sql` | new file | +533/-0 |  |  |

Plus 7 test file(s), 2 doc file(s).

**Removed symbols**

None.

**Scope**

No Scope line in the issue.

**Supervisor checks passed**

whitespace/conflict-marker check, apply pending migrations to the local Supabase stack, OutdoorKoi host test suites (layout-aware), database suite (migration rehearsal, SQL tests, local profile), scope check, dangling reference check

**Consistency**

0 error(s), 6 warning(s) between the rationale above and the change.
- WARN: ambiguous reference: lib/utils/pond_camera_storage.dart matches 2 files (MobileUI/mobile_app/lib/utils/pond_camera_storage.dart, archive/pre-refactor/MobileUI/mobile_app/lib/utils/pond_camera_storage.dart); use the repo-relative path
- WARN: references something that doesn't exist: rpc/http_get
- WARN: references something that doesn't exist: GY33_R/G/B/C
- WARN: Embedded/full_sketch/secrets.h.example (modified) changed but not mentioned in the record
- WARN: Embedded/libraries/koi_sensing/src/koi_sensing.h (modified) changed but not mentioned in the record
- WARN: Embedded/sensor_node/secrets.h.example (modified) changed but not mentioned in the record

**Commit**

This record is committed with the change it describes; `git log -1 -- docs/decisions/0012-least-privilege-access.md` gives the commit.
<!-- verified-facts:end -->
