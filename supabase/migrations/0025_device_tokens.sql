-- 0025_device_tokens.sql
--
-- Per-device tokens and token-scoped sensor ingest (issue #12).
--
-- Until now the sensor node inserted SensorData rows directly with the
-- publishable (anon) key, so anyone holding that key could write readings
-- for any pond. This migration adds the replacement path; 0026 then
-- revokes the direct insert. Apply this one first, flash the node with the
-- token build, check rows arrive, and only then apply 0026 (rollout order
-- in docs/decisions/0012-least-privilege-access.md).
--
-- devices (0018) gains, all defaulted or nullable, so existing rows and
-- its readers (record_device_contacts, pond_device_activity,
-- GET /v1/ponds/{pond}/devices) are unchanged:
--   device_id                 uuid, unique, generated for every row
--   revoked_at                set by the owner to refuse every token of
--                             the device; null means active
--   ingest_window_started_at  start of the current rate-limit window
--   ingest_window_batches     batches accepted in that window
--
-- device_tokens, one row per token issued (several per device while a
-- rotation overlaps):
--   device_id      devices.device_id
--   token_hash     sha256 of the token text; the token itself is never
--                  stored and is shown once, by issue_device_token
--   label          optional note ("sensor node, flashed 2026-10-12")
--   created_at     when issued
--   expires_at     null while the token is the newest; set to the end of
--                  the overlap when a newer token is issued
--   revoked_at     set by revoke_device_token; null means not revoked
--   last_used_at   newest accepted ingest_readings call
-- A token is accepted while revoked_at is null, expires_at is null or in
-- the future, and its device has revoked_at null and kind 'sensor'.
--
-- issue_device_token(p_pond_id, p_overlap default 24 hours, p_label)
--   returns text
--   Owner only (SQL editor). Creates the pond's sensor devices row if it
--   has none, sets expires_at = now() + p_overlap on the device's tokens
--   that are still valid (rotation: the old token keeps working for the
--   overlap, so the node can be reflashed), stores the hash of a new
--   random token (32 bytes from pgcrypto, 64 hex characters) and returns
--   the token. p_overlap is 0 to 30 days. Refuses a pond not in UserData
--   and a revoked device.
--
-- revoke_device_token(p_token_id) returns boolean
--   Owner only (SQL editor). Sets revoked_at on one token; true when it
--   was not already revoked. To refuse a whole device:
--   update public.devices set revoked_at = now() where pond_id = ... and kind = 'sensor';
--
-- ingest_readings(device_token text, rows jsonb) returns jsonb
--   The sensor node's write path, called through PostgREST
--   (POST /rest/v1/rpc/ingest_readings) with the publishable key.
--   Security definer, so the caller needs no SensorData privilege.
--   1. The token must match an accepted token (above); otherwise
--      PT401 (HTTP 401), with the same message whether the token is
--      unknown, expired, revoked or its device revoked.
--   2. At most 6 accepted batches per device in a 60 second window;
--      the next one gets PT429 (HTTP 429) until the window ends.
--      Refused calls are not counted.
--   3. rows must be a JSON array of 1 to 32 objects (else 22023, or
--      PT413 for more than 32). Each object may hold only sensor_type
--      and data1 (the "v1" row shape the node has always sent). Any
--      other key, including userID, pond_id, id and created_at, refuses
--      the whole batch (22023): the pond is always the token's device's
--      pond and the time is the database's insert time.
--   4. Each row is then accepted or rejected on its own. Rejected rows
--      are listed in the reply with a reason and are not stored:
--        unknown_sensor_type  not one of temp, TDS, pH, LUX, battery_mv,
--                             reset_reason
--        not_a_number         data1 missing, null (the node's NaN) or
--                             not a JSON number
--        out_of_range         outside the channel's range below
--        duplicate            a second row of the same sensor_type
--   5. Accepted rows are inserted into SensorData with userID = the
--      device's pond and created_at = now(); the reply is
--      {accepted, rejected: [{index, sensor_type, reason}], format: 'v1',
--       received_at}. index is the row's 0-based position.
--   Ranges (inclusive): temp -10..60 C, pH 0..14, TDS 0..5000 ppm,
--   LUX 0..200000 lx, battery_mv 0..20000 mV, reset_reason an integer
--   0..255.
--
-- v1 compatibility and its sunset: the row shape accepted here is the one
-- the node already sends, so only the request wrapper changes. When #17
-- adds a sample time it adds an optional field to the accepted set; once
-- every node runs that firmware a later migration may make it required
-- and refuse v1 rows. The direct anon insert into SensorData (the old v1
-- path) ends with 0026.
--
-- Access: devices and device_contact stay backend only (0018).
-- device_tokens has RLS on, no policies, and is revoked from public, anon
-- and authenticated; service_role may read and write it. ingest_readings
-- is executable by anon (the node) and service_role; the two token
-- functions by the owner (postgres) only.

alter table public.devices
    add column if not exists device_id uuid not null default gen_random_uuid(),
    add column if not exists revoked_at timestamptz,
    add column if not exists ingest_window_started_at timestamptz,
    add column if not exists ingest_window_batches integer not null default 0
        check (ingest_window_batches >= 0);

alter table public.devices add constraint devices_device_id_key unique (device_id);

create table public.device_tokens (
    id           bigint generated by default as identity primary key,
    device_id    uuid not null references public.devices (device_id) on delete cascade,
    token_hash   bytea not null unique check (octet_length(token_hash) = 32),
    label        text check (label is null or length(label) <= 200),
    created_at   timestamptz not null default now(),
    expires_at   timestamptz,
    revoked_at   timestamptz,
    last_used_at timestamptz
);
create index device_tokens_device on public.device_tokens (device_id);

alter table public.device_tokens enable row level security;
revoke all on public.device_tokens from public, anon, authenticated;
grant select, insert, update, delete on public.device_tokens to service_role;
revoke all on sequence public.device_tokens_id_seq from public, anon, authenticated;
grant usage, select on sequence public.device_tokens_id_seq to service_role;


create or replace function public.issue_device_token(
    p_pond_id bigint,
    p_overlap interval default interval '24 hours',
    p_label text default null
)
returns text
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_device public.devices;
    v_token text;
begin
    if p_overlap is null or p_overlap < interval '0' or p_overlap > interval '30 days' then
        raise exception 'issue_device_token: overlap must be between 0 and 30 days'
            using errcode = '22023';
    end if;
    if not exists (select 1 from public."UserData" u where u."userID" = p_pond_id) then
        raise exception 'issue_device_token: pond % is not in UserData', p_pond_id
            using errcode = '23503';
    end if;

    insert into public.devices (pond_id, kind) values (p_pond_id, 'sensor')
    on conflict (pond_id, kind) do nothing;
    select d.* into v_device from public.devices d
    where d.pond_id = p_pond_id and d.kind = 'sensor'
    for update;
    if v_device.revoked_at is not null then
        raise exception 'issue_device_token: the sensor device of pond % is revoked; clear devices.revoked_at first',
            p_pond_id using errcode = '55000';
    end if;

    -- Rotation: tokens still valid now stop at the end of the overlap.
    update public.device_tokens t
    set expires_at = least(coalesce(t.expires_at, 'infinity'::timestamptz), now() + p_overlap)
    where t.device_id = v_device.device_id
      and t.revoked_at is null
      and (t.expires_at is null or t.expires_at > now());

    v_token := pg_catalog.encode(extensions.gen_random_bytes(32), 'hex');
    insert into public.device_tokens (device_id, token_hash, label)
    values (v_device.device_id, pg_catalog.sha256(pg_catalog.convert_to(v_token, 'UTF8')), p_label);
    return v_token;
end;
$$;

create or replace function public.revoke_device_token(p_token_id bigint)
returns boolean
language sql
security invoker
set search_path = ''
as $$
    with revoked as (
        update public.device_tokens t set revoked_at = now()
        where t.id = p_token_id and t.revoked_at is null
        returning 1
    )
    select exists (select 1 from revoked);
$$;


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
    v_now      timestamptz := now();
    v_token_id bigint;
    v_device   public.devices;
    v_item     jsonb;
    v_index    integer;
    v_type     text;
    v_value    numeric;
    v_reason   text;
    v_bad_key  text;
    v_seen     text[] := '{}';
    v_accept   jsonb := '[]'::jsonb;
    v_reject   jsonb := '[]'::jsonb;
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
        where k not in ('sensor_type', 'data1') order by k limit 1;
        if v_bad_key is not null then
            raise exception 'ingest_readings: row % has field "%", only sensor_type and data1 are accepted '
                            '(the pond comes from the device token)', v_index, v_bad_key
                using errcode = '22023';
        end if;
    end loop;

    -- 4. Each row.
    for v_item, v_index in
        select e.value, (e.ordinality - 1)::integer
        from jsonb_array_elements(ingest_readings.rows) with ordinality e
    loop
        v_type := case when jsonb_typeof(v_item -> 'sensor_type') = 'string' then v_item ->> 'sensor_type' end;
        v_value := case when jsonb_typeof(v_item -> 'data1') = 'number' then (v_item ->> 'data1')::numeric end;
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
            when v_type = any (v_seen) then 'duplicate'
        end;
        if v_reason is null then
            v_seen := v_seen || v_type;
            v_accept := v_accept || jsonb_build_object('sensor_type', v_type, 'data1', v_value);
        else
            v_reject := v_reject || jsonb_build_object(
                'index', v_index, 'sensor_type', v_item -> 'sensor_type', 'reason', v_reason);
        end if;
    end loop;

    -- 5. Store.
    insert into public."SensorData" (created_at, sensor_type, data1, "userID")
    select v_now, a ->> 'sensor_type', (a ->> 'data1')::real, v_device.pond_id
    from jsonb_array_elements(v_accept) a;

    update public.devices d
    set ingest_window_started_at = v_device.ingest_window_started_at,
        ingest_window_batches = v_device.ingest_window_batches + 1
    where d.device_id = v_device.device_id;
    update public.device_tokens t set last_used_at = v_now where t.id = v_token_id;

    return jsonb_build_object(
        'accepted', jsonb_array_length(v_accept),
        'rejected', v_reject,
        'format', 'v1',
        'received_at', v_now);
end;
$$;

revoke all on function public.issue_device_token(bigint, interval, text) from public, anon, authenticated, service_role;
revoke all on function public.revoke_device_token(bigint) from public, anon, authenticated, service_role;
revoke all on function public.ingest_readings(text, jsonb) from public, anon, authenticated;
grant execute on function public.ingest_readings(text, jsonb) to anon, service_role;
