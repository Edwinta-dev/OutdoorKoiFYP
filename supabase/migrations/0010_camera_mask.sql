-- 0010_camera_mask.sql
--
-- Per-pond water mask for the camera's green-ratio analysis (issue #39).
-- The camera service computes the green ratio over the pixels inside the
-- pond's mask only, so planting, edges and decking in view no longer
-- change it.
--
-- Mask format: a normalised polygon, a JSON array of [x, y] points with
-- x and y in 0..1 as fractions of the frame's width and height, origin at
-- the top-left corner. 3 to 64 points; the enclosed area (shoelace
-- formula) must be at least 0.01 of the frame. The backend checks the
-- same rules before saving (koi/camera/mask.py); camera_mask_is_valid
-- below enforces them in the table checks.
--
-- Versions: each save of a pond's mask gets the next mask_version (1, 2,
-- ...) and is kept unchanged in camera_mask_version, so a stored frame's
-- mask_version always names the polygon it was analysed over.
-- camera_config holds the mask in force now. Its updated_at is the time
-- of the last save; the version, not the time, identifies the mask.
--
-- imageTable gains two nullable columns, written by the camera service:
--   mask_version    the camera_mask_version the frame was analysed over;
--                   null = the whole frame (every frame stored before this
--                   migration, and frames from a pond with no mask or
--                   whose mask could not be read). Rows written before
--                   this migration keep null and stay readable.
--   baseline_reset  why the camera's smoothed baseline and its frame
--                   counters were restarted at this frame, or null.
--                   'mask_changed': this frame's mask_version differs from
--                   the previous frame's, so the old baseline was measured
--                   over a different area. The reset is decided from these
--                   stored columns, so it survives service restarts.
--
-- A save and a concurrent upload: the camera service reads mask and
-- mask_version from one camera_config row and stores that version with
-- the ratio it computed over that polygon. A frame analysed just before
-- a save therefore carries the old version, and the next frame (new
-- version) resets the baseline.
--
-- Camera identity: one camera per pond (the upload's X-User-ID), so the
-- mask is keyed by pond only.
--
-- Access: backend only (service role). RLS on with no policies on both
-- new tables, and the default grants to anon and authenticated revoked.
-- save_camera_mask and camera_mask_is_valid are not callable by anon or
-- authenticated. Ownership is checked by the API
-- (GET/PUT /v1/ponds/{pond}/camera/mask): only the account linked to the
-- pond reaches it. imageTable's existing grants are unchanged.
--
-- pond_id is UserData."userID", as in pond_profile (0008); no cascade on
-- delete, so a pond's mask history is not removed with its UserData row.

create or replace function public.camera_mask_is_valid(p_mask jsonb)
returns boolean
language plpgsql
immutable
set search_path = ''
as $$
declare
    n integer;
    i integer;
    pt jsonb;
    x numeric;
    y numeric;
    x0 numeric;
    y0 numeric;
    xp numeric;
    yp numeric;
    twice_area numeric := 0;
begin
    if p_mask is null or jsonb_typeof(p_mask) <> 'array' then
        return false;
    end if;
    n := jsonb_array_length(p_mask);
    if n < 3 or n > 64 then
        return false;
    end if;
    for i in 0 .. n - 1 loop
        pt := p_mask -> i;
        if jsonb_typeof(pt) <> 'array' or jsonb_array_length(pt) <> 2
           or jsonb_typeof(pt -> 0) <> 'number' or jsonb_typeof(pt -> 1) <> 'number' then
            return false;
        end if;
        x := (pt ->> 0)::numeric;
        y := (pt ->> 1)::numeric;
        if x < 0 or x > 1 or y < 0 or y > 1 then
            return false;
        end if;
        if i = 0 then
            x0 := x;
            y0 := y;
        else
            twice_area := twice_area + (xp * y - x * yp);
        end if;
        xp := x;
        yp := y;
    end loop;
    twice_area := twice_area + (xp * y0 - x0 * yp);
    return abs(twice_area) / 2 >= 0.01;
end;
$$;

revoke all on function public.camera_mask_is_valid(jsonb) from public, anon, authenticated;
-- The table checks call it as the writing role.
grant execute on function public.camera_mask_is_valid(jsonb) to service_role;


create table if not exists public.camera_mask_version (
    pond_id       bigint not null references public."UserData" ("userID") on update cascade,
    mask_version  integer not null check (mask_version > 0),
    mask          jsonb not null check (public.camera_mask_is_valid(mask)),
    created_at    timestamptz not null default now(),
    primary key (pond_id, mask_version)
);

alter table public.camera_mask_version enable row level security;

revoke all on table public.camera_mask_version from anon, authenticated;


create table if not exists public.camera_config (
    pond_id       bigint primary key references public."UserData" ("userID") on update cascade,
    mask          jsonb not null check (public.camera_mask_is_valid(mask)),
    mask_version  integer not null check (mask_version > 0),
    updated_at    timestamptz not null default now(),
    foreign key (pond_id, mask_version) references public.camera_mask_version (pond_id, mask_version)
);

alter table public.camera_config enable row level security;

revoke all on table public.camera_config from anon, authenticated;


-- Saved masks are immutable: a changed mask is a new version. pond_id
-- may still follow a UserData id change (on update cascade).
create or replace function public.refuse_camera_mask_version_change()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    raise exception 'camera_mask_version rows are immutable; save a new mask version instead'
        using errcode = 'check_violation';
end;
$$;

revoke all on function public.refuse_camera_mask_version_change() from public, anon, authenticated;

drop trigger if exists camera_mask_version_immutable on public.camera_mask_version;
create trigger camera_mask_version_immutable
    before update of mask, mask_version, created_at or delete on public.camera_mask_version
    for each row execute function public.refuse_camera_mask_version_change();


-- save_camera_mask(p_pond_id, p_mask)
--   Stores p_mask as the pond's next mask version and makes it the mask
--   in force, in one transaction. Concurrent saves for one pond are
--   serialised by a transaction advisory lock on the pond id (also for a
--   pond's first save, when no camera_config row exists to lock), so
--   versions never repeat.
--   Inputs:  p_pond_id  UserData."userID" (must exist)
--            p_mask     a polygon as described above
--   Output:  the camera_config row: pond_id, mask, mask_version, updated_at
--   Errors:  22023 (invalid_parameter_value) for an invalid polygon;
--            23503 (foreign_key_violation) for an unknown pond.
--   Access:  service role only.
create or replace function public.save_camera_mask(p_pond_id bigint, p_mask jsonb)
returns table (pond_id bigint, mask jsonb, mask_version integer, updated_at timestamptz)
language plpgsql
set search_path = ''
as $$
#variable_conflict use_column
declare
    v_version integer;
begin
    if not public.camera_mask_is_valid(p_mask) then
        raise exception 'mask must be 3 to 64 [x, y] points in 0..1 enclosing at least 0.01 of the frame'
            using errcode = 'invalid_parameter_value';
    end if;

    perform pg_advisory_xact_lock(hashtext('camera_mask'), hashtext(p_pond_id::text));

    select c.mask_version into v_version
    from public.camera_config c
    where c.pond_id = p_pond_id;
    v_version := coalesce(v_version, 0) + 1;

    insert into public.camera_mask_version (pond_id, mask_version, mask)
    values (p_pond_id, v_version, p_mask);

    insert into public.camera_config (pond_id, mask, mask_version, updated_at)
    values (p_pond_id, p_mask, v_version, now())
    on conflict on constraint camera_config_pkey do update
        set mask = excluded.mask, mask_version = excluded.mask_version, updated_at = excluded.updated_at;

    return query
        select c.pond_id, c.mask, c.mask_version, c.updated_at
        from public.camera_config c
        where c.pond_id = p_pond_id;
end;
$$;

revoke all on function public.save_camera_mask(bigint, jsonb) from public, anon, authenticated;
grant execute on function public.save_camera_mask(bigint, jsonb) to service_role;


alter table public."imageTable"
    add column if not exists mask_version integer,
    add column if not exists baseline_reset text;
