-- 0027_sensor_payload_v2.sql
--
-- Sensor payload v2 (issue #17), including the database half of #97.
--
-- Until now a SensorData row held only id, created_at (the database
-- insert time), sensor_type, data1 and userID. The node's true sample
-- time, its identity and its cycle were not stored, so every reader timed
-- a reading by when it was stored, and aggregate_daily_sensor_data
-- re-aggregated the whole table on every graph call and failed outright
-- on a row with no pond (userID null), which emptied every pond's graphs.
--
-- 1. SensorData columns, all nullable, so every row written before this
--    migration loads unchanged and reads as "unknown":
--      device_id     devices.device_id of the node that sent the row. Set
--                    only by ingest_readings, from the token; never taken
--                    from the payload. On delete of the device: null.
--      boot_id       16 lowercase hex characters: a random 64-bit number
--                    the node draws on every cold boot (power-on, reset,
--                    brownout, crash). Not a counter, so it cannot repeat
--                    after the RTC memory is lost or NVS is erased or
--                    rolled back.
--      cycle_seq     the node's wake cycle within that boot, counted in
--                    RTC memory from 1. boot_id and cycle_seq are both
--                    set or both null.
--      sampled_at    when the probe was read, by the node's clock, sent
--                    only once the clock was set by SNTP. Null means the
--                    sample time is unknown: a legacy row, or a node whose
--                    clock was not synchronised (flags bit 1).
--      flags         bit mask set by the node (smallint, 0..15):
--                      1 time_unsynced     clock not set: no sampled_at
--                      2 temp_fallback     pH/TDS compensated with 25 C
--                                          because the water temperature
--                                          read failed
--                      4 ph_uncalibrated   fewer than two pH buffers
--                      8 lux_saturated     light sensor saturated
--                    Null on legacy rows (unknown, not "no flags").
--      reset_reason  why the node last booted, as ESP-IDF names it
--                    (koi/models/device_health.py RESET_REASONS spelling:
--                    power_on, deep_sleep_wake, brownout, ...).
--      battery_mv    supply voltage in millivolts, when the node has a
--                    divider wired (optional, 0..20000).
--
--    Retry key: unique (device_id, boot_id, cycle_seq, sensor_type) where
--    boot_id is not null. A cycle the node re-sends after a failed upload
--    is stored once; a new cycle (next cycle_seq, or a new boot_id after a
--    reboot) is stored even when its sample time equals an earlier one.
--    (device_id, cycle_seq, sensor_type) alone was not enough: cycle_seq
--    lives in RTC memory and restarts after every power loss, and an NVS
--    counter can be rolled back, so the random boot_id is part of the
--    key. Legacy rows and v1 rows (no boot_id) have no retry key: each is
--    a separate reading, as before, and is never deduplicated.
--
--    Indexes for per-pond reads:
--      idx_sensordata_pond_created     ("userID", created_at, id): the
--          incremental scans by insert time (sensor_ingest_pending, the
--          daily refresh's changed-rows scan).
--      idx_sensordata_pond_effective   ("userID", coalesce(sampled_at,
--          created_at), id): window reads by reading time.
--      idx_sensordata_pond_type_effective ("userID", sensor_type,
--          coalesce(sampled_at, created_at) desc, id desc): the newest
--          reading per channel.
--    Built in the migration's transaction, so node inserts wait for it.
--
-- 2. Reading time and its provenance. Every reader below uses
--    coalesce(sampled_at, created_at) as the reading's time and reports
--    which it was: time_basis 'sample' (the node's clock) or 'ingestion'
--    (the database insert time, the only time legacy rows have). There is
--    no backfill: created_at is when the row was stored, not when the
--    probe was read, so it is never copied into sampled_at. A newer insert
--    time never stands in for an older sample time.
--
-- 3. Canonical channels: sensor_channel(sensor_type) maps the spellings
--    seen in SensorData to one channel each, and nothing else:
--      sensor_type   channel  API field (dashboard)   unit
--      pH            pH       ph                      pH
--      TDS           TDS      tds                     ppm
--      temp          temp     water_temp / tempC      degC
--      LUX, lux      LUX      lux                     lux
--    Any other spelling (ph, Tds, Temp, ...) maps to null and is not read
--    as a water channel, so a case change cannot create a fifth channel.
--    ingest_readings accepts only the canonical spellings.
--
-- 4. ingest_readings(device_token, rows), same signature and access as
--    0025 (anon and service_role). Steps 1-3 (token, rate limit, batch
--    shape) are unchanged except that a row may now also hold boot_id,
--    cycle_seq, sampled_at, flags, reset_reason and battery_mv. userID,
--    pond_id, device_id, id and created_at still refuse the whole batch:
--    the pond and device come from the token, the insert time from the
--    database. Each row is then accepted or rejected on its own, with
--    0025's reasons plus:
--      invalid_cycle            boot_id without cycle_seq or the reverse,
--                               boot_id not 16 lowercase hex characters,
--                               cycle_seq not an integer 0..2^53-1
--      invalid_sampled_at       not an ISO 8601 text with seconds and an
--                               explicit offset (Z or +hh:mm), not a real
--                               date, or sent with flags bit 1
--                               (time_unsynced)
--      sampled_at_in_future     more than 5 minutes after receipt
--      sampled_at_too_old       more than 30 days before receipt
--      invalid_flags            not an integer 0..15
--      invalid_reset_reason     not one of the names above
--      invalid_battery_mv       not an integer 0..20000
--      duplicate                a second row with the same (boot_id,
--                               cycle_seq, sensor_type) in this batch
--                               (v1 rows: the same sensor_type)
--    Accepted rows are inserted with userID and device_id from the token
--    and created_at = now(); sampled_at is stored only as sent and is
--    never filled from the receipt time. A row whose retry key is already
--    stored is not inserted again. Reply: {accepted, stored,
--    already_stored, rejected: [{index, sensor_type, reason}], format,
--    received_at}: accepted = stored + already_stored; format is 'v1'
--    (no row has a cycle key), 'v2' (every row has one) or 'mixed'.
--
-- 5. Daily aggregates (D1/#97). daily_sensor_averages gains sample_count
--    (rows averaged) and sample_time_count (of those, rows with a
--    sampled_at); both null on rows written before this migration.
--    aggregate_daily_sensor_data() (whole table, every call) is replaced
--    by aggregate_daily_sensor_data(p_from, p_to, p_pond_id default
--    null) returns integer: recomputes the given Singapore days (at most
--    366) for one pond or, with null, every pond. A row with no pond is
--    skipped. For each (pond, day) with at least one water-channel
--    reading, that day's daily rows are deleted and rewritten from the
--    readings, grouped by canonical channel and by the Singapore date of
--    coalesce(sampled_at, created_at); LUX at or below 50 lx is left out
--    as before. Days with no readings keep their rows. Backend only.
--
--    daily_sensor_refresh (pond_id, covered_from, refreshed_at) records,
--    per pond, the earliest day already recomputed and when.
--    reconcile_daily_sensor_averages(p_pond_id, p_days) is the bounded
--    reconciliation job: it recomputes the days of the last p_days (1 to
--    366) that were never recomputed for the pond, plus every day that
--    received a row since the previous run (rows with created_at at or
--    after refreshed_at minus 10 minutes, so a transaction that committed
--    late is not missed; a late sample lands on its own sample day). It
--    returns {pond_id, from, to, days_recomputed, rows_written}. The
--    table starts empty, so the first call per pond recomputes its whole
--    window: old daily rows built by insert date and by any spelling are
--    rewritten. Backend only; get_historical_graph_payload calls it.
--
-- 6. Readers:
--    get_historical_graph_payload   reconciles only the requested pond
--        and window, then reads as before; each daily_trends item adds
--        sample_count and time_basis ('sample', 'ingestion', 'mixed', or
--        null for a row from before this migration). Access unchanged.
--    get_pond_telemetry_history     dropped and recreated with an extra
--        result column time_basis ('sample', 'ingestion' or 'mixed' per
--        hour). Buckets are genuine UTC hours of the reading time; the
--        pond filter, the channel list (canonical) and the rule that an
--        hour needs pH and TDS (#50) are unchanged. Backend only.
--    get_bundled_dashboard_payload  raw_sensor picks each sensor_type's
--        newest reading by reading time, then id (was created_at only);
--        new latest_readings: {channel: {id, sensor_type, value,
--        sampled_at, ingested_at, time, time_basis, flags}} per canonical
--        channel; telemetry_history items add time_basis. The values
--        substituted for missing history hours are #50's and unchanged.
--        Backend only.
--    pond_dashboard_sources         readings add sampled_at and flags and
--        pick the newest by reading time, then id.
--    sensor_ingest_pending, sensor_ingest_history   rows add sampled_at
--        and flags, so the poller times a reading by its sample time when
--        it has one (koi/models/sensor_inputs.py effective_sample_time).
--    Signatures, security and grants are unchanged except where noted.
--
-- Access: daily_sensor_refresh has RLS on, no policies, and is revoked
-- from public, anon and authenticated; service_role may read and write it.
-- sensor_channel, aggregate_daily_sensor_data and
-- reconcile_daily_sensor_averages are executable by service_role only.

-- 1. SensorData ------------------------------------------------------------
alter table public."SensorData"
    add column if not exists device_id uuid references public.devices (device_id) on delete set null,
    add column if not exists boot_id text,
    add column if not exists cycle_seq bigint,
    add column if not exists sampled_at timestamptz,
    add column if not exists flags smallint,
    add column if not exists reset_reason text,
    add column if not exists battery_mv integer;

alter table public."SensorData"
    add constraint sensordata_boot_id_format check (boot_id is null or boot_id ~ '^[0-9a-f]{16}$'),
    add constraint sensordata_cycle_key_pair check ((boot_id is null) = (cycle_seq is null)),
    add constraint sensordata_cycle_seq_range check (cycle_seq is null or cycle_seq >= 0),
    add constraint sensordata_flags_range check (flags is null or flags between 0 and 15),
    add constraint sensordata_reset_reason_format check (reset_reason is null or reset_reason ~ '^[a-z_]{1,32}$'),
    add constraint sensordata_battery_mv_range check (battery_mv is null or battery_mv between 0 and 20000);

create unique index if not exists sensordata_cycle_key
    on public."SensorData" (device_id, boot_id, cycle_seq, sensor_type)
    where boot_id is not null;
create index if not exists idx_sensordata_pond_created
    on public."SensorData" ("userID", created_at, id);
create index if not exists idx_sensordata_pond_effective
    on public."SensorData" ("userID", (coalesce(sampled_at, created_at)), id);
create index if not exists idx_sensordata_pond_type_effective
    on public."SensorData" ("userID", sensor_type, (coalesce(sampled_at, created_at)) desc, id desc);

comment on column public."SensorData".created_at is
    'When the database stored the row (ingestion time), not when the probe was read.';
comment on column public."SensorData".sampled_at is
    'When the probe was read, by the node''s SNTP-set clock; null = unknown (legacy row or unsynchronised clock). Never filled from created_at.';
comment on column public."SensorData".boot_id is
    'Random 64-bit id (16 hex) drawn by the node on every cold boot; with cycle_seq and sensor_type, the retry key.';
comment on column public."SensorData".flags is
    'Node flags: 1 time_unsynced, 2 temp_fallback, 4 ph_uncalibrated, 8 lux_saturated; null on legacy rows.';

-- 3. Canonical channels ----------------------------------------------------
create or replace function public.sensor_channel(p_sensor_type text)
returns text
language sql
immutable
set search_path = ''
as $$
    select case p_sensor_type
        when 'pH' then 'pH'
        when 'TDS' then 'TDS'
        when 'temp' then 'temp'
        when 'LUX' then 'LUX'
        when 'lux' then 'LUX'
    end;
$$;

-- 4. ingest_readings v2 ----------------------------------------------------
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
    c_known_flags    constant integer  := 15;
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

-- 5. Daily aggregates ------------------------------------------------------
alter table public.daily_sensor_averages
    add column if not exists sample_count integer,
    add column if not exists sample_time_count integer;

drop function if exists public.aggregate_daily_sensor_data();

create or replace function public.aggregate_daily_sensor_data(
    p_from date,
    p_to date,
    p_pond_id bigint default null
)
returns integer
language plpgsql
security invoker
set search_path = ''
as $$
declare
    c_max_days constant integer := 366;
    v_start timestamptz;
    v_end   timestamptz;
    v_rows  integer;
begin
    if p_from is null or p_to is null or p_to < p_from or p_to - p_from >= c_max_days then
        raise exception 'aggregate_daily_sensor_data: p_from and p_to are required, in order, at most % days',
            c_max_days using errcode = '22023';
    end if;
    v_start := p_from::timestamp at time zone 'Asia/Singapore';
    v_end := (p_to + 1)::timestamp at time zone 'Asia/Singapore';

    -- Daily rows of a (pond, day) that has water-channel readings are
    -- rewritten; a day with none keeps its rows. Rows with no pond never
    -- take part.
    delete from public.daily_sensor_averages a
    where a.record_date between p_from and p_to
      and (p_pond_id is null or a.userid = p_pond_id)
      and exists (
          select 1 from public."SensorData" d
          where d."userID" = a.userid
            and public.sensor_channel(d.sensor_type) is not null
            and coalesce(d.sampled_at, d.created_at) >= (a.record_date::timestamp at time zone 'Asia/Singapore')
            and coalesce(d.sampled_at, d.created_at) < ((a.record_date + 1)::timestamp at time zone 'Asia/Singapore'));

    -- An upsert, so a reading committed between the two statements cannot
    -- make the insert collide with a row the delete did not see.
    insert into public.daily_sensor_averages
        (userid, sensor_type, avg_value, min_value, max_value, record_date, sample_count, sample_time_count)
    select d."userID",
           public.sensor_channel(d.sensor_type),
           round(avg(d.data1)::numeric, 2),
           round(min(d.data1)::numeric, 2),
           round(max(d.data1)::numeric, 2),
           (coalesce(d.sampled_at, d.created_at) at time zone 'Asia/Singapore')::date,
           count(*)::integer,
           (count(*) filter (where d.sampled_at is not null))::integer
    from public."SensorData" d
    where d."userID" is not null
      and (p_pond_id is null or d."userID" = p_pond_id)
      and public.sensor_channel(d.sensor_type) is not null
      and coalesce(d.sampled_at, d.created_at) >= v_start
      and coalesce(d.sampled_at, d.created_at) < v_end
      and d.data1 is not null
      and d.data1 <> 'NaN'::real
      -- Pitch-black night-time readings are left out of LUX, as before.
      and (public.sensor_channel(d.sensor_type) <> 'LUX' or d.data1 > 50.0)
    group by 1, 2, 6
    on conflict (userid, sensor_type, record_date) do update set
        avg_value = excluded.avg_value,
        min_value = excluded.min_value,
        max_value = excluded.max_value,
        sample_count = excluded.sample_count,
        sample_time_count = excluded.sample_time_count,
        created_at = now();
    get diagnostics v_rows = row_count;
    return v_rows;
end;
$$;

create table if not exists public.daily_sensor_refresh (
    pond_id      bigint primary key,
    covered_from date not null,
    refreshed_at timestamptz not null
);
comment on table public.daily_sensor_refresh is
    'Per pond: earliest Singapore day recomputed into daily_sensor_averages, and when. Written by reconcile_daily_sensor_averages (0027).';
alter table public.daily_sensor_refresh enable row level security;
revoke all on table public.daily_sensor_refresh from public, anon, authenticated;
grant select, insert, update, delete on table public.daily_sensor_refresh to service_role;

create or replace function public.reconcile_daily_sensor_averages(p_pond_id bigint, p_days integer)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
    c_overlap constant interval := interval '10 minutes';
    v_now   timestamptz := now();
    v_today date := (now() at time zone 'Asia/Singapore')::date;
    v_from  date;
    v_state public.daily_sensor_refresh;
    v_day   date;
    v_days  integer := 0;
    v_rows  integer := 0;
begin
    if p_pond_id is null or p_days is null or p_days < 1 or p_days > 366 then
        raise exception 'reconcile_daily_sensor_averages: p_pond_id is required and p_days must be 1 to 366'
            using errcode = '22023';
    end if;
    v_from := v_today - p_days;

    -- One run per pond at a time; the first run sees covered_from after
    -- today, so it recomputes the whole window.
    insert into public.daily_sensor_refresh (pond_id, covered_from, refreshed_at)
    values (p_pond_id, v_today + 1, '-infinity')
    on conflict (pond_id) do nothing;
    select r.* into v_state from public.daily_sensor_refresh r where r.pond_id = p_pond_id for update;

    -- Days of the window never recomputed.
    if v_from < v_state.covered_from then
        v_rows := v_rows + public.aggregate_daily_sensor_data(v_from, v_state.covered_from - 1, p_pond_id);
        v_days := v_days + (v_state.covered_from - v_from);
    end if;

    -- Days already covered that received a row since the previous run.
    if v_state.refreshed_at > '-infinity'::timestamptz then
        for v_day in
            select distinct (coalesce(d.sampled_at, d.created_at) at time zone 'Asia/Singapore')::date as day
            from public."SensorData" d
            where d."userID" = p_pond_id
              and d.created_at >= v_state.refreshed_at - c_overlap
            order by 1
        loop
            if v_day >= v_state.covered_from and v_day >= v_from - 366 and v_day <= v_today + 1 then
                v_rows := v_rows + public.aggregate_daily_sensor_data(v_day, v_day, p_pond_id);
                v_days := v_days + 1;
            end if;
        end loop;
    end if;

    update public.daily_sensor_refresh r
    set covered_from = least(r.covered_from, v_from), refreshed_at = v_now
    where r.pond_id = p_pond_id;

    return jsonb_build_object('pond_id', p_pond_id, 'from', v_from, 'to', v_today,
                              'days_recomputed', v_days, 'rows_written', v_rows);
end;
$$;

-- 6. Readers ---------------------------------------------------------------

-- As in 0026, reconciling only this pond's window instead of the whole
-- table, with sample_count and time_basis on each daily item.
CREATE OR REPLACE FUNCTION public.get_historical_graph_payload(p_userid bigint, p_days integer DEFAULT 30)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
DECLARE
    v_daily_trends JSONB;
    v_interventions JSONB;
    v_max_wc_id BIGINT;
BEGIN
    -- Bounded inputs (issue #12): the app asks for 7 or 30 days.
    IF p_userid IS NULL OR p_days IS NULL OR p_days < 1 OR p_days > 366 THEN
        RAISE EXCEPTION 'get_historical_graph_payload: p_userid is required and p_days must be 1 to 366'
            USING ERRCODE = '22023';
    END IF;

    -- Bring this pond's daily aggregates for the window up to date (0027).
    PERFORM public.reconcile_daily_sensor_averages(p_userid, p_days);

    -- A. Fetch Daily Sensor Averages for the last N days
    SELECT jsonb_agg(
        jsonb_build_object(
            'date', record_date,
            'sensor_type', sensor_type,
            'avg_value', avg_value,
            'min_value', min_value,
            'max_value', max_value,
            'sample_count', sample_count,
            'time_basis', CASE
                WHEN sample_count IS NULL OR sample_time_count IS NULL THEN NULL
                WHEN sample_time_count = 0 THEN 'ingestion'
                WHEN sample_time_count = sample_count THEN 'sample'
                ELSE 'mixed' END,
            'after_maintenance', sensor_type IN ('TDS', 'tds', 'pH', 'ph') AND EXISTS (
                SELECT 1 FROM public."pondInterventions" i
                WHERE i."userID" = p_userid AND i.event_type = 'FILTER_CLEAN'
                  AND i.event_timestamp < ((record_date + 1)::timestamp AT TIME ZONE 'Asia/Singapore')
                  AND i.event_timestamp + interval '24 hours' > (record_date::timestamp AT TIME ZONE 'Asia/Singapore')
            )
        ) ORDER BY record_date ASC, sensor_type ASC
    ) INTO v_daily_trends
    FROM public.daily_sensor_averages
    WHERE userID = p_userID
      AND record_date >= (NOW() AT TIME ZONE 'Asia/Singapore')::date - p_days;

    -- B. Find ID of largest WATER_CHANGE event in period (Major Reset Marker)
    SELECT id INTO v_max_wc_id
    FROM public."pondInterventions"
    WHERE "userID" = p_userID
      AND event_type = 'WATER_CHANGE'
      AND event_timestamp >= NOW() - (p_days || ' days')::INTERVAL
    ORDER BY COALESCE(volume_percentage, 0) DESC, volume_litres DESC NULLS LAST
    LIMIT 1;

    -- C. Fetch All Intervention Events in period. 'id' is the integer
    -- row id and 'event_id' the event's stable UUID (0015); 'pct' and
    -- 'litres' are kept for the app until #56.
    SELECT jsonb_agg(
        jsonb_build_object(
            'id', id,
            'event_id', event_id,
            'event_type', event_type,
            'timestamp', event_timestamp,
            'pct', volume_percentage,
            'litres', volume_litres,
            'food_grams', food_grams,
            'salt_grams', salt_grams,
            'notes', notes,
            'is_major_reset', (id = v_max_wc_id)
        ) ORDER BY event_timestamp ASC, id ASC  -- id breaks ties, as in pond_interventions_since
    ) INTO v_interventions
    FROM public."pondInterventions"
    WHERE "userID" = p_userID
      AND event_timestamp >= NOW() - (p_days || ' days')::INTERVAL;

    RETURN jsonb_build_object(
        'daily_trends', COALESCE(v_daily_trends, '[]'::jsonb),
        'interventions', COALESCE(v_interventions, '[]'::jsonb)
    );
END;
$function$;

-- The result gains a column, so the function is dropped and recreated
-- (its only caller, get_bundled_dashboard_payload, resolves it at run time).
drop function if exists public.get_pond_telemetry_history(bigint, integer);

create function public.get_pond_telemetry_history(p_user_id bigint, p_days integer default 7)
returns table(bucket_time timestamptz, ph numeric, tds numeric, temp_c numeric, lux numeric, time_basis text)
language sql
stable
security invoker
set search_path = ''
as $$
  with readings as (
    select coalesce(d.sampled_at, d.created_at) as at,
           public.sensor_channel(d.sensor_type) as channel,
           d.data1,
           d.sampled_at is not null as sampled
    from public."SensorData" d
    where d."userID" = p_user_id
      and coalesce(d.sampled_at, d.created_at) >= now() - make_interval(days => p_days)
      and public.sensor_channel(d.sensor_type) is not null
  ),
  hourly_buckets as (
    select
      -- A UTC wall hour turned back into the instant it names (0006).
      date_trunc('hour', r.at at time zone 'UTC') at time zone 'UTC' as bucket_time,
      r.channel,
      avg(r.data1)::numeric as avg_val,
      bool_and(r.sampled) as all_sampled,
      bool_or(r.sampled) as any_sampled
    from readings r
    group by 1, 2
  )
  select
    h.bucket_time,
    round(max(case when h.channel = 'pH'   then h.avg_val end), 2) as ph,
    round(max(case when h.channel = 'TDS'  then h.avg_val end), 2) as tds,
    round(max(case when h.channel = 'temp' then h.avg_val end), 2) as temp_c,
    round(max(case when h.channel = 'LUX'  then h.avg_val end), 2) as lux,
    case when bool_and(h.all_sampled) then 'sample'
         when bool_or(h.any_sampled) then 'mixed'
         else 'ingestion' end as time_basis
  from hourly_buckets h
  group by h.bucket_time
  -- Only return hours where at least pH and TDS were recorded (#50 owns this rule)
  having max(case when h.channel = 'pH' then h.avg_val end) is not null
     and max(case when h.channel = 'TDS' then h.avg_val end) is not null
  order by h.bucket_time asc;
$$;

-- As in 0026, with raw_sensor ordered by reading time then id,
-- latest_readings per canonical channel, and time_basis per history hour.
CREATE OR REPLACE FUNCTION public.get_bundled_dashboard_payload(p_user_id bigint)
 RETURNS jsonb
 LANGUAGE plpgsql
 SET search_path TO ''
AS $function$
DECLARE
    -- User profile & station lookup variables
    v_user_lat REAL;
    v_user_lon REAL;
    v_assigned_stations JSONB;
    v_temp_stn TEXT;
    v_rain_stn TEXT;
    v_wind_stn TEXT;
    v_slot_2hr TEXT;
    v_slot_24hr TEXT;

    -- Telemetry & Forecast accumulators
    v_raw_sensor JSONB;
    v_latest_readings JSONB;
    v_nea_temp JSONB;
    v_nea_rain JSONB;
    v_nea_wind JSONB;
    v_nea_2hr JSONB;
    v_nea_24hr_regional JSONB;
    v_nea_24hr_general JSONB;
    v_nea_4day JSONB;
    v_nea_uv JSONB;
    v_ph_series JSONB;
BEGIN
    -- 1. Fetch user coordinates & pre-computed station lookup map
    SELECT
        latitude,
        longitude,
        "ClosestStations"
    INTO
        v_user_lat,
        v_user_lon,
        v_assigned_stations
    FROM public."UserData"
    WHERE "userID" = p_user_id
    LIMIT 1;

    -- Extract specific station slots from assigned_stations JSON
    IF v_assigned_stations IS NOT NULL THEN
        v_temp_stn := v_assigned_stations->>'air-temperature';
        v_rain_stn := v_assigned_stations->>'rainfall';
        v_wind_stn := v_assigned_stations->>'wind-speed';
        v_slot_2hr := v_assigned_stations->>'two-hr-forecast';
        v_slot_24hr := v_assigned_stations->>'twenty-four-hr-forecast';
    END IF;

    -- 2. FETCH CATEGORY 1: Raw Sensor Data (Latest value per sensor_type).
    -- Newest by reading time (sample time when known, else insert time),
    -- then id, so a late upload of an old sample never becomes "latest".
    SELECT jsonb_object_agg(sensor_type, data1) INTO v_raw_sensor
    FROM (
        SELECT DISTINCT ON (sensor_type)
            sensor_type,
            data1
        FROM public."SensorData"
        WHERE "userID" = p_user_id
          AND sensor_type IS NOT NULL
        ORDER BY sensor_type, COALESCE(sampled_at, created_at) DESC, id DESC
    ) latest_sensors;

    -- The same choice per canonical channel, with its own times and flags.
    SELECT jsonb_object_agg(channel, jsonb_build_object(
               'id', id,
               'sensor_type', sensor_type,
               'value', data1,
               'sampled_at', sampled_at,
               'ingested_at', created_at,
               'time', COALESCE(sampled_at, created_at),
               'time_basis', CASE WHEN sampled_at IS NOT NULL THEN 'sample' ELSE 'ingestion' END,
               'flags', flags)) INTO v_latest_readings
    FROM (
        SELECT DISTINCT ON (public.sensor_channel(sensor_type))
            public.sensor_channel(sensor_type) AS channel,
            id, sensor_type, data1, sampled_at, created_at, flags
        FROM public."SensorData"
        WHERE "userID" = p_user_id
          AND public.sensor_channel(sensor_type) IS NOT NULL
        ORDER BY public.sensor_channel(sensor_type), COALESCE(sampled_at, created_at) DESC, id DESC
    ) latest_channels;

    -- 3. FETCH CATEGORY 2: NEA Regional Telemetry (Extracted from nested data JSON)
    IF v_temp_stn IS NOT NULL THEN
        SELECT jsonb_build_object('value', data->'air_temperature') INTO v_nea_temp
        FROM public.weather_telemetry
        WHERE station_id = v_temp_stn AND data ? 'air_temperature'
        LIMIT 1;
    END IF;

    IF v_rain_stn IS NOT NULL THEN
        SELECT jsonb_build_object('value', data->'rainfall') INTO v_nea_rain
        FROM public.weather_telemetry
        WHERE station_id = v_rain_stn AND data ? 'rainfall'
        LIMIT 1;
    END IF;

    IF v_wind_stn IS NOT NULL THEN
        SELECT jsonb_build_object('value', data->'wind_speed') INTO v_nea_wind
        FROM public.weather_telemetry
        WHERE station_id = v_wind_stn AND data ? 'wind_speed'
        LIMIT 1;
    END IF;

    -- 4. FETCH CATEGORY 3: 2-Hour & 24-Hour Forecasts (Regional + General)
    IF v_slot_2hr IS NOT NULL THEN
        SELECT data INTO v_nea_2hr
        FROM public.weather_forecasts
        WHERE forecast_type = '2hr' AND slot_id = v_slot_2hr
        LIMIT 1;
    END IF;

    IF v_slot_24hr IS NOT NULL THEN
        -- Regional specific forecast
        SELECT data INTO v_nea_24hr_regional
        FROM public.weather_forecasts
        WHERE forecast_type = '24hr' AND slot_id = v_slot_24hr
        LIMIT 1;
    END IF;

    -- General 24-Hour forecast & overall validPeriod
    SELECT data INTO v_nea_24hr_general
    FROM public.weather_forecasts
    WHERE forecast_type = '24hr' AND slot_id = 'GENERAL'
    LIMIT 1;

    -- 5. FETCH CATEGORY 4: 4-Day Outlook (Top 4 most recent entries)
SELECT jsonb_agg(
    jsonb_build_object(
        'slot_id', slot_id,
        'data', CASE WHEN jsonb_typeof(data) = 'string' THEN data::jsonb ELSE data END,
        'valid_period', CASE WHEN jsonb_typeof(valid_period) = 'string' THEN valid_period::jsonb ELSE valid_period END
    )
) INTO v_nea_4day
FROM (
    SELECT slot_id, data, valid_period
    FROM (
        -- Step 1: Grab the 4 latest forecast records
        SELECT slot_id, data, valid_period, updated_at
        FROM public.weather_forecasts
        WHERE forecast_type = '4day'
        ORDER BY updated_at DESC
        LIMIT 4
    ) top_four
    -- Step 2: Flip the order so the entry closest to today is first
    ORDER BY (valid_period->>'timestamp')::timestamptz ASC
) four_day_sub;

-- 6. FETCH CATEGORY 5: Current UV Index with Nighttime Fallback
SELECT COALESCE(
  (
    SELECT jsonb_build_object(
      'slot_id', slot_id,
      'data', CASE
        WHEN jsonb_typeof(data) = 'string' THEN data::jsonb
        ELSE data
      END,
      'valid_period', CASE
        WHEN jsonb_typeof(valid_period) = 'string' THEN valid_period::jsonb
        ELSE valid_period
      END
    )
    FROM public.weather_forecasts
    WHERE forecast_type = 'uv'
      -- Format current SGT time directly to match slot_id (e.g., '15:00')
      AND slot_id = to_char(NOW() AT TIME ZONE 'Asia/Singapore', 'HH24:00')
    LIMIT 1
  ),
  -- Fallback returned automatically between 19:00 and 07:00 when no slot matches
  jsonb_build_object(
    'slot_id', null,
    'data', jsonb_build_object(
      'value', 0,
      'unit', 'UV Index',
      'status', 'Nighttime (Inactive)'
    ),
    'valid_period', jsonb_build_object(
      'timestamp', to_char(NOW() AT TIME ZONE 'Asia/Singapore', 'YYYY-MM-DD"T"HH24:MI:SS+08:00'),
      'is_daylight', false
    )
  )
) INTO v_nea_uv;


    -- 7. Bundle and return unified JSON payload
    RETURN jsonb_build_object(
        'raw_sensor', COALESCE(v_raw_sensor, '{}'::jsonb),
        'latest_readings', COALESCE(v_latest_readings, '{}'::jsonb),
        'telemetry_history', (
                SELECT COALESCE(jsonb_agg(
                    jsonb_build_object(
                        'time', bucket_time,
                        'ph', COALESCE(ph, 7.4),
                        'tds', COALESCE(tds, 180.0),
                        'tempC', COALESCE(temp_c, 26.0),
                        'lux', COALESCE(lux, 500.0),
                        'time_basis', time_basis
                    ) ORDER BY bucket_time ASC
                ), '[]'::jsonb)
                FROM public.get_pond_telemetry_history(p_user_id, 7)
            ),
        'nea_telemetry', jsonb_build_object(
            'air_temp', COALESCE(v_nea_temp, '{}'::jsonb),
            'rainfall', COALESCE(v_nea_rain, '{}'::jsonb),
            'wind_speed', COALESCE(v_nea_wind, '{}'::jsonb)
        ),
        'nea_forecasts', jsonb_build_object(
            'forecast_2hr', COALESCE(v_nea_2hr, '{}'::jsonb),
            'forecast_24hr', jsonb_build_object(
                'regional', COALESCE(v_nea_24hr_regional, '{}'::jsonb),
                'general', COALESCE(v_nea_24hr_general, '{}'::jsonb)
            ),
            'outlook_4day', COALESCE(v_nea_4day, '[]'::jsonb),
            'uv_index', COALESCE(v_nea_uv, '[]'::jsonb)
        )
    );
END;
$function$;

-- As in 0012, with sampled_at and flags, newest by reading time then id.
create or replace function public.pond_dashboard_sources(p_pond_id bigint)
returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
    with pond as (
        select u."ClosestStations"::jsonb as stations
        from public."UserData" u
        where u."userID" = p_pond_id
    ),
    assigned as (
        select s.value as station_id
        from pond p, jsonb_each_text(case when jsonb_typeof(p.stations) = 'object' then p.stations end) s
        where s.key in ('air-temperature', 'rainfall', 'wind-speed')
    )
    select jsonb_build_object(
        'pond_exists', exists (select 1 from pond),
        'stations', (select p.stations from pond p),
        'readings', coalesce((
            select jsonb_agg(jsonb_build_object(
                       'id', r.id, 'sensor_type', r.sensor_type, 'value', r.data1, 'created_at', r.created_at,
                       'sampled_at', r.sampled_at, 'flags', r.flags)
                   order by r.sensor_type)
            from (
                select distinct on (d.sensor_type) d.id, d.sensor_type, d.data1, d.created_at, d.sampled_at, d.flags
                from public."SensorData" d
                where d."userID" = p_pond_id and d.sensor_type is not null
                order by d.sensor_type, coalesce(d.sampled_at, d.created_at) desc, d.id desc
            ) r), '[]'::jsonb),
        'telemetry', coalesce((
            select jsonb_agg(jsonb_build_object(
                       'station_id', t.station_id, 'metric_type', t.metric_type, 'data', t.data,
                       'source_times', t.source_times, 'valid_start', t.valid_start, 'valid_end', t.valid_end,
                       'updated_at', t.updated_at)
                   order by t.station_id)
            from public.weather_telemetry t
            where t.metric_type = 'realtime_sensor'
              and t.station_id in (select a.station_id from assigned a)), '[]'::jsonb),
        'forecasts', coalesce((
            select jsonb_agg(jsonb_build_object(
                       'forecast_type', f.forecast_type, 'slot_id', f.slot_id, 'data', f.data,
                       'valid_period', f.valid_period, 'updated_at', f.updated_at,
                       'source_issued_at', f.source_issued_at)
                   order by f.forecast_type, f.slot_id)
            from public.weather_forecasts f
            left join pond p on true
            where (f.forecast_type = '2hr' and f.slot_id = p.stations ->> 'two-hr-forecast')
               or (f.forecast_type = '24hr'
                   and (f.slot_id = 'GENERAL' or f.slot_id = p.stations ->> 'twenty-four-hr-forecast'))
               or f.forecast_type in ('4day', 'uv')), '[]'::jsonb)
    );
$$;

-- As in 0014, with sampled_at and flags on each row.
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
        select d.id, d.sensor_type, d.data1, d.created_at, d.sampled_at, d.flags
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
                       'id', p.id, 'sensor_type', p.sensor_type, 'value', p.data1, 'created_at', p.created_at,
                       'sampled_at', p.sampled_at, 'flags', p.flags)
                   order by p.created_at, p.id)
            from pending p), '[]'::jsonb));
$$;

-- As in 0015, with sampled_at and flags on each row.
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
               'id', d.id, 'sensor_type', d.sensor_type, 'value', d.data1, 'created_at', d.created_at,
               'sampled_at', d.sampled_at, 'flags', d.flags)
           order by l.effective_sample_time, d.id), '[]'::jsonb)
    from public.sensor_ingest_ledger l
    join public."SensorData" d on d.id = l.sensor_row_id and d."userID" = p_pond_id
    where l.pond_id = p_pond_id
      and d.sensor_type = any(p_sensor_types)
      and (p_after is null or l.effective_sample_time > p_after)
      and l.effective_sample_time <= p_until;
$$;

-- Access -------------------------------------------------------------------
revoke all on function public.sensor_channel(text) from public, anon, authenticated;
revoke all on function public.aggregate_daily_sensor_data(date, date, bigint) from public, anon, authenticated;
revoke all on function public.reconcile_daily_sensor_averages(bigint, integer) from public, anon, authenticated;
revoke all on function public.get_pond_telemetry_history(bigint, integer) from public, anon, authenticated;
grant execute on function public.sensor_channel(text) to service_role;
grant execute on function public.aggregate_daily_sensor_data(date, date, bigint) to service_role;
grant execute on function public.reconcile_daily_sensor_averages(bigint, integer) to service_role;
grant execute on function public.get_pond_telemetry_history(bigint, integer) to service_role;
