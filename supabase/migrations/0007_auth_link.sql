-- 0007_auth_link.sql
--
-- Links a pond to the Supabase account that owns it (issue #11). The
-- digital twin API verifies the caller's access token, looks up the pond
-- whose auth_uid equals the token's sub, and refuses (403) any request
-- whose user_id is not that pond.
--
-- auth_uid is nullable: every pond created before this migration stays
-- unlinked, and an unlinked pond cannot be reached through the API with
-- auth on until the owner links it with link_pond_to_account below.
-- Knowing a pond's numeric id is not proof of owning it, so neither the
-- app nor the API can set auth_uid; the guard trigger refuses any write
-- of it by the anon and authenticated roles.
--
-- The unique index is an interim one-pond-per-account mapping. Issue #67
-- moves ownership to its own table and drops it once an account can own
-- several ponds.
--
-- Access after this migration (grants on the 0001 functions are #12's):
--   backend only (service role): auth_session_active, save_pond_snapshot,
--     take_worker_lease
--   owner only (SQL editor): link_pond_to_account
--   still callable with the app's key, any pond id, until #12:
--     get_bundled_dashboard_payload, get_historical_graph_payload,
--     get_pond_telemetry_history, aggregate_daily_sensor_data

alter table public."UserData"
    add column if not exists auth_uid uuid references auth.users (id) on delete set null;

create unique index if not exists "UserData_auth_uid_key" on public."UserData" (auth_uid);


-- Refuses, for the app's roles, a change of auth_uid and a change of a
-- linked pond's userID (which would point the account at another pond's
-- data). The backend (service role) and the owner (SQL editor) are not
-- affected. Other app-role table access is #12's.
create or replace function public.guard_userdata_auth_uid()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if current_user in ('anon', 'authenticated')
       and ((tg_op = 'INSERT' and new.auth_uid is not null)
            or (tg_op = 'UPDATE' and new.auth_uid is distinct from old.auth_uid)
            or (tg_op = 'UPDATE' and old.auth_uid is not null and new."userID" is distinct from old."userID")) then
        raise exception 'UserData.auth_uid can only be set by the pond owner through link_pond_to_account'
            using errcode = '42501';
    end if;
    return new;
end;
$$;

revoke all on function public.guard_userdata_auth_uid() from public, anon, authenticated;

drop trigger if exists "UserData_guard_auth_uid" on public."UserData";
create trigger "UserData_guard_auth_uid"
    before insert or update on public."UserData"
    for each row execute function public.guard_userdata_auth_uid();


-- True when the session named by an access token's session_id claim still
-- exists for that user and has not reached its not_after time. Signing
-- out or revoking a session deletes its row, so a token that is still
-- inside its expiry is refused once its session is gone.
create or replace function public.auth_session_active(p_session_id uuid, p_user_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select exists (
        select 1 from auth.sessions s
        where s.id = p_session_id
          and s.user_id = p_user_id
          and (s.not_after is null or s.not_after > now())
    );
$$;

revoke all on function public.auth_session_active(uuid, uuid) from public, anon, authenticated;
grant execute on function public.auth_session_active(uuid, uuid) to service_role;


-- The owner-mediated link of a legacy pond to an account (shared with
-- #49). Run by the owner in the SQL editor after confirming, outside the
-- app, that the account holder keeps that pond. Refuses to move a pond
-- already linked to another account, or to give an account a second pond
-- (the interim one-pond rule). Returns the pond id.
create or replace function public.link_pond_to_account(p_user_id bigint, p_auth_uid uuid)
returns bigint
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_current uuid;
begin
    select auth_uid into v_current from public."UserData" where "userID" = p_user_id for update;
    if not found then
        raise exception 'pond % does not exist', p_user_id using errcode = 'P0002';
    end if;
    if not exists (select 1 from auth.users where id = p_auth_uid) then
        raise exception 'account % does not exist', p_auth_uid using errcode = 'P0002';
    end if;
    if v_current is not null and v_current <> p_auth_uid then
        raise exception 'pond % is already linked to another account', p_user_id using errcode = '23505';
    end if;
    if exists (select 1 from public."UserData" where auth_uid = p_auth_uid and "userID" <> p_user_id) then
        raise exception 'account % already owns a pond', p_auth_uid using errcode = '23505';
    end if;
    update public."UserData" set auth_uid = p_auth_uid where "userID" = p_user_id;
    return p_user_id;
end;
$$;

-- Owner only: not the app, and not the backend's service role either.
revoke all on function public.link_pond_to_account(bigint, uuid) from public, anon, authenticated, service_role;
