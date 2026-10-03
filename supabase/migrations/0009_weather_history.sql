-- 0009_weather_history.sql
--
-- NEA weather history beside the latest caches (issue #24).
--
-- weather_telemetry and weather_forecasts stay what they are: latest
-- caches keyed by (station_id, metric_type) and (forecast_type, slot_id),
-- read by get_bundled_dashboard_payload with the same keys and JSON as
-- before. A cache row is overwritten by every fetch, so it cannot hold
-- history, and its updated_at is a write time, not a provider time. This
-- migration adds separate history tables that keep every observation and
-- every forecast issuance, and the ingestion job (koi/weather) writes
-- both. Nothing is backfilled from the cache rows: one current row cannot
-- recover readings that were overwritten before this migration.
--
-- Objects
--   weather_station_regime     station-network regimes of dataset-wide
--                              series (seeded below from the NEA analysis)
--   weather_observation        one row per provider reading
--   weather_forecast_issuance  one row per forecast issuance, slot and
--                              validity window
--   weather_ingest_window      per-product backfill windows, for resuming
--                              and for observability
--   weather_telemetry.source_times       new, nullable (see the cache rules)
--   weather_forecasts.source_issued_at   new, nullable
--   ingest_weather_batch(jsonb)          the job's only write path
--   weather_forecast_as_of(...)          as-of forecast selection
--   weather_observations_as_of(...)      as-of observation window
--   weather_rainfall_total(...)          rainfall total with coverage
--
-- Times. Every instant is timestamptz.
--   observed_from, observed_to  the period the reading describes; equal
--                               for an instantaneous reading
--   fetched_at                  when the job received the response
--   issued_at                   the provider's issue time; null when the
--                               provider or record does not give one
--   source_updated_at           the provider's revision time; null when
--                               not given
--   available_at                generated: the latest provider time
--                               (issued or revised), else fetched_at; the
--                               earliest moment the issuance is known to
--                               have existed in this form
--   valid_from, valid_to        the forecast's validity window [from, to)
--
-- Observation semantics (column semantics):
--   instantaneous     a sample at one instant
--   interval_total    an amount over [observed_from, observed_to]; NEA
--                     rainfall is a 5-minute total in mm
--   interval_mean     a mean over the period (NEA UV index, hourly)
--   cumulative_total  a running total since some reset; never summed
--   rate              an intensity such as mm/h; never summed
-- Units are normalised by the job: degC, %, mm, knot, deg, UV index.
-- Wind speed stays in knots as NEA reports it and as the cache holds it.
--
-- Deduplication keys
--   observation  (source, series, station_id, metric, observed_from, observed_to)
--   issuance     (source, product, slot_id, valid_from, valid_to,
--                 issued_at, source_updated_at), nulls not distinct
-- source is the provider ('nea_v2'), the same for live fetches and
-- backfills, so the same reading fetched twice is one row. A repeated
-- fetch inserts nothing; a later issuance or revision for the same
-- validity window is a new row, so several issuances for one future day
-- are kept.
--
-- Station-network regimes. NEA_Data_Analysis/daily_climate.csv is a
-- dataset-wide daily series built from two different station networks:
-- a two-station "cluster" average with a few fixed samples per station
-- per day until 2025-06-01, then a continuous one-minute mean across 11
-- to 33 stations from 2025-06-02 (UNIFIED_NEA_CHAPTER.md section x.2.5;
-- the notebook re-derives its hot-day threshold per regime). The
-- regime describes that network-mean series only. A single station's
-- readings are not a network mean, so an observation can carry a
-- regime_id only when its series is 'network_mean': the foreign key on
-- (regime_id, series) makes a regime on a 'station' row impossible, and
-- a calibration derived on the network mean cannot be attached to a
-- pond's assigned station by accident.
--
-- Cache rules (ingest_weather_batch)
--   weather_telemetry: one 'realtime_sensor' row per station, data
--   {"air_temperature", "rainfall", "wind_speed"} as the existing writer
--   stores it (rainfall is the latest 5-minute total). A candidate value
--   replaces a metric only when it is newer than that metric's time in
--   source_times or, for a row whose source_times is null (written by the
--   old writer), not older than valid_start. valid_start becomes the
--   oldest metric time in the row, so the row never claims to be fresher
--   than its stalest value, and valid_end the latest valid_until (the
--   observation time plus the 5-minute reporting interval). updated_at is set on every change (the old writer leaves it,
--   defect D13).
--   weather_forecasts: a candidate replaces a slot only when its source
--   time is newer than source_issued_at or, for an old-writer row, not
--   older than updated_at. A '4day' candidate must also be at least as
--   new as the newest '4day' source_issued_at, because the dashboard
--   picks the four most recently written 4day rows. updated_at is set on
--   every change.
--   A backfill therefore cannot put an older reading or issuance in the
--   cache; the job also sends no cache candidates when backfilling.
--
-- As-of rules
--   A forecast is usable at time T when available_at <= T. With no
--   provider time that is the fetch time, so a forecast whose issue time
--   is unknown is never used for a time before it was fetched.
--   An observation is usable at T when observed_to <= T.
--   Ties are broken by available_at, issued_at, fetched_at, then id, all
--   newest first, so the selection is deterministic.
--
-- Rainfall totals (weather_rainfall_total). Only station rows with
-- semantics 'interval_total' and unit 'mm' are summed, and only those
-- lying wholly inside the requested window. Intervals are taken in order
-- of observed_from; one that overlaps an interval already counted is
-- skipped and counted in overlapping_skipped, so a repeated or
-- overlapping snapshot is never added twice. Uncovered time is returned
-- as missing intervals and coverage < 1; no data gives total_mm null and
-- status 'no_data', never zero.
--
-- Retention. History rows are kept indefinitely; nothing here deletes
-- them. They feed the advisory rules (#26), rainfall (#27), calibration
-- (#32), state rebuild (#20) and backtests (#33). The job keeps history
-- only for the stations and forecast areas it is configured for plus
-- those assigned to ponds (koi/weather/job.py), which bounds the volume
-- to a few thousand rows a day.
--
-- Access: backend only (service role). RLS on with no policies, default
-- grants to anon and authenticated revoked, functions not executable by
-- them. The dashboard's source times reach the app through #13 and #61.

create table if not exists public.weather_station_regime (
    regime_id          text primary key,
    series             text not null check (series in ('network_mean')),
    metrics            text[] not null,
    valid_from         date not null,
    valid_to           date,
    station_count_min  integer check (station_count_min > 0),
    station_count_max  integer check (station_count_max >= station_count_min),
    sampling           text not null,
    description        text not null,
    evidence           text not null,
    constraint weather_station_regime_id_series_key unique (regime_id, series),
    constraint weather_station_regime_dates check (valid_to is null or valid_to >= valid_from)
);

comment on table public.weather_station_regime is
    'Station-network regimes of dataset-wide network-mean series. valid_to is the last day (inclusive). Not applicable to single-station observations.';

insert into public.weather_station_regime
    (regime_id, series, metrics, valid_from, valid_to, station_count_min, station_count_max, sampling,
     description, evidence)
values
    ('nea_cluster_2stn', 'network_mean', '{air_temperature,relative_humidity}', '2020-02-01', '2025-06-01', 2, 2,
     'a few fixed samples per station per day (16 per day typical across both stations)',
     'Two-station cluster average. Higher day-to-day variance than the later network mean; thresholds calibrated here do not transfer unchanged.',
     'NEA_Data_Analysis/daily_climate.csv (source cluster/cluster, stations_used 2/2); UNIFIED_NEA_CHAPTER.md x.2.5; forecast_reality_pipeline.ipynb regime-aware HOT_THR'),
    ('nea_network_mean', 'network_mean', '{air_temperature,relative_humidity}', '2025-06-02', null, 11, 33,
     'one-minute readings from every reporting station',
     'Mean across the full reporting network. Lower day-to-day variance at an unchanged mean; per-regime thresholds are z-score matched to the cluster regime.',
     'NEA_Data_Analysis/daily_climate.csv (source network_mean(N stations), N 11 to 33); UNIFIED_NEA_CHAPTER.md x.2.5')
on conflict (regime_id) do nothing;


create table if not exists public.weather_observation (
    id             bigint generated by default as identity primary key,
    source         text not null,
    series         text not null default 'station' check (series in ('station', 'network_mean')),
    station_id     text not null,
    metric         text not null check (metric in ('air_temperature', 'relative_humidity', 'rainfall',
                                                   'wind_speed', 'wind_direction', 'uv_index')),
    value          double precision not null,
    unit           text not null check (unit in ('degC', '%', 'mm', 'knot', 'deg', 'UV index')),
    semantics      text not null check (semantics in ('instantaneous', 'interval_total', 'interval_mean',
                                                      'cumulative_total', 'rate')),
    observed_from  timestamptz not null,
    observed_to    timestamptz not null,
    fetched_at     timestamptz not null,
    regime_id      text,
    provenance     jsonb not null default '{}'::jsonb,
    created_at     timestamptz not null default now(),
    constraint weather_observation_period check (observed_from <= observed_to),
    constraint weather_observation_instant check (case semantics
        when 'instantaneous' then observed_from = observed_to
        when 'interval_total' then observed_from < observed_to
        when 'interval_mean' then observed_from < observed_to
        else true end),
    constraint weather_observation_regime_fkey foreign key (regime_id, series)
        references public.weather_station_regime (regime_id, series),
    constraint weather_observation_dedup_key
        unique (source, series, station_id, metric, observed_from, observed_to)
);

create index if not exists weather_observation_station_metric_idx
    on public.weather_observation (station_id, metric, observed_to);


create table if not exists public.weather_forecast_issuance (
    id                 bigint generated by default as identity primary key,
    source             text not null,
    product            text not null check (product in ('2hr', '24hr', '4day')),
    slot_id            text not null,
    issued_at          timestamptz,
    source_updated_at  timestamptz,
    valid_from         timestamptz not null,
    valid_to           timestamptz not null,
    payload            jsonb not null,
    fetched_at         timestamptz not null,
    available_at       timestamptz generated always as
                           (coalesce(greatest(issued_at, source_updated_at), fetched_at)) stored,
    provenance         jsonb not null default '{}'::jsonb,
    created_at         timestamptz not null default now(),
    constraint weather_forecast_issuance_validity check (valid_from < valid_to),
    constraint weather_forecast_issuance_dedup_key unique nulls not distinct
        (source, product, slot_id, valid_from, valid_to, issued_at, source_updated_at)
);

create index if not exists weather_forecast_issuance_slot_idx
    on public.weather_forecast_issuance (product, slot_id, available_at);


create table if not exists public.weather_ingest_window (
    product       text not null,
    window_start  timestamptz not null,
    window_end    timestamptz not null,
    status        text not null check (status in ('done', 'empty', 'failed')),
    attempts      integer not null default 0 check (attempts >= 0),
    pages         integer not null default 0,
    records       integer not null default 0,
    inserted      integer not null default 0,
    duplicates    integer not null default 0,
    malformed     integer not null default 0,
    last_error    text,
    updated_at    timestamptz not null default now(),
    primary key (product, window_start),
    constraint weather_ingest_window_period check (window_start < window_end)
);


alter table public.weather_telemetry add column if not exists source_times jsonb;
alter table public.weather_forecasts add column if not exists source_issued_at timestamptz;

comment on column public.weather_telemetry.source_times is
    'Provider observation time of each metric in data, written by koi/weather; null on rows from the older writer.';
comment on column public.weather_forecasts.source_issued_at is
    'Provider issue (or revision) time of data, written by koi/weather; null on rows from the older writer.';


alter table public.weather_station_regime enable row level security;
alter table public.weather_observation enable row level security;
alter table public.weather_forecast_issuance enable row level security;
alter table public.weather_ingest_window enable row level security;

revoke all on table public.weather_station_regime from anon, authenticated;
revoke all on table public.weather_observation from anon, authenticated;
revoke all on table public.weather_forecast_issuance from anon, authenticated;
revoke all on table public.weather_ingest_window from anon, authenticated;
revoke all on sequence public.weather_observation_id_seq from anon, authenticated;
revoke all on sequence public.weather_forecast_issuance_id_seq from anon, authenticated;


-- ingest_weather_batch(p_batch jsonb) returns jsonb
--
-- Input keys, each an array (missing = empty):
--   observations     weather_observation columns other than id/created_at
--   forecasts        weather_forecast_issuance columns other than id,
--                    available_at and created_at
--   telemetry_cache  {station_id, metric, value, observed_at, valid_until}
--   forecast_cache   {forecast_type, slot_id, data, valid_period, source_time}
--   stations         {station_id, station_name, latitude, longitude, tag};
--                    station_id null for a forecast area (matched by name)
-- Output: {"observations": {"received", "inserted"},
--          "forecasts": {"received", "inserted"},
--          "telemetry_cache_updated", "forecast_cache_updated",
--          "stations_changed"}
-- Access: service role only.
create or replace function public.ingest_weather_batch(p_batch jsonb)
returns jsonb
language plpgsql
set search_path = ''
as $$
declare
    v_obs_received   integer;
    v_obs_inserted   integer;
    v_fc_received    integer;
    v_fc_inserted    integer;
    v_tel_updated    integer := 0;
    v_fc_updated     integer := 0;
    v_st_changed     integer := 0;
    v_n              integer;
    c                record;
    t                public.weather_telemetry%rowtype;
    f                public.weather_forecasts%rowtype;
    v_times          jsonb;
    v_prev           timestamptz;
    v_newest_4day    timestamptz;
begin
    with r as (
        select * from jsonb_to_recordset(coalesce(p_batch->'observations', '[]'::jsonb)) as x(
            source text, series text, station_id text, metric text, value double precision, unit text,
            semantics text, observed_from timestamptz, observed_to timestamptz, fetched_at timestamptz,
            regime_id text, provenance jsonb)
    ), ins as (
        insert into public.weather_observation (source, series, station_id, metric, value, unit, semantics,
                                                observed_from, observed_to, fetched_at, regime_id, provenance)
        select source, coalesce(series, 'station'), station_id, metric, value, unit, semantics,
               observed_from, observed_to, fetched_at, regime_id, coalesce(provenance, '{}'::jsonb)
        from r
        on conflict on constraint weather_observation_dedup_key do nothing
        returning 1
    )
    select (select count(*) from r), (select count(*) from ins) into v_obs_received, v_obs_inserted;

    with r as (
        select * from jsonb_to_recordset(coalesce(p_batch->'forecasts', '[]'::jsonb)) as x(
            source text, product text, slot_id text, issued_at timestamptz, source_updated_at timestamptz,
            valid_from timestamptz, valid_to timestamptz, payload jsonb, fetched_at timestamptz,
            provenance jsonb)
    ), ins as (
        insert into public.weather_forecast_issuance (source, product, slot_id, issued_at, source_updated_at,
                                                      valid_from, valid_to, payload, fetched_at, provenance)
        select source, product, slot_id, issued_at, source_updated_at, valid_from, valid_to, payload,
               fetched_at, coalesce(provenance, '{}'::jsonb)
        from r
        on conflict on constraint weather_forecast_issuance_dedup_key do nothing
        returning 1
    )
    select (select count(*) from r), (select count(*) from ins) into v_fc_received, v_fc_inserted;

    -- Latest telemetry cache, one metric of one station at a time.
    for c in
        select * from jsonb_to_recordset(coalesce(p_batch->'telemetry_cache', '[]'::jsonb)) as x(
            station_id text, metric text, value jsonb, observed_at timestamptz, valid_until timestamptz)
        order by station_id, observed_at
    loop
        select * into t from public.weather_telemetry
        where station_id = c.station_id and metric_type = 'realtime_sensor'
        for update;
        if not found then
            insert into public.weather_telemetry (station_id, metric_type, data, valid_start, valid_end,
                                                  updated_at, source_times)
            values (c.station_id, 'realtime_sensor', jsonb_build_object(c.metric, c.value), c.observed_at,
                    c.valid_until, now(), jsonb_build_object(c.metric, c.observed_at));
            v_tel_updated := v_tel_updated + 1;
            continue;
        end if;
        v_prev := (t.source_times->>c.metric)::timestamptz;
        if (v_prev is not null and c.observed_at <= v_prev)
           or (t.source_times is null and c.observed_at < t.valid_start) then
            continue;
        end if;
        v_times := coalesce(t.source_times, '{}'::jsonb) || jsonb_build_object(c.metric, c.observed_at);
        update public.weather_telemetry
        set data = data || jsonb_build_object(c.metric, c.value),
            source_times = v_times,
            valid_start = (select min(value::timestamptz) from jsonb_each_text(v_times)),
            valid_end = greatest(valid_end, c.valid_until),
            updated_at = now()
        where station_id = c.station_id and metric_type = 'realtime_sensor';
        v_tel_updated := v_tel_updated + 1;
    end loop;

    -- Latest forecast cache, one slot at a time.
    for c in
        select * from jsonb_to_recordset(coalesce(p_batch->'forecast_cache', '[]'::jsonb)) as x(
            forecast_type text, slot_id text, data jsonb, valid_period jsonb, source_time timestamptz)
        order by forecast_type, slot_id, source_time
    loop
        if c.forecast_type = '4day' then
            select max(source_issued_at) into v_newest_4day
            from public.weather_forecasts where forecast_type = '4day';
            if c.source_time < v_newest_4day then
                continue;
            end if;
        end if;
        select * into f from public.weather_forecasts
        where forecast_type = c.forecast_type and slot_id = c.slot_id
        for update;
        if found and ((f.source_issued_at is not null and c.source_time <= f.source_issued_at)
                      or (f.source_issued_at is null and c.source_time < f.updated_at)) then
            continue;
        end if;
        insert into public.weather_forecasts (forecast_type, slot_id, data, valid_period, updated_at,
                                              source_issued_at)
        values (c.forecast_type, c.slot_id, c.data, c.valid_period, now(), c.source_time)
        on conflict (forecast_type, slot_id) do update
            set data = excluded.data, valid_period = excluded.valid_period, updated_at = excluded.updated_at,
                source_issued_at = excluded.source_issued_at;
        v_fc_updated := v_fc_updated + 1;
    end loop;

    -- Station lookup: add supported metrics, follow the provider's name
    -- and location, never remove a tag or change a station__id.
    for c in
        select * from jsonb_to_recordset(coalesce(p_batch->'stations', '[]'::jsonb)) as x(
            station_id text, station_name text, latitude double precision, longitude double precision,
            tag text)
    loop
        if c.station_id is not null then
            update public."WeatherStationLookup" s
            set "Measurement" = case when coalesce(s."Measurement", '[]'::jsonb) ? c.tag
                                     then s."Measurement"
                                     else coalesce(s."Measurement", '[]'::jsonb) || to_jsonb(c.tag) end,
                station__name = coalesce(c.station_name, s.station__name),
                location__latitude = coalesce(c.latitude, s.location__latitude),
                location__longitude = coalesce(c.longitude, s.location__longitude)
            where s.station__id = c.station_id
              and (not coalesce(s."Measurement", '[]'::jsonb) ? c.tag
                   or s.station__name is distinct from coalesce(c.station_name, s.station__name)
                   or s.location__latitude is distinct from coalesce(c.latitude, s.location__latitude)
                   or s.location__longitude is distinct from coalesce(c.longitude, s.location__longitude));
            get diagnostics v_n = row_count;
            v_st_changed := v_st_changed + v_n;
            if v_n = 0 and not exists (select 1 from public."WeatherStationLookup"
                                       where station__id = c.station_id) then
                insert into public."WeatherStationLookup" ("Id", station__id, station__name, location__latitude,
                                                           location__longitude, "Measurement")
                select coalesce(max("Id"), 0) + 1, c.station_id, c.station_name, c.latitude, c.longitude,
                       jsonb_build_array(c.tag)
                from public."WeatherStationLookup";
                v_st_changed := v_st_changed + 1;
            end if;
        else
            update public."WeatherStationLookup" s
            set location__latitude = coalesce(c.latitude, s.location__latitude),
                location__longitude = coalesce(c.longitude, s.location__longitude)
            where s.station__name = c.station_name and coalesce(s."Measurement", '[]'::jsonb) ? c.tag
              and (s.location__latitude is distinct from coalesce(c.latitude, s.location__latitude)
                   or s.location__longitude is distinct from coalesce(c.longitude, s.location__longitude));
            get diagnostics v_n = row_count;
            v_st_changed := v_st_changed + v_n;
            if not exists (select 1 from public."WeatherStationLookup" s
                           where s.station__name = c.station_name
                             and coalesce(s."Measurement", '[]'::jsonb) ? c.tag) then
                insert into public."WeatherStationLookup" ("Id", station__id, station__name, location__latitude,
                                                           location__longitude, "Measurement")
                select coalesce(max("Id"), 0) + 1, null, c.station_name, c.latitude, c.longitude,
                       jsonb_build_array(c.tag)
                from public."WeatherStationLookup";
                v_st_changed := v_st_changed + 1;
            end if;
        end if;
    end loop;

    return jsonb_build_object(
        'observations', jsonb_build_object('received', v_obs_received, 'inserted', v_obs_inserted),
        'forecasts', jsonb_build_object('received', v_fc_received, 'inserted', v_fc_inserted),
        'telemetry_cache_updated', v_tel_updated,
        'forecast_cache_updated', v_fc_updated,
        'stations_changed', v_st_changed);
end;
$$;


-- weather_forecast_as_of(product, slot_id, as_of, valid_at default null)
-- The newest issuance of one product and slot usable at p_as_of
-- (available_at <= p_as_of); with p_valid_at, only issuances whose
-- validity window contains it. At most one row. Access: service role.
create or replace function public.weather_forecast_as_of(
    p_product text, p_slot_id text, p_as_of timestamptz, p_valid_at timestamptz default null)
returns table (id bigint, product text, slot_id text, issued_at timestamptz, source_updated_at timestamptz,
               available_at timestamptz, fetched_at timestamptz, valid_from timestamptz, valid_to timestamptz,
               payload jsonb)
language sql
stable
set search_path = ''
as $$
    select f.id, f.product, f.slot_id, f.issued_at, f.source_updated_at, f.available_at, f.fetched_at,
           f.valid_from, f.valid_to, f.payload
    from public.weather_forecast_issuance f
    where f.product = p_product and f.slot_id = p_slot_id and f.available_at <= p_as_of
      and (p_valid_at is null or (f.valid_from <= p_valid_at and p_valid_at < f.valid_to))
    order by f.available_at desc, f.issued_at desc nulls last, f.fetched_at desc, f.id desc
    limit 1;
$$;


-- weather_observations_as_of(station_id, metric, from, to, as_of)
-- Station observations of one metric lying inside [p_from, p_to] and
-- already observed at p_as_of (observed_to <= p_as_of), oldest first.
-- Access: service role.
create or replace function public.weather_observations_as_of(
    p_station_id text, p_metric text, p_from timestamptz, p_to timestamptz, p_as_of timestamptz)
returns table (id bigint, source text, station_id text, metric text, value double precision, unit text,
               semantics text, observed_from timestamptz, observed_to timestamptz, fetched_at timestamptz,
               regime_id text)
language sql
stable
set search_path = ''
as $$
    select o.id, o.source, o.station_id, o.metric, o.value, o.unit, o.semantics, o.observed_from,
           o.observed_to, o.fetched_at, o.regime_id
    from public.weather_observation o
    where o.series = 'station' and o.station_id = p_station_id and o.metric = p_metric
      and o.observed_from >= p_from and o.observed_to <= p_to and o.observed_to <= p_as_of
    order by o.observed_to, o.observed_from, o.id;
$$;


-- weather_rainfall_total(station_id, from, to, as_of default null) returns jsonb
-- Rainfall over [p_from, p_to] at one station from disjoint interval
-- totals (rules in the header). Output:
--   {"station_id", "from", "to", "as_of", "total_mm" (null without data),
--    "unit": "mm", "window_seconds", "covered_seconds", "coverage" (0-1),
--    "intervals", "overlapping_skipped", "missing": [{"from", "to"}],
--    "status": "complete" | "partial" | "no_data"}
-- Access: service role.
create or replace function public.weather_rainfall_total(
    p_station_id text, p_from timestamptz, p_to timestamptz, p_as_of timestamptz default null)
returns jsonb
language plpgsql
stable
set search_path = ''
as $$
declare
    v_end      timestamptz := least(p_to, coalesce(p_as_of, p_to));
    v_cursor   timestamptz := p_from;
    v_total    double precision := 0;
    v_covered  double precision := 0;
    v_count    integer := 0;
    v_skipped  integer := 0;
    v_missing  jsonb := '[]'::jsonb;
    v_window   double precision := extract(epoch from (p_to - p_from));
    r          record;
begin
    if p_to <= p_from then
        raise exception 'weather_rainfall_total: p_to must be after p_from';
    end if;
    for r in
        select o.observed_from, o.observed_to, o.value
        from public.weather_observation o
        where o.series = 'station' and o.station_id = p_station_id and o.metric = 'rainfall'
          and o.semantics = 'interval_total' and o.unit = 'mm'
          and o.observed_from >= p_from and o.observed_to <= v_end
        order by o.observed_from, o.observed_to, o.id
    loop
        if r.observed_from < v_cursor then
            v_skipped := v_skipped + 1;
            continue;
        end if;
        if r.observed_from > v_cursor then
            v_missing := v_missing || jsonb_build_array(jsonb_build_object('from', v_cursor, 'to', r.observed_from));
        end if;
        v_total := v_total + r.value;
        v_covered := v_covered + extract(epoch from (r.observed_to - r.observed_from));
        v_count := v_count + 1;
        v_cursor := r.observed_to;
    end loop;
    if v_cursor < p_to then
        v_missing := v_missing || jsonb_build_array(jsonb_build_object('from', v_cursor, 'to', p_to));
    end if;
    return jsonb_build_object(
        'station_id', p_station_id, 'from', p_from, 'to', p_to, 'as_of', p_as_of,
        'total_mm', case when v_count > 0 then round(v_total::numeric, 3) end,
        'unit', 'mm',
        'window_seconds', v_window,
        'covered_seconds', v_covered,
        'coverage', round((v_covered / v_window)::numeric, 6),
        'intervals', v_count,
        'overlapping_skipped', v_skipped,
        'missing', v_missing,
        'status', case when v_count = 0 then 'no_data'
                       when v_covered >= v_window then 'complete'
                       else 'partial' end);
end;
$$;


revoke all on function public.ingest_weather_batch(jsonb) from public, anon, authenticated;
revoke all on function public.weather_forecast_as_of(text, text, timestamptz, timestamptz)
    from public, anon, authenticated;
revoke all on function public.weather_observations_as_of(text, text, timestamptz, timestamptz, timestamptz)
    from public, anon, authenticated;
revoke all on function public.weather_rainfall_total(text, timestamptz, timestamptz, timestamptz)
    from public, anon, authenticated;
grant execute on function public.ingest_weather_batch(jsonb) to service_role;
grant execute on function public.weather_forecast_as_of(text, text, timestamptz, timestamptz) to service_role;
grant execute on function public.weather_observations_as_of(text, text, timestamptz, timestamptz, timestamptz)
    to service_role;
grant execute on function public.weather_rainfall_total(text, timestamptz, timestamptz, timestamptz)
    to service_role;
