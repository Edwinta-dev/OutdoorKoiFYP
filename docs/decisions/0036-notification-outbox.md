# 0036: Notification outbox
Status: implemented · Issue: #36 · Date: 2026-10-10

## In short

The worker now writes a row to a new `notification_outbox` table when a pond's assessment turns red, when a lead-time ladder action appears, when the sensor node has been silent for three expected intervals, and when the newest camera frame reports an obstruction. Each row has a dedupe key, and each kind has a cool-down, so a flapping status does not repeat. A sender interface exists with a no-op sender and an FCM stub; nothing is delivered yet.

## Problem and constraints

The owner only learns about a red status, a weather action or a dead node by opening the app. Push delivery is a later issue, so this one only had to decide what to send and record it durably. Constraints: schema changes only through a new migration (`supabase/migrations/0024_notification_outbox.sql`), both storage adapters must behave the same, the camera's `POST /upload` contract must not change, and a failure in this path must not stop a poll.

## Approach

- **Table.** `notification_outbox` (id, pond, kind, severity, title, body, data jsonb, created_at, dedupe_key, sent_at, error), backend only. Kind and severity are checked lists, and `data` must be a JSON object.
- **Storage interface.** `Backend/koi/storage/base.py::Storage.enqueue_notification`, `Backend/koi/storage/base.py::Storage.fetch_notifications` and `Backend/koi/storage/base.py::Storage.record_notification_result` are the three outbox calls; `Backend/koi/storage/base.py::NOTIFICATION_COLUMNS` lists the table's columns for both adapters.
- **Write path.** The database function `enqueue_notification` inserts a row unless the pond already has one with the same `dedupe_key` created within the cool-down before the new row's `created_at`. The check and the insert happen together under a transaction advisory lock keyed on pond and key. `Backend/koi/storage/memory.py::MemoryStorage.enqueue_notification` does the same thing under the store's lock, with the same column checks (`MemoryStorage._notification_problem`, using the class attributes `MemoryStorage.NOTIFICATION_KINDS` and `MemoryStorage.NOTIFICATION_SEVERITIES`). `Backend/koi/storage/supabase_storage.py::SupabaseStorage` calls the RPC and reads and updates the table through PostgREST.
- **Triggers.** These are pure functions in `Backend/koi/notifications.py`:
  - `status_red`: Red now, and the previous stored assessment was not Red or did not exist.
  - `ladder_actions`: every ladder action that has a lead time.
  - `node_silent`: `device_health.device_status` returns `silent`.
  - `camera_obstructed`: the newest frame's `current_state` is `obstruction` and the frame is at most 24 h old.
- **Keys and cool-downs.**
  - `status_red:<domain>`: 12 h.
  - `ladder_action:<rule>:<Singapore date>`: 24 h, which means at most once per rule per day.
  - `node_silent:sensor`: 24 h.
  - `camera_obstructed:camera`: 24 h.
- **Worker.** `Backend/koi/worker/poller.py::_poll_user` reads the latest stored assessment of each domain just before it pushes the cycle's rows (`notifications.previous_assessments`). After the pushes it calls `notifications.notify_cycle`, which works out the ladder from retained weather as of the cycle time, reads device activity and the frame rows already fetched, and writes entries. `notify_cycle` never raises.
- **Senders.** `Sender` protocol, `NoopSender` (`delivers = False`, so `deliver_pending` leaves rows waiting), `FcmSender` (raises `SendError`, which is recorded in `error`), `build_sender` and `deliver_pending`. None of these is wired into a scheduler yet.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Unique index on (pond, dedupe_key) with occurrence-specific keys | A key per occurrence (for example including the transition time) does not stop flapping, and a fixed key would never fire again. |
| Cool-down per (pond, kind) | A red chemistry status would suppress a red algae status for 12 h. |
| Re-notify every cycle while red | The issue asks for a change to red, and that would repeat every 15 minutes. |
| Call `koi/api/routes.py::_lead_time_actions` from the worker | It needs the Flask app context, so the 15 lines were duplicated in `notifications.lead_time_actions`. |

