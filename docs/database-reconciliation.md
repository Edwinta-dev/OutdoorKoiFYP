# Database reconciliation

Issue #5. Compares the supplied schema snapshot (the observed database)
with the committed migrations, and records what to do about each
difference. Deployment of the result is tracked in issue #74.

Status (2026-10-01): live evidence collected (section 2b); baseline
corrected in place (section 9); fresh-path and dump-upgrade-path
rehearsal and SQL tests pass on the local Supabase stack (section 10).
`0002`-`0005` were applied to the live project on 2026-10-01 after a
verified backup, a rehearsal on a restored copy of live and a tested
rollback (section 11).

## 1. Observed source

| Item | Value |
|---|---|
| Supplied filename | `schema(1).sql` |
| Committed as | `schema.sql` at the repository root, commit `c6c0170` (2026-10-01) |
| SHA-256 | `02db33d25ac9474a4dd2ceb8a23ecbb443f04dd2fac399213a2f2d36da366259` |
| Size | 1,563 lines |
| Kind | `pg_dump` schema-only output (`public` schema plus extensions, publication, grants) |
| Reviewed | 2026-10-01 |
| Secret/data review | No `COPY` or `INSERT` statements, so no rows. No passwords, keys, JWTs or Supabase key prefixes. The only URL is the public OneMap search endpoint, which has no token. |

The committed file must stay byte-identical to what was supplied. Check it
with `sha256sum schema.sql`. If the owner wants it in another location (for
example `supabase/observed/`), move it with `git mv` and re-check the
checksum. Do not edit, reformat or regenerate it.

The dump records the schema at one moment, defects included. It says
nothing about row contents, applied-migration history, storage buckets,
cron jobs or deployed service versions.

## 2. Evidence

Collected from the live project on 2026-10-01 with the read-only
Supabase MCP server, at the owner's request in an attended session.
Section 2b has the results. Still open:

