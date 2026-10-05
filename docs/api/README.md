# Digital twin API, version 1

`openapi.yaml` in this folder is the API's description. It is generated
from the route table (`Backend/koi/api/spec.py`) and the pydantic models
(`Backend/koi/api/schemas.py` for requests, `Backend/koi/api/responses.py`
for responses). Do not edit it by hand. After changing a route or a model,
run this from `Backend/`:

```
python -m koi.api.openapi            # rewrite docs/api/openapi.yaml
python -m koi.api.openapi --check    # exit 1 if it is out of date
```

`tests/api/test_openapi.py` fails when the committed file is out of date
or misses a route the app serves. Every API response in the backend test
suite is validated against this document (`Backend/tests/api_contract.py`),
and `tests/api/test_contract.py` checks that every key the Dart models
read is a property of the matching schema.

## Versions and the old paths

Every route is served under `/v1`, with the pond in the path. The paths
the app calls today are kept as deprecated aliases of their `/v1` route.
They give the same response with two extra headers: `Deprecation:
@1790985600` (RFC 9745, 2026-10-03) and, when the pond is in the path,
`Link: </v1/...>; rel="successor-version"`. They are removed by the
mobile repositories issue, once the app calls `/v1`.

| Old path (deprecated) | `/v1` path |
|---|---|
| `GET /assessment/{id}` | `GET /v1/ponds/{pond}/assessments/chemistry` |
| `GET /assessment/evaporation/{id}` | `GET /v1/ponds/{pond}/assessments/evaporation` |
| `GET /assessment/algae/{id}` | `GET /v1/ponds/{pond}/assessments/algae` |
| `GET /assessment/all/{id}` | `GET /v1/ponds/{pond}/assessments` |
| `GET /forecast/{id}` | `GET /v1/ponds/{pond}/forecasts/chemistry` |
| `GET /forecast/evaporation/{id}` | `GET /v1/ponds/{pond}/forecasts/evaporation` |
| `GET /forecast/algae/{id}` | `GET /v1/ponds/{pond}/forecasts/algae` |
| `GET /ratings/algae/{id}` | `GET /v1/ponds/{pond}/ratings/algae` |
| `POST /events/{feeding,water-change,top-up,algal-scrub,salt,filter-clean}` | `POST /v1/ponds/{pond}/events/{...}` |
| `POST /events/algae-rating`, `/events/algae-rating/undo` | `POST /v1/ponds/{pond}/events/algae-rating`, `.../undo` |
| (new) | `GET /v1/ponds/{pond}/dashboard` |

`/v1/ponds/{pond}/profile` and `/v1/ponds/{pond}/camera/mask` were
already versioned. On a `/v1` event route the body's `user_id` may be left
out; if it is sent and differs from the path's pond, the reply is 400
`pond_mismatch`. `/health` and `/ready` are served both as `/v1/health`
and `/v1/ready` and unversioned, without deprecation, because probes call
them. `/metrics` stays unversioned (Prometheus text).

## Conditional GET and caching

The three pond forecast endpoints include `uncertainty.low` and
`uncertainty.high`: pointwise envelopes of numeric trajectory fields in
their original units. `trajectory` is the existing central projection.
`uncertainty.first_crossing_days` maps each crossing field to `low`,
`high` and `not_crossed_runs`. Day 0 means already crossed. A null low
means none of the runs crosses within the requested horizon; a null high
means at least one run does not cross within it. Scrub benefit includes
its own crossing range under `scrub_benefit_days_bought`.

These are model sensitivity estimates, not statistical confidence
intervals. Each domain runs at most three scenarios per request, using
`koi/models/uncertainty.json`. Algae uses the paired nitrate trajectories
from three chemistry runs, and computes scrub benefit within its own
three runs. The registry caches by pond, domain, horizon and depth query
until the next poll or event; profile changes and a newer stored snapshot
also invalidate it. The cache is process-local and uses the existing
snapshot-version protocol for both storage adapters. No schema change is
needed. A concurrent snapshot save conflict ends the forecast rather than
re-running it beyond the cost cap.

Every GET that returns 200 JSON has a strong `ETag` computed over the
response body, `Cache-Control: private, no-cache` and `Vary:
Authorization`. A request with a matching `If-None-Match` gets 304 with no
body. `no-cache` means a client revalidates every time it uses a stored
response. The server computes the ETag from the body it would send at that
moment, so it changes whenever anything in the body changes:

- a new reading, or a reading crossing its `fresh_until` (no new row
  needed);
