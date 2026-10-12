# 0042: Sensor node battery voltage, reset reason and low-battery warning
<!-- verify-banner:begin -->
> ⚠ Agent-written record: its claims are hypotheses to verify against the code, not ground truth. Evidence levels in Verified facts: [checked] confirmed by the tool · [tested] cited test exists and passed in this run · [claimed] not verified.
<!-- verify-banner:end -->
Status: implemented · Issue: #42 · Date: 2026-10-12

## In short
The sensor node can now measure its own supply through a switched resistor divider, send it as `battery_mv` with every cycle, and set a new `battery_low` flag (16) when it is below a threshold configured on the node. The database accepts the new flag (migration 0028), the device summary reads battery and reset reason from payload v2 rows, and the poller writes a `battery_low` warning to the notification outbox once a day while the newest reading is flagged.

## How it works
1. The owner fits a divider from the battery to a spare analogue pin, with a transistor switch driven by a spare output pin, and fills in the pins, resistor values and low threshold in the node's private settings file.
2. Each wake the node reads its probes, then tries Wi-Fi.
3. Straight after the Wi-Fi attempt it switches the divider on, waits briefly, takes the median of 31 analogue readings, switches the divider off and converts the reading to battery millivolts. When Wi-Fi is connected the radio is drawing current, so this is a voltage under load.
4. Every row of the cycle carries that voltage. If it is below the node's threshold, every row also carries the low-battery flag.
5. The database stores the rows. It now accepts the extra flag value.
6. When the backend builds the device summary it takes one battery reading per cycle (not one per row), from old-style battery rows and from the new column, and fits the existing trend line through them. The last reset is taken from the newest boot and dated from that boot's first cycle.
7. Each poll, if the newest battery reading in the last 24 hours carries the low flag, the outbox gets a warning telling the owner to replace or recharge the batteries. The same warning is not written again for 24 hours.

## Main design choice
The low threshold lives on the node, and the server only follows the node's flag. The node decides "low" against `KOI_BATTERY_LOW_MV` (`Embedded/sensor_node/secrets.h.example`, default 4400 mV), and `koi/notifications.py::battery_low` warns only when the newest reading has `low` true.

The alternative was a server-side threshold (a constant or a `KOI_` setting) compared with `battery_mv`. That would also cover legacy `battery_mv` rows, which have no flag. It lost because the battery chemistry differs by node: the issue's default suits 4 alkaline cells behind a linear regulator, while README.md describes a 12 V pack. A server constant of 4400 mV would never warn on a 12 V pack, and two thresholds (node and server) could disagree about the same reading. With the flag, the owner sets the threshold once, next to the divider values that define the voltage scale. A server threshold would be better if many nodes with older firmware sent `battery_mv` rows without flags.

## Traps: why the obvious fix is wrong
- `batteryMvFromAdc` returns -1 for anything above 20000 mV (`battery_monitor.h::BATTERY_MV_MAX`). `ingest_readings` rejects the whole row for an out-of-range `battery_mv` (`invalid_battery_mv`), so clamping or sending it would lose the pH or temperature reading on that row.
- `readCycle` sets `cycle.batteryMv = -1` explicitly. `PendingCycle cycle = {}` makes it 0, and `uploadPending` sends `battery_mv` whenever it is `>= 0`.
- The battery is read after `connectWifi()`, not inside `readCycle`. The issue asks for the voltage under Wi-Fi load; moving it back into the probe sequence reads it with the radio off.
- `batteryIsLow` treats 0 mV as not low. A running node cannot be at 0 V; 0 means the divider is missing or open, and a flag would raise a false warning every day.
- `pond_device_activity` takes one battery reading per (`device_id`, `boot_id`, `cycle_seq`, reading time). The node repeats `battery_mv` on every row of the cycle, so a plain row list would count each reading 4 times and over-weight cycles with more channels in the trend.
- Flag 16 must be accepted by the database before a node sets it. Against 0027 a flagged row is rejected as `invalid_flags`, with its reading.

