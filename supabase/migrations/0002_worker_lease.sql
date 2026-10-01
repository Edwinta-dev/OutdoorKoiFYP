-- 0002_worker_lease.sql
--
-- Lets the digital twin API run as several processes next to one poller
-- worker (issue #8):
--
--   * pond_chemistry_state.snapshot_version counts saves of each pond's
--     snapshot. A process remembers the version it loaded, reloads when the
--     stored one is newer, and save_pond_snapshot refuses a save whose base
--     version is no longer current, so two processes cannot overwrite each
--     other's changes.
--   * worker_lease names the one poller worker allowed to poll this cycle.
--     take_worker_lease takes an absent or expired lease, or renews one the
--     caller already holds; any other worker stays on standby.
--
-- Existing snapshot rows take version 1, the same as a first save, so they
-- keep loading unchanged. Only the backend (secret key) calls these.

alter table public.pond_chemistry_state
    add column if not exists snapshot_version bigint not null default 1;

create table if not exists public.worker_lease (
    name       text primary key,
    holder     text not null,
    expires_at timestamptz not null
);

-- No policies: only the backend's secret key (which bypasses RLS) reads or writes it.
alter table public.worker_lease enable row level security;

-- Saves one pond's snapshot and returns its new version, or null when
-- p_base_version is stale. p_base_version 0 means "no stored snapshot
-- expected" (a new pond); null saves unconditionally.
create or replace function public.save_pond_snapshot(
    p_user_id bigint,
    p_snapshot jsonb,
    p_base_version bigint
)
returns bigint
language plpgsql
as $$
declare
    v_version bigint;
begin
    if p_base_version is null then
        insert into public.pond_chemistry_state (user_id, snapshot, updated_at, snapshot_version)
        values (p_user_id, p_snapshot, now(), 1)
        on conflict (user_id) do update
            set snapshot = excluded.snapshot,
                updated_at = excluded.updated_at,
                snapshot_version = pond_chemistry_state.snapshot_version + 1
        returning snapshot_version into v_version;
    elsif p_base_version = 0 then
        insert into public.pond_chemistry_state (user_id, snapshot, updated_at, snapshot_version)
        values (p_user_id, p_snapshot, now(), 1)
        on conflict (user_id) do nothing
        returning snapshot_version into v_version;
    else
        update public.pond_chemistry_state
           set snapshot = p_snapshot,
               updated_at = now(),
               snapshot_version = snapshot_version + 1
         where user_id = p_user_id
           and snapshot_version = p_base_version
        returning snapshot_version into v_version;
    end if;
    return v_version;
end;
$$;

-- Takes or renews the named lease for p_ttl_seconds. True when p_holder
-- holds it afterwards.
create or replace function public.take_worker_lease(
    p_name text,
    p_holder text,
    p_ttl_seconds integer
)
returns boolean
language plpgsql
as $$
declare
    v_taken boolean;
begin
    insert into public.worker_lease (name, holder, expires_at)
    values (p_name, p_holder, now() + make_interval(secs => p_ttl_seconds))
    on conflict (name) do update
        set holder = excluded.holder,
            expires_at = excluded.expires_at
        where worker_lease.holder = excluded.holder
           or worker_lease.expires_at < now()
    returning true into v_taken;
    return coalesce(v_taken, false);
end;
$$;

-- The app's publishable key must not call these. Supabase's default
-- privileges grant execute on new public functions to the backend's secret
-- key role explicitly, so it keeps access after these revokes.
revoke all on function public.save_pond_snapshot(bigint, jsonb, bigint) from public, anon, authenticated;
revoke all on function public.take_worker_lease(text, text, integer) from public, anon, authenticated;
