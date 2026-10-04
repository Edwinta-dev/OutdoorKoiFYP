-- 0017_evaluation_daily.sql
--
-- Retention and daily summaries for evaluation rows (issue #22).
--
-- The poller writes one row to each of pond_chemistry_evaluations,
-- pond_evaporation_evaluations and pond_algae_evaluations per pond every
-- poll (about 290 a day per pond), with no limit. The worker's daily
-- retention job (koi/worker/retention.py) now folds every local day older
-- than the retention period (KOI_EVALUATION_RETENTION_DAYS, default 30)
-- into one evaluation_daily row per pond, domain and day, and deletes the
-- detailed rows of that day in the same transaction.
--
-- Two daily summaries, kept apart
--   daily_sensor_averages (0001) holds OBSERVED values: the sensor node's
--   SensorData readings averaged per local day by
--   aggregate_daily_sensor_data(). It is unchanged.
--   evaluation_daily (this file) holds MODEL OUTPUT: the twin's estimates
--   (TAN, nitrite, nitrate, water lost, green ratio, days to the next
--   action ...) and the Green / Amber / Red outcomes it reported. Nothing
--   in it is a measurement.
--
-- Local day: the pond's calendar date, (evaluated_at at time zone
--   p_time_zone)::date, the convention of daily_sensor_averages and of
--   koi/models/local_time.py (issue #15). Default 'Asia/Singapore'.
--
-- evaluation_daily, one row per (pond_id, domain, local_date)
--   pond_id             the evaluation rows' userid
--   domain              'chemistry' | 'evaporation' | 'algae'. A domain
--                       with no rows that day has no row: an absent domain
--                       stays absent.
--   local_date          the pond's local calendar day
--   time_zone           the zone local_date was taken in
--   row_count           detailed rows folded in (and deleted)
--   first_evaluated_at, last_evaluated_at
--                       evaluated_at of the first and last of them
--   status_counts       {"Green": n, "Amber": n, ...}: rows per status
--   metrics             {<column>: {"n", "first", "first_at", "last",
--                       "last_at", "min", "max"}} for the domain's
--                       headline values:
--                         chemistry    tan_ppm, no2_ppm, no3_ppm,
--                                      ph_reactivity
--                         evaporation  loss_litres, loss_pct,
--                                      evaporation_mm_per_day,
--                                      loss_litres_per_day, water_temp_c,
--                                      days_to_topup
--                         algae        green_ratio, growth_rate_per_day,
--                                      days_to_scrub
--                       n counts the rows with a value. A null value is
--                       left out of every statistic, so a value missing
--                       all day reads n 0 and null first, last, min and
--                       max: missing, never zero. first and last are the
--                       first and last non-null values by
--                       (evaluated_at, id), with their times.
--   model_versions      [{"model_version", "rows", "first_evaluated_at",
--                       "last_evaluated_at"}] ordered by first time: each
--                       backend version that wrote rows that day
--                       (migration 0016), so a model change within a day
--                       is kept. model_version null: rows written before
--                       0016, version unknown.
--   summarized_at       when the row was first written
--   updated_at          when rows were last folded into it
--
-- summarize_evaluation_days(p_before date, p_time_zone text,
--                           p_max_days integer) returns jsonb
--   Folds the detailed rows of every pond and domain whose local date is
--   before p_before, oldest p_max_days local dates first, into
--   evaluation_daily and deletes them. Returns {"days": [dates],
--   "rows": n, "summaries": n}; days empty means nothing was left to do.
--   Rules:
--     * p_before may not be later than today in p_time_zone, so an
--       unfinished day is never summarised.
--     * The newest local day of each pond and domain is never summarised,
--       so the latest evaluation (what the API serves) always stays as a
--       detailed row, even for a pond that stopped polling.
--     * Summaries are written first; the delete takes exactly the rows
--       that were summarised (by id, from the same snapshot). The function
--       then checks that the rows deleted, the rows summarised and the
--       summary rows written all agree, and raises (rolling everything
--       back) if they do not. A failure at any point leaves the detailed
--       rows and the summaries as they were, so a retry starts again.
--     * Idempotent: a summarised day has no detailed rows left, so a
--       second run does nothing. A detailed row that arrives later for a
--       day already summarised is merged into its summary (counts added,
--       first/last by time, min/max widened, versions combined) and then
--       deleted.
--     * One run at a time: a transaction-level advisory lock serialises
--       concurrent calls.
--   Only evaluation rows are deleted. The inputs a rebuild or replay
--   reads (SensorData, sensor_ingest_ledger, pondInterventions,
--   imageTable, algae_severity_ratings, weather history, engine
--   snapshots) are not touched.
--
-- Indexes: the primary key (pond_id, domain, local_date) serves reads by
--   pond and date. The detailed tables' (userid, evaluated_at desc)
--   indexes serve the newest-day check.
--
-- Access: evaluation_daily has RLS on and no policies; the table, the
--   function and its helpers are revoked from public, anon and
--   authenticated and granted to service_role (the backend) only. The
--   three evaluation tables' grants and policies are unchanged.

create table if not exists public.evaluation_daily (
    pond_id            bigint not null,
    domain             text not null check (domain in ('chemistry', 'evaporation', 'algae')),
    local_date         date not null,
    time_zone          text not null,
    row_count          integer not null check (row_count > 0),
    first_evaluated_at timestamptz not null,
    last_evaluated_at  timestamptz not null,
    status_counts      jsonb not null check (jsonb_typeof(status_counts) = 'object'),
    metrics            jsonb not null check (jsonb_typeof(metrics) = 'object'),
    model_versions     jsonb not null check (jsonb_typeof(model_versions) = 'array'),
    summarized_at      timestamptz not null default now(),
    updated_at         timestamptz not null default now(),
    primary key (pond_id, domain, local_date),
    check (first_evaluated_at <= last_evaluated_at)
);

comment on table public.evaluation_daily is
    'Daily summary of the model''s evaluation rows (estimates and status outcomes, not measurements); '
    'see daily_sensor_averages for observed sensor values. Written by summarize_evaluation_days (0017).';

-- No policies: only the backend's secret key (which bypasses RLS) reads or writes it.
alter table public.evaluation_daily enable row level security;

revoke all on table public.evaluation_daily from public, anon, authenticated;
grant select, insert, update, delete on table public.evaluation_daily to service_role;

-- Adds the numeric values of two {key: count} objects.
create or replace function public.evaluation_daily_merge_counts(p_a jsonb, p_b jsonb)
returns jsonb
language sql
immutable
set search_path = ''
as $$
    select coalesce(jsonb_object_agg(k, total), '{}'::jsonb)
    from (
        select k, sum(v::numeric) as total
        from (select key as k, value #>> '{}' as v from jsonb_each(coalesce(p_a, '{}'::jsonb))
              union all
              select key, value #>> '{}' from jsonb_each(coalesce(p_b, '{}'::jsonb))) s
        group by k
    ) t;
$$;

-- Merges two metrics objects (see the header): n added, first/last by
-- time (the earlier summary wins a tie), min/max widened. A null side
-- never replaces a value.
create or replace function public.evaluation_daily_merge_metrics(p_a jsonb, p_b jsonb)
returns jsonb
language sql
immutable
set search_path = ''
as $$
    select coalesce(jsonb_object_agg(k, jsonb_build_object(
        'n', coalesce((a ->> 'n')::bigint, 0) + coalesce((b ->> 'n')::bigint, 0),
        'first', case when (b ->> 'first_at') is not null
                       and ((a ->> 'first_at') is null
                            or (b ->> 'first_at')::timestamptz < (a ->> 'first_at')::timestamptz)
                      then b -> 'first' else coalesce(a -> 'first', 'null'::jsonb) end,
        'first_at', case when (b ->> 'first_at') is not null
                          and ((a ->> 'first_at') is null
                               or (b ->> 'first_at')::timestamptz < (a ->> 'first_at')::timestamptz)
                         then b -> 'first_at' else coalesce(a -> 'first_at', 'null'::jsonb) end,
        'last', case when (b ->> 'last_at') is not null
                      and ((a ->> 'last_at') is null
                           or (b ->> 'last_at')::timestamptz > (a ->> 'last_at')::timestamptz)
                     then b -> 'last' else coalesce(a -> 'last', 'null'::jsonb) end,
        'last_at', case when (b ->> 'last_at') is not null
                         and ((a ->> 'last_at') is null
                              or (b ->> 'last_at')::timestamptz > (a ->> 'last_at')::timestamptz)
                        then b -> 'last_at' else coalesce(a -> 'last_at', 'null'::jsonb) end,
        'min', coalesce(to_jsonb(least((a ->> 'min')::numeric, (b ->> 'min')::numeric)), 'null'::jsonb),
        'max', coalesce(to_jsonb(greatest((a ->> 'max')::numeric, (b ->> 'max')::numeric)), 'null'::jsonb)
    )), '{}'::jsonb)
    from (select key as k from jsonb_object_keys(coalesce(p_a, '{}'::jsonb)) key
          union
          select key from jsonb_object_keys(coalesce(p_b, '{}'::jsonb)) key) keys,
         lateral (select p_a -> keys.k as a, p_b -> keys.k as b) sides;
$$;

-- Merges two model_versions arrays by model_version (null: unknown).
create or replace function public.evaluation_daily_merge_versions(p_a jsonb, p_b jsonb)
returns jsonb
language sql
immutable
set search_path = ''
as $$
    select coalesce(jsonb_agg(jsonb_build_object(
               'model_version', version, 'rows', total,
               'first_evaluated_at', first_at, 'last_evaluated_at', last_at)
           order by first_at, version nulls first), '[]'::jsonb)
    from (
        select e ->> 'model_version' as version,
               sum((e ->> 'rows')::bigint) as total,
               min((e ->> 'first_evaluated_at')::timestamptz) as first_at,
               max((e ->> 'last_evaluated_at')::timestamptz) as last_at
        from (select jsonb_array_elements(coalesce(p_a, '[]'::jsonb)) as e
              union all
              select jsonb_array_elements(coalesce(p_b, '[]'::jsonb))) s
        group by e ->> 'model_version'
    ) t;
$$;

create or replace function public.summarize_evaluation_days(
    p_before date,
    p_time_zone text default 'Asia/Singapore',
    p_max_days integer default 31
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_boundary timestamptz;
    v_today date;
    v_days date[];
    v_batch bigint;
    v_groups bigint;
    v_summarised bigint;
    v_written bigint;
    v_deleted bigint;
begin
    if p_before is null or p_time_zone is null or p_max_days is null or p_max_days < 1 then
        raise exception 'summarize_evaluation_days: p_before, p_time_zone and p_max_days >= 1 are required';
    end if;
    v_today := (now() at time zone p_time_zone)::date;
    if p_before > v_today then
        raise exception 'summarize_evaluation_days: % is after today (%) in %', p_before, v_today, p_time_zone;
    end if;
    -- One run at a time.
    perform pg_advisory_xact_lock(hashtext('public.summarize_evaluation_days'));

    v_boundary := p_before::timestamp at time zone p_time_zone;

    -- Every detailed row before the boundary that is not on its pond and
    -- domain's newest local day, with its headline values.
    create temporary table if not exists evaluation_daily_batch (
        domain text, id bigint, pond_id bigint, evaluated_at timestamptz, local_date date,
        status text, model_version text, metrics jsonb
    ) on commit drop;
    truncate pg_temp.evaluation_daily_batch;

    insert into pg_temp.evaluation_daily_batch
    select d.*
    from (
        select 'chemistry'::text, e.id, e.userid, e.evaluated_at,
               (e.evaluated_at at time zone p_time_zone)::date, e.status, e.model_version,
               jsonb_build_object('tan_ppm', e.tan_ppm, 'no2_ppm', e.no2_ppm, 'no3_ppm', e.no3_ppm,
                                  'ph_reactivity', e.ph_reactivity)
        from public.pond_chemistry_evaluations e
        where e.evaluated_at < v_boundary
          and (e.evaluated_at at time zone p_time_zone)::date
              < (select (max(n.evaluated_at) at time zone p_time_zone)::date
                 from public.pond_chemistry_evaluations n where n.userid = e.userid)
        union all
        select 'evaporation', e.id, e.userid, e.evaluated_at,
               (e.evaluated_at at time zone p_time_zone)::date, e.status, e.model_version,
               jsonb_build_object('loss_litres', e.loss_litres, 'loss_pct', e.loss_pct,
                                  'evaporation_mm_per_day', e.evaporation_mm_per_day,
                                  'loss_litres_per_day', e.loss_litres_per_day, 'water_temp_c', e.water_temp_c,
                                  'days_to_topup', e.days_to_topup)
        from public.pond_evaporation_evaluations e
        where e.evaluated_at < v_boundary
          and (e.evaluated_at at time zone p_time_zone)::date
              < (select (max(n.evaluated_at) at time zone p_time_zone)::date
                 from public.pond_evaporation_evaluations n where n.userid = e.userid)
        union all
        select 'algae', e.id, e.userid, e.evaluated_at,
               (e.evaluated_at at time zone p_time_zone)::date, e.status, e.model_version,
               jsonb_build_object('green_ratio', e.green_ratio, 'growth_rate_per_day', e.growth_rate_per_day,
                                  'days_to_scrub', e.days_to_scrub)
        from public.pond_algae_evaluations e
        where e.evaluated_at < v_boundary
          and (e.evaluated_at at time zone p_time_zone)::date
              < (select (max(n.evaluated_at) at time zone p_time_zone)::date
                 from public.pond_algae_evaluations n where n.userid = e.userid)
    ) d;

    -- The oldest p_max_days local dates only, so one call stays small.
    select coalesce(array_agg(local_date order by local_date), '{}')
    into v_days
    from (select distinct b.local_date from pg_temp.evaluation_daily_batch b order by 1 limit p_max_days) s;
    delete from pg_temp.evaluation_daily_batch b where b.local_date <> all (v_days);

    select count(*) into v_batch from pg_temp.evaluation_daily_batch;
    if v_batch = 0 then
        return jsonb_build_object('days', '[]'::jsonb, 'rows', 0, 'summaries', 0);
    end if;

    -- 1. Write (or merge into) the summaries.
    with groups as (
        select b.pond_id, b.domain, b.local_date, count(*) as row_count,
               min(b.evaluated_at) as first_at, max(b.evaluated_at) as last_at
        from pg_temp.evaluation_daily_batch b
        group by 1, 2, 3
    ),
    statuses as (
        select pond_id, domain, local_date, jsonb_object_agg(coalesce(status, 'unknown'), n) as status_counts
        from (select b.pond_id, b.domain, b.local_date, b.status, count(*) as n
              from pg_temp.evaluation_daily_batch b group by 1, 2, 3, 4) s
        group by 1, 2, 3
    ),
    values_ as (
        select b.pond_id, b.domain, b.local_date, b.id, b.evaluated_at, m.key as metric, m.value
        from pg_temp.evaluation_daily_batch b, jsonb_each(b.metrics) m
    ),
    metric_stats as (
        select pond_id, domain, local_date, metric, jsonb_build_object(
                   'n', count(*) filter (where value <> 'null'::jsonb),
                   'first', coalesce((array_agg(value order by evaluated_at, id)
                                      filter (where value <> 'null'::jsonb))[1], 'null'::jsonb),
                   'first_at', to_jsonb(min(evaluated_at) filter (where value <> 'null'::jsonb)),
                   'last', coalesce((array_agg(value order by evaluated_at desc, id desc)
                                     filter (where value <> 'null'::jsonb))[1], 'null'::jsonb),
                   'last_at', to_jsonb(max(evaluated_at) filter (where value <> 'null'::jsonb)),
                   'min', to_jsonb(min((value #>> '{}')::numeric) filter (where value <> 'null'::jsonb)),
                   'max', to_jsonb(max((value #>> '{}')::numeric) filter (where value <> 'null'::jsonb))
               ) as stats
        from values_
        group by 1, 2, 3, 4
    ),
    metrics as (
        select pond_id, domain, local_date, jsonb_object_agg(metric, stats) as metrics
        from metric_stats
        group by 1, 2, 3
    ),
    versions as (
        select pond_id, domain, local_date,
               jsonb_agg(jsonb_build_object('model_version', model_version, 'rows', n,
                                            'first_evaluated_at', first_at, 'last_evaluated_at', last_at)
                         order by first_at, model_version nulls first) as model_versions
        from (select b.pond_id, b.domain, b.local_date, b.model_version, count(*) as n,
                     min(b.evaluated_at) as first_at, max(b.evaluated_at) as last_at
              from pg_temp.evaluation_daily_batch b group by 1, 2, 3, 4) s
        group by 1, 2, 3
    ),
    written as (
        insert into public.evaluation_daily as d
            (pond_id, domain, local_date, time_zone, row_count, first_evaluated_at, last_evaluated_at,
             status_counts, metrics, model_versions)
        select g.pond_id, g.domain, g.local_date, p_time_zone, g.row_count, g.first_at, g.last_at,
               s.status_counts, coalesce(m.metrics, '{}'::jsonb), v.model_versions
        from groups g
        join statuses s using (pond_id, domain, local_date)
        join versions v using (pond_id, domain, local_date)
        left join metrics m using (pond_id, domain, local_date)
        on conflict (pond_id, domain, local_date) do update set
            row_count = d.row_count + excluded.row_count,
            first_evaluated_at = least(d.first_evaluated_at, excluded.first_evaluated_at),
            last_evaluated_at = greatest(d.last_evaluated_at, excluded.last_evaluated_at),
            status_counts = public.evaluation_daily_merge_counts(d.status_counts, excluded.status_counts),
            metrics = public.evaluation_daily_merge_metrics(d.metrics, excluded.metrics),
            model_versions = public.evaluation_daily_merge_versions(d.model_versions, excluded.model_versions),
            updated_at = now()
        returning 1
    )
    select (select count(*) from groups), (select coalesce(sum(row_count), 0) from groups),
           (select count(*) from written)
    into v_groups, v_summarised, v_written;

    -- 2. Delete exactly the rows that were summarised.
    with dc as (
        delete from public.pond_chemistry_evaluations e
        using pg_temp.evaluation_daily_batch b
        where b.domain = 'chemistry' and e.id = b.id
        returning 1
    ),
    de as (
        delete from public.pond_evaporation_evaluations e
        using pg_temp.evaluation_daily_batch b
        where b.domain = 'evaporation' and e.id = b.id
        returning 1
    ),
    da as (
        delete from public.pond_algae_evaluations e
        using pg_temp.evaluation_daily_batch b
        where b.domain = 'algae' and e.id = b.id
        returning 1
    )
    select (select count(*) from dc) + (select count(*) from de) + (select count(*) from da)
    into v_deleted;

    -- 3. Verify before committing; any mismatch rolls back all of it.
    if v_written <> v_groups or v_summarised <> v_batch or v_deleted <> v_batch then
        raise exception 'summarize_evaluation_days: % rows read, % summarised into % of % summaries, % deleted',
            v_batch, v_summarised, v_written, v_groups, v_deleted;
    end if;

    return jsonb_build_object('days', to_jsonb(v_days), 'rows', v_batch, 'summaries', v_groups);
end;
$$;

revoke all on function public.evaluation_daily_merge_counts(jsonb, jsonb) from public, anon, authenticated;
revoke all on function public.evaluation_daily_merge_metrics(jsonb, jsonb) from public, anon, authenticated;
revoke all on function public.evaluation_daily_merge_versions(jsonb, jsonb) from public, anon, authenticated;
revoke all on function public.summarize_evaluation_days(date, text, integer) from public, anon, authenticated;
grant execute on function public.evaluation_daily_merge_counts(jsonb, jsonb) to service_role;
grant execute on function public.evaluation_daily_merge_metrics(jsonb, jsonb) to service_role;
grant execute on function public.evaluation_daily_merge_versions(jsonb, jsonb) to service_role;
grant execute on function public.summarize_evaluation_days(date, text, integer) to service_role;
