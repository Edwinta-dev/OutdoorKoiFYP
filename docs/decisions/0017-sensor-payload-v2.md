# 0017: Sensor payload v2 across database, ingestion and firmware
Status: implemented · Issue: #17 · Date: 2026-10-11

## In short

A sensor reading can now carry the node's own sample time, the device and boot it came from, its wake cycle, quality flags, the reset reason and an optional battery voltage. The database refuses spoofed or malformed fields, stores a re-sent cycle once, and every reader times a reading by its sample time when it has one and by its insert time otherwise, saying which. The graph no longer re-aggregates the whole table or fails on a row with no pond (#97, database half).

## Problem and constraints

`SensorData` had only `id`, `created_at` (insert time), `sensor_type`, `data1` and `userID`. Every chart, the dashboard and the poller therefore treated "when the database stored it" as "when the probe read it". `aggregate_daily_sensor_data()` rebuilt every day of every pond on each graph call, and one row with a null `userID` made it fail, which emptied every pond's graphs.

Constraints: new columns nullable, no edit of applied migrations, legacy rows keep loading, the camera's `POST /upload` untouched, tests on the local stack only, and #50 owns the "hour needs pH and TDS" rule and the substituted history values.

## Approach

- **`supabase/migrations/0027_sensor_payload_v2.sql`**
  - `SensorData` gains nullable `device_id`, `boot_id`, `cycle_seq`, `sampled_at`, `flags`, `reset_reason` and `battery_mv`, with check constraints, the partial unique retry key `sensordata_cycle_key` (`device_id`, `boot_id`, `cycle_seq`, `sensor_type`) where `boot_id` is set, and three per-pond indexes.
  - `public.sensor_channel` maps `pH`, `TDS`, `temp`, `LUX` and `lux` to the four canonical channels and anything else to null.
  - `public.ingest_readings` (same signature and grants) accepts the v2 fields. Pond and device always come from the token. Each new field has its own rejection reason. `sampled_at` must be ISO 8601 with an explicit offset, no more than 5 minutes ahead and no more than 30 days old, and not sent with the "clock not set" flag. Inserts use `on conflict do nothing` on the retry key, and the reply reports `stored` and `already_stored`. v1 rows are still accepted.
  - `public.aggregate_daily_sensor_data(p_from, p_to, p_pond_id)` replaces the zero-argument version. It covers at most 366 Singapore days, skips rows with no pond, and groups by canonical channel and the Singapore date of `coalesce(sampled_at, created_at)`. For each (pond, day) that has readings, it rewrites that day's rows. It also writes `sample_count` and `sample_time_count`.
  - `public.reconcile_daily_sensor_averages(p_pond_id, p_days)` with the table `daily_sensor_refresh` is the bounded reconciliation job. It recomputes days of the window that were never covered, plus days that received rows since the last run (10-minute overlap). `get_historical_graph_payload` calls it for the requested pond and window only, and each daily item adds `sample_count` and `time_basis`.
  - `get_pond_telemetry_history` is recreated with a `time_basis` column. `get_bundled_dashboard_payload` orders `raw_sensor` by reading time then id, and adds `latest_readings` (per channel: id, value, `sampled_at`, `ingested_at`, `time`, `time_basis`, `flags`) and a history `time_basis`. `pond_dashboard_sources`, `sensor_ingest_pending` and `sensor_ingest_history` return `sampled_at` and `flags`.
- **Backend.** `Backend/koi/storage/memory.py::MemoryStorage` mirrors the SQL readers (`_latest_key`, `_sensor_row`). `Backend/koi/api/dashboard.py::_reading` picks the newest reading by reading time and sets `sample_time`, `sample_time_basis` (`sample` or `unknown`), `flags`, and freshness from the sample time. `Backend/koi/api/responses.py::SensorReading` gains `flags`. The OpenAPI document and the Dart fixture are regenerated. The poller needed no change: `Backend/koi/models/sensor_inputs.py::effective_sample_time` already reads `sampled_at`.
- **Firmware.** `Embedded/libraries/koi_sensing/src/sample_cycle.h` holds the host-tested pieces: boot id, ISO formatting, the sample time worked back from `millis()`, flags, reset-reason names, the retry rule, and the RTC queue `PendingCycles`. `Embedded/sensor_node/sensor_node.ino` does the following:
  - On each cold boot it draws a boot id: the NVS counter in the high 32 bits and `esp_random()` with the bootloader entropy source in the low 32.
  - It counts cycles in RTC memory.
  - It runs SNTP after Wi-Fi, and again every 240 cycles.
  - It sends `sampled_at` only when the clock was set.
  - It leaves failed temperature and light reads out of the upload.
  - It keeps up to 4 unsent cycles in RTC memory and re-sends them with the next cycle.
  - The bench service mode is compiled in with `KOI_SERVICE_MODE`.
