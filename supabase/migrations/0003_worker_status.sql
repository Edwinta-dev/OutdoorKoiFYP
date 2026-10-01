-- 0003_worker_status.sql
--
-- The poller worker's report on its last cycle (issue #10). The worker runs
-- as its own process, so the API's GET /ready and GET /metrics read this
-- row to report when the last successful poll was, how long the cycle
-- took, which ponds failed and how old each pond's newest sensor reading
-- was.
--
--   name               the lease the worker polls under ('poller')
--   holder             the worker that wrote the row (worker_lease.holder)
--   cycle_started_at   when the last cycle started
--   cycle_finished_at  when it finished
--   cycle_duration_sec its length in seconds
--   last_success_at    the end of the newest cycle that advanced every pond
--                      it could; null until the first one
--   ponds              {"<user id>": {"result": "ok" | "skipped" | "failed",
--                       "failures_total": <failures since the worker
--                       started>, "sensor_recorded_at": <raw_sensor
--                       recorded_at or null>, "reason": <short text or
--                       null>}}
--
-- A new table only; nothing existing changes. Only the backend's secret
-- key reads or writes it.

create table if not exists public.worker_status (
    name               text primary key,
    holder             text not null,
    cycle_started_at   timestamptz not null,
    cycle_finished_at  timestamptz not null,
    cycle_duration_sec double precision not null default 0,
    last_success_at    timestamptz,
    ponds              jsonb not null default '{}'::jsonb
);

-- No policies: only the backend's secret key (which bypasses RLS) reads or writes it.
alter table public.worker_status enable row level security;
