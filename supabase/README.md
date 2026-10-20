# Supabase schema

`migrations/` contains the repository's numbered schema changes. Every schema
change belongs in a migration; do not make an unrecorded live-only change.

| File | What it contains | Live status |
|---|---|---|
| `0001_baseline.sql` | The live schema as observed: the supplied dump (`schema.sql`) plus one comment line, so every public function matches live exactly. Defects included. | Recorded as applied on 2026-10-01 (`migration repair`); never run it there. |
| `0002_worker_lease.sql` | `pond_chemistry_state.snapshot_version`, the `worker_lease` table, and `save_pond_snapshot` / `take_worker_lease` used by the separated API and poller (issue #8). | Applied 2026-10-01. |
| `0003_worker_status.sql` | The `worker_status` table: the poller's last-cycle report, read by the API's `/ready` and `/metrics` (issue #10). | Applied 2026-10-01. |
| `0004_observed_storage.sql` | The live storage buckets (`imageAnalysisBucket`, `FishImages`) and their eight anon policies, as observed. | Applied 2026-10-01 (no change on live). |
| `0005_algae_severity_ratings.sql` | The `algae_severity_ratings` table for the app's rating card. | Applied 2026-10-01. |
| `0006_hourly_history_utc_instants.sql` | `get_pond_telemetry_history` returns UTC instants whatever the session time zone (defect D9, issue #15). | Applied 2026-10-03. |
| `0007_auth_link.sql` | `UserData.auth_uid` (nullable, unique: one pond per account until #67), a trigger refusing app-role changes to the link, `auth_session_active` (backend only) and `link_pond_to_account` (owner only, SQL editor) for the API's sign-in check (issue #11). Access per function is listed in its header. | Applied 2026-10-03. |
| `0008_pond_profile.sql` | The effective-dated `pond_profile` table (volume, depth, biomass in grams, fish, tap water, aeration), seeded from `UserData` with biomass converted from kg to g, and a trigger recording later `UserData` volume or biomass writes as new rows (issue #16). Backend only; the trigger function is security definer so the app's onboarding upsert keeps working. | Applied 2026-10-03. |
| `0009_weather_history.sql` | NEA weather history beside the latest caches (issue #24): `weather_observation`, `weather_forecast_issuance`, `weather_ingest_window`, the seeded `weather_station_regime`, nullable `weather_telemetry.source_times` and `weather_forecasts.source_issued_at`, the job's write path `ingest_weather_batch` and the as-of reads `weather_forecast_as_of`, `weather_observations_as_of`, `weather_rainfall_total`. Backend only. Rules in the file header and [docs/weather-ingestion.md](../docs/weather-ingestion.md). | Applied 2026-10-03. |
| `0010_camera_mask.sql` | The camera's per-pond water mask (issue #39): `camera_config` (the mask in force, a normalised polygon, with its `mask_version`), the immutable `camera_mask_version` history, `camera_mask_is_valid` (3 to 64 points in 0..1, area at least 0.01 of the frame) and `save_camera_mask` (next version, one transaction, serialised per pond), plus nullable `imageTable.mask_version` and `imageTable.baseline_reset`. New tables and functions backend only; `imageTable` grants unchanged. | Applied 2026-10-03. |
| `0011_frame_quality.sql` | The camera's frame quality gate and thumbnails (issue #40): nullable `imageTable.quality` (versioned result: status pass/fail/unknown, reason codes, metrics and thresholds; null on older rows reads as unknown) and `imageTable.thumbnail_path` (object path beside the frame, checked not to be a URL), and `image_quality_status` (backend only). No index; `imageTable` grants unchanged. | Applied 2026-10-03. |
| `0012_pond_dashboard_sources.sql` | `pond_dashboard_sources(p_pond_id)`, the rows behind `GET /v1/ponds/{pond}/dashboard` (issue #13): the newest `SensorData` row per `sensor_type` (ties by id), the cache rows of the pond's assigned stations (`metric_type` `realtime_sensor` only) and the forecast rows for its area and region, with their times and nothing substituted. Read only (`stable`), security invoker, service role only. `SensorData` grants, policies and triggers unchanged; `get_bundled_dashboard_payload` unchanged. | Applied (in the live migration history; objects checked read-only on 2026-10-05). |
| `0013_sensordata_latest_index.sql` | Index `idx_sensordata_user_type_latest` on `SensorData` (`userID`, `sensor_type`, `created_at desc`, `id desc`) for the newest reading per channel read by `pond_dashboard_sources` (issue #13). Added after the sensor-sketch freeze was lifted; built in the migration's transaction, so node inserts wait during the build. Columns, grants, policies and triggers unchanged. | Applied (in the live migration history; objects checked read-only on 2026-10-05). |
| `0014_sensor_ingest_ledger.sql` | Lossless sensor ingestion for the poller (issue #18): `sensor_ingest_cursor` (per pond, the newest `SensorData.created_at` ingested) and `sensor_ingest_ledger` (one row per `SensorData` row ingested, with its effective sample time, time basis `ingestion` or `sample`, disposition and the snapshot version that applied it); `sensor_ingest_pending` (the pond's rows not in the ledger, from the watermark minus an overlap, read only) and `save_pond_snapshot_with_ingest` (snapshot compare and set, ledger rows and watermark in one transaction; a row already in the ledger fails the save). Both tables RLS on with no policies; tables and functions revoked from `anon` and `authenticated`, granted to `service_role`. `SensorData` unchanged (read only). | Applied (in the live migration history; objects checked read-only on 2026-10-05). |
| `0015_event_ledger.sql` | The event ledger (issue #19): `pondInterventions.event_id uuid` (nullable, unique, default `gen_random_uuid()`; existing rows backfilled with `intervention_event_id(id)`, an md5 of the row id, so a re-run gives the same UUIDs), index `idx_pondinterventions_user_type_time`, and three backend-only functions: `backfill_intervention_event_ids` (one pond's null event_ids), `pond_interventions_since` (one pond's rows for reconciliation) and `sensor_ingest_history` (ingested readings for a replay). `get_historical_graph_payload` adds `event_id` to each intervention and keeps `id`, `pct` and `litres` until #56. `pondInterventions` grants and policies unchanged. | Applied (in the live migration history; objects checked read-only on 2026-10-05). |
| `0016_evaluation_provenance.sql` | Model provenance on the three evaluation tables (issue #21): nullable `model_version text`, `input_cutoff timestamptz` (newest sample, event or frame time consumed; `evaluated_at` stays the computation time), `forecast_issued_at timestamptz` (the provider's issue time when every forecast record used shares it, else null) and `inputs jsonb` (run, input counts and per-record forecast provenance), with a shape check (non-empty version, object inputs). No defaults and no backfill: rows written before it read null, meaning unknown. Grants, policies and indexes unchanged. | Applied (in the live migration history; objects checked read-only on 2026-10-05). |
| `0017_evaluation_daily.sql` | Retention and daily summaries for evaluation rows (issue #22): the `evaluation_daily` table (one row per pond, domain and local day: row count, first/last time, status counts, first/last/min/max of the headline estimates with a null kept null, and the model versions that wrote them), `summarize_evaluation_days(p_before, p_time_zone, p_max_days)` (summarises the days before `p_before`, never today or a pond's newest day, then deletes exactly the summarised rows and checks the counts, in one transaction; idempotent; late rows merged) and three merge helpers. Model estimates and outcomes only; observed values stay in `daily_sensor_averages`. RLS on with no policies; table and functions revoked from `anon` and `authenticated`, granted to `service_role`. The evaluation tables' grants and policies are unchanged. | Applied (in the live migration history; objects checked read-only on 2026-10-05). |
| `0018_device_health.sql` | Device freshness and health (issue #23): `devices` (one row per pond and kind, `sensor` or `camera`: nullable `expected_interval_seconds`, the configured cadence; `last_seen_at`, receipt time, forward only; `next_expected_at`) and `device_contact` (one row per contact received, with the time it was expected next under the interval then in force; kept 30 days), `record_device_contacts(p_pond_id, p_kind, p_contacts)` (idempotent; called by the poller per ingested upload and by the camera service per stored frame) and `pond_device_activity(p_pond_id, p_since)` (devices, contacts in the window plus the one before it, `battery_mv` rows, newest `reset_reason` row; read only). `battery_mv` and `reset_reason` are new `sensor_type` spellings the node may send; the poller does not ingest them as model inputs. RLS on with no policies; tables and functions revoked from `anon` and `authenticated`, granted to `service_role`. `SensorData` unchanged. | Applied (in the live migration history; objects checked read-only on 2026-10-05). |
| `0019_salt_filter_events.sql` | Salt addition and filter cleaning (issue #29): nullable `pondInterventions.salt_grams numeric` and `notes text`, with `NOT VALID` checks so new SALT rows need a finite positive mass and notes stay within 1000 characters (older rows are kept). `pond_interventions_since` and `get_historical_graph_payload` return both fields with unchanged signatures and grants; graph daily pH/TDS rows derive `after_maintenance` from filter-cleaning intervals (24 hours, Singapore days), other channels are false, and interventions are ordered by event time then id. | Applied 2026-10-05 (checked read-only on live afterwards). |
| `0020_kit_readings.sql` | Owner-entered test-kit readings (issue #30): the `kit_readings` table (pond, `taken_at`, kit, ammonia, nitrite, nitrate, pH, KH, notes, and the model's estimates, differences and comparison provenance as `jsonb`), all measurement fields nullable, with checks that values are finite and nonnegative and pH is within 0..14; index `kit_readings_pond_time` (pond, `taken_at`, id). The reference to `UserData` is `on update cascade` with no cascading delete, as for `pond_profile` and `camera_config`. Readings never update engine snapshots or evaluations. RLS on with no policies; table and identity sequence revoked from `anon` and `authenticated`, granted to `service_role`. Existing tables unchanged. | Applied 2026-10-05 (checked read-only on live afterwards). |
| `0021_camera_colour.sql` | Camera colour observations (issue #90): nullable `imageTable.gcc real` and versioned `colour jsonb` (normalised ExG/RGB means and a whole-frame GCC grid). `image_quality_status` recognises quality versions 1 and 2; signature, backend-only access and existing table grants/indexes unchanged. | Applied before 2026-10-11 (date not recorded; `migration list` and a dry-run push confirmed 2026-10-11). |
| `0022_pond_calibration.sql` | Per-pond calibration versions (issue #32): the append-only `pond_calibration` table (pond, `parameter` one of `evaporation_shelter_factor`, `water_air_temperature`, `nitrification_rate_scale`; `status` `fitted` or `pending`; `value`, `lag_hours`, `fitted_at`, `effective_from`, `sample_count`, `error` and `details` as `jsonb` objects, training and evaluation intervals with evaluation after training, `method_version`), checks that a fitted row has a finite value and a pending one none; index `pond_calibration_in_force` (pond, parameter, `effective_from`, id). The reference to `UserData` is `on update cascade` with no cascading delete. The version in force at a time is the newest fitted row with `effective_from` at or before it. RLS on with no policies; table and identity sequence revoked from `anon` and `authenticated`, granted to `service_role`. Existing tables unchanged. | Applied before 2026-10-11 (date not recorded; `migration list` and a dry-run push confirmed 2026-10-11). |
| `0023_camera_regions.sql` | Named camera regions (issue #91): `camera_regions_are_valid`, nullable `regions jsonb` on `camera_config` and `camera_mask_version` (`water_gap`, `rim`, `plants`, optional `reference`), `mask` made nullable there (a row needs a mask, regions or both), `save_camera_mask(p_pond_id, p_mask, p_regions default null)` replacing the two-argument version, and nullable `imageTable.regions` (per-region metrics). Rows with a mask and no regions behave as before; the camera's `POST /upload` is unchanged. | Applied before 2026-10-11 (date not recorded; `migration list` and a dry-run push confirmed 2026-10-11). |
| `0024_notification_outbox.sql` | The notification outbox (issue #36): `notification_outbox` (pond, kind, severity, title, body, data, `dedupe_key`, `created_at`, `sent_at`, error) and `enqueue_notification` with a per-key cool-down. Backend only: RLS on with no policies, revoked from anon and authenticated, granted to `service_role`. Existing tables unchanged. | Applied before 2026-10-11 (date not recorded; `migration list` and a dry-run push confirmed 2026-10-11). |
| `0025_device_tokens.sql` | Per-device tokens and token-scoped sensor ingest (issue #12): `devices` gains `device_id` (uuid, unique), `revoked_at` and the rate-limit window columns, all defaulted or nullable; `device_tokens` (sha256 hash only, `created_at`, `expires_at` for rotation overlap, `revoked_at`, `last_used_at`); owner-only `issue_device_token(p_pond_id, p_overlap default 24 hours, p_label)` and `revoke_device_token(p_token_id)`; `ingest_readings(device_token, rows)` (security definer, executable by `anon` and `service_role`): pond from the token, only `sensor_type`/`data1`, six channels with ranges, at most 32 rows and 6 calls per device per minute. Rules in the file header. The direct `SensorData` insert still works. | Not applied. Apply alone, before 0026 (see below). |
| `0026_least_privilege.sql` | Least-privilege grants (issue #12): default privileges of `postgres` no longer grant `anon`/`authenticated` (or `PUBLIC` execute); `http` moved from `public` to `extensions`; definer and app-callable functions get `search_path=''` and qualified names; `get_bundled_dashboard_payload` and `get_pond_telemetry_history` become invoker and backend only; `get_historical_graph_payload` refuses `p_days` outside 1..366; every table, sequence and function revoked from `PUBLIC`, `anon`, `authenticated`, then the app's signed-out calls granted back. Ends the node's direct `SensorData` insert. Matrix in [docs/access-matrix.md](../docs/access-matrix.md). | Not applied. Apply only after the node uploads through `ingest_readings`. |
| `0027_sensor_payload_v2.sql` | Sensor payload v2 (issue #17, and the database half of #97): nullable `SensorData` columns `device_id`, `boot_id`, `cycle_seq`, `sampled_at`, `flags`, `reset_reason`, `battery_mv` with checks, the retry key unique (`device_id`, `boot_id`, `cycle_seq`, `sensor_type`) where `boot_id` is set, and three per-pond indexes; `sensor_channel` (canonical spellings: `lux` reads as `LUX`, nothing else is folded); `ingest_readings` accepts v2 rows (server-derived pond and device, row-level checks of the new fields, sample time never filled from receipt, a repeated cycle stored once; v1 rows still accepted); `daily_sensor_averages.sample_count`/`sample_time_count`; `aggregate_daily_sensor_data(p_from, p_to, p_pond_id)` (bounded, skips rows with no pond) replacing the whole-table version; `daily_sensor_refresh` and `reconcile_daily_sensor_averages(p_pond_id, p_days)`, which `get_historical_graph_payload` now calls for the requested pond and window only; `get_pond_telemetry_history` (recreated, adds `time_basis`), `get_bundled_dashboard_payload` (adds `latest_readings`, history `time_basis`), `pond_dashboard_sources`, `sensor_ingest_pending` and `sensor_ingest_history` read `coalesce(sampled_at, created_at)` and return `sampled_at`/`flags`. No backfill. Rules in the file header. | Not applied. Apply after 0026 (see below). |
| `0028_battery_low.sql` | Sensor node battery (issue #42): `SensorData.flags` gains bit 16 `battery_low` (check and `ingest_readings` now accept 0..31; the function is otherwise 0027's); `notification_outbox.kind` accepts `battery_low`; `pond_device_activity` also reads the v2 `battery_mv` column (one reading per cycle, with `low` from the flag) and the v2 `reset_reason` (dated from the first cycle of that boot). Same signatures and access. Rules in the file header. | Not applied. Apply after 0027 and before flashing a node that sets flag 16. |

How each file was derived, the live evidence and every known difference
and defect are in
[docs/database-reconciliation.md](../docs/database-reconciliation.md).

## The observed snapshot

`schema.sql` at the repository root is the owner-supplied schema-only dump
(`schema(1).sql`), committed unmodified (SHA-256 in the reconciliation
report). Do not edit or regenerate it. `.gitattributes` keeps its bytes and
those of `0001_baseline.sql`, whose function bodies use CRLF as on live.

The live project had no migration history when it was checked, so
`0001_baseline.sql` was corrected in place to match it; the earlier
reconstruction is kept in `archive/pre-refactor/supabase/`. From now on,
never edit an applied migration; add the next numbered one instead.

## Local stack and tests

Requires Docker Desktop and the Supabase CLI. From the repository root:

```bash
supabase start                   # first run downloads the images
supabase db reset                # rebuild the local database from migrations/
python tools/db_rehearsal.py     # fresh path and dump-upgrade path must converge
cd Backend && python -m pytest -q tests/sql      # SQL behaviour tests
cd Backend && python -m pytest -q -k schema      # text-based reference check
python tools/check.py database   # rehearsal, SQL tests and the dev stack's local database profile
```

`python -m koi.dev --profile supabase --check` (docs/dev.md) writes the
demo seed into the local database (two ponds, a sensor row with no pond,
a pond with pH but no TDS, two readings at the same instant, the camera's
old plain-text state, the weather cache slots, station metadata and an
engine snapshot from before `snapshot_version`), checks every migration is
applied, serves the API over the local REST API, validates its responses
against the OpenAPI document and compares them with the in-memory profile,
then removes the seeded rows.

`tools/db_rehearsal.py` restores `schema.sql` into a clean local database,
loads synthetic legacy rows, applies every later migration, and compares
the result with a fresh `supabase db reset`: public schema (definitions,
grants, policies, publication membership), storage buckets and policies,
and every legacy row. It leaves the local database at the current
migrations.

The SQL tests run each case in a rolled-back transaction, refuse any
`KOI_TEST_DB_URL` that is not local, and skip when no local database
answers (as in CI). If Docker is not running, report them as SKIP; text
parsing does not replace them.

Tests, scripts and CI never contact the live project, the NEA API or
PythonAnywhere.

## Bringing the live project in line (owner, issue #74)

Done on 2026-10-01 with the steps below, after a full backup of live was
taken, restored and checked locally, the upgrade rehearsed on that copy,
and a rollback script tested (backups and rollback are kept off-repo by
the owner). Verified afterwards: history shows `0001`-`0005`, the new
objects exist with RLS on and closed to `anon`, the existing snapshot has
`snapshot_version` 1, and all seven public function checksums are
unchanged. Keep the steps for any future project built from the same
dump.

Live needs `0002`, `0003` and `0005` (`0004` is a no-op there). `0001`
must not run on live; it is recorded as applied because it matches live,
which the reconciliation report shows (function checksums equal, dump
restored and compared).

1. Back up the database (Supabase dashboard, Database, Backups).
2. Link the CLI: `supabase link --project-ref mkzfdxhzmrnapvrhshte`.
3. Record the baseline as applied, without running it:
   `supabase migration repair --status applied 0001`.
4. Review what would run: `supabase db push --dry-run`. It must list
   exactly `0002`-`0005`.
5. Apply: `supabase db push`.
6. Check: `supabase migration list --linked` shows `0001`-`0005` applied;
   `select snapshot_version from pond_chemistry_state;` returns 1 for the
   existing row; the API's `/ready` returns 200 once the worker has
   written its first status.

Agents must not run `migration repair` or `db push`; these steps are the
owner's.

## Applying 0006-0011 (owner, 2026-10-03)

These migrations are applied to the live project as of 2026-10-03. Evidence
recorded by the owner:

1. A full live backup was taken first (roles, schema and data; storage files
   excluded) and retained off-repository. The read-only backup used
   `supabase db dump --linked` (`--role-only`, default schema dump, and
   `--data-only --use-copy`).
2. The owner restored the backup locally and rehearsed the upgrade with
   `supabase db reset`, `tools/db_rehearsal.py`
   (all legacy rows kept, `pond_profile` seeded, fresh and upgrade paths
   converge) and `tests/sql` (126 passed).
   The backup was kept off-repository; it does not include storage object
   files.
3. Before applying, `supabase migration list --linked` showed `0001`-`0005`
   applied and `0006`-`0011` pending; the owner ran `supabase db push
   --dry-run`, then `supabase db push`.
4. Post-apply checks: history shows `0001`-`0011`; every new object exists;
   `pond_profile` has one row per `UserData` pond (8); the `SensorData`
   definition, grants, policies and triggers are identical before and
   after, so the sensor sketch's anonymous insert is unaffected.

The repository was unlinked again afterwards (`supabase unlink`), so local
commands cannot reach live by accident. The next migration in the original
sequence was `0012`.

## Applying 0025 and 0026 (owner, issue #12)

`supabase db push` applies every pending migration, and `0026` ends the
sensor node's direct insert, so the two go in separately:

1. In a clean worktree of this commit, delete
   `supabase/migrations/0026_least_privilege.sql` (locally only), then
   `supabase db push --dry-run` must list only `0025`; push.
2. In the SQL editor: `select public.issue_device_token(<pond id>);` and put
   the token in the node's `secrets.h` as `SENSOR_DEVICE_TOKEN`. Flash the
   node and check new `SensorData` rows arrive and the serial log shows
   `ingest_readings -> n stored` (`full_sketch`: `Supabase: n of m stored`).
3. Restore the file (`git checkout -- supabase/migrations`), dry-run (only
   `0026`), push. No backend code changed and the API, worker and camera
   service use the secret key, so a redeploy is optional; check the
   API's `/ready` (the worker's lease and status) and that the pond's
   snapshot keeps updating, a new camera frame row,
   and the app signed out (onboarding, events, species, camera history,
   history graph).

Full order and checks in
[docs/decisions/0012-least-privilege-access.md](../docs/decisions/0012-least-privilege-access.md).

## Applying 0027 (owner, issue #17)

`0027` needs `0025` (it references `devices.device_id`) and keeps v1 rows
working, so it can go in with `0026` or after it. In step 1 above,
delete both `0026` and `0027` locally; in step 3 restore them and the
dry-run lists `0026` and `0027`. Then:

1. Deploy the API and worker from the same commit (the dashboard reads
   `sampled_at` and `flags`; older code ignores them, so the order is not
   critical).
2. Flash `sensor_node` (the v2 build). Over serial, check `Cold boot
   (...)`, a `boot_id`, increasing `cycle` numbers, `Clock set by SNTP`
   and `sampled_at` times in UTC; `ingest_readings -> n accepted`.
3. In the SQL editor: `select boot_id, cycle_seq, sampled_at, created_at,
   flags from public."SensorData" order by id desc limit 8;` shows the
   sample time before the insert time.
4. The first graph call per pond recomputes its window. Older history
   can be recomputed with
   `select public.aggregate_daily_sensor_data('2026-01-01', '2026-12-31');`
   (at most 366 days per call).

Full checks in
[docs/decisions/0017-sensor-payload-v2.md](../docs/decisions/0017-sensor-payload-v2.md).

## Rules for new migrations

- Name files `NNNN_short_description.sql`, using the next unused number.
- Add nullable or defaulted columns and explicit backfills. Preserve
  unknown legacy values instead of inventing them.
- From `0026` on, new tables, sequences and functions created by `postgres`
  grant nothing to `anon` or `authenticated` and functions are not
  executable by `PUBLIC`; `service_role` keeps access. Grant anything the
  app or the node needs explicitly, enable RLS on backend-only tables, and
  update [docs/access-matrix.md](../docs/access-matrix.md). Until `0026` is
  applied on live, keep the explicit revokes as `0002`-`0024` do. Create
  extensions in `extensions`, never in `public`.
- Every changed RPC needs documented inputs, outputs and access rules, and
  a test in `Backend/tests/sql`.
- After adding a migration, run `supabase db reset`, the rehearsal and the
  SQL tests.
