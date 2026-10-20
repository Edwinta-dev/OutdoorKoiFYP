# 0044: Sensor node store-and-forward for failed uploads
<!-- verify-banner:begin -->
> ⚠ Agent-written record: its claims are hypotheses to verify against the code, not ground truth. Evidence levels in Verified facts: [checked] confirmed by the tool · [tested] cited test exists and passed in this run · [claimed] not verified.
<!-- verify-banner:end -->
Status: implemented · Issue: #44 · Date: 2026-10-12

## In short
The sensor node now keeps up to 24 unsent cycles in RTC memory instead of 4. When that ring is full, the oldest cycle moves to a second ring of 24 in flash (NVS) instead of being dropped, and the flash ring survives power loss. Once Wi-Fi is back, the node sends the backlog oldest first, each cycle with its original boot id, cycle number and sample time, in batches the server accepts.

## How it works
1. Each wake the node reads its probes into one cycle record, stamped with its boot id, cycle number and, when the clock is set, its sample time.
2. The cycle joins a queue in the memory that survives deep sleep. If that queue already holds 24 cycles, the oldest one is written to flash first, so nothing is lost while the outage is shorter than about 48 cycles.
3. If the flash queue is also full, its oldest cycle is given up to make room.
4. When Wi-Fi connects, the node builds a batch from the oldest cycles: the flash queue first, then the memory queue, stopping when the next cycle would take the batch past 32 readings.
5. If the server stores the batch, those cycles are removed and the node sends the next batch, at most two per wake. A temporary failure keeps everything for the next wake. A permanent refusal (for example a revoked token) drops that batch only.
6. After a power cut the memory queue is empty, but the flash queue is read back and sent first.
7. A batch that was stored but not yet removed (power lost in between) is sent again; the database's unique index stores each reading once.

## Main design choice
Two rings: RTC memory first, NVS only for overflow (`Embedded/libraries/koi_sensing/src/store_forward.h::bufferPush`). Flash is written only when an outage outlasts the RTC ring, so a node with healthy Wi-Fi never writes NVS for buffering.

Alternatives seriously possible:
- Write every unsent cycle to NVS straight away. That keeps all unsent cycles through power loss, at the cost of a flash write per failed cycle and slower wakes during every Wi-Fi blip. It would be better if power cuts during outages turned out to be common (for example a node on a solar supply that browns out at night).
- One NVS blob holding the whole spill ring. Fewer keys, but every spill rewrites the whole ring (about 2 KB) instead of one 80-byte slot plus an 8-byte header.

The issue asks for "one request" for the backlog. The server refuses more than 32 rows per call with 413 (`supabase/migrations/0028_battery_low.sql` `c_max_rows`), and the scope (`Embedded/sensor_node/**`) does not cover a migration, so the backlog goes in batches of up to 32 rows, two per wake (`Embedded/sensor_node/sensor_node.ino::UPLOAD_BATCHES_PER_WAKE`).

## Traps: why the obvious fix is wrong
- Do not send the whole backlog in one request: more than 32 rows is a 413, which `ingestShouldRetry` treats as permanent, so the whole backlog would be dropped.
- The write order in `store_forward.h::spillAppend` matters: slot first, then header; when full, the header releases the oldest slot before that slot is overwritten. Writing the header first, or overwriting the slot before releasing it, lets a power cut leave the header pointing at a slot holding the wrong (newest) cycle.
- `batchPlan` must read the spill before the RTC ring. Every spilled cycle is older than every RTC cycle, including after a power cut (new cycles only enter RTC). Reversing it breaks oldest-first.
- `SpillMeta::recordSize` and `capacity` tie stored slots to this build's layout. Changing `PendingCycle` or `SPILL_CAPACITY` makes the old spill read as empty, by design: the bytes would otherwise be misread.
- `BatchOutcome` lives in the library, not the sketch: the Arduino builder puts auto-generated prototypes above sketch-level type declarations, so a sketch enum used as a return type does not compile.

## Components
- `Embedded/libraries/koi_sensing/src/store_forward.h::SpillStorage` - NVS blob interface (memory in tests)
- `store_forward.h::SpillMeta`, `spillLoad`, `spillAppend` - NVS ring header and append
- `store_forward.h::bufferPush` - RTC ring with spill on overflow
- `store_forward.h::UploadBatch`, `batchPlan`, `batchCommit` - oldest-first batches by row count
- `store_forward.h::batchOutcome` - stored / retry / refused
- `Embedded/libraries/koi_sensing/src/sample_cycle.h::PENDING_CYCLE_CAPACITY` - 4 to 24
- `Embedded/sensor_node/sensor_node.ino::NvsSpillStorage` - Preferences namespace `koiq`
- `sensor_node.ino::uploadPending`, `uploadBatch`, `bufferCycle` - drain loop and logging
- `Embedded/tests/test_all.cpp::test_store_forward` - host tests

