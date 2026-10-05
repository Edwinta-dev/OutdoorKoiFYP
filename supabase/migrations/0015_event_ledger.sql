-- 0015_event_ledger.sql
--
-- Event ledger: idempotent, time-ordered and replayable interventions
-- (issue #19).
--
-- The app writes an event to "pondInterventions" and then posts it to
-- the digital twin. If the post failed the timeline showed the event but
-- the model never applied it; a retried post applied it twice. Each event
-- now carries a stable UUID, event_id, which the twin's snapshot records
-- in its own ledger (koi/models/event_ledger.py): a post or a row whose
-- event_id is already there is not applied again, and the poller applies
-- every row whose event_id is not (reconciliation).
--
-- pondInterventions.event_id uuid
--   Nullable, unique (pondinterventions_event_id_key; several nulls are
--   allowed). New rows get gen_random_uuid() unless the client sends its
--   own (the app generates one per logged event and posts the same value
--   to the twin). The integer id stays the row's key; event_id is only
--   the idempotency key and is never compared with id.
--   Existing rows are backfilled with intervention_event_id(id), a UUID
--   derived from the row id (md5 of 'pondInterventions:' || id). The
--   derivation is deterministic, so a backfill that stopped part way and
--   is run again gives every row the same UUID it would have had, and
--   rows it already filled keep theirs. The default is set only after
--   the backfill, so ADD COLUMN does not fill old rows with random UUIDs.
--   A row inserted with an explicit null event_id is filled the same way
--   by backfill_intervention_event_ids, which the poller calls each cycle.
--
-- intervention_event_id(p_id bigint) returns uuid
--   The derived UUID above. Immutable.
--
-- backfill_intervention_event_ids(p_pond_id bigint) returns integer
--   Sets event_id = intervention_event_id(id) on the pond's rows where it
--   is null; returns how many rows it set. Only the given pond's rows.
--
-- pond_interventions_since(p_pond_id bigint, p_since timestamptz) returns jsonb
--   The pond's rows with event_timestamp >= p_since (null: every row), as
--   [{id, event_id, event_type, event_timestamp, volume_percentage,
--   volume_litres, food_grams, protein_percentage, algae_method,
--   created_at}] ordered by event_timestamp, id. Read only. Only the given
--   pond's rows: an event_id is never looked up across ponds.
--
-- sensor_ingest_history(p_pond_id, p_sensor_types, p_after, p_until) returns jsonb
--   SensorData rows of the pond that are in sensor_ingest_ledger (0014)
--   with effective_sample_time in (p_after, p_until] (null p_after: no
--   lower bound), as [{id, sensor_type, value, created_at}] ordered by
--   effective_sample_time, id; the same shape sensor_ingest_pending
--   returns. The twin replays these when an event arrives older than the
--   newest input it has applied. Read only.
--
-- get_historical_graph_payload(p_userid, p_days)
--   Unchanged except that each intervention also carries 'event_id'. The
--   keys the app reads today ('id', the integer row id, and the
--   abbreviated 'pct' and 'litres') are kept: they stay until the app
--   reads event_id and the full column names (the mobile repositories
--   issue, #56), and are removed then by a later migration. Grants are
--   kept (create or replace keeps them).
--
-- idx_pondinterventions_user_type_time on ("userID", event_type,
--   event_timestamp desc): the index the reconciliation report
--   (docs/database-reconciliation.md 5.6) assigns to this issue, for the
--   graph RPC and pond_interventions_since.
--
-- Access: the new functions are revoked from public, anon and
-- authenticated and granted to service_role (the backend) only.
-- pondInterventions' grants, policies and triggers are unchanged: the
-- app keeps inserting with its current payload, which now gets a UUID by
-- default. Both indexes are built in this migration's transaction, so
-- app inserts wait for the build (the table is small: 40 rows when last
-- counted).

alter table public."pondInterventions" add column if not exists event_id uuid;

create or replace function public.intervention_event_id(p_id bigint)
returns uuid
language sql
immutable
set search_path = ''
as $$
    select md5('pondInterventions:' || p_id::text)::uuid;
$$;

update public."pondInterventions"
set event_id = public.intervention_event_id(id)
where event_id is null;

alter table public."pondInterventions" alter column event_id set default gen_random_uuid();

create unique index if not exists pondinterventions_event_id_key
    on public."pondInterventions" (event_id);

create index if not exists idx_pondinterventions_user_type_time
    on public."pondInterventions" ("userID", event_type, event_timestamp desc);

create or replace function public.backfill_intervention_event_ids(p_pond_id bigint)
returns integer
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_count integer;
begin
    update public."pondInterventions"
    set event_id = public.intervention_event_id(id)
    where "userID" = p_pond_id
      and event_id is null;
    get diagnostics v_count = row_count;
    return v_count;
end;
$$;

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
               'created_at', i.created_at)
           order by i.event_timestamp, i.id), '[]'::jsonb)
    from public."pondInterventions" i
    where i."userID" = p_pond_id
      and (p_since is null or i.event_timestamp >= p_since);
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
               'id', d.id, 'sensor_type', d.sensor_type, 'value', d.data1, 'created_at', d.created_at)
           order by l.effective_sample_time, d.id), '[]'::jsonb)
    from public.sensor_ingest_ledger l
    join public."SensorData" d on d.id = l.sensor_row_id and d."userID" = p_pond_id
    where l.pond_id = p_pond_id
      and d.sensor_type = any(p_sensor_types)
      and (p_after is null or l.effective_sample_time > p_after)
      and l.effective_sample_time <= p_until;
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

revoke all on function public.intervention_event_id(bigint) from public, anon, authenticated;
revoke all on function public.backfill_intervention_event_ids(bigint) from public, anon, authenticated;
revoke all on function public.pond_interventions_since(bigint, timestamptz) from public, anon, authenticated;
revoke all on function public.sensor_ingest_history(bigint, text[], timestamptz, timestamptz)
    from public, anon, authenticated;
grant execute on function public.intervention_event_id(bigint) to service_role;
grant execute on function public.backfill_intervention_event_ids(bigint) to service_role;
grant execute on function public.pond_interventions_since(bigint, timestamptz) to service_role;
grant execute on function public.sensor_ingest_history(bigint, text[], timestamptz, timestamptz) to service_role;