## Components
- `Embedded/libraries/koi_sensing/src/battery_monitor.h::batteryMvFromAdc` - divider maths, range guard.
- `battery_monitor.h::batteryIsLow`, `uploadRowFlags`, `SAMPLE_FLAG_BATTERY_LOW` - flag rule.
- `Embedded/sensor_node/sensor_node.ino::readBatteryMv`, `reportBattery`, `setup` - switched read after Wi-Fi.
- `sensor_node.ino::handleCommand` `battery` - service-mode reading for the multimeter check.
- `Embedded/sensor_node/secrets.h.example` - pins, resistors, threshold.
- `supabase/migrations/0028_battery_low.sql` - flags 0..31, outbox kind, `pond_device_activity`.
- `Backend/koi/models/device_health.py::last_reset`, `RESET_CODES`, `FLAG_BATTERY_LOW` - v2 reset names.
- `Backend/koi/storage/memory.py::MemoryStorage.fetch_device_activity` - mirrors 0028.
- `Backend/koi/notifications.py::battery_low` - outbox trigger.

## Breaks or needs follow-up
- A node flashed with this firmware and the divider configured, before 0028 is applied on live, loses every reading taken while the battery is low (rejected as `invalid_flags`). Follow-up: owner applies 0028 before flashing (Owner actions).
- `notification_outbox` rows of kind `battery_low` cannot be written before 0028 is applied; `notifications.enqueue` logs the failure and the poll continues.

## Problem and constraints
The node did not measure its battery (Section 3.10.3 specifies a switched divider on a spare ADC1 pin read during the upload). Payload v2 (0017) already had the `battery_mv` column, but nothing filled it, and `pond_device_activity` read battery and reset only from legacy `sensor_type` rows. Constraints: new migration only, legacy rows keep loading, the camera untouched, no live services in tests.

## Alternatives considered
- Read the battery inside `readCycle` with the probes - the radio is off then, so it misses the load the issue asks for.
- Leave `battery_mv` out of cycles queued after a Wi-Fi failure - a node failing Wi-Fi because its battery is sagging would then report nothing about the battery.
- Date the last reset by its newest row - every cycle of a boot repeats the reason, so "last reset" would always read "a few minutes ago".

## Trade-offs
- A cycle queued after a failed Wi-Fi attempt carries a voltage read with the radio already off, a little higher than the connected readings. This would matter if the trend mixed many such cycles.
- The battery read adds about 0.64 s awake per cycle (`N_SAMPLES` 31 x `SAMPLE_GAP_MS` 20 ms, plus `BATTERY_SETTLE_MS` 20 ms).
- Legacy `battery_mv` rows never warn (no flag).

## Key parameters
- `KOI_BATTERY_LOW_MV` 4400 mV (`secrets.h.example`, default in `sensor_node.ino`): from the issue (Section 3.2.8). Raise it for an earlier warning; a 12 V pack needs about 11000+.
- `KOI_BATTERY_R_TOP_OHM` / `KOI_BATTERY_R_BOTTOM_OHM` 100k / 47k: 6.4 V (4 fresh alkaline cells) gives 2.05 V at the pin, inside the accurate part of the 11 dB ADC range. A larger ratio reads higher voltages at lower resolution.
- `KOI_BATTERY_ADC_PIN` 33, `KOI_BATTERY_EN_PIN` 13: chosen from the pins `sensor_node.ino` does not use (34, 35, 25, 4, 26, 21, 22, 27, 23, and 32/18/19 in the service build). Table 3.7 is not in the repository, so it was not checked against it.
- `BATTERY_SETTLE_MS` 20 ms (`sensor_node.ino`): judgement, several time constants of 100k||47k with a 0.1 uF filter capacitor.
- `notifications.COOLDOWNS[BATTERY_LOW]` 24 h: a daily reminder, like `node_silent`.
- `BATTERY_MV_MAX` 20000 (`battery_monitor.h`): 0027's accepted range.

## Assumptions
- The switch is active high and the divider draws no current when it is off. If the owner wires an active-low switch, the divider stays on through sleep and drains the battery, and is off when the node reads it.
- The midpoint stays below about 3.1 V (the 11 dB ADC ceiling); above it the reading saturates and the voltage reads low.
- Every node of a pond uses the same threshold for its own battery (one sensor node per pond by design).