- **Check runner.** `tools/check.py::compile_targets` compiles `sensor_node`, `camera_node`, and `sensor_node` with `-DKOI_SERVICE_MODE`. `Embedded/sensor_bench/` is deleted.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Retry key (`device_id`, `cycle_seq`, `sensor_type`) | `cycle_seq` is in RTC memory and restarts after every power loss, so a new cycle after a reboot would be dropped as a repeat. |
| NVS counter alone as boot id | It repeats after an NVS erase or rollback. The random half covers that. |
| Random 64 bits alone | Without the radio, `esp_random()` may be pseudo-random. The counter keeps ids distinct while NVS is intact. |
| Backfill `sampled_at = created_at` | That would label insert times as sample times. Readers use `coalesce` and report `ingestion`. |
| Per-channel sample times | `group_rows` groups by time, so one cycle would split into four one-channel inputs (cycle grouping is #82). The node sends one sample time per cycle: the start of its read sequence. |
| Recompute the whole window on every graph call | It stays bounded but repeats work. The watermark table recomputes only new or changed days. |

## Trade-offs

- The pH reading is taken about 8 s after the cycle's `sampled_at`. This would matter if sub-minute timing were ever needed.
- Unsent cycles are lost on power loss or reset, because the queue is in RTC memory. Moving it to flash would cost a write per cycle.
- `get_historical_graph_payload` still writes, and still serves any pond to the publishable key until #116.
- Daily rows now cover only the four water channels. Old `battery_mv` daily rows are removed when their day is recomputed.
- `pond_device_activity` still reads battery and reset values only from `sensor_type` rows. The v2 columns are stored but not yet shown under `/devices`.
- `full_sketch` (the build on the board per 0012) still sends v1 rows. They are accepted, but have no sample time.

## Key parameters

- **Future skew 5 min, maximum age 30 days** (`ingest_readings`). These allow for clock drift and a long outage. Lowering the age limit drops late retries; raising the skew admits bad clocks.
- **Flags 0..15.** These are the four defined bits. A new bit needs a migration.
- **Clock considered set after 2026-01-01 UTC** (`CLOCK_SET_AFTER_EPOCH_S`). An unset ESP32 clock reads 1970.
- **SNTP wait 5 s, resync every 240 cycles** (about hourly at 15 s). A longer wait costs battery; less frequent resyncs allow more drift.
- **Queue 4 cycles × 4 readings.** This is 16 rows, well under the server's 32. Raising the queue size costs RTC memory and larger requests.
- **Reconcile overlap 10 min.** It covers transactions that commit late. Raising it repeats more work.
- **Aggregation span 366 days, `p_days` 1..366.** These match the graph's existing bound.
- **LUX > 50 lx** in daily averages: unchanged from 0001.

## Assumptions

- Deep sleep keeps the ESP32 system clock running (ESP-IDF behaviour). A cold boot is treated as having lost it.
- `bootloader_random_enable()` gives real entropy before the ADC is used (it compiles; entropy was not measured).
- The node's whole read cycle is one sample instant for the model.
- No process deletes `SensorData` rows. Days with no readings keep their daily rows in case one does.

## Edge cases

- **Handled:**
  - legacy rows read as `ingestion` everywhere;
  - a sample uploaded hours late lands on its sample hour and day, and does not become the "latest" reading;
  - a repeated upload is stored once;
  - a reboot or two cycles with equal times are both stored;
  - a missing channel stays null;
  - two ponds stay separate, and a pond-less row is skipped;
  - local midnight is split by sample time;
  - case variants (`ph`, `Temp`, `lux`) are refused at ingest;
  - an unsynced clock produces no `sampled_at` and the flag.
- **Not handled:**
  - a node whose clock is set but wrong by under 5 minutes into the future is believed;
  - a device id shared across two ponds is impossible by design (one device per pond).

## Changes outside the scope

The issue has no **Scope:** line. These changes were made beyond the files the issue names:
- Existing tests that pinned the old contracts now assert the new ones. `test_sql_dashboard` (index plan, which now also disables sorting), `test_sql_event_ledger` (row keys), `test_sql_least_privilege` (new signature), `test_sql_local_days` and `test_sql_rpcs` (explicit date range), `test_sql_sensor_ingest` (column list), and `tools/test_check.py`.
- `README.md`, `docs/api/README.md`, `docs/access-matrix.md`, `supabase/README.md` and `docs/database-reconciliation.md` (D1, D5) are updated. `ingest_reply.h` comments and the 400 text are updated.

## How it was verified

- New `Backend/tests/sql/test_sql_sensor_payload_v2.py`: 56 cases on the local stack, covering every acceptance case, the validation and spoofing cases, and access.
- New scenario `payload_v2` in `tests/dashboard_scenarios.py`, checked in memory and through SQL for identical responses.
- Two poller tests in `tests/worker/test_sensor_ingest.py`.
- Host tests for `sample_cycle.h`: 109 checks before, 152 after.
- `tools/db_rehearsal.py`: converges and keeps legacy rows. The dev stack's local database profile passes with the seed's pond-less row.
- ESP32 compiles of both `sensor_node` builds and `camera_node`.
- **Not verified:** anything on the board (SNTP, entropy, RTC queue across sleep, battery divider), and anything on live.

## To change this

- To favour precise per-channel times, send `sampled_at` per reading and group by cycle key in `sensor_inputs` (#82). The cost is a larger payload.
- To keep unsent cycles through power loss, move `PendingCycles` to NVS. The cost is a flash write per cycle.
- To show v2 battery and reset values in `/devices`, read the columns in `pond_device_activity`.
- Likely bug sources:
  - a new reader that orders by `created_at` instead of `coalesce(sampled_at, created_at)`;
  - a new channel spelling not added to `sensor_channel`.

## Rollback

The firmware's v2 rows depend on 0027's `ingest_readings`. Against 0025's version they are refused as a whole batch (400), and the node drops them. To revert, write a new migration that restores 0026's function bodies and 0025's `ingest_readings`, and recreates the zero-argument aggregate. The new columns can stay, since they are nullable. Afterwards, check the graph for a pond, `/v1/.../dashboard`, and that the poller's ledger keeps advancing.

<!-- verified-facts:begin (generated by integrity record; do not edit) -->
## Verified facts

Generated 2026-10-12 03:02 UTC for issue #17 attempt 3; compared HEAD..working tree (base bc575ba).

**Changed components**

| Component | Change | Lines +/- | Tags | Description |
|---|---|---|---|---|
| `Backend/koi/api/dashboard.py::_reading` | modified | +11/-3 |  |  |
| `Backend/koi/api/dashboard.py::_reading.reading_time` | added | +2/-0 |  |  |
| `Backend/koi/api/dashboard.py::<module>` | modified | +6/-3 |  |  |
| `Backend/koi/api/responses.py::SensorReading` | modified | +11/-5 |  | The newest SensorData row of one channel. Every channel carries its |
| `Backend/koi/api/responses.py::ChannelHealth` | modified | +2/-1 |  |  |
| `Backend/koi/dev/seed.py::SeedHistory.payload` | modified | +2/-1 |  | What get_bundled_dashboard_payload returns for the pond at `at`: |
| `Backend/koi/dev/seed.py::SeedHistory.payload.reading_key` | added | +4/-0 |  |  |
| `Backend/koi/models/device_health.py::<module>` | modified | +4/-3 |  |  |
| `Backend/koi/models/sensor_inputs.py::group_rows` | modified | +3/-3 |  | Rows of one pond ({id, sensor_type, value, created_at, sampled_at, |
| `Backend/koi/models/sensor_inputs.py::<module>` | modified | +3/-2 |  |  |
| `Backend/koi/storage/memory.py::MemoryStorage.fetch_pending_sensor_rows` | modified | +1/-2 |  | The same rows sensor_ingest_pending (migration 0014) returns. |
| `Backend/koi/storage/memory.py::MemoryStorage.fetch_ingested_sensor_rows` | modified | +1/-2 |  | The same rows sensor_ingest_history (migration 0015) returns. |
| `Backend/koi/storage/memory.py::MemoryStorage._sensor_row` | added | +7/-0 |  | A SensorData row as sensor_ingest_pending and |
| `Backend/koi/storage/memory.py::MemoryStorage.fetch_dashboard_sources` | modified | +3/-2 |  | The same rows pond_dashboard_sources (migration 0012) returns, |
| `Backend/koi/storage/memory.py::MemoryStorage._reading_key` | modified | +1/-1 |  | SensorData insert order: created_at, then id (the SQL tie breaker). |
| `Backend/koi/storage/memory.py::MemoryStorage._latest_key` | added | +5/-0 |  | Newest SensorData reading (migration 0027): its reading time, |
| `Backend/koi/storage/memory.py::TABLE_COLUMNS` | modified | +5/-3 |  |  |
| `Backend/koi/tools/rebuild.py::HistoryStorage.fetch_dashboard_payload` | modified | +2/-2 |  |  |
| `Backend/koi/tools/rebuild.py::HistoryStorage.fetch_dashboard_payload.reading_key` | added | +4/-0 |  |  |
| `Embedded/libraries/koi_sensing/src/ingest_reply.h` | modified | +4/-2 |  |  |
| `Embedded/libraries/koi_sensing/src/koi_sensing.h` | modified | +1/-0 |  |  |
| `Embedded/libraries/koi_sensing/src/sample_cycle.h` | new file | +168/-0 |  |  |
| `Embedded/sensor_bench/sensor_bench.ino` | deleted file | +0/-610 |  |  |
| `Embedded/sensor_node/sensor_node.ino` | modified | +475/-85 |  |  |
| `supabase/migrations/0027_sensor_payload_v2.sql` | new file | +1081/-0 |  |  |
| `tools/check.py::compile_targets` | added | +3/-0 |  | Every firmware compile: (sketch, FQBN, macro or None). |
| `tools/check.py::check_firmware` | modified | +6/-4 |  |  |
| `tools/check.py::SKETCHES` | modified | +0/-1 |  |  |
| `tools/check.py::SKETCH_VARIANTS` | added | +3/-0 |  |  |
| `tools/check.py::<module>` | added | +5/-0 |  |  |

Plus 18 test file(s), 6 doc file(s).

**Removed symbols**

- `Embedded/sensor_bench/sensor_bench.ino::gyRead` (function): no remaining references
- `Embedded/sensor_bench/sensor_bench.ino::loadPhCal` (function): no remaining references
- `Embedded/sensor_bench/sensor_bench.ino::loadTdsK` (function): no remaining references
- `Embedded/sensor_bench/sensor_bench.ino::readGy33` (function): no remaining references
- `Embedded/sensor_bench/sensor_bench.ino::readTsl` (function): no remaining references
- `Embedded/sensor_bench/sensor_bench.ino::runCycle` (function): no remaining references
- `Embedded/sensor_bench/sensor_bench.ino::tempForCompensation` (function): no remaining references

**Scope**

No Scope line in the issue.

**Supervisor checks passed**

not available

**Consistency**

0 error(s), 19 warning(s) between the rationale above and the change.
- WARN: Backend/koi/api/responses.py::ChannelHealth (modified) changed but not mentioned in the record
- WARN: Backend/koi/dev/seed.py::SeedHistory.payload (modified) changed but not mentioned in the record
- WARN: Backend/koi/dev/seed.py::SeedHistory.payload.reading_key (added) changed but not mentioned in the record
- WARN: Backend/koi/models/device_health.py::<module> (modified) changed but not mentioned in the record
- WARN: Backend/koi/storage/memory.py::TABLE_COLUMNS (modified) changed but not mentioned in the record
- WARN: Backend/koi/tools/rebuild.py::HistoryStorage.fetch_dashboard_payload (modified) changed but not mentioned in the record
- WARN: Backend/koi/tools/rebuild.py::HistoryStorage.fetch_dashboard_payload.reading_key (added) changed but not mentioned in the record
- WARN: Embedded/libraries/koi_sensing/src/koi_sensing.h (modified) changed but not mentioned in the record
- WARN: Embedded/sensor_bench/sensor_bench.ino (deleted file) changed but not mentioned in the record
- WARN: tools/check.py::check_firmware (modified) changed but not mentioned in the record
- WARN: tools/check.py::SKETCHES (modified) changed but not mentioned in the record
- WARN: tools/check.py::SKETCH_VARIANTS (added) changed but not mentioned in the record
- WARN: removed function gyRead (from Embedded/sensor_bench/sensor_bench.ino) without mentioning it
- WARN: removed function loadPhCal (from Embedded/sensor_bench/sensor_bench.ino) without mentioning it
- WARN: removed function loadTdsK (from Embedded/sensor_bench/sensor_bench.ino) without mentioning it
- WARN: removed function readGy33 (from Embedded/sensor_bench/sensor_bench.ino) without mentioning it
- WARN: removed function readTsl (from Embedded/sensor_bench/sensor_bench.ino) without mentioning it
- WARN: removed function runCycle (from Embedded/sensor_bench/sensor_bench.ino) without mentioning it
- WARN: removed function tempForCompensation (from Embedded/sensor_bench/sensor_bench.ino) without mentioning it

**Commit**

This record is committed with the change it describes; `git log -1 -- docs/decisions/0017-sensor-payload-v2.md` gives the commit.
<!-- verified-facts:end -->
