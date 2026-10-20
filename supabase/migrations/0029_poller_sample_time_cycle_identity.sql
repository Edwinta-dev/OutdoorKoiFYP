-- Issue #82: discovery still advances on created_at, while replay receives
-- true sample time and the complete v2 device-cycle identity.
alter table public.sensor_ingest_ledger
    drop constraint if exists sensor_ingest_ledger_disposition_check;
alter table public.sensor_ingest_ledger
    add constraint sensor_ingest_ledger_disposition_check
    check (disposition in ('applied', 'superseded', 'unusable', 'rebuild_required'));

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

create or replace function public.sensor_ingest_history(
    p_pond_id bigint,
    p_sensor_types text[],
    p_after timestamptz,
    p_until timestamptz
)
returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
    select coalesce(jsonb_agg(jsonb_build_object(
               'id', d.id, 'sensor_type', d.sensor_type, 'value', d.data1,
               'created_at', d.created_at, 'sampled_at', d.sampled_at, 'flags', d.flags,
               'device_id', d.device_id, 'boot_id', d.boot_id, 'cycle_seq', d.cycle_seq)
           order by l.effective_sample_time, d.id), '[]'::jsonb)
    from public.sensor_ingest_ledger l
    join public."SensorData" d on d.id = l.sensor_row_id and d."userID" = p_pond_id
    where l.pond_id = p_pond_id
      and d.sensor_type = any(p_sensor_types)
      and (p_after is null or l.effective_sample_time > p_after)
      and l.effective_sample_time <= p_until;
$$;

revoke all on function public.sensor_ingest_pending(bigint, text[], timestamptz, timestamptz, integer, integer)
    from public, anon, authenticated;
revoke all on function public.sensor_ingest_history(bigint, text[], timestamptz, timestamptz)
    from public, anon, authenticated;
grant execute on function public.sensor_ingest_pending(bigint, text[], timestamptz, timestamptz, integer, integer)
    to service_role;
grant execute on function public.sensor_ingest_history(bigint, text[], timestamptz, timestamptz)
    to service_role;
