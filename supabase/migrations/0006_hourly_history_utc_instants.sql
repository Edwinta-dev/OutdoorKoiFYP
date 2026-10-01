-- 0006_hourly_history_utc_instants.sql
--
-- Defect D9 (docs/database-reconciliation.md), issue #15.
-- get_pond_telemetry_history built bucket_time as
-- date_trunc('hour', created_at AT TIME ZONE 'UTC'), a timestamp without
-- time zone holding UTC wall time, and returned it as timestamptz. That
-- implicit cast reads the wall time in the session's TimeZone, so any
-- session not set to UTC (for example Asia/Singapore) shifted every
-- hourly bucket by its offset. The extra AT TIME ZONE 'UTC' turns the UTC
-- wall time back into the instant it names, whatever the session zone.
--
-- Only that expression changes: same signature, result columns, pond
-- filter, sensor list, hours kept (pH and TDS both present) and
-- SECURITY DEFINER. CREATE OR REPLACE keeps the owner and grants. For a
-- UTC session (the Supabase default) the returned values are unchanged.

CREATE OR REPLACE FUNCTION public.get_pond_telemetry_history(p_user_id bigint, p_days integer DEFAULT 7)
RETURNS TABLE(bucket_time timestamp with time zone, ph numeric, tds numeric, temp_c numeric, lux numeric)
    LANGUAGE sql SECURITY DEFINER
    AS $$
  WITH hourly_buckets AS (
    SELECT
      date_trunc('hour', created_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' AS bucket_time,
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
