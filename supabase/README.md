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
| `0012_pond_dashboard_sources.sql` | `pond_dashboard_sources(p_pond_id)`, the rows behind `GET /v1/ponds/{pond}/dashboard` (issue #13): the newest `SensorData` row per `sensor_type` (ties by id), the cache rows of the pond's assigned stations (`metric_type` `realtime_sensor` only) and the forecast rows for its area and region, with their times and nothing substituted. Read only (`stable`), security invoker, service role only. `SensorData` grants, policies and triggers unchanged; `get_bundled_dashboard_payload` unchanged. | Not yet applied on live (owner). |
| `0013_sensordata_latest_index.sql` | Index `idx_sensordata_user_type_latest` on `SensorData` (`userID`, `sensor_type`, `created_at desc`, `id desc`) for the newest reading per channel read by `pond_dashboard_sources` (issue #13). Added after the sensor-sketch freeze was lifted; built in the migration's transaction, so node inserts wait during the build. Columns, grants, policies and triggers unchanged. | Not yet applied on live (owner). |

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
```

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

The same steps, without `migration repair`, for the next batch:

1. Rehearsed locally first: `supabase db reset`, `tools/db_rehearsal.py`
   (all legacy rows kept, `pond_profile` seeded, fresh and upgrade paths
   converge) and `tests/sql` (126 passed).
2. Read-only backup of roles, schema and data with `supabase db dump
   --linked` (`--role-only`; default; `--data-only --use-copy`), kept
   off-repo by the owner. It does not include storage object files.
3. `supabase migration list --linked` showed `0001`-`0005` applied and
   `0006`-`0011` pending; the owner ran `supabase db push --dry-run`, then
   `supabase db push`.
4. Checked afterwards: history shows `0001`-`0011`; every new object exists;
   `pond_profile` has one row per `UserData` pond (8); the `SensorData`
   definition, grants, policies and triggers are identical before and
   after, so the sensor sketch's anonymous insert is unaffected.

The repository was unlinked again afterwards (`supabase unlink`), so local
commands cannot reach live by accident. The next migration is `0012`.

## Rules for new migrations

- Name files `NNNN_short_description.sql`, using the next unused number.
- Add nullable or defaulted columns and explicit backfills. Preserve
  unknown legacy values instead of inventing them.
- The live default privileges grant `anon` and `authenticated` everything on
  new tables and functions. Until issue #12 changes that, revoke explicitly
  and enable RLS on backend-only objects, as `0002`, `0003` and `0005` do.
- Every changed RPC needs documented inputs, outputs and access rules, and
  a test in `Backend/tests/sql`.
- After adding a migration, run `supabase db reset`, the rehearsal and the
  SQL tests.
