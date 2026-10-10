# NEA weather ingestion and history (issue #24)

The backend now keeps NEA weather history beside the latest caches.
Schema and rules: `supabase/migrations/0009_weather_history.sql`.
Code: `Backend/koi/weather/`. Command: `python -m koi.weather`.

## What changed and what did not

- `weather_telemetry` and `weather_forecasts` stay latest caches with the
  same keys and JSON that `get_bundled_dashboard_payload` reads. Each gains
  one nullable column (`source_times`, `source_issued_at`) that only the new
  job writes; a writer that names the original columns is unaffected.
- History lives in new tables: `weather_observation` (one row per reading)
  and `weather_forecast_issuance` (one row per issuance, slot and validity
  window). Nothing was backfilled from the cache rows: one current row
  cannot recover readings overwritten before this migration, and
  `updated_at` is a write time, not a provider issue time.
- `WeatherStationLookup` is maintained from the provider's station
  metadata: a station or 2-hour area not yet listed is added, a station
  gains the tag of each metric it reports (`AirTemp`, `Rainfall`, `Wind`,
  `RelativeHumidity`, `2HourForecast`), and its name and location follow
  the provider. Tags are never removed and `station__id` values are never
  changed, so `resolve_closest_station` and the `ClosestStations` keys
  (`air-temperature`, `rainfall`, `wind-speed`, `two-hr-forecast`,
  `twenty-four-hr-forecast`) work as before. New 2-hour areas get a null
  `station__id`, because the live spelling for area rows is not known here
  and the station trigger reads the area name.
- The SensorData upload path and the camera `POST /upload` contract are
  untouched.

## Times

| Column | Meaning |
|---|---|
| `observed_from`, `observed_to` | The period a reading describes; equal for an instantaneous reading. |
| `fetched_at` | When the job received the response. |
| `issued_at` | The provider's issue time; null when the provider or record does not give one. |
| `source_updated_at` | The provider's revision time; null when not given. |
| `available_at` | Generated: the latest provider time, else `fetched_at`. |
| `valid_from`, `valid_to` | The forecast's validity window, `[from, to)`. |

As-of rules, used by `weather_forecast_as_of`, `weather_observations_as_of`
and `koi/weather/history.py`:

- A forecast is usable at time T when `available_at <= T`. A forecast whose
  issue time is unknown is therefore never used for a time before it was
  fetched, and a backfilled forecast without an issue time is never used
  for a past prediction.
- An observation is usable at T when `observed_to <= T`.
- Ties: newest `available_at`, then `issued_at` (unknown last),
  `fetched_at`, `id`.

## Observation semantics and units

| Product | Metric | Unit | Semantics | Period |
|---|---|---|---|---|
| `air-temperature` | `air_temperature` | degC | instantaneous | the reading time |
| `relative-humidity` | `relative_humidity` | % | instantaneous | the reading time |
| `wind-speed` | `wind_speed` | knot | instantaneous | the reading time |
| `wind-direction` | `wind_direction` | deg | instantaneous | the reading time |
| `rainfall` | `rainfall` | mm | interval_total | the 5 minutes ending at the reading time |
| `uv` | `uv_index` | UV index | interval_mean | the hour ending at `hour` |

Wind speed stays in knots as NEA reports it and as the cache and models
already expect (`forecast_utils` converts to m/s). A reading whose unit is
not the expected one is rejected and counted as malformed.

Rainfall semantics come from the response's `readingType`: an "N Minute
Total" is an interval total over the N minutes ending at the timestamp; a
cumulative type is stored as `cumulative_total` and never summed; any other
or missing `readingType` rejects the readings rather than guess.

Rainfall totals (`weather_rainfall_total`, `history.rainfall_total`) sum
only interval totals in mm lying wholly inside the requested window (a
trailing 24 hours, or a Singapore local day from `history.local_day_window`).
An interval overlapping one already counted (a repeated snapshot, or an
hourly total over 5-minute totals) is skipped and counted. The result
always carries `coverage`, `covered_seconds` and the `missing` intervals;
with no data `total_mm` is null and `status` is `no_data`, never 0 mm.

UV: NEA publishes readings between about 07:00 and 19:00 Singapore time.
`history.uv_reading_status` reports the newest reading less than an hour
old, or `night` (outside those hours) or `missing` (inside them) with no
value. The dashboard's existing night fallback (0, "Nighttime (Inactive)")
is unchanged; showing source times and honest gaps in the app is #13 and
#61.

## Station-network regimes

`weather_station_regime` is seeded from the repository's analysis
evidence (`NEA_Data_Analysis/daily_climate.csv`, `UNIFIED_NEA_CHAPTER.md`
section x.2.5, the regime-aware `HOT_THR` in
`forecast_reality_pipeline.ipynb`):

| Regime | Days | Stations | Sampling |
|---|---|---|---|
| `nea_cluster_2stn` | 2020-02-01 to 2025-06-01 | 2 | a few fixed samples per station per day |
| `nea_network_mean` | from 2025-06-02 | 11 to 33 | one-minute readings from every reporting station |

A regime describes a dataset-wide network-mean series. A pond's assigned
station is a single station, not a network mean, so its observations have
`series = 'station'` and no regime. The foreign key on
`(regime_id, series)` refuses a regime on a `station` row, so a threshold
or calibration derived on the network mean cannot be attached to one
station's readings without saying so explicitly. Calibration (#32) uses
only the pond's assigned station's own `station` rows, with no regime.

## Deduplication and the caches

- Observation key: `(source, series, station_id, metric, observed_from,
  observed_to)`. Issuance key: `(source, product, slot_id, valid_from,
  valid_to, issued_at, source_updated_at)`, nulls not distinct. `source` is
  `nea_v2` for both live fetches and backfills, so the same reading fetched
  twice is one row; a later issuance for the same day is a new row.
