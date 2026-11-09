-- Issue #149: immutable per-pond placements of the shared colour-card template.
create or replace function public.camera_card_placements_are_valid(p jsonb)
returns boolean
language plpgsql immutable
set search_path = ''
as $$
declare
    mask_id text;
    item jsonb;
    key_name text;
    value jsonb;
begin
    if jsonb_typeof(p) <> 'object' or p - array['white','nh3','ph'] <> '{}'::jsonb
       or (select count(*) from jsonb_object_keys(p)) <> 3 then return false; end if;
    foreach mask_id in array array['white','nh3','ph'] loop
        item := p -> mask_id;
        if jsonb_typeof(item) <> 'object'
           or item - array['cx','cy','scale','angle'] <> '{}'::jsonb
           or (select count(*) from jsonb_object_keys(item)) <> 4 then return false; end if;
        foreach key_name in array array['cx','cy','scale','angle'] loop
            value := item -> key_name;
            if jsonb_typeof(value) <> 'number' then return false; end if;
        end loop;
        if (item ->> 'scale')::numeric < 0.2 or (item ->> 'scale')::numeric > 3 then return false; end if;
    end loop;
    return true;
exception when others then return false;
end;
$$;
revoke all on function public.camera_card_placements_are_valid(jsonb) from public, anon, authenticated;
grant execute on function public.camera_card_placements_are_valid(jsonb) to authenticated, service_role;

create table public.camera_card_masks (
    pond_id bigint not null references public."UserData" ("userID") on update cascade,
    version integer not null check (version > 0),
    placements jsonb not null check (public.camera_card_placements_are_valid(placements)),
    reference_image_id bigint null references public."imageTable" (id) on update cascade,
    created_at timestamptz not null default now(),
    primary key (pond_id, version)
);
alter table public.camera_card_masks enable row level security;
revoke all on table public.camera_card_masks from anon, authenticated;
grant select, insert on table public.camera_card_masks to authenticated;
grant all on table public.camera_card_masks to service_role;
create policy camera_card_masks_owner_read on public.camera_card_masks for select to authenticated
    using (exists (select 1 from public."UserData" u
                   where u."userID" = pond_id and u.auth_uid = (select auth.uid())));
create policy camera_card_masks_owner_insert on public.camera_card_masks for insert to authenticated
    with check (exists (select 1 from public."UserData" u
                        where u."userID" = pond_id and u.auth_uid = (select auth.uid())));

create or replace function public.refuse_camera_card_mask_change()
returns trigger language plpgsql set search_path = '' as $$
begin
    raise exception 'camera_card_masks rows are immutable; save a new version instead'
        using errcode = 'check_violation';
end;
$$;
revoke all on function public.refuse_camera_card_mask_change() from public, anon, authenticated;
create trigger camera_card_masks_immutable
    before update of version, placements, reference_image_id, created_at or delete on public.camera_card_masks
    for each row execute function public.refuse_camera_card_mask_change();

create or replace function public.save_camera_card_masks(
    p_pond_id bigint, p_placements jsonb, p_reference_image_id bigint default null)
returns table (pond_id bigint, version integer, placements jsonb, reference_image_id bigint, created_at timestamptz)
language plpgsql set search_path = '' as $$
declare v_version integer;
begin
    if not public.camera_card_placements_are_valid(p_placements) then
        raise exception 'placements must contain white, nh3 and ph; each needs numeric cx, cy, scale 0.2..3 and angle'
            using errcode = 'invalid_parameter_value';
    end if;
    perform pg_advisory_xact_lock(hashtext('camera_card_masks'), hashtext(p_pond_id::text));
    select coalesce(max(m.version), 0) + 1 into v_version
    from public.camera_card_masks m where m.pond_id = p_pond_id;
    insert into public.camera_card_masks as m (pond_id, version, placements, reference_image_id)
    values (p_pond_id, v_version, p_placements, p_reference_image_id);
    return query select m.pond_id, m.version, m.placements, m.reference_image_id, m.created_at
        from public.camera_card_masks m where m.pond_id = p_pond_id and m.version = v_version;
end;
$$;
revoke all on function public.save_camera_card_masks(bigint, jsonb, bigint) from public, anon, authenticated;
grant execute on function public.save_camera_card_masks(bigint, jsonb, bigint) to service_role;
