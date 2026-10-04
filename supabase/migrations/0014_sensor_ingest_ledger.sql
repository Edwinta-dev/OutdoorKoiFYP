-- 0014_sensor_ingest_ledger.sql
--
-- Lossless sensor ingestion for the poller (issue #18).
--
-- Until now the poller read get_bundled_dashboard_payload's raw_sensor,
-- the newest value of each sensor_type, every cycle. Readings stored
-- between two polls were never seen, and with no new rows the same
-- reading was fed to the model again. The poller now reads every
-- SensorData row of a pond once, through the objects below.
--
-- Discovery: ledger plus overlapping scan window
--   SensorData ids come from a sequence, so a row can be committed after
--   a row with a higher id (two uploads in flight at once), and
--   created_at is now() at the start of the inserting transaction, so it
--   can also be older than rows already committed. A cursor on id, or on
--   (created_at, id), would skip such a row for good. Instead:
--     * sensor_ingest_cursor.watermark is the newest created_at the
--       poller has ingested for the pond (ingestion progress, not model
--       time);
--     * each cycle scans the rows with created_at at or after
--       watermark - overlap, and leaves out every row already in
--       sensor_ingest_ledger. A row committed late is found as long as
--       it committed within the overlap of its own created_at. The node
--       inserts through PostgREST with the anon role, whose statement
--       timeout is seconds; the worker passes an overlap of an hour by
--       default (KOI_SENSOR_INGEST_OVERLAP_MINUTES).
--   The order the model applies readings in, (effective sample time, id),
--   is decided after discovery, in koi/models/sensor_inputs.py, and is
--   never used as the scan position.
--
-- Atomic progress: save_pond_snapshot_with_ingest saves the engine
--   snapshot (compare and set, as save_pond_snapshot), records each
--   ingested row in the ledger and moves the watermark, in one
--   transaction. A worker that stops between discovery and the save
--   leaves neither, so the next cycle finds the same rows and applies
--   them to the same stored snapshot. A row already in the ledger makes
--   the whole save fail rather than apply it twice.
--
-- sensor_ingest_cursor
--   pond_id     UserData."userID" (no foreign key, as pond_chemistry_state)
--   watermark   newest SensorData.created_at ingested for the pond
--   updated_at  when the watermark last moved
--
-- sensor_ingest_ledger, one row per SensorData row ingested
--   pond_id                SensorData."userID"
--   sensor_row_id          SensorData.id (no foreign key: SensorData is
--                          left exactly as it is)
--   sensor_type            SensorData.sensor_type as stored
--   effective_sample_time  the time the model used for the reading:
--                          created_at until the node sends a sample time
--                          (issue #17)
--   time_basis             'ingestion' when effective_sample_time is the
--                          database insert time, 'sample' when it is the
--                          node's own sample time
--   disposition            'applied'    passed to the model
--                          'superseded' another row of the same channel
--                                       at the same time, with a higher
--                                       id, was applied instead
--                          'unusable'   the value was not a finite
--                                       number; the channel was passed
--                                       as missing
--   snapshot_version       pond_chemistry_state.snapshot_version of the
--                          save that ingested it
--   ingested_at            when the save ran
--
-- sensor_ingest_pending(p_pond_id, p_sensor_types, p_start, p_until,
--                       p_overlap_seconds, p_limit) returns jsonb
--   {cursor: watermark or null, scan_from: timestamptz or null,
--    rows: [{id, sensor_type, value, created_at}]}: the pond's SensorData
--   rows of the given sensor types with created_at in
--   [scan_from, p_until] (p_until null: no upper bound) and not in the
--   ledger, ordered by created_at, id, at most p_limit. scan_from is
--   watermark - p_overlap_seconds when the pond has a cursor, otherwise
--   p_start (null: every row). value is data1 as stored. Read only.
--
-- save_pond_snapshot_with_ingest(p_user_id, p_snapshot, p_base_version,
--                                p_ingest) returns bigint
--   save_pond_snapshot's result (the new version, or null when
--   p_base_version is stale, in which case nothing is written). p_ingest
--   is {rows: [{sensor_row_id, sensor_type, effective_sample_time,
--   time_basis, disposition}], watermark: timestamptz or null}. The
--   watermark only moves forward.
--
-- Access: both tables have RLS on and no policies; both tables and both
-- functions are revoked from public, anon and authenticated and granted
-- to service_role (the backend) only. SensorData's columns, grants,
-- policies, triggers and indexes are unchanged; the scan uses
-- idx_sensordata_user_type_latest (0013) through its
-- ("userID", sensor_type, created_at) prefix.

create table if not exists public.sensor_ingest_cursor (
    pond_id    bigint primary key,
    watermark  timestamptz not null,
    updated_at timestamptz not null default now()
);

create table if not exists public.sensor_ingest_ledger (
    pond_id               bigint not null,
    sensor_row_id         bigint not null,
    sensor_type           text,
    effective_sample_time timestamptz not null,
    time_basis            text not null check (time_basis in ('ingestion', 'sample')),
    disposition           text not null check (disposition in ('applied', 'superseded', 'unusable')),
    snapshot_version      bigint not null,
    ingested_at           timestamptz not null default now(),
    primary key (pond_id, sensor_row_id)
);

-- No policies: only the backend's secret key (which bypasses RLS) reads or writes them.
alter table public.sensor_ingest_cursor enable row level security;
alter table public.sensor_ingest_ledger enable row level security;

revoke all on table public.sensor_ingest_cursor from public, anon, authenticated;
revoke all on table public.sensor_ingest_ledger from public, anon, authenticated;
grant select, insert, update, delete on table public.sensor_ingest_cursor to service_role;
grant select, insert, update, delete on table public.sensor_ingest_ledger to service_role;

create or replace function public.sensor_ingest_pending(
    p_pond_id bigint,
    p_sensor_types text[],
    p_start timestamptz,
    p_until timestamptz,
    p_overlap_seconds integer,
    p_limit integer
)
returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
    with cur as (
        select c.watermark
        from public.sensor_ingest_cursor c
        where c.pond_id = p_pond_id
    ),
    bound as (
        select coalesce((select cur.watermark - make_interval(secs => p_overlap_seconds) from cur), p_start)
               as scan_from
    ),
    pending as (
        select d.id, d.sensor_type, d.data1, d.created_at
        from public."SensorData" d, bound b
        where d."userID" = p_pond_id
          and d.sensor_type = any(p_sensor_types)
          and (b.scan_from is null or d.created_at >= b.scan_from)
          and (p_until is null or d.created_at <= p_until)
          and not exists (
              select 1 from public.sensor_ingest_ledger l
              where l.pond_id = p_pond_id and l.sensor_row_id = d.id)
        order by d.created_at, d.id
        limit p_limit
    )
    select jsonb_build_object(
        'cursor', (select cur.watermark from cur),
        'scan_from', (select b.scan_from from bound b),
        'rows', coalesce((
            select jsonb_agg(jsonb_build_object(
                       'id', p.id, 'sensor_type', p.sensor_type, 'value', p.data1, 'created_at', p.created_at)
                   order by p.created_at, p.id)
            from pending p), '[]'::jsonb));
$$;

create or replace function public.save_pond_snapshot_with_ingest(
    p_user_id bigint,
    p_snapshot jsonb,
    p_base_version bigint,
    p_ingest jsonb
)
returns bigint
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_version bigint;
begin
    v_version := public.save_pond_snapshot(p_user_id, p_snapshot, p_base_version);
    if v_version is null then
        return null;
    end if;

    -- A row already in the ledger raises unique_violation and rolls the
    -- snapshot save back with it.
    insert into public.sensor_ingest_ledger
        (pond_id, sensor_row_id, sensor_type, effective_sample_time, time_basis, disposition, snapshot_version)
    select p_user_id, (e ->> 'sensor_row_id')::bigint, e ->> 'sensor_type',
           (e ->> 'effective_sample_time')::timestamptz, e ->> 'time_basis', e ->> 'disposition', v_version
    from jsonb_array_elements(coalesce(p_ingest -> 'rows', '[]'::jsonb)) e;

    if p_ingest ->> 'watermark' is not null then
        insert into public.sensor_ingest_cursor (pond_id, watermark, updated_at)
        values (p_user_id, (p_ingest ->> 'watermark')::timestamptz, now())
        on conflict (pond_id) do update
            set watermark = greatest(sensor_ingest_cursor.watermark, excluded.watermark),
                updated_at = excluded.updated_at;
    end if;
    return v_version;
end;
$$;

revoke all on function public.sensor_ingest_pending(bigint, text[], timestamptz, timestamptz, integer, integer)
    from public, anon, authenticated;
revoke all on function public.save_pond_snapshot_with_ingest(bigint, jsonb, bigint, jsonb)
    from public, anon, authenticated;
grant execute on function public.sensor_ingest_pending(bigint, text[], timestamptz, timestamptz, integer, integer)
    to service_role;
grant execute on function public.save_pond_snapshot_with_ingest(bigint, jsonb, bigint, jsonb) to service_role;
