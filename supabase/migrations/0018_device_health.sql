-- 0018_device_health.sql
--
-- Device freshness and health tracking (issue #23).
--
-- Each pond has up to two devices, the sensor node and the camera. The
-- backend records every contact it receives from either (the poller for
-- each sensor upload it ingests, the camera service for each stored
-- frame) and reads them back for GET /v1/ponds/{pond}/devices and for
-- the confidence carried by each assessment (koi/models/device_health.py).
--
-- Two times are kept apart:
--   * devices.last_seen_at is receipt time: the newest
--     SensorData.created_at the poller has ingested, or the time the
--     camera service stored a frame.
--   * Channel freshness (the last usable sample time per sensor channel)
--     comes from the engine snapshot, not from here. A node that
--     reconnects with old buffered samples is recently seen but its
--     channels can still be stale.
--
-- devices, one row per pond and device kind
--   pond_id                    UserData."userID" (no foreign key, as
--                              sensor_ingest_cursor)
--   kind                       'sensor' or 'camera'
--   expected_interval_seconds  the cadence configured for the device;
--                              null means the backend default (sensor:
--                              KOI_SENSOR_CADENCE_MINUTES; camera: the
--                              wake it was last given). Set by the owner
--                              or by the interval-change issue (#43);
--                              nothing here changes it.
--   last_seen_at               newest receipt time; only moves forward
--   next_expected_at           when the newest contact said the device
--                              would next report
--   created_at, updated_at
--
-- device_contact, one row per contact received
--   pond_id, kind              as devices
--   received_at                receipt time of the contact
--   expected_next_at           when the device was expected to report
--                              next, given the interval in force when the
--                              contact was received (the sensor's
--                              configured cadence, or the camera's
--                              reply); null when unknown. A later
--                              interval change does not rewrite earlier
--                              rows, so missed cycles are counted against
--                              the interval that applied at the time.
--   Rows older than 30 days are deleted when the pond's device next
--   reports.
--
-- record_device_contacts(p_pond_id, p_kind, p_contacts jsonb) returns jsonb
--   p_contacts is [{received_at, expected_next_at}]. Adds each contact
--   not already recorded (a repeat is ignored, so a retried poll cycle
--   changes nothing), moves devices.last_seen_at forward to the newest
--   received_at, sets next_expected_at from that newest contact, deletes
--   the device's contacts older than 30 days and returns the devices
--   row. Contacts older than 30 days are not stored.
--
-- pond_device_activity(p_pond_id, p_since) returns jsonb
--   {devices: [devices rows],
--    contacts: [{kind, received_at, expected_next_at}] with received_at at
--              or after p_since, plus each kind's newest contact before
--              it (the start of the first gap), ordered by kind,
--              received_at,
--    battery: [{at, mv}] the pond's SensorData rows of sensor_type
--             'battery_mv' with created_at at or after p_since, oldest
--             first (at is created_at, mv is data1 as stored),
--    reset: {at, code} the pond's newest SensorData row of sensor_type
--           'reset_reason' (code is data1, the ESP32 esp_reset_reason_t
--           value), or null}.
--   Read only.
--
-- 'battery_mv' and 'reset_reason' are new sensor_type spellings the
-- sensor node may send in its batched insert; the poller does not ingest
-- them as model inputs. SensorData's columns, grants, policies, triggers
-- and indexes are unchanged; the two reads use
-- idx_sensordata_user_type_latest (0013).
--
-- Access: both tables have RLS on and no policies; both tables and both
-- functions are revoked from public, anon and authenticated and granted
-- to service_role (the backend) only.

create table if not exists public.devices (
    pond_id                   bigint not null,
    kind                      text not null check (kind in ('sensor', 'camera')),
    expected_interval_seconds integer check (expected_interval_seconds is null or expected_interval_seconds > 0),
    last_seen_at              timestamptz,
    next_expected_at          timestamptz,
    created_at                timestamptz not null default now(),
    updated_at                timestamptz not null default now(),
    primary key (pond_id, kind)
);

create table if not exists public.device_contact (
    pond_id          bigint not null,
    kind             text not null check (kind in ('sensor', 'camera')),
    received_at      timestamptz not null,
    expected_next_at timestamptz,
    primary key (pond_id, kind, received_at),
    check (expected_next_at is null or expected_next_at > received_at)
);

-- No policies: only the backend's secret key (which bypasses RLS) reads or writes them.
alter table public.devices enable row level security;
alter table public.device_contact enable row level security;

revoke all on table public.devices from public, anon, authenticated;
revoke all on table public.device_contact from public, anon, authenticated;
grant select, insert, update, delete on table public.devices to service_role;
grant select, insert, update, delete on table public.device_contact to service_role;

create or replace function public.record_device_contacts(
    p_pond_id bigint,
    p_kind text,
    p_contacts jsonb
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_keep_from timestamptz := now() - interval '30 days';
    v_newest timestamptz;
    v_next timestamptz;
    v_row public.devices;
begin
    if p_kind not in ('sensor', 'camera') then
        raise exception 'record_device_contacts: unknown device kind %', p_kind
            using errcode = '22023';
    end if;

    with given as (
        select (c ->> 'received_at')::timestamptz as received_at,
               (c ->> 'expected_next_at')::timestamptz as expected_next_at
        from jsonb_array_elements(coalesce(p_contacts, '[]'::jsonb)) c
    )
    insert into public.device_contact (pond_id, kind, received_at, expected_next_at)
    select distinct on (g.received_at) p_pond_id, p_kind, g.received_at,
           case when g.expected_next_at > g.received_at then g.expected_next_at end
    from given g
    where g.received_at is not null and g.received_at >= v_keep_from
    order by g.received_at
    on conflict (pond_id, kind, received_at) do nothing;

    select max((c ->> 'received_at')::timestamptz) into v_newest
    from jsonb_array_elements(coalesce(p_contacts, '[]'::jsonb)) c;

    if v_newest is not null then
        select dc.expected_next_at into v_next
        from public.device_contact dc
        where dc.pond_id = p_pond_id and dc.kind = p_kind and dc.received_at = v_newest;

        insert into public.devices (pond_id, kind, last_seen_at, next_expected_at)
        values (p_pond_id, p_kind, v_newest, v_next)
        on conflict (pond_id, kind) do update
            set next_expected_at = case
                    when devices.last_seen_at is null or excluded.last_seen_at >= devices.last_seen_at
                    then excluded.next_expected_at else devices.next_expected_at end,
                last_seen_at = greatest(devices.last_seen_at, excluded.last_seen_at),
                updated_at = now();
    end if;

    delete from public.device_contact dc
    where dc.pond_id = p_pond_id and dc.kind = p_kind and dc.received_at < v_keep_from;

    select d.* into v_row from public.devices d where d.pond_id = p_pond_id and d.kind = p_kind;
    return case when v_row.pond_id is null then null else to_jsonb(v_row) end;
end;
$$;

create or replace function public.pond_device_activity(
    p_pond_id bigint,
    p_since timestamptz
)
returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
    with recent as (
        select dc.kind, dc.received_at, dc.expected_next_at
        from public.device_contact dc
        where dc.pond_id = p_pond_id and dc.received_at >= p_since
    ),
    before as (
        select distinct on (dc.kind) dc.kind, dc.received_at, dc.expected_next_at
        from public.device_contact dc
        where dc.pond_id = p_pond_id and dc.received_at < p_since
        order by dc.kind, dc.received_at desc
    ),
    contacts as (
        select * from recent
        union all
        select * from before
    )
    select jsonb_build_object(
        'devices', coalesce((
            select jsonb_agg(to_jsonb(d) order by d.kind)
            from public.devices d
            where d.pond_id = p_pond_id), '[]'::jsonb),
        'contacts', coalesce((
            select jsonb_agg(jsonb_build_object(
                       'kind', c.kind, 'received_at', c.received_at, 'expected_next_at', c.expected_next_at)
                   order by c.kind, c.received_at)
            from contacts c), '[]'::jsonb),
        'battery', coalesce((
            select jsonb_agg(jsonb_build_object('at', s.created_at, 'mv', s.data1) order by s.created_at, s.id)
            from public."SensorData" s
            where s."userID" = p_pond_id and s.sensor_type = 'battery_mv' and s.created_at >= p_since),
            '[]'::jsonb),
        'reset', (
            select jsonb_build_object('at', s.created_at, 'code', s.data1)
            from public."SensorData" s
            where s."userID" = p_pond_id and s.sensor_type = 'reset_reason'
            order by s.created_at desc, s.id desc
            limit 1));
$$;

revoke all on function public.record_device_contacts(bigint, text, jsonb) from public, anon, authenticated;
revoke all on function public.pond_device_activity(bigint, timestamptz) from public, anon, authenticated;
grant execute on function public.record_device_contacts(bigint, text, jsonb) to service_role;
grant execute on function public.pond_device_activity(bigint, timestamptz) to service_role;
