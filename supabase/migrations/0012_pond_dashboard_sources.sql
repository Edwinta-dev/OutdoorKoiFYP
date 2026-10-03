-- 0012_pond_dashboard_sources.sql
--
-- The source rows of GET /v1/ponds/{pond}/dashboard (issue #13).
--
-- get_bundled_dashboard_payload (0001) stays as it is: the app still
-- calls it until the mobile repositories issue moves the app to /v1, and
-- the poller still reads it. The versioned API does not wrap it, because
-- it cannot be made honest from outside:
--   - raw_sensor holds one value per sensor_type with no time, so a
--     channel that stopped reporting looks as current as one that did not;
--   - the NEA values are read from any weather_telemetry row of the
--     station that has the JSON key, whatever its metric_type;
--   - the 4-day outlook is "the 4 most recently written rows", not the
--     days from today;
--   - a missing UV hour reads as a measured night-time 0, even at noon;
--   - telemetry_history replaces missing readings with 7.4 / 180 / 26 /
--     500.
--
-- pond_dashboard_sources returns the rows themselves, with their times,
-- and leaves every interpretation (freshness, units, missing values,
-- which UV hour is "now", which outlook days are current) to one Python
-- normaliser, koi/api/dashboard.py, which MemoryStorage feeds from the
-- same tables. It never substitutes a value.
--
-- Result (jsonb):
--   pond_exists  boolean, the pond has a UserData row
--   stations     UserData."ClosestStations" (json) as jsonb, null when unset
--   readings     the newest SensorData row of each sensor_type of the
--                pond: [{id, sensor_type, value, created_at}], value is
--                data1 as stored. Newest = created_at desc, then id desc,
--                so two rows written in the same instant resolve the same
--                way every time. created_at is the database insert time;
--                the node sends no sample time yet (issue #17).
--   telemetry    weather_telemetry rows of the pond's assigned
--                air-temperature, rainfall and wind-speed stations with
--                metric_type 'realtime_sensor' (the ingestion job's
--                cache rows): [{station_id, metric_type, data,
--                source_times, valid_start, valid_end, updated_at}]. A row
--                of another metric_type, or of another station, is never
--                returned. Which metric of the row belongs to which
--                assignment is decided by the normaliser.
--   forecasts    weather_forecasts rows: '2hr' for the assigned area,
--                '24hr' for 'GENERAL' and the assigned region, every
--                '4day' row and every 'uv' row: [{forecast_type, slot_id,
--                data, valid_period, updated_at, source_issued_at}].
--                updated_at is when the cache row was written;
--                source_issued_at (0009) is the provider's issue time,
--                null for rows written before 0009.
--
-- Read only: stable, no writes, and unlike get_historical_graph_payload
-- it does not run aggregate_daily_sensor_data.
--
-- Cost: SensorData has no index on ("userID", sensor_type, created_at),
-- so the newest-per-type read scans the pond's rows, as
-- get_bundled_dashboard_payload already does. No index is added here
-- while the sensor sketch is live-tested: building one would lock the
-- table against the node's inserts.
--
-- Access: security invoker, service role only (the API's storage). The
-- app's anon and authenticated roles cannot execute it. SensorData's
-- grants, policies and triggers are unchanged.

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
                       'id', r.id, 'sensor_type', r.sensor_type, 'value', r.data1, 'created_at', r.created_at)
                   order by r.sensor_type)
            from (
                select distinct on (d.sensor_type) d.id, d.sensor_type, d.data1, d.created_at
                from public."SensorData" d
                where d."userID" = p_pond_id and d.sensor_type is not null
                order by d.sensor_type, d.created_at desc, d.id desc
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

revoke all on function public.pond_dashboard_sources(bigint) from public, anon, authenticated;
grant execute on function public.pond_dashboard_sources(bigint) to service_role;
