# Database access matrix

Who may do what in the Supabase database, by role, after migrations
`0025_device_tokens.sql` and `0026_least_privilege.sql` (issue #12), with
the objects `0027_sensor_payload_v2.sql` (issue #17) adds. The
"After #116" column is the target once the app signs in (#49) and #116
enables row level security on the pond tables; #116 implements that column
as written here.

## Roles

| Role | Who uses it | How it authenticates |
|---|---|---|
| `anon` | The app when signed out; the sensor node | Publishable key only |
| `authenticated` | The app when signed in (Supabase Auth session) | Publishable key plus the user's access token |
| device (through `anon`) | The sensor node | Publishable key plus a per-device token passed to `ingest_readings`; the token decides the pond |
| `service_role` | The API, the poller worker, the camera service (backend) | Secret key, bypasses RLS |
| `postgres` | The owner in the SQL editor; migrations | Database password |

"Owner" in the tables below means the account whose `auth.uid()` equals
`UserData.auth_uid` for the pond (linked by `link_pond_to_account`, #11).

## Tables

| Object | Kind | `anon` now | `authenticated` now | After #116 (`authenticated`, owner rows only unless noted) | `service_role` |
|---|---|---|---|---|---|
| `UserData` | pond profile | select, insert, update, delete | same as `anon` | select, update, delete own row (`auth_uid = auth.uid()`); insert with `auth_uid` null, then linked; `anon` none | all |
| `pondInterventions` | pond events | select, insert | same as `anon` | select, insert own pond's rows (`"userID"` of a pond the caller owns); `anon` none | all |
| `imageTable` | camera frames | select | same as `anon` | select own pond's rows (`"user_ID"`); `anon` none. Inserts stay backend (camera service, #86) | all |
| `Fish_Database` | shared reference | select | select | select for `anon` and `authenticated` (public reference data, no owner column) | all |
| `weather_telemetry`, `weather_forecasts` | shared NEA caches | select (existing public read policies) | select | unchanged: public read, no owner column | all |
| `SensorData` | readings | none | none | none; reads go through the API | all |
| `daily_sensor_averages` | daily readings | none | none | none (read inside `get_historical_graph_payload`) | all |
| `pond_chemistry_state`, `pond_*_evaluations`, `evaluation_daily`, `pond_profile`, `kit_readings`, `pond_calibration`, `camera_config`, `camera_mask_version`, `algae_severity_ratings`, `notification_outbox` | backend state | none | none | none; reached through the API | all |
| `devices`, `device_contact`, `device_tokens` | devices | none | none | none | all |
| `sensor_ingest_cursor`, `sensor_ingest_ledger`, `worker_lease`, `worker_status` | backend bookkeeping (#8, #18) | none | none | none | all |
| `daily_sensor_refresh` | daily aggregate bookkeeping (#17), RLS on, no policies | none | none | none (written inside `get_historical_graph_payload`) | all |
| `WeatherStationLookup`, `weather_station_regime`, `weather_observation`, `weather_forecast_issuance`, `weather_ingest_window` | weather reference and history | none | none | none (read by definer triggers and the backend) | all |

Sequences: `anon` and `authenticated` have usage on `UserData_id_seq` and
`pondinterventions_id_seq` only (inserts without an id).

Tables with RLS on and no policies are backend only on purpose; they need
no client policy. #116 adds policies only where the table above lists an
owner rule.

## Functions in `public`

| Function | Security | `anon` | `authenticated` | After #116 | `service_role` |
|---|---|---|---|---|---|
| `get_historical_graph_payload(p_userid, p_days)` | definer, `search_path=''`, p_days 1..366 | execute, any pond | execute, any pond | `authenticated` only, and the function refuses a pond the caller does not own | execute |
| `ingest_readings(device_token, rows)` | definer, `search_path=''` | execute (token decides pond) | none | unchanged | execute |
| `get_bundled_dashboard_payload`, `get_pond_telemetry_history`, `pond_dashboard_sources` | invoker | none | none | none | execute |
| `aggregate_daily_sensor_data(p_from, p_to, p_pond_id)`, `reconcile_daily_sensor_averages`, `sensor_channel` (#17), `resolve_closest_station`, `summarize_evaluation_days` and its merge helpers | invoker (internal) | none | none | none | execute |
| `take_worker_lease`, `save_pond_snapshot`, `save_pond_snapshot_with_ingest` (#8) | invoker | none | none | none | execute |
| other backend RPCs (`sensor_ingest_*`, `record_device_contacts`, `pond_device_activity`, `save_camera_mask`, weather `*_as_of`, `ingest_weather_batch`, `enqueue_notification`, ...) | invoker | none | none | none | execute |
| `auth_session_active` | definer | none | none | none | execute |
| `link_pond_to_account`, `issue_device_token`, `revoke_device_token` | owner tools | none | none | none | none (owner, SQL editor) |
| trigger functions (`guard_userdata_auth_uid`, `record_userdata_profile_change`, `process_user_location_and_stations`, `calculate_user_nearest_stations_trigger`, ...) | as created | none | none | none | as before |

No function in `public` is executable by `PUBLIC`.

## Device ingest

`ingest_readings` authenticates the token (sha256 match in `device_tokens`,
not revoked, not expired, device not revoked), takes the pond from the
device row, accepts per row only `sensor_type` and `data1` plus the
payload v2 fields `boot_id`, `cycle_seq`, `sampled_at`, `flags`,
`reset_reason` and `battery_mv` (0027), only the channels `temp`, `TDS`,
`pH`, `LUX`, `battery_mv`, `reset_reason` in that spelling, at most 32
rows per call and 6 calls per device per minute. `userID`, `device_id`,
`pond_id`, `id` and `created_at` refuse the whole batch. Details and
ranges are in the headers of `0025_device_tokens.sql` and
`0027_sensor_payload_v2.sql`.

## Extensions and outbound HTTP

`http` lives in `extensions`, which PostgREST does not expose, so no
client can call `http_get` and friends through `/rest/v1/rpc`. The only
caller is the definer trigger `process_user_location_and_stations`
(OneMap postal-code lookup; moving it out of the trigger is #53).
`pg_net` (`net.*`) is used by the owner's `pg_cron` weather jobs and is
not in an API schema either.

## Default privileges

Objects that `postgres` (the migration creator) creates later in `public`
grant nothing to `anon` or `authenticated`, and functions it creates in
any schema are not executable by `PUBLIC`; `service_role` keeps table,
sequence and function access. A new app-facing object therefore needs an
explicit grant in its migration. `supabase_admin` (owner of extensions)
still grants its new objects in `public` to the client roles; a migration
cannot change that role's defaults, so extensions go in `extensions`.

## Not covered here

Storage buckets and policies, and the camera's `POST /upload`, are #86.