- a new evaluation, or a changed profile (the hypoxia flag uses aeration);
- a new forecast issuance or cache write, a forecast passing `valid_to`,
  or the Singapore hour changing (UV);
- a changed station assignment;
- next actions, once the action ladder issue fills them.

A 304 therefore never extends a reading's "fresh" status past
`fresh_until`.

## GET /v1/ponds/{pond}/dashboard

This is the one call the dashboard screen needs. It returns the latest
reading of each channel, the three latest assessments with the hypoxia
flag, next actions, the weather now and the forecasts. It only reads: no
engine runs, nothing is written, and no aggregate is refreshed.

The database rows come from `pond_dashboard_sources(p_pond_id)` (migration
0012). That function is read only (`stable`), security invoker, and
executable by the service role only. `koi/api/dashboard.py` interprets
the rows. MemoryStorage returns the same rows from its own tables, and
`tests/sql/test_sql_dashboard.py` checks that both storages give identical
responses for the synthetic ponds in `tests/dashboard_scenarios.py`.

### Which SQL it wraps

The versioned API does not wrap `get_bundled_dashboard_payload`; the app
and the poller still call it unchanged. That function returns one
untimed value per sensor and takes NEA values from any cache row of the
station that has the key. It labels a missing UV hour as a measured night
0, and fills missing history with 7.4 / 180 / 26 / 500. None of these can
be corrected from outside the function. `pond_dashboard_sources` returns
the rows with their times instead.

The two history RPCs (`get_pond_telemetry_history`,
`get_historical_graph_payload`) are not used by `/v1`; migration 0019
extends graph history with salt, notes and maintenance markers.
`get_historical_graph_payload` runs `aggregate_daily_sensor_data()` on
every call, for every pond, as security definer, and the app still calls
it directly. A `/v1` history endpoint, with bounded windows and the
refresh moved to the worker, is not part of this version.

### Kinds of value

Each section says what kind of value it holds: `readings.kind` and
`weather.kind` are `observed`, `assessments.kind` is `model_estimate`, and
`forecast.kind` is `projection`.

### Old keys and their `/v1` fields

All times are ISO 8601 instants in UTC. "null" means the value is not
known, and `status` says why. No value is ever substituted.

