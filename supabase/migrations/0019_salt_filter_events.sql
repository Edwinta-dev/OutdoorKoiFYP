-- Issue #29. Nullable fields preserve older intervention rows.
-- RPC contracts keep their signatures and grants (CREATE OR REPLACE).
-- pond_interventions_since: service-role only, same pond/window/order;
-- adds salt_grams (grams, nullable) and notes (text, nullable).
-- get_historical_graph_payload: existing access; adds those fields to
-- interventions and derives after_maintenance for pH/TDS daily rows whose
-- Singapore day overlaps [FILTER_CLEAN timestamp, timestamp + 24 hours).
-- Derived markers also cover retained aggregates after raw sensor retention,
-- and follow intervention edits/deletions without a stale persisted flag.
ALTER TABLE public."pondInterventions" ADD COLUMN salt_grams numeric;
ALTER TABLE public."pondInterventions" ADD COLUMN notes text;
ALTER TABLE public."pondInterventions" ADD CONSTRAINT salt_event_mass
    CHECK (event_type <> 'SALT' OR (salt_grams IS NOT NULL AND salt_grams > 0
           AND salt_grams NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric))) NOT VALID;
ALTER TABLE public."pondInterventions" ADD CONSTRAINT intervention_notes_length
    CHECK (notes IS NULL OR length(notes) <= 1000) NOT VALID;

create or replace function public.pond_interventions_since(p_pond_id bigint, p_since timestamptz)
returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
    select coalesce(jsonb_agg(jsonb_build_object(
               'id', i.id, 'event_id', i.event_id, 'event_type', i.event_type,
               'event_timestamp', i.event_timestamp, 'volume_percentage', i.volume_percentage,
               'volume_litres', i.volume_litres, 'food_grams', i.food_grams,
               'protein_percentage', i.protein_percentage, 'algae_method', i.algae_method,
               'created_at', i.created_at, 'salt_grams', i.salt_grams, 'notes', i.notes)
           order by i.event_timestamp, i.id), '[]'::jsonb)
    from public."pondInterventions" i
    where i."userID" = p_pond_id
      and (p_since is null or i.event_timestamp >= p_since);
$$;

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
$$;

