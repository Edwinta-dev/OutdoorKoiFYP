-- 0030_sensor_ingest_pending_bound.sql
--
-- Issue #120. 0029 dropped the scan's lower bound, so every SensorData row
-- not in sensor_ingest_ledger was pending however old (0014 never
-- backfilled the ledger), oldest first under p_limit. Restores 0027's
-- bound: created_at >= cursor watermark - overlap, or p_start before the
-- first cursor. 0029's sample-time and cycle-identity columns are kept;
-- signature, security and grants are unchanged.

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
        select c.watermark from public.sensor_ingest_cursor c where c.pond_id = p_pond_id
    ),
    bound as (
        select coalesce((select cur.watermark - make_interval(secs => p_overlap_seconds) from cur), p_start)
               as scan_from
    ),
    pending as (
        select d.id, d.sensor_type, d.data1, d.created_at, d.sampled_at, d.flags,
               d.device_id, d.boot_id, d.cycle_seq
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
                       'id', p.id, 'sensor_type', p.sensor_type, 'value', p.data1,
                       'created_at', p.created_at, 'sampled_at', p.sampled_at, 'flags', p.flags,
                       'device_id', p.device_id, 'boot_id', p.boot_id, 'cycle_seq', p.cycle_seq)
                   order by p.created_at, p.id)
            from pending p), '[]'::jsonb));
$$;

revoke all on function public.sensor_ingest_pending(bigint, text[], timestamptz, timestamptz, integer, integer)
    from public, anon, authenticated;
grant execute on function public.sensor_ingest_pending(bigint, text[], timestamptz, timestamptz, integer, integer)
    to service_role;