| Evidence | Why | How to get it |
|---|---|---|
| Deployed backend, worker and camera versions | Needed for #74's deployment ledger. | Commit hash running on PythonAnywhere |
| Enabled API schemas | Not in the dump or the MCP queries. | Supabase dashboard, API settings |
| Edge function source for `fetch-NEA-Realtime` and `fetch-NEA-forecasts` | They write the weather cache; their code is not in this repository. | Supabase dashboard, Edge Functions (#24) |

## 2b. Live evidence (read-only, 2026-10-01)

| Question | Answer | Consequence |
|---|---|---|
| Applied-migration history | None: no `supabase_migrations` schema exists. | `0001` was never applied, so the baseline is corrected in place (section 9). |
| `0002`, `0003` applied? | No: no `worker_lease`, `worker_status` or `snapshot_version`. | Pending owner deployment (#74). |
| `algae_severity_ratings` | Absent. | Moved to `0005` (N). Rating endpoints fail on live until it is applied. |
| Postgres version | 17.6 | Matches the local stack (`major_version = 17`). |
| Do live functions match the dump? | Six of seven `pg_get_functiondef` md5 sums match exactly. `get_bundled_dashboard_payload` differs only by one comment line live has (`-- 6. FETCH CATEGORY 5: ...`). | The dump is behaviourally exact; `0001` adds that line so all seven match live. |
| `SensorData` rows with no pond | 0 of 108; all rows have `userID` 455 (2026-08-12 to 2026-10-01). | D1 does not occur today. |
| `imageTable.current_state` | 40 rows, all JSON-array text such as `["base",0.0009]`; all `user_ID` 455. | Text column kept; readers parse the JSON text. |
| Storage buckets | `imageAnalysisBucket` (public, `image/jpeg`), `FishImages` (public). No `pond-images`. | Recorded in `0004` (E). App references to `pond-images` are defect D10. |
| Storage policies | Eight: anon select, insert, update and delete `.jpg` objects under a top-level `public/` folder, four per bucket. | Recorded in `0004` (E); tightening is #12. |
| `pg_cron` jobs | `fetch-nea-realtime-5min` (`*/5`) and `fetch-nea-forecasts-30min` (`*/30`), each `net.http_post` to the edge functions `fetch-NEA-Realtime` and `fetch-NEA-forecasts`. | This is the external weather writer. Jobs are not part of the schema migrations; #24 owns them. |
| Weather cache shapes | `weather_telemetry`: one `realtime_sensor` row per station, `data` = `{"rainfall", "wind_speed", "air_temperature"}`, `valid_start`/`valid_end` current. `weather_forecasts`: `2hr` per area (`{"forecast"}`), `24hr` per region and `GENERAL` (NEA blocks with `relativeHumidity`), `4day` per weekday, `uv` per hour (`{"uv"}`). | Used in the SQL tests. `weather_telemetry.updated_at` stays at 2026-08-21 because the writer's upsert does not set it (D13); the data itself is current. |
| Row counts | `UserData` 8, `SensorData` 108, `imageTable` 40, `pondInterventions` 40, `daily_sensor_averages` 205, `WeatherStationLookup` 134, `Fish_Database` 62. | Small; `0002`-`0005` add objects only. |
| Security advisors | Errors: RLS disabled on ten public tables. Warnings: seven functions with mutable `search_path`, five `SECURITY DEFINER` functions executable by `anon` and `authenticated`, `http` extension in `public`. | Confirms D2 and D3; #12. |
| `anon` privileges | `SELECT`, `INSERT`, `DELETE` on every public table; `EXECUTE` on `http_get`. | Confirms D2. |

## 2a. Owner-reported firmware state

Reported by the owner on 2026-10-01 and since confirmed: the owner's
newest sensor sketch, committed as `Embedded/full_sketch/full_sketch.ino`,
sends `userID` 455 with every `SensorData` reading, and every live row
carries it (section 2b). `Embedded/sensor_node/sensor_node.ino` still shows
the older payload without it.

Effect on #5: the missing pond id is not a blocker. Live `SensorData`
already has a nullable `"userID"` column, so rows from the newer sketch
are stored with their pond and per-pond readers see them. Rows written
before that sketch was flashed may still have a null `userID`; the count
query in section 2 still applies to them (D1).

The corrected `0001` defines `SensorData."userID"`. The sketch sets the
key through a constant (`COL_USER_ID`), which the text-based schema check
does not follow, so that check does not cover it; the SQL tests do. #17
records which pond id the board sends and how it is configured.

## 3. Disposition key

| Code | Meaning |
|---|---|
| **E** | Existing database behaviour. Goes into the observed baseline as it is, even when wrong, so a fresh database matches live. |
| **N** | Deliberate new feature, not in live. Goes in its own numbered migration. |
| **C** | Corrective change to existing behaviour. Goes in its own numbered migration owned by the named issue, with an adapter where an API shape changes. |
| **U** | Unknown. Needs owner evidence from section 2 before anything is done. |
| **R** | Reconstruction error in `0001`: something `0001` invented that live does not have. Removed from the baseline; re-added as **N** only if still wanted. |

"Owner" in the tables below is the issue that writes the migration.

## 4. Extensions, types, publication

| Object | Dump | `0001` | Disposition | Owner |
|---|---|---|---|---|
| `pg_cron` (pg_catalog) | installed | absent | E. Two jobs call the NEA edge functions (section 2b); jobs are data, not schema. | #5, #24 |
| `pg_net` (extensions) | installed | absent | E | #5 |
| `http` (**public** schema) | installed; all its functions granted to `anon` | absent | E in baseline; C to revoke from `anon`/`authenticated` (D2) and to stop calling it from a trigger (D6). | #5 (baseline), #12 (revoke) |
| `pg_stat_statements`, `pgcrypto`, `uuid-ossp`, `supabase_vault` | installed | absent | E. Supabase installs these by default; the baseline records them with `if not exists`. | #5 |
| Type `public."Region"` | enum with labels `'"North"'`, `'"South"'`, `'"East"'`, `'"West"'`, `'"Central"'` (the double quotes are part of each label); no column uses it | absent | E. Unused; drop only after owner confirms nothing outside this repository uses it (U). | #5 |
| Publication `supabase_realtime` | contains `SensorData` only | not mentioned | E | #5; changes in #51 |

## 5. Tables

### 5.1 `SensorData`

| Column / property | Dump | `0001` | Disposition | Owner |
|---|---|---|---|---|
| `id` | `bigint` identity, sequence `TempData_id_seq`, PK `TempData_pkey` | identity, default sequence and PK names | E. Keep the legacy sequence and constraint names. | #5 |
| `created_at` | `timestamptz not null default now()` | same | match | |
| `sensor_type` | `text`, **nullable** | `text not null` | E (nullable) | #5 |
| `data1` | `real` | `double precision` | E | #5 |
| `"userID"` | `bigint`, nullable | **absent** | E. Readers that filter by pond depend on it. | #5 |
| Index `(sensor_type, created_at desc)` | absent | present | R. A `("userID", sensor_type, created_at desc)` index is wanted; add as N. | #17 |
| Table comment | `'Data from ESP32 temp sensor'` | none | E | #5 |
| Firmware does not send `userID` | n/a | n/a | C. The committed sensor node (`Embedded/sensor_node/sensor_node.ino`) sends no pond id, so its rows have `userID` null and every per-pond reader skips them (D1). The owner reports that a newer sketch, not yet in this repository, already sends `userID` (see section 2a). Not a blocker for #5. | #17 |

### 5.2 `UserData`

| Column / property | Dump | `0001` | Disposition | Owner |
|---|---|---|---|---|
| `"userID"` | `bigint` identity, sequence `UserData_id_seq` | plain `bigint` PK | E | #5 |
| `volume` | `bigint` | `double precision` | E | #5 |
| `biomass` | `numeric` | `double precision` | E | #5 |
| `region` | `text` | absent | E | #5 |
| `latitude`, `longitude` | `real` | `double precision` | E | #5 |
| `manualpostallocation` | `numeric` | `text` | E. The app sends an integer, and the trigger pads it to 6 digits. | #5 |
| `"ClosestStations"` | **`json`** | `jsonb` | E | #5 |
| `"ClosestStations"` keys | `air-temperature`, `rainfall`, `wind-speed`, `two-hr-forecast`, `twenty-four-hr-forecast` | `air_temp`, `rainfall`, ..., `forecast_area` | R. Live keys are what the dashboard RPC and the app's saved copy use. | #5 |

### 5.3 `WeatherStationLookup`

In the dump only: `"Id" bigint` PK, `station__id text`, `station__name text`,
`location__latitude`, `location__longitude double precision`,
`"Measurement" jsonb`. RLS is enabled and the table has no policies, so
only the owner role and `SECURITY DEFINER` functions can read it.
`0001` omits it. Disposition E; owner #5. Its rows are reference data
that #24 should seed or document (U).

### 5.4 `daily_sensor_averages`

| Column / property | Dump | `0001` | Disposition | Owner |
|---|---|---|---|---|
| `avg_value`, `min_value`, `max_value` | `numeric not null` | `double precision`, nullable | E | #5 |
| `created_at` | `timestamptz not null default now()` | absent | E | #5 |
| Unique constraint | `unique_user_sensor_date (userid, sensor_type, record_date)` | unnamed unique, same columns | E (keep the name) | #5 |
| Index `idx_daily_sensor_lookup` | same columns as the unique constraint | absent | E. Redundant with the unique index; drop as C. | #22 |

### 5.5 `Fish_Database`

The dump has no `"Product URL"` column; `0001` adds one. Disposition R.
No code reads it. Every other column matches.

### 5.6 `pondInterventions`

| Column / property | Dump | `0001` | Disposition | Owner |
|---|---|---|---|---|
| Numeric columns | `numeric` | `double precision` | E | #5 |
| PK and sequence names | `pondinterventions_pkey`, `pondinterventions_id_seq` (lower case) | defaults | E | #5 |
| Index on `("userID", event_type, event_timestamp desc)` | absent | present | R. Wanted for the graph RPC; re-add as N. | #19 |

### 5.7 `imageTable`

| Column / property | Dump | `0001` | Disposition | Owner |
|---|---|---|---|---|
| `"user_ID"` | `integer` | `bigint` | E. Widening to `bigint` is C. | #5 (baseline), #12 or #67 (widen) |
| `current_state` | `text` | `jsonb` | E. The camera writes a JSON pair; PostgREST stores it as text. Readers must accept old strings. Converting to `jsonb` is C after the section 2 sample. | #5, #38 |
| `green_ratio` | `real` | `double precision` | E | #5 |
| Index | `idx_imagetable_user_time ("user_ID", created_at desc)` | same columns, different name | E (keep the live name) | #5 |

### 5.8 Evaluation tables and `pond_chemistry_state`

| Object | Dump | `0001` | Disposition | Owner |
|---|---|---|---|---|
| `pond_chemistry_evaluations` `tan_ppm`, `no2_ppm`, `no3_ppm`, `advisory` | `numeric not null` / `text not null` | nullable `double precision` / `text` | E. The backend always writes these; confirm in the storage tests. | #5 |
| `pond_chemistry_evaluations.sensor_warnings` | `jsonb default '[]'`, nullable | `not null default '[]'` | E | #5 |
| All evaluation numeric columns | `numeric` | `double precision` | E | #5 |
| Indexes | `idx_chem_eval_user_time` **and** `idx_pond_chemistry_user` (identical); `idx_evap_eval_user_time`; `idx_algae_eval_user_time` | one per table, different names | E. Drop the duplicate as C. | #5 (baseline), #22 (drop) |
| `pond_chemistry_state` | `user_id`, `snapshot`, `updated_at` | same | match. `snapshot_version` comes from `0002`. | |

### 5.9 Weather cache tables

| Object | Dump | `0001` | Disposition | Owner |
|---|---|---|---|---|
| `weather_telemetry` | latest cache: PK `(station_id varchar(100), metric_type varchar(50))`, `data jsonb not null`, `valid_start`, `valid_end timestamptz not null`, `updated_at` | time series: `id`, `metric`, `value`, `recorded_at`, coordinates, unique `(metric, station_id, recorded_at)` | R. `0001`'s table is invented; the baseline takes the dump's shape. | #5 |
| `weather_forecasts` | PK `(forecast_type varchar(20), slot_id varchar(50))`, `data jsonb not null`, `valid_period jsonb`, `updated_at` | `id`, `payload`, `area`, `fetched_at`, `valid_from`, `valid_to` | R | #5 |
| `forecast_type` values | `'2hr'`, `'24hr'`, `'4day'`, `'uv'`; `slot_id` is an area name, `REG_*`, `GENERAL`, or `HH24:00` for UV | `'forecast_2hr'`, ... | R | #5 |
| RLS | enabled, policy `"Allow public read access"` (`select using (true)`) on both | none | E | #5 |
| `data` JSON shapes | not in dump | invented | U. Fixtures need real sample rows. | #5, #24 |

### 5.10 `algae_severity_ratings`

Only in the reconstructed `0001`; absent on live. The backend reads and
writes it (`koi/storage/supabase_storage.py`, `koi/api/routes.py`).
Disposition **N**: now `0005_algae_severity_ratings.sql`, with RLS on and
the default grants to `anon` and `authenticated` revoked. Until it is
applied, rating endpoints fail against live. Owner #5 (done), #74 (deploy).

### 5.11 Storage buckets

The reconstructed `0001` inserted `imageAnalysisBucket` and
`pond-images`. Live has `imageAnalysisBucket` and `FishImages`, with eight
anon policies (section 2b). Disposition E: `0004_observed_storage.sql`
records them as they are; on live it is a no-op. `pond-images` is R; the
app still uploads species photos to it (D10). Owner #5 (done).

### 5.12 Objects from `0002` and `0003`

`pond_chemistry_state.snapshot_version`, `worker_lease`,
`save_pond_snapshot`, `take_worker_lease` (`0002`) and `worker_status`
(`0003`) are absent from the dump and from live (section 2b): never applied.
They are N relative to the observed baseline and unchanged. The
rehearsal (section 10) applies them on top of the restored dump with
legacy rows, without error and without changing any row. Owner #74.

## 6. Functions and triggers

| Function | Dump | `0001` | Disposition | Owner |
|---|---|---|---|---|
| `get_bundled_dashboard_payload(bigint)` | plpgsql, `SECURITY DEFINER`, no `search_path`. Per-pond latest sensor values, no `recorded_at`. NEA values from the station map. Forecasts from the cache. UV for the current SGT hour, with a night fallback. History from `get_pond_telemetry_history(p_user_id, 7)` with missing values replaced by 7.4 / 180 / 26 / 500. | invoker, `stable`. Reads **all** sensor rows (no pond filter); adds `recorded_at`, `relative_humidity`, `two_hr_forecast`, `rainfall_mm`; 24 h per-minute history. | E for the body. C for: pond-safe access (D3), `recorded_at` for the poller (D7), no substituted readings (D4), `search_path` (D3). Keys the app reads that live never returns (`two_hr_forecast`, `rainfall_mm`, `relative_humidity`) need an adapter decision (U). | #5 (import), #12, #13, #50 |
| `get_historical_graph_payload(bigint, int default 30)` | `SECURITY DEFINER`. Calls `aggregate_daily_sensor_data()` first. `is_major_reset` marks the single largest water change in the period. Intervention keys `pct`, `litres`, `food_grams`. Window in SGT. | invoker. No refresh call. `is_major_reset` = water change ≥ 50 % or algae scrub. Keys `volume_percentage`, `volume_litres`, `protein_percentage`, `algae_method`. UTC window. No default for `p_days`. | E. Any key change needs an adapter that keeps the legacy keys. | #5 (import), #56 (change) |
| `get_pond_telemetry_history(bigint, int default 7)` | SQL, `SECURITY DEFINER`. Hourly UTC buckets of `pH`, `TDS`, `temp`, `LUX` for one pond; keeps only hours with **both** pH and TDS. | absent | E. The missing-TDS rule hides hours when the TDS probe is off; changing that is C. | #5, #50 |
| `aggregate_daily_sensor_data()` | Recomputes **all** history on every call, grouped by `"userID"`, sensor and SGT date. Excludes `LUX` ≤ 50 lx. Upserts on `(userid, sensor_type, record_date)`. | absent; `0001` instead has `refresh_daily_sensor_averages(date)`: UTC day, cross join copies the same sensors into **every** pond, excludes only `LUX = -1`. | E for the dump function. `refresh_daily_sensor_averages` is R (wrong pond grouping). Fixes for D1 and D5 are C. | #5, #15, #22 |
| `resolve_closest_station(float8, float8, text)` | Haversine over `WeatherStationLookup` where `"Measurement" ? target` | absent | E | #5 |
| `calculate_user_nearest_stations_trigger()` | `SECURITY DEFINER`. Fills `"ClosestStations"` from coordinates; region by fixed lat/lon bounds. | `assign_closest_stations()`, invented keys and lookup | E; `assign_closest_stations` is R. | #5 |
| `process_user_location_and_stations()` | `SECURITY DEFINER`. Geocodes `manualpostallocation` with a **synchronous OneMap HTTP call inside the trigger**, then does the same station work. | absent | E. Moving the HTTP call out of the trigger is C (D6). | #5, #53 |
| Trigger `trigger_auto_assign_stations` | `before insert or update of latitude, longitude` | `"UserData_assign_closest_stations"` | E; the `0001` trigger is R. | #5 |
| Trigger `trigger_process_user_location` | `before insert or update of latitude, longitude, manualpostallocation` | absent | E. Both triggers fire on a coordinate change and do the same work twice (D6). | #5, #53 |

## 7. Policies, grants, default privileges

| Object | Dump | `0001` | Disposition | Owner |
|---|---|---|---|---|
| RLS | enabled only on `WeatherStationLookup`, `weather_forecasts`, `weather_telemetry` | not set | E | #5 |
| Table grants | `GRANT ALL` on **every** public table and sequence to `anon`, `authenticated`, `service_role` | none | E in the observed baseline. Do not adopt as the target design (C, D2). | #5 (record), #12 (fix) |
| Function grants | `GRANT ALL` on every public function, including the `http_*` family and the `SECURITY DEFINER` RPCs, to `anon` and `authenticated` | none | E; C as above | #5, #12 |
| Default privileges | `postgres` role grants `ALL` on new tables, sequences and functions in `public` to `anon`, `authenticated`, `service_role` | none | E. This is why `0002` revokes explicitly; every new migration must do the same until #12 changes the defaults. | #5, #12 |

## 8. Defects in the observed state

These are recorded, not fixed, by the observed baseline. Each fix is a
separate migration owned by the named issue.

| ID | Defect | Effect | Owner |
|---|---|---|---|
| D1 | `aggregate_daily_sensor_data` groups by `"userID"` and inserts into `daily_sensor_averages.userid`, which is `not null`. Any `SensorData` row with a null `userID` (the committed sensor node sends none; the owner's newer sketch reportedly does, section 2a) makes the whole insert fail. | `get_historical_graph_payload` errors whenever such a row exists. Row counts are in section 2. | #17 (pond id in payload and backfill), #5 (test) |
| D2 | `anon` holds `ALL` on every table and on `http_get`/`http_post` etc. | Anyone with the publishable key can read or change any pond's data and make the database send HTTP requests. | #12 |
| D3 | `SECURITY DEFINER` RPCs take any `p_user_id`, are executable by `anon`, and set no `search_path`. | Any caller can read any pond's dashboard. A mutable `search_path` lets objects in other schemas shadow the ones the functions mean to use. | #12 |
| D4 | Dashboard history substitutes 7.4 pH, 180 ppm TDS, 26 °C and 500 lx for missing values. | Charts show readings that were never measured. | #50 |
| D5 | Graph RPC recomputes every day of aggregates on each call. | Cost grows with table size; a read RPC writes rows. | #22 |
| D6 | Two `UserData` triggers do the same station lookup, and one makes a blocking OneMap HTTP request inside the write. | Onboarding writes are slow and fail or hang when OneMap is slow. | #53 |
| D7 | `raw_sensor` has no `recorded_at`. | The poller's `sensor_recorded_at`, `/metrics` sensor age and stale-reading checks get null against live. | #13 (dashboard, `0012`); #18: the poller no longer reads `raw_sensor` and reports each channel's `SensorData.created_at` (`0014`) |
| D8 | Duplicate index on `pond_chemistry_evaluations (userid, evaluated_at desc)`. | Extra write cost. | #22 |
| D9 | `get_pond_telemetry_history` returns `date_trunc('hour', created_at at time zone 'UTC')`, a `timestamp`, as `timestamptz`. | Correct only while the session time zone is UTC (the Supabase default). | #15: fixed by `0006_hourly_history_utc_instants.sql` (tested under UTC and Asia/Singapore sessions) |
| D10 | The app uploads and reads species photos in a `pond-images` bucket (`fish_image_helper.dart`, `image_controller.dart`, `fish_tips_view.dart`); live has no such bucket (it has `FishImages`, whose policies also require a `public/` folder). | Species photo uploads fail on live. Listed in `KNOWN_UNDEFINED` in `test_schema.py`. | Owner decision: point the app at `FishImages` or add a `pond-images` bucket with policies (not yet assigned) |
| D11 | The app reads `nea_forecasts.two_hr_forecast` and `nea_forecasts.rainfall_mm` (`ph_outcome_card.dart`, `solar_outcome_card.dart`); live never returns them (the reconstruction invented them). | Those cards use their fallbacks. Listed in `KNOWN_MISSING_PAYLOAD_KEYS` in `test_schema.py`. | #13 |
| D12 | `algae_engine.parse_image_rows` drops the label of a bare-string `current_state` such as `base`: the failed `json.loads` sets it to `None` before the string branch runs. | None today: every live row is a JSON array (section 2b). | #38 |
| D13 | The NEA realtime writer's upsert does not set `weather_telemetry.updated_at`, which stays at its first insert (2026-08-21). | Misleading freshness column; no reader uses it. | #24 |

## 9. Migration strategy

Live has no migration history (section 2b), so `0001` was never applied
and is corrected in place:

1. The reconstruction is kept at
   `archive/pre-refactor/supabase/0001_baseline_reconstructed.sql`.
2. `0001_baseline.sql` is the supplied dump, statement for statement, plus
   the one comment line live has, so all seven public function bodies
   have the same `pg_get_functiondef` md5 as live. `.gitattributes` keeps
   its bytes (the function bodies use CRLF, as on live).
3. `0002` and `0003` are unchanged.
4. `0004_observed_storage.sql` (E) records the live buckets and storage
   policies; `0005_algae_severity_ratings.sql` (N) adds the ratings table.
5. Each **C** item goes in a later migration under its owning issue.

Never run `0001` against live: its objects already exist there. Bringing
live in line means applying `0002`-`0005` only, which the rehearsal
covers. supabase/README.md gives the owner's procedure.

## 10. Verification status

Local Supabase stack (CLI 2.119.0, Postgres 17), 2026-10-01.

| Check | Command | Result |
|---|---|---|
| Fresh path: clean database, then `0001`-`0005` | `supabase db reset` | PASS, no errors |
| Function bodies match live | md5 of `pg_get_functiondef` for the seven public functions, local against live | PASS, all seven equal |
| Upgrade path: clean database, `schema.sql`, synthetic legacy rows (`tools/rehearsal/legacy_rows.sql`), then `0002`-`0005`; compared with the fresh path | `python tools/db_rehearsal.py` | PASS: every legacy row kept (13 tables); public schema (definitions, grants, policies, publication membership) and storage buckets and policies identical, ignoring comment lines |
| The rehearsal can fail | same script with `0005` left out of path B | FAIL as expected, reporting the missing `algae_severity_ratings` |
| SQL tests: two ponds never mixed (dashboard and graph), empty and unknown pond, hours without TDS dropped, substituted temperature and light (D4), Singapore dates and dark-LUX filter, weather cache shapes through `forecast_utils.current_conditions`, 4-day order, JSON-text image states through `parse_image_rows`, pre-`0002` snapshot saved with version 1 and a stale save refused, backend-only objects closed to `anon` | `cd Backend && python -m pytest -q tests/sql` | PASS, 12 tests; they skip without a local database and refuse a non-local `KOI_TEST_DB_URL` |
| Text-based schema check | `cd Backend && python -m pytest -q -k schema` | PASS, 12 tests, with D10 and D11 listed as known gaps that fail the suite once fixed |

Not verified: the edge functions that write the weather cache.

## 11. Live application (2026-10-01)

| Step | Result |
|---|---|
| Full backup of live (`pg_dump -Fc`, all schemas), plus the Supabase CLI roles, schema and data dumps | Taken twice, the second immediately before the push; checksummed; stored off-repo by the owner |
| Backup restored into the local stack | PASS: one transaction, no errors; row counts equal the dump in all 15 tables with data |
| `0002`-`0005` rehearsed on the restored copy of live | PASS: about 0.5 s each; no row changed; SQL tests pass on the real data |
| Rollback script (drops only what `0002`, `0003` and `0005` add, plus the history schema) tested locally | PASS: schema after rollback identical to live's pre-migration schema apart from blank lines |
| `supabase migration repair --status applied 0001`, `supabase db push --dry-run` (listed exactly `0002`-`0005`), `supabase db push` | PASS |
| Read-only check afterwards | History `0001`-`0005`; new objects present with RLS on and closed to `anon`; snapshot of pond 455 at `snapshot_version` 1; 2 buckets and 8 storage policies; all seven function md5 sums unchanged; sensor rows kept arriving during and after the push |

Backup finding: the Supabase CLI's `db dump` omits storage policies (it
dumps `public` only for the schema) and drops comment lines that start in
column 0 inside function bodies, which is why the supplied dump lacked
one line of `get_bundled_dashboard_payload`. A raw `pg_dump -Fc` of the
whole database has both. Use the raw dump as the restore source.