## Trade-offs

- A ladder rule notifies at most once per Singapore day. A second rain nowcast that afternoon is not sent.
- A pond that stays red is not reminded. Only a new change to red after 12 h notifies again.
- The ladder inputs are duplicated in two places, so a change to one must be copied to the other.

## Key parameters

| Parameter | Value | How chosen | Effect of raising / lowering |
|---|---|---|---|
| `notifications.COOLDOWNS[status_red]` | 12 h | Picked in this change, not tuned on data; limits a flapping status to two per day per domain | Higher: fewer repeats, a genuine second red episode the same day is missed. Lower: more repeats from flapping. |
| `notifications.COOLDOWNS[ladder_action]` | 24 h | Matches the per-day key | Lower than 24 h has no effect while the key includes the date. |
| `notifications.COOLDOWNS[node_silent]` | 24 h | Picked in this change: one reminder a day for a dead node | Lower: hourly-style nagging while the node stays down. |
| `notifications.COOLDOWNS[camera_obstructed]` | 24 h | Same reasoning as node_silent | As above. |
| `notifications.OBSTRUCTION_MAX_AGE` | 24 h | Picked in this change: a frame older than a day says little about the view now | Higher: a stale obstruction frame can still notify. Lower: a camera that uploads rarely never notifies. |
| Silent threshold | 3 expected intervals | From the issue; reuses `device_health.SILENT_AFTER_INTERVALS` | Not changed here. |
| Severity per kind | status_red critical; the other three warning | Red is the only state that needs action now | Only affects how a later sender presents it. |
| Text limits | title 1-200, body 1-2000, dedupe_key 1-200 characters | Generous bounds for push text | Enforced in the migration and in `MemoryStorage`. |
| `fetch_notifications` limit | 100 rows; `deliver_pending` 50 | Bounded reads per call | Only matters once delivery is scheduled. |

## Assumptions

- The previous stored assessment is the one from the last cycle. If its read fails, the change counts as "from unknown" and may notify; the cool-down limits that to once in 12 h.
- `imageTable.current_state == "obstruction"` is what the camera writes for a blocked view (`koi/camera/hsvEngine.py`).

## Edge cases

- A failing outbox write, sources read or device read is logged and skipped. The poll still completes (tested).
- A silent camera device does not notify. Only the sensor node is checked.
- Rows from `koi.dev` replays are written with the replay's time, because `created_at` is the cycle's `now`.
- Rows still waiting are never retried after an `error` is recorded. Not handled.

## Changes outside the scope

- `Backend/koi/storage/memory.py::TABLE_COLUMNS`, `Backend/koi/storage/memory.py::USER_COLUMN` and `Backend/koi/storage/memory.py::_STAMPED`: one line each registering `notification_outbox`. `MemoryStorage` builds its tables from `TABLE_COLUMNS` and its `add_rows`/`_select` helpers look up the pond column and timestamp column in the other two, so without these lines the in-memory adapter has no outbox table. Every other table is registered the same way.
- The previous attempt's module-level checks in `memory.py` and the added section of the `poller.py` module docstring were moved into `MemoryStorage` and removed, respectively, to stay inside the scope.
- New tests: `Backend/tests/worker/test_notification_outbox.py`, `Backend/tests/storage/test_outbox_storage.py`, `Backend/tests/sql/test_sql_outbox.py`.
- `supabase/README.md` has no row for 0024 because it is outside the scope (it also lacks a row for 0023).

## How it was verified

- `python -m pytest -q -k outbox`: 40 passed. That covers every trigger, dedupe and cool-down in memory and SQL, the senders, the poller writing node_silent, obstruction and ladder entries, and the poller surviving a failing outbox.
- The SQL tests ran against the local stack after `supabase db reset`.
- `python tools/check.py all`: every step passed (backend pytest 1759 passed, ruff, mypy, flutter), apart from the three ESP32 compile steps, which were skipped because the esp32 core is not installed.
- Not verified: real FCM delivery, and behaviour against the live database.