## Edge cases
- Handled: no divider configured (no `battery_mv`, no flag); divider open (0 mV, sent but not flagged, dropped by `battery_trend`); voltage over 20000 mV (not sent); a low reading that recovers (no warning once the newest is unflagged); readings repeated across a cycle's rows (counted once); a cycle without a sample time (timed by receipt); a newer legacy reset row (wins).
- Not handled: a voltage wobbling around the threshold under load sets and clears the flag from cycle to cycle; the owner sees at most one warning a day, but the flag on rows is noisy.

## Not verified
- Anything on the board: the divider reading against a multimeter, the settle time, the load drop under Wi-Fi, the pins against report Table 3.7, and whether GPIO 13 and 33 are free on the actual wiring.
- That ESP32 Arduino's `lroundf` behaves as on the host (it compiles).
- The outbox text on a phone (no sender exists yet).

## Changes outside the scope
- `Embedded/libraries/koi_sensing/src/battery_monitor.h` (new), `koi_sensing.h` (include) and `Embedded/tests/test_all.cpp`: the issue asks for a `koi_sensing` helper with host tests, which live outside `Embedded/sensor_node/**`.
- `supabase/README.md`: a row for 0028, as for every migration.
- Tests under `Backend/tests/`: `test_sql_sensor_payload_v2.py` pinned flag 16 as invalid; its two cases now use 32. `test_notification_outbox.py` pins `COOLDOWNS`; it now includes `battery_low`.

## How it was verified
- Host tests `Embedded/tests/test_all.cpp::test_battery_monitor`: 152 checks before, 177 after.
- `python tools/check.py firmware`: host tests and the three ESP32 compiles (`sensor_node`, `sensor_node -DKOI_SERVICE_MODE`, `camera_node`) with `secrets.h.example`, which enables the divider.
- `Backend/tests/worker/test_battery_low.py` (10 tests: trigger, cool-down, `notify_cycle`, poller, memory activity, reset names).
- `Backend/tests/api/test_devices_api.py::test_device_battery_trend_and_reset_from_payload_v2_rows`.
- `Backend/tests/sql/test_sql_battery.py` (7 tests on the local stack after `supabase db reset`, including memory vs SQL equality), plus the existing SQL suites for payload v2, device health and the outbox.
- `cd Backend && python -m pytest -q -k battery`: 26 passed.

## To change this
- To warn from the server for nodes without the flag, compare `latest_mv` with a new setting in `notifications.battery_low`; cost: a second threshold to keep in step with the node.
- To stop the flag flapping, add hysteresis to `uploadRowFlags` (an RTC-held "was low" bit cleared only above threshold + margin); cost: state in RTC memory that a reset loses.
- To read at rest instead of under load, call `readBatteryMv()` before `connectWifi()`; cost: higher readings that hide a sagging battery.
- Likely bug sources: a new flag bit not added to 0028's 0..31 range; a reader of `battery` that assumes one row per reading.

## Rollback
The firmware's flagged rows depend on 0028. To revert, write a new migration restoring the 0..15 check (after clearing or masking stored flag 16), 0027's `ingest_readings`, 0018's `pond_device_activity` and 0024's outbox kinds (after deleting `battery_low` rows); and flash firmware without `KOI_BATTERY_ADC_PIN`. Then check `/v1/ponds/{pond}/devices` and that `ingest_readings` accepts a cycle.

<!-- verified-facts:begin (generated by integrity record; do not edit) -->
## Verified facts

Generated 2026-10-12 13:32 UTC for issue #42 attempt 1; compared HEAD..working tree (base 4a1e190). Records are claims to check against the code, not proof.

**Change summary**

- `Backend/koi/`: 4 file(s), 14 component(s), +105/-15 lines
- `Embedded/libraries/`: 2 file(s), 0 component(s), +47/-0 lines
- `Embedded/sensor_node/`: 2 file(s), 0 component(s), +73/-12 lines
- `supabase/migrations/`: 1 file(s), 0 component(s), +372/-0 lines
- plus 6 test file(s), 1 doc file(s)
- new files: `Embedded/libraries/koi_sensing/src/battery_monitor.h`, `supabase/migrations/0028_battery_low.sql`