- Cache writes go through `ingest_weather_batch` only and are guarded: a
  value replaces a cache entry only when its provider time is newer than
  the stored one (`source_times` per metric, `source_issued_at` per slot),
  or, for a row written by the old writer, not older than its
  `valid_start` / `updated_at`. A `4day` row must also be at least as new
  as the newest `4day` row, because the dashboard picks the four most
  recently written. Backfills send no cache candidates at all.
- Cache shapes written: `weather_telemetry` one `realtime_sensor` row per
  station with `{"air_temperature", "rainfall", "wind_speed"}` (rainfall is
  the latest 5-minute total); `weather_forecasts` `2hr` per area
  (`{"forecast"}`), `24hr` `GENERAL` (general block without `validPeriod`)
  and `REG_<REGION>` (`{"code", "text"}` of the period in force), `4day` per
  weekday `MON`..`SUN` with `valid_period {"timestamp"}`, and `uv` per
  Singapore hour `HH:00` with `{"uv"}`.

## Retention

History rows are kept indefinitely; nothing deletes them. The job keeps
station history only for stations listed in `WEATHER_HISTORY_STATIONS`
plus those assigned to ponds (`ClosestStations`), and 2-hour forecasts only
for `WEATHER_HISTORY_AREAS` plus assigned areas (`*` keeps every one).
24-hour and 4-day forecasts and UV are kept whole. At the live 5-minute
cadence that is roughly 300 rows a day per station and metric. Consumers:

| Issue | Reads |
|---|---|
| #26 advisory rules | `fetch_forecast_as_of` for 24-hour periods and the 4-day outlook as issued before the rule's time; observed temperature per regime |
| #27 rainfall | `fetch_rainfall_total` (value plus coverage) per local day or trailing 24 hours |
| #32 calibration | `fetch_weather_observations` of the assigned station (air temperature, wind speed, rainfall), a day per call over the 60 days before the run, binned by hour; the 24-hour forecast's humidity as of each 6-hour block (`koi/worker/poller.py`) |
| #20 state rebuild | as-of observations and forecasts at each replayed time (`koi.tools.rebuild`: newest air temperature, rainfall and wind speed observed in the hour before each poll; 2-hour, 24-hour and 4-day forecasts usable then; a missing part is reported as an incomplete interval) |
| #33 backtests | as-of forecasts only (`available_at <= prediction time`) |

The live poller still reads the latest caches through the dashboard
payload; switching each consumer to these as-of reads is part of its own
issue. The state rebuild (#20) already reads only history.

## Endpoint and auth assumptions (owner to verify)

These come from the reference scrapers in `PythonSimulatorProject/` and
the notebook; the job has not called the live service.

- Base URL `https://api-open.data.gov.sg/v2/real-time/api`, paths
  `air-temperature`, `rainfall`, `wind-speed`, `relative-humidity`,
  `wind-direction`, `uv`, `two-hr-forecast`, `twenty-four-hr-forecast`,
  `four-day-outlook`.
- No parameters returns the latest; `?date=YYYY-MM-DD` one Singapore day,
  paginated with `data.paginationToken` passed back as `paginationToken`.
- Optional API key in the `x-api-key` header; HTTP 429 when rate limited.
  The job spaces requests (`NEA_MIN_REQUEST_INTERVAL_SECONDS`), honours
  `Retry-After`, retries 5xx, network errors and non-JSON bodies with
  exponential backoff (`NEA_MAX_ATTEMPTS`), and does not retry other 4xx or
  a body with `code != 0`.
- Rainfall `readingType` names a 5-minute total ending at the timestamp;
  UV `hour` ends the averaged hour; forecast issue time is `timestamp`,
  revision time `update_timestamp` / `updatedTimestamp`.
- The 4-day cache slot spelling (`MON`..`SUN`) and the 2-hour
  `valid_period` keys (`start`, `end`) follow the local test fixtures; check
  them against live rows before switching writers.

## Running

```bash
cd Backend
python -m koi.weather live                                  # every product
python -m koi.weather live --products air-temperature,rainfall,wind-speed,relative-humidity,uv
python -m koi.weather live --products two-hr-forecast,twenty-four-hr-forecast,four-day-outlook
python -m koi.weather backfill --start 2026-09-01 --end 2026-09-30 --products rainfall
```

Each product logs one `weather_product_ingested` event (pages, records,
malformed, inserted, duplicates, cache rows updated, stations changed,
error). A backfill records each day in `weather_ingest_window`; `done` and
`empty` days are skipped on the next run unless `--force`, `failed` days
are retried. The command exits 1 when any product or day failed.

## Owner actions

1. Apply `0009_weather_history.sql` (after `0006`-`0008`), following
   `supabase/README.md`.
2. Identify the current writer: the `pg_cron` jobs
   `fetch-nea-realtime-5min` and `fetch-nea-forecasts-30min` calling the
   edge functions `fetch-NEA-Realtime` and `fetch-NEA-forecasts`
   (docs/database-reconciliation.md). Record their schedule and code.
3. Provision `NEA_API_KEY` if the public limit is not enough, set
   `SUPABASE_URL`, `SUPABASE_SERVICEROLE_KEY` and the history lists, and
   schedule the two `live` commands above (5 and 30 minutes).
4. Run both writers in parallel for a few days. Verify: cache rows keep
   their shape (`select * from weather_forecasts where forecast_type in
   ('4day', '2hr') limit 10`), history has no duplicate keys, and the
   dashboard values match. Then disable the cron jobs.
5. Backfill: check how far back the provider serves `?date=` for each
   product before relying on it. The schema dump has no historical
   observations; the analysis CSVs are a separate, read-only study.
