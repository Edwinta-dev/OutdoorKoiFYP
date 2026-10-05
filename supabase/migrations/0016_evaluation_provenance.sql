-- 0016_evaluation_provenance.sql
--
-- Model provenance on every evaluation row (issue #21).
--
-- pond_chemistry_evaluations, pond_evaporation_evaluations and
-- pond_algae_evaluations each gain four nullable columns, written by the
-- poller and the API's event endpoints on every insert
-- (koi/provenance.py):
--
--   model_version       text, the backend that made the row: koi.__version__
--                       plus "+g<commit>" when the git commit is known
--                       (".dirty" appended when the working tree had
--                       uncommitted changes), for example "0.1.0+g1a2b3c4d5e6f".
--
--   input_cutoff        timestamptz, the newest input time the pond's twin
--                       had consumed when the row was made: the latest of
--                       its sensor sample times, logged event times and
--                       camera frame times. It is model input time, not
--                       computation time; evaluated_at stays the time the
--                       row was computed. null when the twin had consumed
--                       no input.
--
--   forecast_issued_at  timestamptz, the weather provider's issue time
--                       (weather_forecasts.source_issued_at, migration
--                       0009) of the forecast the row used, when every
--                       forecast record it used has that same issue time.
--                       null when the issue time is unknown or the row used
--                       records with different issue times (for example
--                       the 2-hour nowcast and the 4-day outlook); the
--                       per-record times are in inputs.forecasts. Never
--                       the cache's updated_at or the evaluation time.
--
--   inputs              jsonb object, what the run used:
--                         {"run": "poll" | "event" | "rating" | "rating_undo",
--                          "sensor_groups": n, "events": n,
--                          "camera_frames": n, "ratings": n,
--                          "forecasts": {<product>: {"available": bool,
--                            "records": [{"forecast_type", "slot_id",
--                                         "issued_at", "cache_updated_at"}],
--                            "issued_at": common issue time or null}}}
--                       The counts are inputs applied to the twin in that
--                       run. A record's issued_at is null when the cache
--                       row has no source_issued_at or no longer holds the
--                       data the run read.
--
-- Rows written before this migration keep null in all four: their
-- provenance is unknown, and readers show it as unknown. They are not
-- backfilled, because the version and inputs that made them were not
-- recorded.
--
-- Indexes: none added; the rows are read newest first per pond by the
-- existing (userid, evaluated_at desc) indexes.
--
-- Access: the tables' grants and policies are unchanged.

alter table public.pond_chemistry_evaluations
    add column if not exists model_version text,
    add column if not exists input_cutoff timestamptz,
    add column if not exists forecast_issued_at timestamptz,
    add column if not exists inputs jsonb;

alter table public.pond_evaporation_evaluations
    add column if not exists model_version text,
    add column if not exists input_cutoff timestamptz,
    add column if not exists forecast_issued_at timestamptz,
    add column if not exists inputs jsonb;

alter table public.pond_algae_evaluations
    add column if not exists model_version text,
    add column if not exists input_cutoff timestamptz,
    add column if not exists forecast_issued_at timestamptz,
    add column if not exists inputs jsonb;

alter table public.pond_chemistry_evaluations
    drop constraint if exists pond_chemistry_evaluations_provenance_shape,
    add constraint pond_chemistry_evaluations_provenance_shape check (
        (model_version is null or model_version <> '')
        and (inputs is null or jsonb_typeof(inputs) = 'object')
    );

alter table public.pond_evaporation_evaluations
    drop constraint if exists pond_evaporation_evaluations_provenance_shape,
    add constraint pond_evaporation_evaluations_provenance_shape check (
        (model_version is null or model_version <> '')
        and (inputs is null or jsonb_typeof(inputs) = 'object')
    );

alter table public.pond_algae_evaluations
    drop constraint if exists pond_algae_evaluations_provenance_shape,
    add constraint pond_algae_evaluations_provenance_shape check (
        (model_version is null or model_version <> '')
        and (inputs is null or jsonb_typeof(inputs) = 'object')
    );

comment on column public.pond_chemistry_evaluations.model_version is
    'Backend version that made the row (koi.__version__ plus +g<commit>); null on rows before 0016.';
comment on column public.pond_chemistry_evaluations.input_cutoff is
    'Newest sensor sample, event or camera frame time the twin had consumed; null on rows before 0016.';
comment on column public.pond_chemistry_evaluations.forecast_issued_at is
    'Provider issue time shared by every forecast record used; null when unknown or mixed (see inputs.forecasts).';
comment on column public.pond_chemistry_evaluations.inputs is
    'Inputs applied in the run (counts) and per-record forecast provenance; null on rows before 0016.';

comment on column public.pond_evaporation_evaluations.model_version is
    'Backend version that made the row (koi.__version__ plus +g<commit>); null on rows before 0016.';
comment on column public.pond_evaporation_evaluations.input_cutoff is
    'Newest sensor sample, event or camera frame time the twin had consumed; null on rows before 0016.';
comment on column public.pond_evaporation_evaluations.forecast_issued_at is
    'Provider issue time shared by every forecast record used; null when unknown or mixed (see inputs.forecasts).';
comment on column public.pond_evaporation_evaluations.inputs is
    'Inputs applied in the run (counts) and per-record forecast provenance; null on rows before 0016.';

comment on column public.pond_algae_evaluations.model_version is
    'Backend version that made the row (koi.__version__ plus +g<commit>); null on rows before 0016.';
comment on column public.pond_algae_evaluations.input_cutoff is
    'Newest sensor sample, event or camera frame time the twin had consumed; null on rows before 0016.';
comment on column public.pond_algae_evaluations.forecast_issued_at is
    'Provider issue time shared by every forecast record used; null when unknown or mixed (see inputs.forecasts).';
comment on column public.pond_algae_evaluations.inputs is
    'Inputs applied in the run (counts) and per-record forecast provenance; null on rows before 0016.';