**Removed symbols**

- no remaining references: `Embedded/sensor_node/sensor_node.ino::KOI_BATTERY_DIVIDER`

**Scope**

Declared: `Embedded/sensor_node/**`, `Backend/koi/**`, `supabase/migrations/**`. Verdict: violation.
- out of scope: `Embedded/libraries/koi_sensing/src/koi_sensing.h` (modified); explained in the record: yes
- out of scope: `Embedded/tests/test_all.cpp` (modified); explained in the record: yes

**Supervisor checks passed**

whitespace/conflict-marker check, apply pending migrations to the local Supabase stack, OutdoorKoi host test suites (layout-aware), database suite (migration rehearsal, SQL tests, local profile), scope check, dangling reference check

**Evidence**

13 checked by the tool, 4 tested in this run, 22 claimed only (of 39 claims). [checked] = the named code exists and changed, or the cited value matches; [tested] = the cited test exists and the test gate passed in this run; [claimed] = not verified.
- Components: 9 checked, 0 tested, 0 claimed
- Key parameters: 4 checked, 0 tested, 2 claimed
- Edge cases: 0 checked, 0 tested, 1 claimed
- How it was verified: 0 checked, 4 tested, 2 claimed
- Traps: 0 checked, 0 tested, 6 claimed
- Assumptions: 0 checked, 0 tested, 3 claimed
- Trade-offs: 0 checked, 0 tested, 3 claimed
- Not verified: 0 checked, 0 tested, 3 claimed
- Breaks or needs follow-up: 0 checked, 0 tested, 2 claimed
  - [checked] Components: `Embedded/libraries/koi_sensing/src/battery_monitor.h::batteryMvFromAdc` - divider math... (named code exists and changed)
  - [checked] Key parameters: `KOI_BATTERY_LOW_MV` 4400 mV (`secrets.h.example`, default in `sensor_node.ino`): from... (value matches the code: KOI_BATTERY_LOW_MV = 4400)
  - [tested] How it was verified: Host tests `Embedded/tests/test_all.cpp::test_battery_monitor`: 152 checks before, 177... (test gate passed: OutdoorKoi host test suites (layout-aware))
  - [checked] Components: `battery_monitor.h::batteryIsLow`, `uploadRowFlags`, `SAMPLE_FLAG_BATTERY_LOW` - flag r... (named code exists and changed)
  - [checked] Key parameters: `KOI_BATTERY_ADC_PIN` 33, `KOI_BATTERY_EN_PIN` 13: chosen from the pins `sensor_node.in... (value matches the code: KOI_BATTERY_ADC_PIN = 33, KOI_BATTERY_EN_PIN = 13)
  - [tested] How it was verified: `Backend/tests/worker/test_battery_low.py` (10 tests: trigger, cool-down, `notify_cycle... (test gate passed: OutdoorKoi host test suites (layout-aware))
  - [checked] Components: `Embedded/sensor_node/sensor_node.ino::readBatteryMv`, `reportBattery`, `setup` - switc... (named code exists and changed)
  - [checked] Key parameters: `BATTERY_SETTLE_MS` 20 ms (`sensor_node.ino`): judgement, several time constants of 100... (value matches the code: BATTERY_SETTLE_MS = 20)
  - [tested] How it was verified: `Backend/tests/api/test_devices_api.py::test_device_battery_trend_and_reset_from_payloa... (test gate passed: OutdoorKoi host test suites (layout-aware))
  - [checked] Components: `sensor_node.ino::handleCommand` `battery` - service-mode reading for the multimeter ch... (named code exists and changed)
  - full per-claim list (39) in the JSON record

**Consistency**

0 error(s), 1 warning(s) between the rationale above and the change.
- WARN: Backend/koi/storage/base.py::Storage.fetch_device_activity (modified) changed but not mentioned in the record

**Commit**

This record is committed with the change it describes; `git log -1 -- docs/decisions/0042-battery-low-warning.md` gives the commit.
<!-- verified-facts:end -->
