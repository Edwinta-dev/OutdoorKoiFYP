-- 0011_frame_quality.sql
--
-- Frame quality gate and thumbnails for the camera service (issue #40).
--
-- imageTable gains two nullable columns, written by the camera service:
--
--   quality         jsonb, the frame's quality result (koi/camera/quality.py):
--                     {"version": 1,
--                      "status": "pass" | "fail" | "unknown",
--                      "reasons": [reason codes: "too_dark", "too_bright",
--                                  "clipped", "blurred"],
--                      "metrics": {"v_mean", "v_std", "clipped_low",
--                                  "clipped_high", "clipped_fraction",
--                                  "laplacian_var", "blur_judged",
--                                  "width", "height", "pixels"},
--                      "thresholds": {the limits the frame was judged by}}
--                   The metrics and thresholds are kept with every result
--                   so a rejected frame can be diagnosed, and re-judged if
--                   the limits change. null = no result: every row stored
--                   before this migration. Readers treat null, an unknown
--                   version or a malformed value as "unknown", never as
--                   "pass" (image_quality_status below, and
--                   koi.camera.quality.status_of in the backend).
--                   A "fail" frame is stored and shown, but does not move
--                   the camera's baseline or state counters (its
--                   current_state repeats the previous accepted frame's)
--                   and is left out of the algae fit.
--
--   thumbnail_path  text, the object path of the frame's 320 px wide JPEG
--                   thumbnail in the same bucket as the frame, beside it:
--                   "{user_ID}/{unix_seconds}_photo_thumb.jpg". A path, not
--                   a URL, so how a client may read it is decided by the
--                   bucket's access rules (issue #12) at read time, and a
--                   URL minted by old code cannot outlive that decision.
--                   The check refuses anything URL-shaped. null = no
--                   thumbnail (rows before this migration, or the
--                   thumbnail could not be made or stored; the frame is
--                   kept and clients fall back to imageURL).
--
-- Upload order and failures (koi/camera/camera.py): frame object, then
-- thumbnail object, then this row. If the row insert fails, the service
-- deletes both objects and replies 503; if that delete fails as well it
-- logs frame_orphaned with the bucket and paths for manual removal.
--
-- Indexes: none added. Every read of imageTable is by "user_ID" newest
-- first (idx_imagetable_user_time, 0001); quality is read from those rows.
--
-- Access: imageTable's existing grants are unchanged (issue #12 owns them).
-- image_quality_status is backend only.

alter table public."imageTable"
    add column if not exists quality jsonb,
    add column if not exists thumbnail_path text;

alter table public."imageTable"
    drop constraint if exists "imageTable_quality_shape",
    add constraint "imageTable_quality_shape" check (
        quality is null
        or coalesce(jsonb_typeof(quality) = 'object'
                    and jsonb_typeof(quality -> 'version') = 'number'
                    and quality ->> 'status' in ('pass', 'fail', 'unknown'), false)
    );

alter table public."imageTable"
    drop constraint if exists "imageTable_thumbnail_path_is_a_path",
    add constraint "imageTable_thumbnail_path_is_a_path" check (
        thumbnail_path is null
        or (thumbnail_path <> ''
            and thumbnail_path not like '/%'
            and position('://' in thumbnail_path) = 0
            and position('?' in thumbnail_path) = 0)
    );


-- image_quality_status(p_quality)
--   Inputs:  an imageTable.quality value (may be null)
--   Output:  'pass' or 'fail' for a version 1 result with that status;
--            'unknown' for null (rows before 0011), any other version, a
--            malformed value, or a stored 'unknown'.
--   Access:  service role only.
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
             and (p_quality ->> 'version')::numeric = 1
             and p_quality ->> 'status' in ('pass', 'fail')
            then p_quality ->> 'status'
        else 'unknown'
    end;
$$;

revoke all on function public.image_quality_status(jsonb) from public, anon, authenticated;
grant execute on function public.image_quality_status(jsonb) to service_role;
