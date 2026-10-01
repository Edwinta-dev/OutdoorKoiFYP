


SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;


CREATE EXTENSION IF NOT EXISTS "pg_cron" WITH SCHEMA "pg_catalog";






CREATE EXTENSION IF NOT EXISTS "pg_net" WITH SCHEMA "extensions";






COMMENT ON SCHEMA "public" IS 'standard public schema';



CREATE EXTENSION IF NOT EXISTS "http" WITH SCHEMA "public";






CREATE EXTENSION IF NOT EXISTS "pg_stat_statements" WITH SCHEMA "extensions";






CREATE EXTENSION IF NOT EXISTS "pgcrypto" WITH SCHEMA "extensions";






CREATE EXTENSION IF NOT EXISTS "supabase_vault" WITH SCHEMA "vault";






CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA "extensions";






CREATE TYPE "public"."Region" AS ENUM (
    '"North"',
    '"South"',
    '"East"',
    '"West"',
    '"Central"'
);


ALTER TYPE "public"."Region" OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."aggregate_daily_sensor_data"() RETURNS "void"
    LANGUAGE "plpgsql"
    AS $$
BEGIN
    INSERT INTO public.daily_sensor_averages (userID, sensor_type, avg_value, min_value, max_value, record_date)
    SELECT 
        "userID" AS userID,
        sensor_type,
        -- Round output safely as numeric to avoid Postgres code 42883
        ROUND(AVG(data1)::numeric, 2) AS avg_value,
        ROUND(MIN(data1)::numeric, 2) AS min_value,
        ROUND(MAX(data1)::numeric, 2) AS max_value,
        (created_at AT TIME ZONE 'Asia/Singapore')::date AS record_date
    FROM public."SensorData"
    WHERE sensor_type IS NOT NULL
      -- Filter out pitch-black nighttime 0 lx readings for LUX calculations
      AND (sensor_type != 'LUX' OR data1 > 50.0)
    GROUP BY "userID", sensor_type, (created_at AT TIME ZONE 'Asia/Singapore')::date
    ON CONFLICT (userID, sensor_type, record_date) 
    DO UPDATE SET 
        avg_value = EXCLUDED.avg_value,
        min_value = EXCLUDED.min_value,
        max_value = EXCLUDED.max_value,
        created_at = NOW();
END;
$$;


ALTER FUNCTION "public"."aggregate_daily_sensor_data"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."calculate_user_nearest_stations_trigger"() RETURNS "trigger"
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
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
    SELECT station_id INTO temp_stn FROM resolve_closest_station(u_lat, u_lon, 'AirTemp');
    SELECT station_id INTO rain_stn FROM resolve_closest_station(u_lat, u_lon, 'Rainfall');
    SELECT station_id INTO wind_stn FROM resolve_closest_station(u_lat, u_lon, 'Wind');
    SELECT station_name INTO area_stn FROM resolve_closest_station(u_lat, u_lon, '2HourForecast');

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
$$;


ALTER FUNCTION "public"."calculate_user_nearest_stations_trigger"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."get_bundled_dashboard_payload"("p_user_id" bigint) RETURNS "jsonb"
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
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
                FROM get_pond_telemetry_history(p_user_id, 7)
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
$$;


ALTER FUNCTION "public"."get_bundled_dashboard_payload"("p_user_id" bigint) OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."get_historical_graph_payload"("p_userid" bigint, "p_days" integer DEFAULT 30) RETURNS "jsonb"
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
DECLARE
    v_daily_trends JSONB;
    v_interventions JSONB;
    v_max_wc_id BIGINT;