| Old key (RPC) | `/v1` field | Source and rule |
|---|---|---|
| `raw_sensor.pH` | `readings.ph.value` (pH) | Newest `SensorData` row with `sensor_type` `pH` |
| `raw_sensor.TDS` | `readings.tds.value` (ppm) | `TDS` |
| `raw_sensor.temp` | `readings.water_temp.value` (degC) | `temp` (DS18B20, water) |
| `raw_sensor.LUX` | `readings.lux.value` (lux) | `LUX`, or `lux`; the newer row wins |
| (none) | `readings.*.ingested_at` | That row's `SensorData.created_at`, the insert time |
| (none) | `readings.*.sample_time`, `sample_time_basis` | Always null and `unknown` until the node sends a sample time (#17) |
| (none) | `readings.*.fresh_until`, `status` | `ingested_at` + 30 min; `ok`, `stale` after it, `missing` with no row, `invalid` for a non-number or the light sensor's -1 |
| (none) | `readings.*.source_row_id` | `SensorData.id` |
| `telemetry_history` | (not in `/v1`) | See "Which SQL it wraps" |
| `nea_telemetry.air_temp.value` | `weather.air_temperature.value` (degC) | Station `ClosestStations."air-temperature"`, its `weather_telemetry` row with `metric_type` `realtime_sensor`, key `air_temperature` |
| `nea_telemetry.rainfall.value` | `weather.rainfall.value` (mm in the 5 minutes ending at `observed_at`) | Station `"rainfall"`, key `rainfall` |
| `nea_telemetry.wind_speed.value` | `weather.wind_speed.value` (knot) | Station `"wind-speed"`, key `wind_speed` |
| (none) | `weather.*.observed_at` | `weather_telemetry.source_times[key]`, the provider time; null for a row written before migration 0009 |
| (none) | `weather.*.ingested_at`, `fresh_until`, `status` | `updated_at`; `observed_at` (or `ingested_at`) + 1 h; `missing` with no assigned station or no matching row |
| `nea_forecasts.uv_index` | `weather.uv_index` | The `uv` row whose `valid_period.hour` is the current Singapore hour. If there is none, `missing` (value null) from 07:00 to 19:00, else `night_derived` (value 0, not measured). A slot from another day is not this hour's |
| `nea_forecasts.forecast_2hr.forecast` | `forecast.two_hour.text` (`code` when given) | `2hr` row of `"two-hr-forecast"` |
| `nea_forecasts.forecast_24hr.regional` | `forecast.twenty_four_hour_regional` | `24hr` row of `"twenty-four-hr-forecast"` |
| `nea_forecasts.forecast_24hr.general` | `forecast.twenty_four_hour_general` (temperature degC, humidity %, wind km/h) | `24hr` row `GENERAL` |
| (none) | `forecast.*.valid_from`, `valid_to`, `status` | `valid_period.start`/`end`; `expired` once `valid_to` has passed; `missing` with no assignment or row |
| (none) | `forecast.*.issued_at` | `weather_forecasts.source_issued_at`, the provider issue time; null for rows before 0009. `updated_at` is never shown as the issue time |
| (none) | `forecast.*.ingested_at` | `weather_forecasts.updated_at`, the cache write |
| `nea_forecasts.outlook_4day` | `forecast.outlook[]` | Every `4day` row whose day (Singapore date of `valid_period.timestamp`) is today or later, oldest day first. The old RPC took the 4 most recently written rows |
| (none) | `assessments.chemistry`, `evaporation`, `algae` | Latest stored evaluation of each domain; null before the first |
| (none) | `assessments.hypoxia` | `koi/models/hypoxia.py` from the fresh lux and water temperature only; `unknown` when either is not fresh |
| (none) | `next_actions` | Always `[]` until the action ladder issue |
| `UserData.ClosestStations` | `stations` | Each assignment, or null |

Retained forecast issuances (more than the latest per slot) come from
`weather_forecast_issuance` (issue #24). `/v1` reads only the latest
caches.

### Latest row and tie breaker

The newest row of a channel is the one with the latest `created_at`. When
two rows have the same `created_at`, the higher `id` wins. SQL
(`order by created_at desc, id desc`) and MemoryStorage apply the same
rule. Migration 0013 adds the index `idx_sensordata_user_type_latest`
(`userID`, `sensor_type`, `created_at desc`, `id desc`) in that order, so
the read does not scan the pond's rows.

## GET /v1/ponds/{pond}/devices and data_confidence

`GET /v1/ponds/{pond}/devices` lists the sensor node, then the camera
(`Backend/koi/models/device_health.py`, migration 0018). It only reads.

- `last_seen_at` is receipt time: the newest upload the poller has
  ingested, or the newest frame the camera service stored. Each sensor
  channel's `last_sample_at` is the time of its newest usable reading,
  so a node that reconnects with old buffered readings shows as seen
  while its channels stay `old`. `clock` says how far those sample
  times can be trusted; until the node sends sample times (issue #17)
  they are receipt times.
- `expected_interval_seconds` comes from the `devices` row when set
  (`interval_source` `device`), else the backend's sensor cadence
  (`default`) or the camera's last wake (`schedule`).
  `missed_24h` counts reports due in the last 24 hours that did not
  arrive, each judged against the interval in force when the contact
  before it was received.
- `battery` is the trend of the node's `battery_mv` rows and
  `last_reset` its newest `reset_reason` row; both are `unknown` or null
  until the node sends them.
- A channel that has never reported, such as TDS on a pond without the
  probe, is `never_seen`.

Every assessment the API returns (`/assessments*`, the event replies and
the dashboard) carries `data_confidence`: `level` `high`, `reduced` or
`low` and the `reasons`, worked out when the response is sent, so it
falls while the node is silent although the stored assessment does not
change. It is named apart from the algae assessment's own `confidence`,
which describes its growth-rate fit.

## Salt and filter cleaning events (issue #29)

`POST /v1/ponds/{pond}/events/salt` requires positive finite `salt_grams`
(grams), with optional `notes` (up to 1000 characters). `filter-clean`
accepts optional `notes`. Both share `timestamp` and stable UUID `event_id`
with other events; duplicate IDs are applied once and backdated events
use the existing replay window. Their deprecated `/events/...` aliases
require `user_id`. Responses contain all three assessments and the event
outcome, including `event_id`.

Migration 0019 adds nullable `salt_grams numeric` and `notes text` to
`pondInterventions`. The app writes event types `SALT` and `FILTER_CLEAN`
and posts the same UUID to the API. `pond_interventions_since` returns
both fields, null on old rows. `get_historical_graph_payload` returns them
with stable `event_id` in intervention history and derives the boolean
`after_maintenance` on pH/TDS daily rows for Singapore days overlapping
the next 24 hours after filter cleaning. Other channels are false. RPC
signatures and access rules are unchanged. The app shows maintenance in
chart tooltips. See [model assumptions](../models.md).

## Owner test-kit readings (issue #30)

`POST /v1/ponds/{pond}/kit-readings` returns 201 with the stored reading.
It accepts nullable `taken_at` (an ISO 8601 instant with a time zone),
`kit`, `ammonia_mg_l`, `nitrite_mg_l`, `nitrate_mg_l`, `ph`, `kh_dkh` and
`notes`. Concentrations are mg/L and carbonate hardness is dKH. Ammonia
means total ammonia (TAN), the quantity the chemistry engine estimates;
a free NH3 badge result is not the same measurement. Numbers must be
finite and nonnegative; pH must be in 0..14. Future sample times are
rejected. There is no past-age limit. A null sample time stays unknown.

The response stores `estimates`, `differences` (model minus measurement)
and `comparison` provenance with the reading. An evaluation at exactly
the sample instant is used, choosing the higher row ID on a tie.
Otherwise the existing rebuild runs in memory up to that instant using
ingested sensors, logged events and retained weather history. Its model
version and missing-weather intervals are recorded. No current snapshot
or later evaluation is substituted for a historical estimate. With no
sample time or replayable sensors, estimates and differences stay null.
The model has no pH or KH estimate, so those comparisons always stay null.
Kit entries never change snapshots, evaluations or the ingest ledger.

`GET /v1/ponds/{pond}/kit-readings` returns `readings`, ordered by sample
time then ID, unknown times first. Stored comparisons remain unchanged
when later model runs occur. `GET /v1/ponds/{pond}/validation` returns
`analytes`, keyed by the five measurement names. Each has `count`,
`mean_error`, `mean_absolute_error` and `pairs` containing the reading
ID, sample time, measured value, estimated value and signed error in the
analyte's units. Only measured/estimated pairs count; zero is a value.
An analyte without pairs has count 0, null means and an empty list.
Both GETs use the usual authentication, pond access and ETag rules.

Apply migration `0020_kit_readings.sql` before redeploying the API.
It adds a backend-only table with nullable measurement and comparison
fields; existing engine state and model constants are unchanged.
## TDS owner questions (issue #35)

`GET /v1/ponds/{pond}/prompts` returns `enabled` and unanswered `prompts`,
with the usual authentication, pond isolation and ETag rules. With
`KOI_TDS_PROMPTS=false` (default) it returns `enabled: false` and an empty
list, without collecting readings or writing snapshots. Enable the same
setting on the API and worker. The worker produces questions after sensor
ingestion and event reconciliation. No schema migration is required.

Each question contains its UUID `id`, `detection` (`step` or `slope`),
`event_at`, `detected_at`, reading `time_basis`, measured `baseline_ppm`,
`level_ppm`, `delta_ppm`, nullable `slope_ppm_hour`, signal `confidence`,
possible `event_kinds`, nullable `answer` and owner-facing `message`.
Confidence describes the signal, not the likelihood of a particular cause.
Thresholds and suppression rules are in [models.md](../models.md).

`POST /v1/ponds/{pond}/prompts/{prompt_id}` takes `answer`: `water_change`,
`top_up`, `algal_scrub`, `feeding`, `salt`, `filter_clean`, `none of these`
or `dismiss`. Owners may choose an event outside the offered guesses.
Event answers require the same fields as event endpoints: water change
and top-up need `volume_percent` or `volume_litres`, salt needs positive
`salt_grams`, feeding needs `food_grams` and `protein_percent`; scrub has
optional `scrub_type`, and `notes` are optional. Values must be finite.
The signal's first timestamp is the event time and must fall in the existing
30-day replay window. Old questions can still be rejected or dismissed.

The response contains `prompt` and nullable `event_id`. An event uses the
prompt UUID in the ledger and re-assesses the pond. A repeated answer is
a no-op; a different answer to a closed question returns 409. Rejections
persist across restarts; dismissals do not reject later guesses. A missing
question or an answer while the feature is disabled returns 404. No separate
`pondInterventions` row is written, matching existing API event behavior.

Owner actions: redeploy the API and worker; optionally enable
`KOI_TDS_PROMPTS` and configure `KOI_TDS_PROMPT_MIN_STEP_PPM` (20),
`KOI_TDS_PROMPT_CONFIDENCE_THRESHOLD` (0.8) and
`KOI_TDS_PROMPT_COOLDOWN_HOURS` (24) on both. No credentials or hardware
changes are needed.
