# 0082: Poller v2 ordering and cycle grouping
<!-- verify-banner:begin -->
> ⚠ Agent-written record: its claims are hypotheses to verify against the code, not ground truth. Evidence levels in Verified facts: [checked] confirmed by the tool · [tested] cited test exists and passed in this run · [claimed] not verified.
<!-- verify-banner:end -->
Status: implemented · Issue: #82 · Date: 2026-10-19

## In short

Sensor replay now uses `sampled_at` when present and groups v2 rows by device, boot and cycle. Late rows are reconciled even when older than the normal scan overlap and replayed at their sample time from history inside the checkpoint window; a cycle read across two polls is merged back into one input. A late row older than the window is left out of the model and recorded `out_of_window`.

## How it works

1. The poller asks for every unledgered sensor row up to the current poll time. The overlap remains a reconciliation hint, while the discovery cursor and atomic ledger still use insert time.
2. Rows with a sample time use that time; older rows use their insertion time. Rows from one complete device, boot and cycle identity form one model input. Legacy rows without that identity continue to group by effective time.
3. Inputs are ordered by sample time and then row id. The discovery ledger records every row, including rows superseded by a newer row for the same channel.
4. If an input arrives at or behind the model's current sensor time, the twin rewinds to a checkpoint and replays inputs from history with it at its sample time, within the 30-day window (`Backend/koi/models/event_ledger.py::WINDOW`). During the replay, `Backend/koi/models/sensor_inputs.py::merge_inputs` joins an input to the ledgered input with the same `(device_id, boot_id, cycle_seq)`, so a cycle split by the scan limit or a store-and-forward retry becomes one input again.
5. A late input older than the window has no checkpoint to rewind to. The twin leaves it out of chemistry (provenance `placed: False`) and the poller records its applied rows as `out_of_window` (migration 0031). Provenance `late` still means only that the input arrived behind the model's sensor clock.
6. With no checkpoint before a late input (a twin's first local day) or no history, the input is still applied at the last input's time, as `model_time` shows.

## Main design choice

Keep insertion time as the discovery cursor, and order only after retrieval by effective sample time and id. Moving the cursor to sample time could hide newly inserted rows. The pending scan no longer uses its overlap as a hard lower bound: that makes the cursor a constant-cost optimization less reliable, but preserves discovery of old unledgered rows and makes out-of-window arrivals observable. Device-cycle identity is `(device_id, boot_id, cycle_seq)`; sequence alone collides across devices, while boot id is required because a node can restart its sequence after reboot.

For a late row older than the replay window, the alternative was a consumer that rebuilds the pond from `rebuild_required` rows. That means an unattended full rebuild triggered by one stale row, and without one the row distorts the model at a time it does not describe. Leaving it out of the model and recording `out_of_window` keeps the model truthful and the row auditable.

## Traps: why the obvious fix is wrong

- Sorting by the cycle grouping tuple orders device names before timestamps. Groups must be sorted by their earliest effective sample time, then row id.
- Restoring `created_at` as model time loses the sensor's actual sampling order during buffered uploads.
- Dropping the query's lower bound without keeping the ledger anti-join would replay already applied rows. The discovery ledger remains the idempotence boundary.
- A late input that cannot be replayed must not be recorded as an ordinary applied row. The first version wrote `rebuild_required` and applied the row at the clamped time, but nothing read that disposition. The row is now left out of the model, and `out_of_window` says so.
- Grouping only within one poll turns a split cycle into two inputs at one time. The merge has to happen against the ledgered history, which is why it runs inside the replay.

## Components

- `Backend/koi/models/sensor_inputs.py::group_rows` - cycle grouping and time order
- `Backend/koi/models/pond_twin.py::ingest_sensor_inputs` - rewind, replay, out-of-window inputs
- `Backend/koi/models/sensor_inputs.py::merge_inputs` - split cycles rejoined in a replay
- `supabase/migrations/0031_sensor_ingest_out_of_window.sql` - `out_of_window` disposition; `rebuild_required` retired
- `Backend/koi/worker/poller.py::_discover` - reconciliation and ledger disposition
- `Backend/koi/storage/memory.py::fetch_pending_sensor_rows` - in-memory parity
- `supabase/migrations/0029_poller_sample_time_cycle_identity.sql` - SQL retrieval and disposition
- `Backend/tests/models/test_sensor_inputs.py` - ordering, grouping and age bounds
- `Backend/tests/worker/test_sensor_ingest.py` - replay and reconciliation coverage

## Breaks or needs follow-up

Full unledgered scans can cost more on ponds with long sensor histories. Confirm query plans on the host database after migration 0029; consider a bounded indexed reconciliation only if it can still surface every excluded row for rebuild. Apply migration 0029 to each deployed database before deploying the worker change.

## Problem and constraints

Issue #82 depends on payload v2 fields from #17 and the lossless ledger from #18. The implemented change keeps the row id ledger and cursor atomic with the snapshot. No camera contract is involved.

## Alternatives considered

- Keep the overlap as a hard filter - cheaper, but rows older than the window disappear without a rebuild signal.
- Group by effective timestamp only - merges independent devices whose cycles happen to have equal times.
- Group by sequence only - sequence repeats between devices and after reboot.

## Trade-offs

All unledgered rows remain recoverable, but each poll may inspect older `SensorData` rows before reaching new inserts. A future index or explicit pending-rebuild queue could reduce this cost while preserving visibility.

## Key parameters

- 30 days: replay/checkpoint window (`Backend/koi/models/event_ledger.py::WINDOW`). Older late inputs are left out of the model and marked `out_of_window`; increasing the window costs more retained replay history.
- 60 minutes: configured overlap default (`Backend/koi/settings.py::sensor_ingest_overlap_minutes`). It remains a reported reconciliation hint; changing it does not hide rows because pending retrieval scans all unledgered rows.
- 2,000 rows per poll by default (`Backend/koi/settings.py::sensor_ingest_batch_rows`). Smaller batches reduce one poll's work and increase backlog catch-up time.

## Assumptions

The ingestion ledger is committed atomically with the snapshot, and `SensorData` rows remain available for replay. Deleting source rows before rebuilding would prevent history replay. Each complete `(device_id, boot_id, cycle_seq)` tuple identifies one cycle.

## Edge cases

- Handled: equal-time channels, separate devices with equal cycle numbers, sample times older than insertion time, and unledgered rows older than the overlap.
- Handled: late input within retained checkpoint history is replayed at its sample time; a cycle split across polls is merged during that replay; a late input older than the window is left out and recorded `out_of_window`.
- Not handled: a split cycle or late input before the twin's first checkpoint (its first local day) is applied as a separate input at the last input's time.

## Not verified

Production query plans and migration application on a deployed database were not verified. Local SQL-backed ingestion tests ran against the local database. Migration 0031 was not applied to a deployed database.

## Changes outside the scope

`Backend/koi/storage/memory.py` and SQL/worker tests were changed because the model/poller work requires the in-memory adapter to return the same identity as SQL and requires coverage for reconciliation. `Backend/tests/sql/test_sql_event_ledger.py` updates its history-RPC key assertion because migration 0029 adds three nullable device-cycle identity fields to returned rows.

## How it was verified

- Follow-up (split cycles, out-of-window rows): `Backend/tests/worker/test_sensor_ingest.py::test_a_cycle_split_across_two_polls_is_merged_into_one_input` (fails without the merge), `Backend/tests/worker/test_sensor_ingest.py::test_a_late_row_older_than_the_replay_window_is_left_out_and_recorded`, `Backend/tests/models/test_sensor_inputs.py::test_a_late_input_older_than_the_replay_window_is_left_out_of_the_model`; `python tools/check.py all` and `python tools/check.py database --strict` passed (backend 1,987 passed; SQL tests 421 passed; migration rehearsal and local profile 21/21 passed).

- `python -m pytest tests/sql/test_sql_sensor_ingest.py tests/worker/test_sensor_ingest.py tests/models/test_sensor_inputs.py tests/models/test_pond_twin.py -q`: 59 passed, 253 assertions; covers SQL retrieval, grouping, replay, and rebuild signaling.
- `python -m pytest tests/sql/test_sql_event_ledger.py::test_sql_ledger_sensor_history_returns_only_ledgered_rows_in_the_window -q`: 1 passed, 5 assertions; checks the revised history row shape.
- `python tools/check.py all`: backend 1,970 passed and 1 skipped; tools 39 passed; backtest 11/11; firmware host tests 296 passed and all three ESP32 builds passed; Flutter analysis and 266 tests passed.
- The backend suite's one skip is the SQL dev-profile test because Docker is unavailable. The migration rehearsal was not run; deployed databases still need migration 0029.

## To change this

- To reduce pending-query work, add a bounded queue that records rows outside the scan bound as `out_of_window`; do not reinstate a silent lower-bound filter.
- To widen replay coverage, change `Backend/koi/models/event_ledger.py::WINDOW`; cost: more checkpoint and replay history.

## Rollback

Reverting the model and poller restores timestamp grouping and current-time placement of late readings. Migration 0029 may remain applied because it only changes retrieval fields. Reverting the twin's out-of-window handling needs migration 0031's constraint widened again; rows already marked `out_of_window` were never applied to the model.

<!-- verified-facts:begin (generated by integrity record; do not edit) -->
## Verified facts

Generated 2026-10-18 17:15 UTC for issue #82 attempt 20; compared HEAD..working tree (base 3010c5c). Records are claims to check against the code, not proof.

**Change summary**

- `Backend/koi/`: 4 file(s), 9 component(s), +48/-26 lines
- `supabase/migrations/`: 1 file(s), 0 component(s), +87/-0 lines
- plus 4 test file(s)
- new files: `supabase/migrations/0029_poller_sample_time_cycle_identity.sql`

**Removed symbols**

None.

**Scope**

Declared: `Backend/koi/worker/poller.py`, `supabase/migrations/**`, `Backend/koi/models/sensor_inputs.py`, `Backend/koi/models/pond_twin.py`, `Backend/tests/models/test_sensor_inputs.py`, `Backend/tests/models/test_pond_twin.py`. Verdict: violation.
- out of scope: `Backend/koi/storage/memory.py::MemoryStorage.fetch_pending_sensor_rows` (modified); explained in the record: yes
- out of scope: `Backend/koi/storage/memory.py::MemoryStorage._sensor_row` (modified); explained in the record: yes

**Supervisor checks passed**

whitespace/conflict-marker check, apply pending migrations to the local Supabase stack, OutdoorKoi host test suites (layout-aware), database suite (migration rehearsal, SQL tests, local profile), scope check, dangling reference check

**Evidence**

3 checked by the tool, 1 tested in this run, 20 claimed only (of 24 claims). [checked] = the named code exists and changed, or the cited value matches; [tested] = the cited test exists and the test gate passed in this run; [claimed] = not verified.
- Components: 3 checked, 0 tested, 4 claimed
- Key parameters: 0 checked, 0 tested, 3 claimed
- Edge cases: 0 checked, 0 tested, 2 claimed
- How it was verified: 0 checked, 1 tested, 3 claimed
- Traps: 0 checked, 0 tested, 4 claimed
- Assumptions: 0 checked, 0 tested, 1 claimed
- Trade-offs: 0 checked, 0 tested, 1 claimed
- Not verified: 0 checked, 0 tested, 1 claimed
- Breaks or needs follow-up: 0 checked, 0 tested, 1 claimed
  - [checked] Components: `Backend/koi/models/sensor_inputs.py::group_rows` - cycle grouping and time order (named code exists and changed)
  - [tested] How it was verified: `python -m pytest tests/sql/test_sql_event_ledger.py::test_sql_ledger_sensor_history_re... (test gate passed: OutdoorKoi host test suites (layout-aware))
  - [checked] Components: `Backend/koi/worker/poller.py::_discover` - reconciliation and ledger disposition (named code exists and changed)
  - [checked] Components: `supabase/migrations/0029_poller_sample_time_cycle_identity.sql` - SQL retrieval and di... (named code exists and changed)
  - [claimed] Components: `Backend/koi/models/pond_twin.py::ingest_sensor_inputs` - rewind and rebuild signal (`Backend/koi/models/pond_twin.py::ingest_sensor_inputs` not found)
  - [claimed] Key parameters: 30 days: replay/checkpoint window (`Backend/koi/models/event_ledger.py::WINDOW`). Older... (no cited constant with a matching value)
  - [claimed] Edge cases: Handled: equal-time channels, separate devices with equal cycle numbers, sample times o... (names no code)
  - [claimed] How it was verified: `python -m pytest tests/sql/test_sql_sensor_ingest.py tests/worker/test_sensor_ingest.p... (no test named)
  - [claimed] Components: `Backend/koi/storage/memory.py::fetch_pending_sensor_rows` - in-memory parity (`Backend/koi/storage/memory.py::fetch_pending_sensor_rows` not found)
  - [claimed] Key parameters: 60 minutes: configured overlap default (`Backend/koi/settings.py::sensor_ingest_overlap... (no cited constant with a matching value)
  - full per-claim list (24) in the JSON record

**Consistency**

0 error(s), 6 warning(s) between the rationale above and the change.
- WARN: 'Main design choice' names no alternative; say which 1-2 real options were possible and why this one won
- WARN: references something that doesn't exist: Backend/koi/models/pond_twin.py::ingest_sensor_inputs (no such component in the old or new version)
- WARN: references something that doesn't exist: Backend/koi/storage/memory.py::fetch_pending_sensor_rows (no such component in the old or new version)
- WARN: references something that doesn't exist: Backend/koi/settings.py::sensor_ingest_overlap_minutes (no such component in the old or new version)
- WARN: references something that doesn't exist: Backend/koi/settings.py::sensor_ingest_batch_rows (no such component in the old or new version)
- WARN: Backend/koi/models/pond_twin.py::PondTwin.ingest_sensor_inputs (modified) changed but not mentioned in the record

**Commit**

This record is committed with the change it describes; `git log -1 -- docs/decisions/0082-poller-v2-order-cycle.md` gives the commit.
<!-- verified-facts:end -->