BEGIN
    -- Refresh today's daily aggregates
    PERFORM aggregate_daily_sensor_data();

    -- A. Fetch Daily Sensor Averages for the last N days
    SELECT jsonb_agg(
        jsonb_build_object(
            'date', record_date,
            'sensor_type', sensor_type,
            'avg_value', avg_value,
            'min_value', min_value,
            'max_value', max_value
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

    -- C. Fetch All Intervention Events in period
    SELECT jsonb_agg(
        jsonb_build_object(
            'id', id,
            'event_type', event_type,
            'timestamp', event_timestamp,
            'pct', volume_percentage,
            'litres', volume_litres,
            'food_grams', food_grams,
            'is_major_reset', (id = v_max_wc_id)
        ) ORDER BY event_timestamp ASC
    ) INTO v_interventions
    FROM public."pondInterventions"
    WHERE "userID" = p_userID
      AND event_timestamp >= NOW() - (p_days || ' days')::INTERVAL;

    RETURN jsonb_build_object(
        'daily_trends', COALESCE(v_daily_trends, '[]'::jsonb),
        'interventions', COALESCE(v_interventions, '[]'::jsonb)
    );
END;
$$;


ALTER FUNCTION "public"."get_historical_graph_payload"("p_userid" bigint, "p_days" integer) OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."get_pond_telemetry_history"("p_user_id" bigint, "p_days" integer DEFAULT 7) RETURNS TABLE("bucket_time" timestamp with time zone, "ph" numeric, "tds" numeric, "temp_c" numeric, "lux" numeric)
    LANGUAGE "sql" SECURITY DEFINER
    AS $$
  WITH hourly_buckets AS (
    SELECT 
      date_trunc('hour', created_at AT TIME ZONE 'UTC') AS bucket_time,
      sensor_type,
      AVG(data1)::numeric AS avg_val
    FROM public."SensorData"
    WHERE "userID" = p_user_id
      AND created_at >= NOW() - (p_days || ' days')::INTERVAL
      AND sensor_type IN ('pH', 'TDS', 'temp', 'LUX')
    GROUP BY 1, 2
  )
  SELECT 
    bucket_time,
    ROUND(MAX(CASE WHEN sensor_type = 'pH'   THEN avg_val END), 2) AS ph,
    ROUND(MAX(CASE WHEN sensor_type = 'TDS'  THEN avg_val END), 2) AS tds,
    ROUND(MAX(CASE WHEN sensor_type = 'temp' THEN avg_val END), 2) AS temp_c,
    ROUND(MAX(CASE WHEN sensor_type = 'LUX'  THEN avg_val END), 2) AS lux
  FROM hourly_buckets
  GROUP BY bucket_time
  -- Only return hours where at least pH and TDS were recorded
  HAVING MAX(CASE WHEN sensor_type = 'pH' THEN avg_val END) IS NOT NULL
     AND MAX(CASE WHEN sensor_type = 'TDS' THEN avg_val END) IS NOT NULL
  ORDER BY bucket_time ASC;
$$;


ALTER FUNCTION "public"."get_pond_telemetry_history"("p_user_id" bigint, "p_days" integer) OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."process_user_location_and_stations"() RETURNS "trigger"
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
DECLARE
    onemap_url TEXT;
    http_res http_response;
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
        http_res := http_get(onemap_url);
        
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
    SELECT station_id INTO temp_stn FROM resolve_closest_station(u_lat, u_lon, 'AirTemp');
    SELECT station_id INTO rain_stn FROM resolve_closest_station(u_lat, u_lon, 'Rainfall');
    SELECT station_id INTO wind_stn FROM resolve_closest_station(u_lat, u_lon, 'Wind');
    SELECT station_name INTO area_stn FROM resolve_closest_station(u_lat, u_lon, '2HourForecast');

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
$$;


ALTER FUNCTION "public"."process_user_location_and_stations"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."resolve_closest_station"("user_lat" double precision, "user_lon" double precision, "target_measurement" "text") RETURNS TABLE("station_id" "text", "station_name" "text", "distance_km" double precision)
    LANGUAGE "plpgsql"
    AS $$
BEGIN
    RETURN QUERY
    SELECT 
        s.station__id,
        s.station__name,
        -- Haversine formula for straight-line distance in kilometers
        (6371 * acos(
            LEAST(1.0, GREATEST(-1.0, 
                cos(radians(user_lat)) * cos(radians(s.location__latitude)) * 
                cos(radians(s.location__longitude) - radians(user_lon)) + 
                sin(radians(user_lat)) * sin(radians(s.location__latitude))
            ))
        )) AS distance_km
    FROM public."WeatherStationLookup" s
    -- Checks if target_measurement exists in the JSONB / Array column
    WHERE s."Measurement"::jsonb ? target_measurement
    ORDER BY distance_km ASC
    LIMIT 1;
END;
$$;


ALTER FUNCTION "public"."resolve_closest_station"("user_lat" double precision, "user_lon" double precision, "target_measurement" "text") OWNER TO "postgres";

SET default_tablespace = '';

SET default_table_access_method = "heap";


CREATE TABLE IF NOT EXISTS "public"."Fish_Database" (
    "Title" "text" NOT NULL,
    "Image URL" "text",
    "Common Name" "text",
    "Care Level" "text",
    "Maximum Size" "text",
    "pH" "text",
    "Temperature" "text",
    "Life Span" "text",
    "Behaviour" "text",
    "Tank Region" "text",
    "Gender" "text"
);


ALTER TABLE "public"."Fish_Database" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."SensorData" (
    "id" bigint NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "sensor_type" "text",
    "data1" real,
    "userID" bigint
);


ALTER TABLE "public"."SensorData" OWNER TO "postgres";


COMMENT ON TABLE "public"."SensorData" IS 'Data from ESP32 temp sensor';



ALTER TABLE "public"."SensorData" ALTER COLUMN "id" ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME "public"."TempData_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);



CREATE TABLE IF NOT EXISTS "public"."UserData" (
    "userID" bigint NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "volume" bigint,
    "biomass" numeric,
    "region" "text",
    "longitude" real,
    "latitude" real,
    "manualpostallocation" numeric,
    "ClosestStations" json
);


ALTER TABLE "public"."UserData" OWNER TO "postgres";


ALTER TABLE "public"."UserData" ALTER COLUMN "userID" ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME "public"."UserData_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);



CREATE TABLE IF NOT EXISTS "public"."WeatherStationLookup" (
    "Id" bigint NOT NULL,
    "station__id" "text",
    "station__name" "text",
    "location__latitude" double precision,
    "location__longitude" double precision,
    "Measurement" "jsonb"
);


ALTER TABLE "public"."WeatherStationLookup" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."daily_sensor_averages" (
    "id" bigint NOT NULL,
    "userid" bigint NOT NULL,
    "sensor_type" "text" NOT NULL,
    "avg_value" numeric NOT NULL,
    "min_value" numeric NOT NULL,
    "max_value" numeric NOT NULL,
    "record_date" "date" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."daily_sensor_averages" OWNER TO "postgres";


ALTER TABLE "public"."daily_sensor_averages" ALTER COLUMN "id" ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME "public"."daily_sensor_averages_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);



CREATE TABLE IF NOT EXISTS "public"."imageTable" (
    "id" bigint NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "green_ratio" real,
    "current_state" "text",
    "user_ID" integer,
    "imageURL" "text"
);


ALTER TABLE "public"."imageTable" OWNER TO "postgres";


ALTER TABLE "public"."imageTable" ALTER COLUMN "id" ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME "public"."imageTable_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);



CREATE TABLE IF NOT EXISTS "public"."pondInterventions" (
    "id" bigint NOT NULL,
    "userID" bigint NOT NULL,
    "event_type" "text" NOT NULL,
    "volume_percentage" numeric,
    "volume_litres" numeric,
    "food_grams" numeric,
    "protein_percentage" numeric,
    "algae_method" "text",
    "event_timestamp" timestamp with time zone DEFAULT "now"() NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."pondInterventions" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."pond_algae_evaluations" (
    "id" bigint NOT NULL,
    "userid" bigint NOT NULL,
    "status" "text" NOT NULL,
    "category" "text" NOT NULL,
    "green_ratio" numeric,
    "watch_threshold" numeric,
    "action_threshold" numeric,
    "threshold_mode" "text",
    "growth_rate_per_day" numeric,
    "intrinsic_rate_per_day" numeric,
    "rate_source" "text",
    "confidence" "text",
    "sample_count" integer,
    "days_to_scrub" integer,
    "advisory" "text",
    "scrub_now" boolean DEFAULT false NOT NULL,
    "evaluated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."pond_algae_evaluations" OWNER TO "postgres";


ALTER TABLE "public"."pond_algae_evaluations" ALTER COLUMN "id" ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME "public"."pond_algae_evaluations_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);



CREATE TABLE IF NOT EXISTS "public"."pond_chemistry_evaluations" (
    "id" bigint NOT NULL,
    "userid" bigint NOT NULL,
    "status" "text" NOT NULL,
    "category" "text" NOT NULL,
    "tan_ppm" numeric NOT NULL,
    "no2_ppm" numeric NOT NULL,
    "no3_ppm" numeric NOT NULL,
    "ph_reactivity" numeric,
    "reactivity_trend" numeric,
    "tds_trend" numeric,
    "sensor_warnings" "jsonb" DEFAULT '[]'::"jsonb",
    "advisory" "text" NOT NULL,
    "add_hardener_now" boolean DEFAULT false NOT NULL,
    "evaluated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."pond_chemistry_evaluations" OWNER TO "postgres";


ALTER TABLE "public"."pond_chemistry_evaluations" ALTER COLUMN "id" ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME "public"."pond_chemistry_evaluations_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);



CREATE TABLE IF NOT EXISTS "public"."pond_chemistry_state" (
    "user_id" bigint NOT NULL,
    "snapshot" "jsonb" NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."pond_chemistry_state" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."pond_evaporation_evaluations" (
    "id" bigint NOT NULL,
    "userid" bigint NOT NULL,
    "status" "text" NOT NULL,
    "category" "text" NOT NULL,
    "loss_litres" numeric,
    "loss_pct" numeric,
    "evaporation_mm_per_day" numeric,
    "loss_litres_per_day" numeric,
    "water_temp_c" numeric,
    "feed_cap_grams" numeric,
    "feed_note" "text",
    "days_to_topup" integer,
    "advisory" "text",
    "topup_now" boolean DEFAULT false NOT NULL,
    "evaluated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."pond_evaporation_evaluations" OWNER TO "postgres";


ALTER TABLE "public"."pond_evaporation_evaluations" ALTER COLUMN "id" ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME "public"."pond_evaporation_evaluations_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);



ALTER TABLE "public"."pondInterventions" ALTER COLUMN "id" ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME "public"."pondinterventions_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);



CREATE TABLE IF NOT EXISTS "public"."weather_forecasts" (
    "forecast_type" character varying(20) NOT NULL,
    "slot_id" character varying(50) NOT NULL,
    "data" "jsonb" NOT NULL,
    "valid_period" "jsonb",
    "updated_at" timestamp with time zone DEFAULT "now"()
);


ALTER TABLE "public"."weather_forecasts" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."weather_telemetry" (
    "station_id" character varying(100) NOT NULL,
    "metric_type" character varying(50) NOT NULL,
    "data" "jsonb" NOT NULL,
    "valid_start" timestamp with time zone NOT NULL,
    "valid_end" timestamp with time zone NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"()
);


ALTER TABLE "public"."weather_telemetry" OWNER TO "postgres";


ALTER TABLE ONLY "public"."Fish_Database"
    ADD CONSTRAINT "Fish_Database_pkey" PRIMARY KEY ("Title");



ALTER TABLE ONLY "public"."SensorData"
    ADD CONSTRAINT "TempData_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."UserData"
    ADD CONSTRAINT "UserData_pkey" PRIMARY KEY ("userID");



ALTER TABLE ONLY "public"."WeatherStationLookup"
    ADD CONSTRAINT "WeatherStationLookup_pkey" PRIMARY KEY ("Id");



ALTER TABLE ONLY "public"."daily_sensor_averages"
    ADD CONSTRAINT "daily_sensor_averages_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."imageTable"
    ADD CONSTRAINT "imageTable_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."pond_algae_evaluations"
    ADD CONSTRAINT "pond_algae_evaluations_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."pond_chemistry_evaluations"
    ADD CONSTRAINT "pond_chemistry_evaluations_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."pond_chemistry_state"
    ADD CONSTRAINT "pond_chemistry_state_pkey" PRIMARY KEY ("user_id");



ALTER TABLE ONLY "public"."pond_evaporation_evaluations"
    ADD CONSTRAINT "pond_evaporation_evaluations_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."pondInterventions"
    ADD CONSTRAINT "pondinterventions_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."daily_sensor_averages"
    ADD CONSTRAINT "unique_user_sensor_date" UNIQUE ("userid", "sensor_type", "record_date");



ALTER TABLE ONLY "public"."weather_forecasts"
    ADD CONSTRAINT "weather_forecasts_pkey" PRIMARY KEY ("forecast_type", "slot_id");



ALTER TABLE ONLY "public"."weather_telemetry"
    ADD CONSTRAINT "weather_telemetry_pkey" PRIMARY KEY ("station_id", "metric_type");



CREATE INDEX "idx_algae_eval_user_time" ON "public"."pond_algae_evaluations" USING "btree" ("userid", "evaluated_at" DESC);



CREATE INDEX "idx_chem_eval_user_time" ON "public"."pond_chemistry_evaluations" USING "btree" ("userid", "evaluated_at" DESC);



CREATE INDEX "idx_daily_sensor_lookup" ON "public"."daily_sensor_averages" USING "btree" ("userid", "sensor_type", "record_date");



CREATE INDEX "idx_evap_eval_user_time" ON "public"."pond_evaporation_evaluations" USING "btree" ("userid", "evaluated_at" DESC);



CREATE INDEX "idx_imagetable_user_time" ON "public"."imageTable" USING "btree" ("user_ID", "created_at" DESC);



CREATE INDEX "idx_pond_chemistry_user" ON "public"."pond_chemistry_evaluations" USING "btree" ("userid", "evaluated_at" DESC);



CREATE OR REPLACE TRIGGER "trigger_auto_assign_stations" BEFORE INSERT OR UPDATE OF "latitude", "longitude" ON "public"."UserData" FOR EACH ROW EXECUTE FUNCTION "public"."calculate_user_nearest_stations_trigger"();



CREATE OR REPLACE TRIGGER "trigger_process_user_location" BEFORE INSERT OR UPDATE OF "latitude", "longitude", "manualpostallocation" ON "public"."UserData" FOR EACH ROW EXECUTE FUNCTION "public"."process_user_location_and_stations"();



CREATE POLICY "Allow public read access" ON "public"."weather_forecasts" FOR SELECT USING (true);



CREATE POLICY "Allow public read access" ON "public"."weather_telemetry" FOR SELECT USING (true);



ALTER TABLE "public"."WeatherStationLookup" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."weather_forecasts" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."weather_telemetry" ENABLE ROW LEVEL SECURITY;




ALTER PUBLICATION "supabase_realtime" OWNER TO "postgres";


ALTER PUBLICATION "supabase_realtime" ADD TABLE ONLY "public"."SensorData";









GRANT USAGE ON SCHEMA "public" TO "postgres";
GRANT USAGE ON SCHEMA "public" TO "anon";
GRANT USAGE ON SCHEMA "public" TO "authenticated";
GRANT USAGE ON SCHEMA "public" TO "service_role";











































































































































































GRANT ALL ON FUNCTION "public"."aggregate_daily_sensor_data"() TO "anon";
GRANT ALL ON FUNCTION "public"."aggregate_daily_sensor_data"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."aggregate_daily_sensor_data"() TO "service_role";



GRANT ALL ON FUNCTION "public"."bytea_to_text"("data" "bytea") TO "postgres";
GRANT ALL ON FUNCTION "public"."bytea_to_text"("data" "bytea") TO "anon";
GRANT ALL ON FUNCTION "public"."bytea_to_text"("data" "bytea") TO "authenticated";
GRANT ALL ON FUNCTION "public"."bytea_to_text"("data" "bytea") TO "service_role";



GRANT ALL ON FUNCTION "public"."calculate_user_nearest_stations_trigger"() TO "anon";
GRANT ALL ON FUNCTION "public"."calculate_user_nearest_stations_trigger"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."calculate_user_nearest_stations_trigger"() TO "service_role";



GRANT ALL ON FUNCTION "public"."get_bundled_dashboard_payload"("p_user_id" bigint) TO "anon";
GRANT ALL ON FUNCTION "public"."get_bundled_dashboard_payload"("p_user_id" bigint) TO "authenticated";
GRANT ALL ON FUNCTION "public"."get_bundled_dashboard_payload"("p_user_id" bigint) TO "service_role";



GRANT ALL ON FUNCTION "public"."get_historical_graph_payload"("p_userid" bigint, "p_days" integer) TO "anon";
GRANT ALL ON FUNCTION "public"."get_historical_graph_payload"("p_userid" bigint, "p_days" integer) TO "authenticated";
GRANT ALL ON FUNCTION "public"."get_historical_graph_payload"("p_userid" bigint, "p_days" integer) TO "service_role";



GRANT ALL ON FUNCTION "public"."get_pond_telemetry_history"("p_user_id" bigint, "p_days" integer) TO "anon";
GRANT ALL ON FUNCTION "public"."get_pond_telemetry_history"("p_user_id" bigint, "p_days" integer) TO "authenticated";
GRANT ALL ON FUNCTION "public"."get_pond_telemetry_history"("p_user_id" bigint, "p_days" integer) TO "service_role";



GRANT ALL ON FUNCTION "public"."http"("request" "public"."http_request") TO "postgres";
GRANT ALL ON FUNCTION "public"."http"("request" "public"."http_request") TO "anon";
GRANT ALL ON FUNCTION "public"."http"("request" "public"."http_request") TO "authenticated";
GRANT ALL ON FUNCTION "public"."http"("request" "public"."http_request") TO "service_role";



GRANT ALL ON FUNCTION "public"."http_delete"("uri" character varying) TO "postgres";
GRANT ALL ON FUNCTION "public"."http_delete"("uri" character varying) TO "anon";
GRANT ALL ON FUNCTION "public"."http_delete"("uri" character varying) TO "authenticated";
GRANT ALL ON FUNCTION "public"."http_delete"("uri" character varying) TO "service_role";



GRANT ALL ON FUNCTION "public"."http_delete"("uri" character varying, "content" character varying, "content_type" character varying) TO "postgres";
GRANT ALL ON FUNCTION "public"."http_delete"("uri" character varying, "content" character varying, "content_type" character varying) TO "anon";
GRANT ALL ON FUNCTION "public"."http_delete"("uri" character varying, "content" character varying, "content_type" character varying) TO "authenticated";
GRANT ALL ON FUNCTION "public"."http_delete"("uri" character varying, "content" character varying, "content_type" character varying) TO "service_role";



GRANT ALL ON FUNCTION "public"."http_get"("uri" character varying) TO "postgres";
GRANT ALL ON FUNCTION "public"."http_get"("uri" character varying) TO "anon";
GRANT ALL ON FUNCTION "public"."http_get"("uri" character varying) TO "authenticated";
GRANT ALL ON FUNCTION "public"."http_get"("uri" character varying) TO "service_role";



GRANT ALL ON FUNCTION "public"."http_get"("uri" character varying, "data" "jsonb") TO "postgres";
GRANT ALL ON FUNCTION "public"."http_get"("uri" character varying, "data" "jsonb") TO "anon";
GRANT ALL ON FUNCTION "public"."http_get"("uri" character varying, "data" "jsonb") TO "authenticated";
GRANT ALL ON FUNCTION "public"."http_get"("uri" character varying, "data" "jsonb") TO "service_role";



GRANT ALL ON FUNCTION "public"."http_head"("uri" character varying) TO "postgres";
GRANT ALL ON FUNCTION "public"."http_head"("uri" character varying) TO "anon";
GRANT ALL ON FUNCTION "public"."http_head"("uri" character varying) TO "authenticated";
GRANT ALL ON FUNCTION "public"."http_head"("uri" character varying) TO "service_role";



GRANT ALL ON FUNCTION "public"."http_header"("field" character varying, "value" character varying) TO "postgres";
GRANT ALL ON FUNCTION "public"."http_header"("field" character varying, "value" character varying) TO "anon";
GRANT ALL ON FUNCTION "public"."http_header"("field" character varying, "value" character varying) TO "authenticated";
GRANT ALL ON FUNCTION "public"."http_header"("field" character varying, "value" character varying) TO "service_role";



GRANT ALL ON FUNCTION "public"."http_list_curlopt"() TO "postgres";
GRANT ALL ON FUNCTION "public"."http_list_curlopt"() TO "anon";
GRANT ALL ON FUNCTION "public"."http_list_curlopt"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."http_list_curlopt"() TO "service_role";



GRANT ALL ON FUNCTION "public"."http_patch"("uri" character varying, "content" character varying, "content_type" character varying) TO "postgres";
GRANT ALL ON FUNCTION "public"."http_patch"("uri" character varying, "content" character varying, "content_type" character varying) TO "anon";
GRANT ALL ON FUNCTION "public"."http_patch"("uri" character varying, "content" character varying, "content_type" character varying) TO "authenticated";
GRANT ALL ON FUNCTION "public"."http_patch"("uri" character varying, "content" character varying, "content_type" character varying) TO "service_role";



GRANT ALL ON FUNCTION "public"."http_post"("uri" character varying, "data" "jsonb") TO "postgres";
GRANT ALL ON FUNCTION "public"."http_post"("uri" character varying, "data" "jsonb") TO "anon";
GRANT ALL ON FUNCTION "public"."http_post"("uri" character varying, "data" "jsonb") TO "authenticated";
GRANT ALL ON FUNCTION "public"."http_post"("uri" character varying, "data" "jsonb") TO "service_role";



GRANT ALL ON FUNCTION "public"."http_post"("uri" character varying, "content" character varying, "content_type" character varying) TO "postgres";
GRANT ALL ON FUNCTION "public"."http_post"("uri" character varying, "content" character varying, "content_type" character varying) TO "anon";
GRANT ALL ON FUNCTION "public"."http_post"("uri" character varying, "content" character varying, "content_type" character varying) TO "authenticated";
GRANT ALL ON FUNCTION "public"."http_post"("uri" character varying, "content" character varying, "content_type" character varying) TO "service_role";



GRANT ALL ON FUNCTION "public"."http_put"("uri" character varying, "content" character varying, "content_type" character varying) TO "postgres";
GRANT ALL ON FUNCTION "public"."http_put"("uri" character varying, "content" character varying, "content_type" character varying) TO "anon";
GRANT ALL ON FUNCTION "public"."http_put"("uri" character varying, "content" character varying, "content_type" character varying) TO "authenticated";
GRANT ALL ON FUNCTION "public"."http_put"("uri" character varying, "content" character varying, "content_type" character varying) TO "service_role";



GRANT ALL ON FUNCTION "public"."http_reset_curlopt"() TO "postgres";
GRANT ALL ON FUNCTION "public"."http_reset_curlopt"() TO "anon";
GRANT ALL ON FUNCTION "public"."http_reset_curlopt"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."http_reset_curlopt"() TO "service_role";



GRANT ALL ON FUNCTION "public"."http_set_curlopt"("curlopt" character varying, "value" character varying) TO "postgres";
GRANT ALL ON FUNCTION "public"."http_set_curlopt"("curlopt" character varying, "value" character varying) TO "anon";
GRANT ALL ON FUNCTION "public"."http_set_curlopt"("curlopt" character varying, "value" character varying) TO "authenticated";
GRANT ALL ON FUNCTION "public"."http_set_curlopt"("curlopt" character varying, "value" character varying) TO "service_role";



GRANT ALL ON FUNCTION "public"."process_user_location_and_stations"() TO "anon";
GRANT ALL ON FUNCTION "public"."process_user_location_and_stations"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."process_user_location_and_stations"() TO "service_role";



GRANT ALL ON FUNCTION "public"."resolve_closest_station"("user_lat" double precision, "user_lon" double precision, "target_measurement" "text") TO "anon";
GRANT ALL ON FUNCTION "public"."resolve_closest_station"("user_lat" double precision, "user_lon" double precision, "target_measurement" "text") TO "authenticated";
GRANT ALL ON FUNCTION "public"."resolve_closest_station"("user_lat" double precision, "user_lon" double precision, "target_measurement" "text") TO "service_role";



GRANT ALL ON FUNCTION "public"."text_to_bytea"("data" "text") TO "postgres";
GRANT ALL ON FUNCTION "public"."text_to_bytea"("data" "text") TO "anon";
GRANT ALL ON FUNCTION "public"."text_to_bytea"("data" "text") TO "authenticated";
GRANT ALL ON FUNCTION "public"."text_to_bytea"("data" "text") TO "service_role";



GRANT ALL ON FUNCTION "public"."urlencode"("string" "bytea") TO "postgres";
GRANT ALL ON FUNCTION "public"."urlencode"("string" "bytea") TO "anon";
GRANT ALL ON FUNCTION "public"."urlencode"("string" "bytea") TO "authenticated";
GRANT ALL ON FUNCTION "public"."urlencode"("string" "bytea") TO "service_role";



GRANT ALL ON FUNCTION "public"."urlencode"("data" "jsonb") TO "postgres";
GRANT ALL ON FUNCTION "public"."urlencode"("data" "jsonb") TO "anon";
GRANT ALL ON FUNCTION "public"."urlencode"("data" "jsonb") TO "authenticated";
GRANT ALL ON FUNCTION "public"."urlencode"("data" "jsonb") TO "service_role";



GRANT ALL ON FUNCTION "public"."urlencode"("string" character varying) TO "postgres";
GRANT ALL ON FUNCTION "public"."urlencode"("string" character varying) TO "anon";
GRANT ALL ON FUNCTION "public"."urlencode"("string" character varying) TO "authenticated";
GRANT ALL ON FUNCTION "public"."urlencode"("string" character varying) TO "service_role";
























GRANT ALL ON TABLE "public"."Fish_Database" TO "anon";
GRANT ALL ON TABLE "public"."Fish_Database" TO "authenticated";
GRANT ALL ON TABLE "public"."Fish_Database" TO "service_role";



GRANT ALL ON TABLE "public"."SensorData" TO "anon";
GRANT ALL ON TABLE "public"."SensorData" TO "authenticated";
GRANT ALL ON TABLE "public"."SensorData" TO "service_role";



GRANT ALL ON SEQUENCE "public"."TempData_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."TempData_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."TempData_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."UserData" TO "anon";
GRANT ALL ON TABLE "public"."UserData" TO "authenticated";
GRANT ALL ON TABLE "public"."UserData" TO "service_role";



GRANT ALL ON SEQUENCE "public"."UserData_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."UserData_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."UserData_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."WeatherStationLookup" TO "anon";
GRANT ALL ON TABLE "public"."WeatherStationLookup" TO "authenticated";
GRANT ALL ON TABLE "public"."WeatherStationLookup" TO "service_role";



GRANT ALL ON TABLE "public"."daily_sensor_averages" TO "anon";
GRANT ALL ON TABLE "public"."daily_sensor_averages" TO "authenticated";
GRANT ALL ON TABLE "public"."daily_sensor_averages" TO "service_role";



GRANT ALL ON SEQUENCE "public"."daily_sensor_averages_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."daily_sensor_averages_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."daily_sensor_averages_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."imageTable" TO "anon";
GRANT ALL ON TABLE "public"."imageTable" TO "authenticated";
GRANT ALL ON TABLE "public"."imageTable" TO "service_role";



GRANT ALL ON SEQUENCE "public"."imageTable_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."imageTable_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."imageTable_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."pondInterventions" TO "anon";
GRANT ALL ON TABLE "public"."pondInterventions" TO "authenticated";
GRANT ALL ON TABLE "public"."pondInterventions" TO "service_role";



GRANT ALL ON TABLE "public"."pond_algae_evaluations" TO "anon";
GRANT ALL ON TABLE "public"."pond_algae_evaluations" TO "authenticated";
GRANT ALL ON TABLE "public"."pond_algae_evaluations" TO "service_role";



GRANT ALL ON SEQUENCE "public"."pond_algae_evaluations_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."pond_algae_evaluations_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."pond_algae_evaluations_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."pond_chemistry_evaluations" TO "anon";
GRANT ALL ON TABLE "public"."pond_chemistry_evaluations" TO "authenticated";
GRANT ALL ON TABLE "public"."pond_chemistry_evaluations" TO "service_role";



GRANT ALL ON SEQUENCE "public"."pond_chemistry_evaluations_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."pond_chemistry_evaluations_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."pond_chemistry_evaluations_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."pond_chemistry_state" TO "anon";
GRANT ALL ON TABLE "public"."pond_chemistry_state" TO "authenticated";
GRANT ALL ON TABLE "public"."pond_chemistry_state" TO "service_role";



GRANT ALL ON TABLE "public"."pond_evaporation_evaluations" TO "anon";
GRANT ALL ON TABLE "public"."pond_evaporation_evaluations" TO "authenticated";
GRANT ALL ON TABLE "public"."pond_evaporation_evaluations" TO "service_role";



GRANT ALL ON SEQUENCE "public"."pond_evaporation_evaluations_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."pond_evaporation_evaluations_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."pond_evaporation_evaluations_id_seq" TO "service_role";



GRANT ALL ON SEQUENCE "public"."pondinterventions_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."pondinterventions_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."pondinterventions_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."weather_forecasts" TO "anon";
GRANT ALL ON TABLE "public"."weather_forecasts" TO "authenticated";
GRANT ALL ON TABLE "public"."weather_forecasts" TO "service_role";



GRANT ALL ON TABLE "public"."weather_telemetry" TO "anon";
GRANT ALL ON TABLE "public"."weather_telemetry" TO "authenticated";
GRANT ALL ON TABLE "public"."weather_telemetry" TO "service_role";









ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "service_role";






ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "service_role";






ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "service_role";































