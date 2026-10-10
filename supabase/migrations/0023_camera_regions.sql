-- 0023_camera_regions.sql
--
-- Named camera regions (issue #91). The pond's camera config grows from
-- one water-mask polygon (0010) to named regions, each a polygon checked
-- by camera_mask_is_valid:
--   water_gap  required  a fixed spot of open water: the main algae signal
--   rim        required  a strip of the hard pond edge (wall film)
--   plants     required  plant cover, for context only
--   reference  optional  a fixed matte white or grey patch; each frame's
--                        channels are divided by its channel means before
--                        GCC is computed, so colour drift cancels
-- Regions format: a JSON object {"water_gap": [[x, y], ...], ...} with
-- exactly those keys; camera_regions_are_valid below enforces it, and the
-- backend checks the same rules (koi/camera/mask.py::validate_regions).
--
-- Versioning: regions are saved in the same camera_mask_version row as
-- the mask, so changing any region is a new mask_version and the camera
-- writes baseline_reset = 'mask_changed' on the next frame, as for a mask
-- change. Saved versions stay immutable, now including their regions.
--
-- mask becomes nullable in camera_config and camera_mask_version: a config
-- may hold regions without a water mask (the green ratio then uses the
-- whole frame, as for a pond with no config). A row needs a mask, regions
-- or both. Rows written before this migration have a mask and null
-- regions and are unchanged; a config with only a mask behaves as before.
--
-- save_camera_mask gains p_regions (default null), so the two-argument
-- call keeps working unchanged. The old two-argument function is dropped
-- and replaced, with the same access: service role only.
--
-- imageTable gains regions (jsonb, nullable): the camera's per-region
-- metrics for the frame (koi/camera/hsvEngine.py::region_metrics), null
-- for rows written before this migration and frames of ponds without
-- regions. Version 1:
--   {"version": 1,
--    "reference": null | {"applied", "pixels", "r", "g", "b"},
--    "regions": {"water_gap": {"pixels", "usable_pixels", "gcc", "exg",
--                              "r", "g", "b", "plant_fraction",
--                              "open_fraction", "usable",
--                              "unusable_reason"},
--                "rim" | "plants" | "reference": {"pixels",
--                              "usable_pixels", "gcc", "exg", "r", "g", "b"}},
--    "thresholds": {...}}
-- imageTable's grants and indexes are unchanged.
--
-- Setting regions for pond 455 before the app editor (#60) exists, as the
-- service role (for example in the SQL editor). Coordinates are fractions
-- of the frame, origin top-left; replace them with the spots chosen from a
-- current frame. Passing the mask in force (or null) keeps or clears it:
--
--   select * from public.save_camera_mask(
--       455,
--       (select mask from public.camera_config where pond_id = 455),
--       '{"water_gap": [[0.30, 0.40], [0.45, 0.40], [0.45, 0.55], [0.30, 0.55]],
--         "rim":       [[0.70, 0.00], [0.78, 0.00], [0.78, 1.00], [0.70, 1.00]],
--         "plants":    [[0.00, 0.20], [0.65, 0.20], [0.65, 1.00], [0.00, 1.00]]}'::jsonb);
--
-- The API does the same: PUT /v1/ponds/455/camera/mask with
-- {"polygon": null, "regions": {...}}.

create or replace function public.camera_regions_are_valid(p_regions jsonb)
returns boolean
language plpgsql
immutable
set search_path = ''
as $$
declare
    k text;
begin
    if p_regions is null or jsonb_typeof(p_regions) <> 'object' then
        return false;
    end if;
    if not (p_regions ?& array['water_gap', 'rim', 'plants']) then
        return false;
    end if;
    for k in select jsonb_object_keys(p_regions) loop
        if k not in ('water_gap', 'rim', 'plants', 'reference') then
            return false;
        end if;
        if not public.camera_mask_is_valid(p_regions -> k) then
            return false;
        end if;
    end loop;
    return true;
end;
$$;

revoke all on function public.camera_regions_are_valid(jsonb) from public, anon, authenticated;
-- The table checks call it as the writing role.
grant execute on function public.camera_regions_are_valid(jsonb) to service_role;


alter table public.camera_mask_version
    add column regions jsonb,
    alter column mask drop not null,
    drop constraint camera_mask_version_mask_check,
    add constraint camera_mask_version_mask_check
        check (mask is null or public.camera_mask_is_valid(mask)),
    add constraint camera_mask_version_regions_check
        check (regions is null or public.camera_regions_are_valid(regions)),
    add constraint camera_mask_version_not_empty
        check (mask is not null or regions is not null);

alter table public.camera_config
    add column regions jsonb,
    alter column mask drop not null,
    drop constraint camera_config_mask_check,
    add constraint camera_config_mask_check
        check (mask is null or public.camera_mask_is_valid(mask)),
    add constraint camera_config_regions_check
        check (regions is null or public.camera_regions_are_valid(regions)),
    add constraint camera_config_not_empty
        check (mask is not null or regions is not null);


-- Saved versions stay immutable, including their regions.
drop trigger if exists camera_mask_version_immutable on public.camera_mask_version;
create trigger camera_mask_version_immutable
    before update of mask, regions, mask_version, created_at or delete on public.camera_mask_version
    for each row execute function public.refuse_camera_mask_version_change();


-- save_camera_mask(p_pond_id, p_mask, p_regions default null)
--   As in 0010, with regions saved in the same version as the mask.
--   Inputs:  p_pond_id  UserData."userID" (must exist)
--            p_mask     a polygon, or null for no water mask
--            p_regions  named regions as above, or null for none
--   Output:  the camera_config row: pond_id, mask, mask_version,
--            updated_at, regions
--   Errors:  22023 (invalid_parameter_value) for an invalid polygon or
--            regions, or when both are null;
--            23503 (foreign_key_violation) for an unknown pond.
--   Access:  service role only.
drop function if exists public.save_camera_mask(bigint, jsonb);

create or replace function public.save_camera_mask(p_pond_id bigint, p_mask jsonb, p_regions jsonb default null)
returns table (pond_id bigint, mask jsonb, mask_version integer, updated_at timestamptz, regions jsonb)
language plpgsql
set search_path = ''
as $$
#variable_conflict use_column
declare
    v_version integer;
begin
    if p_mask is null and p_regions is null then
        raise exception 'a camera config needs a mask, regions or both'
            using errcode = 'invalid_parameter_value';
    end if;
    if p_mask is not null and not public.camera_mask_is_valid(p_mask) then
        raise exception 'mask must be 3 to 64 [x, y] points in 0..1 enclosing at least 0.01 of the frame'
            using errcode = 'invalid_parameter_value';
    end if;
    if p_regions is not null and not public.camera_regions_are_valid(p_regions) then
        raise exception 'regions must be water_gap, rim, plants and optionally reference, each a valid mask polygon'
            using errcode = 'invalid_parameter_value';
    end if;

    perform pg_advisory_xact_lock(hashtext('camera_mask'), hashtext(p_pond_id::text));

    select c.mask_version into v_version
    from public.camera_config c
    where c.pond_id = p_pond_id;
    v_version := coalesce(v_version, 0) + 1;

    insert into public.camera_mask_version (pond_id, mask_version, mask, regions)
    values (p_pond_id, v_version, p_mask, p_regions);

    insert into public.camera_config (pond_id, mask, regions, mask_version, updated_at)
    values (p_pond_id, p_mask, p_regions, v_version, now())
    on conflict on constraint camera_config_pkey do update
        set mask = excluded.mask, regions = excluded.regions, mask_version = excluded.mask_version,
            updated_at = excluded.updated_at;

    return query
        select c.pond_id, c.mask, c.mask_version, c.updated_at, c.regions
        from public.camera_config c
        where c.pond_id = p_pond_id;
end;
$$;

revoke all on function public.save_camera_mask(bigint, jsonb, jsonb) from public, anon, authenticated;
grant execute on function public.save_camera_mask(bigint, jsonb, jsonb) to service_role;


alter table public."imageTable"
    add column regions jsonb;

comment on column public."imageTable".regions is
    'Version 1 per-region camera metrics (issue #91): reference correction, and per region pixels, usable_pixels, '
    'gcc, exg, r, g, b; water_gap adds plant_fraction, open_fraction, usable, unusable_reason. '
    'Null for legacy rows and ponds without regions.';
