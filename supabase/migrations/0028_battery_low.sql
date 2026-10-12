-- 0028_battery_low.sql
--
-- Sensor node battery (issue #42). The node reads its supply through a
-- switched divider during the upload and sends battery_mv on every row of
-- the cycle (column added by 0027). Below its configured threshold
-- (secrets.h KOI_BATTERY_LOW_MV, default 4400 mV) it also sets a new flag.
--
-- 1. SensorData.flags gains bit 16, battery_low: the cycle's battery
--    reading was below the node's low threshold. The column check becomes
--    0..31 and ingest_readings accepts flags 0..31 (it refused 16 and up
--    as invalid_flags). The function is otherwise 0027's, unchanged:
--    same signature, security definer, same access (anon and
--    service_role). Rows written before keep their flags (0..15 or null).
--
-- 2. notification_outbox.kind gains 'battery_low' (koi/notifications.py
--    battery_low): a warning when the sensor node's newest battery reading
--    in the device window carries the battery_low flag. The threshold is
--    the node's, so a 12 V pack and 4 alkaline cells are each judged by
--    the value configured on their own node.
--
-- 3. pond_device_activity(p_pond_id, p_since), same signature and access
--    (service_role only), now also reads the v2 columns:
--      battery  every battery reading at or after p_since, oldest first,
--               as {at, mv, low}:
--                 - legacy rows with sensor_type 'battery_mv' (data1 in
--                   mV): at = created_at, low = null (no flag);
--                 - v2 rows with battery_mv set: one entry per cycle (the
--                   node repeats the value on each row of the cycle),
--                   at = coalesce(sampled_at, created_at), low = flags
--                   bit 16 (null when the row has no flags).
--      reset    the newest of
--                 - the newest legacy 'reset_reason' row: {at: created_at,
--                   code: data1, reason: null};
--                 - the newest v2 row with reset_reason set: {at, code:
--                   null, reason}, where at is the reading time of the
--                   first row of that boot (same device and boot_id), so
--                   it says when the node came up rather than when it last
--                   reported. A row without boot_id uses its own time.
--               compared by the legacy row's created_at and the v2 row's
--               reading time; null when there is neither.
--    The other keys (devices, contacts) are unchanged.
--
-- No backfill, no new column. Rows written before this migration load as
-- before.

-- 1. Flags -----------------------------------------------------------------
alter table public."SensorData" drop constraint sensordata_flags_range;
alter table public."SensorData"
    add constraint sensordata_flags_range check (flags is null or flags between 0 and 31);

comment on column public."SensorData".flags is
    'Node flags: 1 time_unsynced, 2 temp_fallback, 4 ph_uncalibrated, 8 lux_saturated, 16 battery_low; null on legacy rows.';

create or replace function public.ingest_readings(device_token text, rows jsonb)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    c_max_rows       constant integer  := 32;
    c_window         constant interval := interval '60 seconds';
    c_window_batches constant integer  := 6;
    c_future_skew    constant interval := interval '5 minutes';
    c_max_age        constant interval := interval '30 days';
    c_known_flags    constant integer  := 31;
    c_time_unsynced  constant integer  := 1;
    c_reset_reasons  constant text[]   := array[
        'unknown', 'power_on', 'external_pin', 'software', 'panic', 'interrupt_watchdog',
        'task_watchdog', 'other_watchdog', 'deep_sleep_wake', 'brownout', 'sdio', 'usb',
        'jtag', 'efuse', 'power_glitch', 'cpu_lockup'];
    v_now      timestamptz := now();
    v_token_id bigint;
    v_device   public.devices;
    v_item     jsonb;
    v_index    integer;
    v_type     text;
    v_value    numeric;
    v_reason   text;
    v_bad_key  text;
    v_boot     text;
    v_seq      numeric;
    v_sampled  timestamptz;
    v_flags    numeric;
    v_reset    text;
    v_battery  numeric;
    v_key      text;
    v_seen     text[] := '{}';
    v_accept   jsonb := '[]'::jsonb;
    v_reject   jsonb := '[]'::jsonb;
    v_keyed    integer := 0;
    v_stored   integer := 0;
begin
    -- 1. The token.
    if ingest_readings.device_token is null
       or length(ingest_readings.device_token) not between 32 and 256 then
        raise exception 'ingest_readings: device token not accepted' using errcode = 'PT401';
    end if;
    select t.id into v_token_id
    from public.device_tokens t
    where t.token_hash = pg_catalog.sha256(pg_catalog.convert_to(ingest_readings.device_token, 'UTF8'))
      and t.revoked_at is null
      and (t.expires_at is null or t.expires_at > v_now);
    if v_token_id is null then
        raise exception 'ingest_readings: device token not accepted' using errcode = 'PT401';
    end if;
    select d.* into v_device
    from public.devices d join public.device_tokens t on t.device_id = d.device_id
    where t.id = v_token_id and d.kind = 'sensor' and d.revoked_at is null
    for update of d;
    if v_device.device_id is null then
        raise exception 'ingest_readings: device token not accepted' using errcode = 'PT401';
    end if;

    -- 2. The rate limit, per device (rotating the token does not reset it).
    if v_device.ingest_window_started_at is null or v_device.ingest_window_started_at <= v_now - c_window then
        v_device.ingest_window_started_at := v_now;
        v_device.ingest_window_batches := 0;
    elsif v_device.ingest_window_batches >= c_window_batches then
        raise exception 'ingest_readings: more than % batches in % seconds; retry after %',
            c_window_batches, extract(epoch from c_window)::integer,
            v_device.ingest_window_started_at + c_window
            using errcode = 'PT429';
    end if;

    -- 3. The batch's shape.
    if ingest_readings.rows is null or jsonb_typeof(ingest_readings.rows) <> 'array' then
        raise exception 'ingest_readings: rows must be a JSON array' using errcode = '22023';
    end if;
    if jsonb_array_length(ingest_readings.rows) = 0 then
        raise exception 'ingest_readings: rows is empty' using errcode = '22023';
    end if;
    if jsonb_array_length(ingest_readings.rows) > c_max_rows then
        raise exception 'ingest_readings: % rows sent, at most % per call',
            jsonb_array_length(ingest_readings.rows), c_max_rows using errcode = 'PT413';
    end if;
    for v_item, v_index in
        select e.value, (e.ordinality - 1)::integer
        from jsonb_array_elements(ingest_readings.rows) with ordinality e
    loop
        if jsonb_typeof(v_item) <> 'object' then
            raise exception 'ingest_readings: row % is not an object', v_index using errcode = '22023';
        end if;
        select k into v_bad_key from jsonb_object_keys(v_item) k
        where k not in ('sensor_type', 'data1', 'boot_id', 'cycle_seq', 'sampled_at', 'flags',
                        'reset_reason', 'battery_mv')
        order by k limit 1;
        if v_bad_key is not null then
            raise exception 'ingest_readings: row % has field "%", which is not accepted '
                            '(the pond and device come from the device token, the insert time from the database)',
                            v_index, v_bad_key
                using errcode = '22023';
        end if;
    end loop;

    -- 4. Each row.
    for v_item, v_index in
        select e.value, (e.ordinality - 1)::integer
        from jsonb_array_elements(ingest_readings.rows) with ordinality e
    loop
        v_reason := null;
        v_type := case when jsonb_typeof(v_item -> 'sensor_type') = 'string' then v_item ->> 'sensor_type' end;
        v_value := case when jsonb_typeof(v_item -> 'data1') = 'number' then (v_item ->> 'data1')::numeric end;
        v_boot := case when jsonb_typeof(v_item -> 'boot_id') = 'string' then v_item ->> 'boot_id' end;
        v_seq := case when jsonb_typeof(v_item -> 'cycle_seq') = 'number' then (v_item ->> 'cycle_seq')::numeric end;
        v_flags := case when jsonb_typeof(v_item -> 'flags') = 'number' then (v_item ->> 'flags')::numeric end;
        v_reset := case when jsonb_typeof(v_item -> 'reset_reason') = 'string' then v_item ->> 'reset_reason' end;
        v_battery := case when jsonb_typeof(v_item -> 'battery_mv') = 'number' then (v_item ->> 'battery_mv')::numeric end;
        v_sampled := null;

        v_reason := case
            when v_type is null
                 or v_type not in ('temp', 'TDS', 'pH', 'LUX', 'battery_mv', 'reset_reason') then 'unknown_sensor_type'
            when v_value is null then 'not_a_number'
            when (v_type = 'temp' and v_value not between -10 and 60)
              or (v_type = 'pH' and v_value not between 0 and 14)
              or (v_type = 'TDS' and v_value not between 0 and 5000)
              or (v_type = 'LUX' and v_value not between 0 and 200000)
              or (v_type = 'battery_mv' and v_value not between 0 and 20000)
              or (v_type = 'reset_reason' and (v_value not between 0 and 255 or v_value <> trunc(v_value)))
                 then 'out_of_range'
            when (v_item ? 'boot_id') <> (v_item ? 'cycle_seq')
              or (v_item ? 'boot_id' and (v_boot is null or v_boot !~ '^[0-9a-f]{16}$'))
              or (v_item ? 'cycle_seq' and (v_seq is null or v_seq <> trunc(v_seq)
                                            or v_seq not between 0 and 9007199254740991))
                 then 'invalid_cycle'
            when v_item ? 'flags' and (v_flags is null or v_flags <> trunc(v_flags)
                                       or v_flags not between 0 and c_known_flags)
                 then 'invalid_flags'
            when v_item ? 'reset_reason' and (v_reset is null or not (v_reset = any (c_reset_reasons)))
                 then 'invalid_reset_reason'
            when v_item ? 'battery_mv' and (v_battery is null or v_battery <> trunc(v_battery)
                                            or v_battery not between 0 and 20000)
                 then 'invalid_battery_mv'
        end;

        if v_reason is null and v_item ? 'sampled_at' then
            if jsonb_typeof(v_item -> 'sampled_at') <> 'string'
               or (v_item ->> 'sampled_at') !~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$'
               or (v_flags is not null and (v_flags::integer & c_time_unsynced) <> 0) then
                v_reason := 'invalid_sampled_at';
            else
                begin
                    v_sampled := (v_item ->> 'sampled_at')::timestamptz;
                exception when others then
                    v_reason := 'invalid_sampled_at';
                end;
                if v_reason is null and v_sampled > v_now + c_future_skew then
                    v_reason := 'sampled_at_in_future';
                elsif v_reason is null and v_sampled < v_now - c_max_age then
                    v_reason := 'sampled_at_too_old';
                end if;
            end if;
        end if;

        if v_reason is null then
            v_key := coalesce(v_boot, '') || '/' || coalesce(v_seq::bigint::text, '') || '/' || v_type;
            if v_key = any (v_seen) then
                v_reason := 'duplicate';
            end if;
        end if;

        if v_reason is null then
            v_seen := v_seen || v_key;
            if v_boot is not null then
                v_keyed := v_keyed + 1;
            end if;
            v_accept := v_accept || jsonb_build_object(
                'sensor_type', v_type, 'data1', v_value, 'boot_id', v_boot, 'cycle_seq', v_seq,
                'sampled_at', v_sampled, 'flags', v_flags, 'reset_reason', v_reset, 'battery_mv', v_battery);
        else
            v_reject := v_reject || jsonb_build_object(
                'index', v_index, 'sensor_type', v_item -> 'sensor_type', 'reason', v_reason);
        end if;
    end loop;

    -- 5. Store. A retry of a cycle already stored is not inserted again.
    insert into public."SensorData"
        (created_at, sensor_type, data1, "userID", device_id, boot_id, cycle_seq, sampled_at, flags,
         reset_reason, battery_mv)
    select v_now, a ->> 'sensor_type', (a ->> 'data1')::real, v_device.pond_id, v_device.device_id,
           a ->> 'boot_id', (a ->> 'cycle_seq')::bigint, (a ->> 'sampled_at')::timestamptz,
           (a ->> 'flags')::smallint, a ->> 'reset_reason', (a ->> 'battery_mv')::integer
    from jsonb_array_elements(v_accept) a
    on conflict (device_id, boot_id, cycle_seq, sensor_type) where boot_id is not null do nothing;
    get diagnostics v_stored = row_count;

    update public.devices d
    set ingest_window_started_at = v_device.ingest_window_started_at,
        ingest_window_batches = v_device.ingest_window_batches + 1
    where d.device_id = v_device.device_id;
    update public.device_tokens t set last_used_at = v_now where t.id = v_token_id;

    return jsonb_build_object(
        'accepted', jsonb_array_length(v_accept),
        'stored', v_stored,
        'already_stored', jsonb_array_length(v_accept) - v_stored,
        'rejected', v_reject,
        'format', case when v_keyed = 0 then 'v1'
                       when v_keyed = jsonb_array_length(v_accept) then 'v2'
                       else 'mixed' end,
        'received_at', v_now);
end;
$$;

revoke all on function public.ingest_readings(text, jsonb) from public, anon, authenticated;
grant execute on function public.ingest_readings(text, jsonb) to anon, service_role;

-- 2. Outbox kind -----------------------------------------------------------
alter table public.notification_outbox drop constraint notification_outbox_kind_check;
alter table public.notification_outbox
    add constraint notification_outbox_kind_check
    check (kind in ('status_red', 'ladder_action', 'node_silent', 'camera_obstructed', 'battery_low'));

-- 3. Device activity -------------------------------------------------------
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
    ),
    legacy_battery as (
        select s.created_at as at, s.data1::double precision as mv, null::boolean as low, s.id
        from public."SensorData" s
        where s."userID" = p_pond_id and s.sensor_type = 'battery_mv' and s.created_at >= p_since
    ),
    cycle_battery as (
        -- One per cycle: every row of a cycle repeats the reading.
        select distinct on (s.device_id, s.boot_id, s.cycle_seq, coalesce(s.sampled_at, s.created_at))
               coalesce(s.sampled_at, s.created_at) as at, s.battery_mv::double precision as mv,
               case when s.flags is null then null else (s.flags & 16) <> 0 end as low, s.id
        from public."SensorData" s
        where s."userID" = p_pond_id and s.battery_mv is not null
          and coalesce(s.sampled_at, s.created_at) >= p_since
        order by s.device_id, s.boot_id, s.cycle_seq, coalesce(s.sampled_at, s.created_at), s.id
    ),
    battery as (
        select * from legacy_battery
        union all
        select * from cycle_battery
    ),
    legacy_reset as (
        select s.created_at as at, s.data1 as code
        from public."SensorData" s
        where s."userID" = p_pond_id and s.sensor_type = 'reset_reason'
        order by s.created_at desc, s.id desc
        limit 1
    ),
    cycle_reset as (
        select coalesce(s.sampled_at, s.created_at) as at, s.reset_reason, s.device_id, s.boot_id
        from public."SensorData" s
        where s."userID" = p_pond_id and s.reset_reason is not null
        order by coalesce(s.sampled_at, s.created_at) desc, s.id desc
        limit 1
    ),
    cycle_reset_boot as (
        select r.at, r.reset_reason,
               coalesce((select min(coalesce(b.sampled_at, b.created_at))
                         from public."SensorData" b
                         where r.boot_id is not null and b."userID" = p_pond_id
                           and b.device_id is not distinct from r.device_id and b.boot_id = r.boot_id),
                        r.at) as boot_at
        from cycle_reset r
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
            select jsonb_agg(jsonb_build_object('at', b.at, 'mv', b.mv, 'low', b.low) order by b.at, b.id)
            from battery b), '[]'::jsonb),
        'reset', (
            select r.obj
            from (
                select l.at, jsonb_build_object('at', l.at, 'code', l.code, 'reason', null) as obj
                from legacy_reset l
                union all
                select c.at, jsonb_build_object('at', c.boot_at, 'code', null, 'reason', c.reset_reason)
                from cycle_reset_boot c
            ) r
            order by r.at desc
            limit 1));
$$;

revoke all on function public.pond_device_activity(bigint, timestamptz) from public, anon, authenticated;
grant execute on function public.pond_device_activity(bigint, timestamptz) to service_role;