## To change this

- Cool-downs: `notifications.COOLDOWNS`.
- Keys: each trigger function.
- Obstruction age: `OBSTRUCTION_MAX_AGE`.
- New kinds also need the `kind` check in a new migration and `MemoryStorage.NOTIFICATION_KINDS` in `memory.py`.
- Likely source of bugs: `lead_time_actions` drifting from `routes._lead_time_actions`.

## Rollback

Nothing reads the outbox yet. If this is reverted, remove the two calls in `_poll_user` and the module. The table can stay, or be dropped by a new migration.

<!-- verified-facts:begin (generated by integrity record; do not edit) -->
## Verified facts

Generated 2026-10-10 08:58 UTC for issue #36 attempt 4; compared HEAD..working tree (base 3f1c005).

**Changed components**

| Component | Change | Lines +/- | Tags | Description |
|---|---|---|---|---|
| `Backend/koi/notifications.py` | new file | +315/-0 |  |  |
| `Backend/koi/notifications.py::_entry` | added | +4/-0 |  |  |
| `Backend/koi/notifications.py::status_red` | added | +13/-0 |  | An entry when the domain's assessment is Red now and its previous |
| `Backend/koi/notifications.py::ladder_actions` | added | +13/-0 |  | One entry per ladder action that has a lead time. |
| `Backend/koi/notifications.py::node_silent` | added | +25/-0 |  | An entry when the sensor node is silent (device_health.device_status) |
| `Backend/koi/notifications.py::camera_obstructed` | added | +15/-0 |  | An entry when the newest stored frame reports an obstruction and is |
| `Backend/koi/notifications.py::lead_time_actions` | added | +15/-0 |  | ladder.actions evaluated on the retained weather visible at now, |
| `Backend/koi/notifications.py::previous_assessments` | added | +8/-0 |  | The pond's latest stored assessment of each domain, read before the |
| `Backend/koi/notifications.py::enqueue` | added | +12/-0 |  | Writes each entry with its kind's cool-down; returns the rows |
| `Backend/koi/notifications.py::notify_cycle` | added | +21/-0 |  | Every trigger for one pond after a poll cycle, written to the |
| `Backend/koi/notifications.py::SendError` | added | +3/-0 |  | A sender could not deliver one entry; the message is stored in the |
| `Backend/koi/notifications.py::Sender` | added | +11/-0 |  | Delivers one outbox row. delivers is False for a sender that does |
| `Backend/koi/notifications.py::Sender.send` | added | +3/-0 |  | Delivers entry (an outbox row) or raises SendError. |
| `Backend/koi/notifications.py::NoopSender` | added | +9/-0 |  | Sends nothing: the default until push notifications exist. |
| `Backend/koi/notifications.py::NoopSender.send` | added | +3/-0 |  |  |
| `Backend/koi/notifications.py::FcmSender` | added | +14/-0 |  | Firebase Cloud Messaging. A stub: the push notification issue adds |
| `Backend/koi/notifications.py::FcmSender.__init__` | added | +3/-0 |  |  |
| `Backend/koi/notifications.py::FcmSender.send` | added | +2/-0 |  |  |
| `Backend/koi/notifications.py::build_sender` | added | +7/-0 |  | The sender called name: "noop" or "fcm". |
| `Backend/koi/notifications.py::deliver_pending` | added | +20/-0 |  | Hands the pond's waiting rows (no sent_at, no error), oldest first, |
| `Backend/koi/notifications.py::log` | added | +1/-0 |  |  |
| `Backend/koi/notifications.py::STATUS_RED` | added | +1/-0 |  |  |
| `Backend/koi/notifications.py::LADDER_ACTION` | added | +1/-0 |  |  |
| `Backend/koi/notifications.py::NODE_SILENT` | added | +1/-0 |  |  |
| `Backend/koi/notifications.py::CAMERA_OBSTRUCTED` | added | +1/-0 |  |  |
| `Backend/koi/notifications.py::KINDS` | added | +1/-0 |  |  |
| `Backend/koi/notifications.py::COOLDOWNS` | added | +6/-0 |  |  |
| `Backend/koi/notifications.py::OBSTRUCTION_MAX_AGE` | added | +1/-0 |  |  |
| `Backend/koi/notifications.py::DOMAIN_TITLES` | added | +1/-0 |  |  |
| `Backend/koi/notifications.py::RED` | added | +1/-0 |  |  |
| `Backend/koi/storage/base.py::Storage.enqueue_notification` | added | +8/-0 |  | Stores entry ({kind, severity, title, body, data, dedupe_key, |
| `Backend/koi/storage/base.py::Storage.fetch_notifications` | added | +4/-0 |  | The pond's outbox rows, oldest created_at first (then id); with |
| `Backend/koi/storage/base.py::Storage.record_notification_result` | added | +4/-0 |  | Sets a row's sent_at (delivered) or error (not delivered). |
| `Backend/koi/storage/base.py::NOTIFICATION_COLUMNS` | added | +2/-0 |  |  |
| `Backend/koi/storage/base.py::<module>` | added | +2/-0 |  |  |
| `Backend/koi/storage/memory.py::MemoryStorage` | modified | +8/-0 |  |  |
| `Backend/koi/storage/memory.py::MemoryStorage._notification_problem` | added | +18/-0 |  |  |
| `Backend/koi/storage/memory.py::MemoryStorage.enqueue_notification` | added | +18/-0 |  | The same as enqueue_notification (migration 0024), including its |
| `Backend/koi/storage/memory.py::MemoryStorage.fetch_notifications` | added | +7/-0 |  |  |
| `Backend/koi/storage/memory.py::MemoryStorage.record_notification_result` | added | +8/-0 |  |  |
| `Backend/koi/storage/memory.py::TABLE_COLUMNS` | modified | +2/-0 |  |  |
| `Backend/koi/storage/memory.py::USER_COLUMN` | modified | +1/-0 |  |  |
| `Backend/koi/storage/memory.py::_STAMPED` | modified | +1/-0 |  |  |
| `Backend/koi/storage/supabase_storage.py::SupabaseStorage.enqueue_notification` | added | +12/-0 |  |  |
| `Backend/koi/storage/supabase_storage.py::SupabaseStorage.fetch_notifications` | added | +7/-0 |  |  |
| `Backend/koi/storage/supabase_storage.py::SupabaseStorage.record_notification_result` | added | +5/-0 |  |  |
| `Backend/koi/worker/poller.py::_poll_user` | modified | +13/-3 |  | Advances one pond to now (default: the current time; koi.dev |
| `supabase/migrations/0024_notification_outbox.sql` | new file | +100/-0 |  |  |

Plus 3 test file(s).

**Removed symbols**

None.

**Scope**

Declared: `supabase/migrations/**`, `Backend/koi/storage/base.py`, `Backend/koi/storage/memory.py::MemoryStorage`, `Backend/koi/storage/supabase_storage.py::SupabaseStorage`, `Backend/koi/worker/poller.py::_poll_user`, `Backend/koi/notifications.py`. Verdict: violation.
- out of scope: `Backend/koi/storage/memory.py::TABLE_COLUMNS` (modified); explained in the record: yes
- out of scope: `Backend/koi/storage/memory.py::USER_COLUMN` (modified); explained in the record: yes
- out of scope: `Backend/koi/storage/memory.py::_STAMPED` (modified); explained in the record: yes

**Supervisor checks passed**

whitespace/conflict-marker check, apply pending migrations to the local Supabase stack, OutdoorKoi host test suites (layout-aware), database suite (migration rehearsal, SQL tests, local profile), scope check, dangling reference check

**Consistency**

0 error(s), 0 warning(s) between the rationale above and the change.

**Commit**

This record is committed with the change it describes; `git log -1 -- docs/decisions/0036-notification-outbox.md` gives the commit.
<!-- verified-facts:end -->