## Breaks or needs follow-up
- A permanently refused batch now drops only that batch, then stops for the wake; before, the whole (at most 4-cycle) queue was dropped. With a revoked token the backlog therefore drains away one batch per wake rather than at once. No follow-up needed unless the owner prefers the old behaviour.
- Cycles buffered before the clock was ever set still go without `sampled_at` and are timed by receipt (the #17 rule, unchanged); a long outage straight after a cold boot gives such rows a receipt time hours late. Follow-up, if wanted: work the time back across deep sleep from the RTC timer once SNTP succeeds.

## Problem and constraints
RTC memory survives deep sleep but not power loss; the old queue held 4 cycles (`PENDING_CYCLE_CAPACITY` was 4), so a 30-minute access-point outage at the backoff sleeps (`sleep_backoff.h::wifiFailSleepSeconds`: 300, 900, 1800 s) fitted, but anything longer than 4 cycles lost data. The server limits batches to 32 rows and 6 accepted calls per 60 s (`0028_battery_low.sql` `c_max_rows`, `c_window_batches`) and rejects `sampled_at` older than 30 days (`c_max_age`).

## Alternatives considered
- Keep the old drop-whole-queue rule on a permanent refusal - with up to 48 cycles that throws away far more than the refused batch (a 413 on one batch would empty the backlog).
- Read the spill header once per cold boot and keep it in RTC - saves one NVS read per wake but leaves two copies to keep in step; one read per wake was chosen.

## Trade-offs
- Up to 24 cycles in RTC are still lost on a power cut during an outage. Matters if outages and power cuts coincide often.
- A drain of the full 48-cycle backlog takes several wakes (16 cycles of two readings or 8 of four per batch, two batches a wake).
- Each wake does one NVS read of the header even with nothing spilled.

## Key parameters
- `sample_cycle.h::PENDING_CYCLE_CAPACITY` = 24: the issue's minimum. 80 bytes a cycle; `pending` is 1928 bytes in the built ELF of the 8 KB RTC slow memory. Raising it costs RTC memory (a `static_assert` caps `PendingCycles` at 2048 bytes).
- `store_forward.h::SPILL_CAPACITY` = 24: matches the RTC ring, about 48 cycles in total; at the 1800 s backoff sleep that is roughly a day of outage. Raising it uses more of the shared NVS partition.
- `store_forward.h::INGEST_MAX_ROWS` = 32: mirrors `c_max_rows`; must not exceed it.
- `store_forward.h::UPLOAD_MAX_CYCLES` = 16: 32 rows / at least 2 readings a cycle.
- `sensor_node.ino::UPLOAD_BATCHES_PER_WAKE` = 2: keeps under 6 calls a minute assuming about two wakes a minute; raising it drains faster and risks 429s (kept and retried, not lost).

## Assumptions
- Every cycle has at least TDS and pH readings (`readCycle` always adds both); if not, a batch may hold fewer rows than it could, nothing breaks.
- The default NVS partition has room for 25 more blobs (about 2 KB of data plus NVS overhead). If not, `putBytes` fails and the node falls back to dropping the oldest cycle, as before #44.
- A wake takes roughly 15 s active plus the 15 s sleep (`sensor_node.ino::SLEEP_S`); not measured.

## Edge cases
- Handled: RTC ring wrap; spill full (oldest spilled dropped); power cut between slot and header writes; a missing or wrong-size slot (skipped); a header from another layout (empty spill); an NVS write failure (oldest RTC cycle dropped, logged); a stored batch whose removal failed (sent again, stored once by the unique index).
- Not handled: a power cut during an outage loses the RTC ring (up to 24 cycles); the owner sees a gap.
- Not handled: a backlog older than 30 days is rejected row by row as `sampled_at_too_old`.

## Not verified
- Anything on the board: NVS writes and reads through `Preferences`, the namespace `koiq` behaviour on first boot, NVS space, wake duration, and the owner's 30-minute access-point test.
- That two batches a wake stay under the server rate limit in practice.

## Changes outside the scope
The issue puts the ring buffer in `koi_sensing` with host tests, so `Embedded/libraries/koi_sensing/src/store_forward.h` (new), `koi_sensing.h` (include), `sample_cycle.h` (capacity and comment) and `Embedded/tests/test_all.cpp` (new tests; the existing queue test's hard-coded `5` became `PENDING_CYCLE_CAPACITY + 1`) are outside `Embedded/sensor_node/**`. This record is under `docs/decisions/`.

## How it was verified
- `python tools/check.py firmware`: host tests 177 passed before, 296 after (`test_store_forward`: wrap, spill, row-limited batches, spill full, power-loss restart, power cut between writes in both append paths, NVS write failure, missing slot, foreign header, refused batch, outcome rule); compiles of `sensor_node`, `sensor_node -DKOI_SERVICE_MODE` and `camera_node` passed.
- `xtensa-esp32-elf-nm` on the built ELF: `pending` is 0x788 (1928) bytes; `.rtc.data` is 1956 bytes.
- `python tools/check.py all` at the end.

## To change this
- To keep every unsent cycle through power cuts, spill on every failed wake instead of on overflow; cost: a flash write per failed wake.
- To drain faster, raise `UPLOAD_BATCHES_PER_WAKE` from 2 to 3; cost: likely 429s at a 30 s wake period.
- To send the backlog in one request, raise `c_max_rows` in a new migration and `INGEST_MAX_ROWS` with it; cost: bigger requests and JSON documents on the node.
- Likely bug sources: changing `PendingCycle` (old spill discarded on first wake), and NVS write order in `spillAppend`.

## Rollback
Nothing on the server depends on this. Reverting restores the 4-cycle RTC queue; the `koiq` NVS namespace would stay in flash unused until the NVS partition is erased.

<!-- verified-facts:begin (generated by integrity record; do not edit) -->
## Verified facts

Generated 2026-10-12 14:07 UTC for issue #44 attempt 2; compared HEAD..working tree (base 12a7a6d). Records are claims to check against the code, not proof.

**Change summary**

- `Embedded/libraries/`: 3 file(s), 0 component(s), +200/-2 lines
- `Embedded/sensor_node/`: 1 file(s), 0 component(s), +85/-23 lines
- plus 1 test file(s)
- new files: `Embedded/libraries/koi_sensing/src/store_forward.h`

**Removed symbols**

None.

**Scope**

Declared: `Embedded/sensor_node/**`. Verdict: violation.
- out of scope: `Embedded/libraries/koi_sensing/src/koi_sensing.h` (modified); explained in the record: yes
- out of scope: `Embedded/libraries/koi_sensing/src/sample_cycle.h` (modified); explained in the record: yes
- out of scope: `Embedded/tests/test_all.cpp` (modified); explained in the record: yes

**Supervisor checks passed**

whitespace/conflict-marker check, apply pending migrations to the local Supabase stack, OutdoorKoi host test suites (layout-aware), scope check, dangling reference check

**Evidence**

13 checked by the tool, 1 tested in this run, 19 claimed only (of 33 claims). [checked] = the named code exists and changed, or the cited value matches; [tested] = the cited test exists and the test gate passed in this run; [claimed] = not verified.
- Components: 8 checked, 0 tested, 1 claimed
- Key parameters: 5 checked, 0 tested, 0 claimed
- Edge cases: 0 checked, 0 tested, 1 claimed
- How it was verified: 0 checked, 1 tested, 2 claimed
- Traps: 0 checked, 0 tested, 5 claimed
- Assumptions: 0 checked, 0 tested, 3 claimed
- Trade-offs: 0 checked, 0 tested, 3 claimed
- Not verified: 0 checked, 0 tested, 2 claimed
- Breaks or needs follow-up: 0 checked, 0 tested, 2 claimed
  - [checked] Components: `Embedded/libraries/koi_sensing/src/store_forward.h::SpillStorage` - NVS blob interface... (named code exists and changed)
  - [checked] Key parameters: `sample_cycle.h::PENDING_CYCLE_CAPACITY` = 24: the issue's minimum. 80 bytes a cycle; `... (value matches the code: PENDING_CYCLE_CAPACITY = 24)
  - [tested] How it was verified: `python tools/check.py firmware`: host tests 177 passed before, 296 after (`test_store_... (test gate passed: OutdoorKoi host test suites (layout-aware))
  - [checked] Components: `store_forward.h::SpillMeta`, `spillLoad`, `spillAppend` - NVS ring header and append (named code exists and changed)
  - [checked] Key parameters: `store_forward.h::SPILL_CAPACITY` = 24: matches the RTC ring, about 48 cycles in total;... (value matches the code: SPILL_CAPACITY = 24)
  - [checked] Components: `store_forward.h::bufferPush` - RTC ring with spill on overflow (named code exists and changed)
  - [checked] Key parameters: `store_forward.h::INGEST_MAX_ROWS` = 32: mirrors `c_max_rows`; must not exceed it. (value matches the code: INGEST_MAX_ROWS = 32)
  - [checked] Components: `store_forward.h::UploadBatch`, `batchPlan`, `batchCommit` - oldest-first batches by ro... (named code exists and changed)
  - [checked] Key parameters: `store_forward.h::UPLOAD_MAX_CYCLES` = 16: 32 rows / at least 2 readings a cycle. (value matches the code: UPLOAD_MAX_CYCLES = 16)
  - [checked] Components: `store_forward.h::batchOutcome` - stored / retry / refused (named code exists and changed)
  - full per-claim list (33) in the JSON record

**Consistency**

0 error(s), 0 warning(s) between the rationale above and the change.

**Commit**

This record is committed with the change it describes; `git log -1 -- docs/decisions/0044-store-and-forward.md` gives the commit.
<!-- verified-facts:end -->
