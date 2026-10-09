-- Issue #90: additive camera colour observations; old rows stay unknown.
-- Access and indexes on imageTable are unchanged. Means exclude crushed
-- and blown pixels; colour v1 stores normalised RGB/ExG and an unmasked
-- row-major GCC grid with the dimensions used for that frame.
alter table public."imageTable"
    add column gcc real,
    add column colour jsonb;

comment on column public."imageTable".gcc is
    'Mean per-pixel G/(R+G+B), over unclipped water-mask pixels or the whole frame; null for no usable pixels or legacy rows.';
comment on column public."imageTable".colour is
    'Version 1: exg, r, g, b means (0..1), grid {rows, cols, gcc: row-major array}; null means/cells when no usable pixels.';

-- Same input/output and backend-only access as 0011; v1 and v2 are known.
create or replace function public.image_quality_status(p_quality jsonb)
returns text
language sql
immutable
set search_path = ''
as $$
    select case
        when p_quality is not null
             and jsonb_typeof(p_quality) = 'object'
             and jsonb_typeof(p_quality -> 'version') = 'number'
             and (p_quality ->> 'version')::numeric in (1, 2)
             and p_quality ->> 'status' in ('pass', 'fail')
            then p_quality ->> 'status'
        else 'unknown'
    end;
$$;

revoke all on function public.image_quality_status(jsonb) from public, anon, authenticated;
grant execute on function public.image_quality_status(jsonb) to service_role;
