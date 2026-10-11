-- 0026_least_privilege.sql
--
-- Least-privilege grants for the app, the sensor node and the backend
-- (issue #12). Apply only after the sensor node uploads through
-- ingest_readings (0025): this migration ends the node's direct insert.
-- The full role/object matrix, and what #116 adds once the app signs in
-- (#49), is in docs/access-matrix.md.
--
-- 1. Default privileges of the migration creator (postgres). New tables,
--    sequences and functions in public no longer grant anything to anon
--    or authenticated, and new functions created by postgres in any
--    schema are no longer executable by PUBLIC. service_role keeps its
--    defaults. Objects created by supabase_admin (the platform role that
--    owns extensions) still follow that role's defaults, which a
--    migration cannot change: create extensions in the extensions schema,
--    never in public.
--
-- 2. The http extension moves from public (an API schema, so every http_*
--    function was callable through /rest/v1/rpc with the publishable key)
--    to extensions, which PostgREST does not expose. Its functions there
--    keep the extension's own grants. Outbound HTTP from the database is
--    still needed: process_user_location_and_stations geocodes a postal
--    code through OneMap (moving that out of the trigger is #53).
--    Nothing else in the schema calls it.
--
-- 3. Functions. Every function that was callable by the app's roles now
--    has a fixed empty search_path and schema-qualified references:
--      get_historical_graph_payload   security definer (it refreshes
--          daily_sensor_averages, which the app may not write); refuses a
--          null pond and p_days outside 1..366 (22023). Still executable
--          by anon and authenticated for any pond id until #116 adds the
--          caller-ownership check.
--      get_bundled_dashboard_payload, get_pond_telemetry_history
--          now security invoker and backend only (the API reads the
--          dashboard as service_role; the app uses GET /v1/.../dashboard).
--      process_user_location_and_stations,
--      calculate_user_nearest_stations_trigger
--          trigger functions, still security definer so the app's
--          UserData writes can read WeatherStationLookup; triggers do not
--          need EXECUTE, so no role is granted it.
--      aggregate_daily_sensor_data, resolve_closest_station
--          internal, backend only.
--    The #8 lease and snapshot functions (take_worker_lease,
--    save_pond_snapshot, save_pond_snapshot_with_ingest) are revoked from
--    public, anon and authenticated again and explicitly granted to
--    service_role. link_pond_to_account, issue_device_token and
--    revoke_device_token stay owner only.
--
-- 4. Tables and sequences. Everything in public is revoked from public,
--    anon and authenticated, then the app's signed-out calls are granted
--    back to anon and authenticated (identical, so a signed-in session
--    keeps working until #116):
--      UserData            select, insert, update, delete (onboarding
--                          upsert, profile read, pond deletion)
--      pondInterventions   select, insert (event log)
--      Fish_Database       select (species list and profiles)
--      imageTable          select (camera history)
--      weather_telemetry,
--      weather_forecasts   select (shared public NEA caches, read through
--                          their existing "Allow public read access"
--                          policies)
--    plus usage on the UserData and pondInterventions identity
--    sequences. SensorData, daily_sensor_averages, the evaluation tables,
--    pond_chemistry_state, WeatherStationLookup, worker_lease and
--    worker_status become backend only. service_role grants are not
--    changed.
--
-- RLS is not enabled on any further table here (#116). Storage buckets and
-- the camera's POST /upload are #86 and unchanged.

-- 1. Default privileges ---------------------------------------------------
alter default privileges for role postgres in schema public revoke all on tables from anon, authenticated;
alter default privileges for role postgres in schema public revoke all on sequences from anon, authenticated;
alter default privileges for role postgres in schema public revoke all on functions from anon, authenticated;
alter default privileges for role postgres revoke execute on functions from public;
alter default privileges for role postgres in schema public grant all on tables to service_role;
alter default privileges for role postgres in schema public grant all on sequences to service_role;
alter default privileges for role postgres in schema public grant execute on functions to service_role;

-- 2. http out of the API schema ------------------------------------------
-- Dropped and recreated (the extension does not support SET SCHEMA). No
-- object depends on it; the trigger below is recreated to call it there.
drop extension if exists http;
create extension if not exists http with schema extensions;

-- 3. Functions ------------------------------------------------------------

-- As in 0001, with extensions.http_get / extensions.http_response and
-- public.resolve_closest_station qualified and search_path fixed.
CREATE OR REPLACE FUNCTION public.process_user_location_and_stations()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
DECLARE
    onemap_url TEXT;
    http_res extensions.http_response;
    res_json JSONB;
    temp_stn VARCHAR;
    rain_stn VARCHAR;
    wind_stn VARCHAR;
    area_stn VARCHAR;
    region_stn VARCHAR;
    u_lat DOUBLE PRECISION;
    u_lon DOUBLE PRECISION;
    postal_str varchar;
BEGIN
    -- -------------------------------------------------------------
    -- PATH 2 & 4: Postal code provided -> Override/Geocode via OneMap
    -- -------------------------------------------------------------
    IF NEW."manualpostallocation" IS NOT NULL AND NEW."manualpostallocation" > 0 THEN
        -- Convert numeric to text and pad with leading zero if needed (e.g. 89312 -> '089312')
        postal_str := LPAD(NEW."manualpostallocation"::BIGINT::TEXT, 6, '0');
    END IF;

    -- -------------------------------------------------------------
    -- PATH 2 & 4: Valid 6-Digit Postal Code -> Query OneMap API
    -- -------------------------------------------------------------
    IF postal_str IS NOT NULL AND LENGTH(postal_str) = 6 THEN
        onemap_url := 'https://www.onemap.gov.sg/api/common/elastic/search?searchVal=' 
                      || postal_str
                      || '&returnGeom=Y&getAddrDetails=Y&pageNum=1';
        
        -- Synchronous HTTP GET request to SLA OneMap API
        http_res := extensions.http_get(onemap_url);
        
        IF http_res.status = 200 THEN
            res_json := http_res.content::jsonb;
            
            -- Check if OneMap returned valid coordinates
            IF jsonb_array_length(res_json->'results') > 0 THEN
                NEW.latitude := (res_json->'results'->0->>'LATITUDE')::REAL;
                NEW.longitude := (res_json->'results'->0->>'LONGITUDE')::REAL;
            END IF;
        END IF;
    END IF;

    -- -------------------------------------------------------------
    -- PATH 1: GPS location provided directly (if postal code was empty)
    -- PATH 3: Handled by Flutter UI blocker (both NULL -> skips calculation)
    -- -------------------------------------------------------------
    IF NEW.latitude IS NULL OR NEW.longitude IS NULL THEN
        RETURN NEW;
    END IF;

    u_lat := NEW.latitude::DOUBLE PRECISION;
    u_lon := NEW.longitude::DOUBLE PRECISION;

    -- Execute Haversine station resolver
    SELECT station_id INTO temp_stn FROM public.resolve_closest_station(u_lat, u_lon, 'AirTemp');
    SELECT station_id INTO rain_stn FROM public.resolve_closest_station(u_lat, u_lon, 'Rainfall');
    SELECT station_id INTO wind_stn FROM public.resolve_closest_station(u_lat, u_lon, 'Wind');
    SELECT station_name INTO area_stn FROM public.resolve_closest_station(u_lat, u_lon, '2HourForecast');

    -- Resolve 24-hour regional bounding box
    IF u_lat > 1.385 THEN region_stn := 'REG_NORTH';
    ELSIF u_lat < 1.285 THEN region_stn := 'REG_SOUTH';
    ELSIF u_lon > 103.90 THEN region_stn := 'REG_EAST';
    ELSIF u_lon < 103.74 THEN region_stn := 'REG_WEST';
    ELSE region_stn := 'REG_CENTRAL';
    END IF;

    -- Construct JSON map
    NEW."ClosestStations" := jsonb_build_object(
        'air-temperature', temp_stn,
        'rainfall', rain_stn,
        'wind-speed', wind_stn,
        'two-hr-forecast', area_stn,
        'twenty-four-hr-forecast', region_stn
    );

    RETURN NEW;
END;
$function$;

-- As in 0001, with public.resolve_closest_station qualified and
-- search_path fixed.
CREATE OR REPLACE FUNCTION public.calculate_user_nearest_stations_trigger()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
DECLARE
    temp_stn VARCHAR;
    rain_stn VARCHAR;
    wind_stn VARCHAR;
    area_stn VARCHAR;
    region_stn VARCHAR;
    u_lat DOUBLE PRECISION;
    u_lon DOUBLE PRECISION;
BEGIN
    -- Check if coordinates are present
    IF NEW.latitude IS NULL OR NEW.longitude IS NULL THEN
        RETURN NEW;
    END IF;

    u_lat := NEW.latitude::DOUBLE PRECISION;
    u_lon := NEW.longitude::DOUBLE PRECISION;

    -- 1. Spatial distance resolution using resolve_closest_station
    SELECT station_id INTO temp_stn FROM public.resolve_closest_station(u_lat, u_lon, 'AirTemp');
    SELECT station_id INTO rain_stn FROM public.resolve_closest_station(u_lat, u_lon, 'Rainfall');
    SELECT station_id INTO wind_stn FROM public.resolve_closest_station(u_lat, u_lon, 'Wind');
    SELECT station_name INTO area_stn FROM public.resolve_closest_station(u_lat, u_lon, '2HourForecast');

    -- 2. Regional 24-hour bounding
    IF u_lat > 1.385 THEN region_stn := 'REG_NORTH';
    ELSIF u_lat < 1.285 THEN region_stn := 'REG_SOUTH';
    ELSIF u_lon > 103.90 THEN region_stn := 'REG_EAST';
    ELSIF u_lon < 103.74 THEN region_stn := 'REG_WEST';
    ELSE region_stn := 'REG_CENTRAL';
    END IF;

    -- 3. Construct and assign JSON map directly to NEW row
    NEW."ClosestStations" := jsonb_build_object(
        'air-temperature', temp_stn,
        'rainfall', rain_stn,
        'wind-speed', wind_stn,
        'two-hr-forecast', area_stn,
        'twenty-four-hr-forecast', region_stn
    );

    RETURN NEW;
END;
$function$;

-- As in 0019, with the input bounds, public.aggregate_daily_sensor_data
-- qualified and search_path fixed. Output unchanged.
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

    -- Refresh today's daily aggregates
    PERFORM public.aggregate_daily_sensor_data();

    -- A. Fetch Daily Sensor Averages for the last N days
    SELECT jsonb_agg(
        jsonb_build_object(
            'date', record_date,
            'sensor_type', sensor_type,
            'avg_value', avg_value,
            'min_value', min_value,
            'max_value', max_value,
            'after_maintenance', sensor_type IN ('TDS', 'tds', 'pH', 'ph') AND EXISTS (
                SELECT 1 FROM public."pondInterventions" i
                WHERE i."userID" = p_userid AND i.event_type = 'FILTER_CLEAN'
                  AND i.event_timestamp < ((record_date + 1)::timestamp AT TIME ZONE 'Asia/Singapore')
                  AND i.event_timestamp + interval '24 hours' > (record_date::timestamp AT TIME ZONE 'Asia/Singapore')
            )
        ) ORDER BY record_date ASC
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

-- As in 0001, security invoker, with public.get_pond_telemetry_history
-- qualified and search_path fixed. Output unchanged.
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

    -- 2. FETCH CATEGORY 1: Raw Sensor Data (Latest value per sensor_type)
    SELECT jsonb_object_agg(sensor_type, data1) INTO v_raw_sensor
    FROM (
        SELECT DISTINCT ON (sensor_type)
            sensor_type,
            data1
        FROM public."SensorData"
        WHERE "userID" = p_user_id
          AND sensor_type IS NOT NULL
        ORDER BY sensor_type, created_at DESC
    ) latest_sensors;

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
        'telemetry_history', (
                SELECT COALESCE(jsonb_agg(
                    jsonb_build_object(
                        'time', bucket_time,
                        'ph', COALESCE(ph, 7.4),
                        'tds', COALESCE(tds, 180.0),
                        'tempC', COALESCE(temp_c, 26.0),
                        'lux', COALESCE(lux, 500.0)
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

alter function public.get_pond_telemetry_history(bigint, integer) security invoker set search_path = '';
alter function public.aggregate_daily_sensor_data() set search_path = '';
alter function public.resolve_closest_station(double precision, double precision, text) set search_path = '';

revoke all on all functions in schema public from public, anon, authenticated;

grant execute on function public.get_historical_graph_payload(bigint, integer) to anon, authenticated, service_role;
grant execute on function public.ingest_readings(text, jsonb) to anon, service_role;
grant execute on function public.get_bundled_dashboard_payload(bigint) to service_role;
grant execute on function public.get_pond_telemetry_history(bigint, integer) to service_role;
grant execute on function public.aggregate_daily_sensor_data() to service_role;
grant execute on function public.resolve_closest_station(double precision, double precision, text) to service_role;
grant execute on function public.take_worker_lease(text, text, integer) to service_role;
grant execute on function public.save_pond_snapshot(bigint, jsonb, bigint) to service_role;
grant execute on function public.save_pond_snapshot_with_ingest(bigint, jsonb, bigint, jsonb) to service_role;

-- 4. Tables and sequences -------------------------------------------------
revoke all on all tables in schema public from public, anon, authenticated;
revoke all on all sequences in schema public from public, anon, authenticated;

grant select, insert, update, delete on table public."UserData" to anon, authenticated;
grant usage on sequence public."UserData_id_seq" to anon, authenticated;
grant select, insert on table public."pondInterventions" to anon, authenticated;
grant usage on sequence public.pondinterventions_id_seq to anon, authenticated;
grant select on table public."Fish_Database" to anon, authenticated;
grant select on table public."imageTable" to anon, authenticated;
grant select on table public.weather_telemetry, public.weather_forecasts to anon, authenticated;
