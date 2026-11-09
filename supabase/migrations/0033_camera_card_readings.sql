-- Issue #151: immutable observations produced while a camera frame is uploaded.
create table public.camera_card_readings (
    image_id bigint primary key references public."imageTable"(id) on delete cascade,
    pond_id bigint not null references public."UserData"("userID") on update cascade,
    card_masks_version integer not null check (card_masks_version > 0),
    white_rgb jsonb not null,
    patches jsonb not null,
    ph jsonb,
    nh3 jsonb,
    usable_dots integer not null check (usable_dots >= 0),
    total_dots integer not null check (total_dots >= usable_dots),
    quality text not null check (quality in ('usable', 'unusable')),
    created_at timestamptz not null default now()
);
create index camera_card_readings_latest on public.camera_card_readings (pond_id, created_at desc);
alter table public.camera_card_readings enable row level security;
revoke all on table public.camera_card_readings from anon, authenticated;
grant select on table public.camera_card_readings to authenticated;
grant all on table public.camera_card_readings to service_role;
create policy camera_card_readings_owner_read on public.camera_card_readings for select to authenticated
    using (exists (select 1 from public."UserData" u
                   where u."userID" = pond_id and u.auth_uid = (select auth.uid())));
