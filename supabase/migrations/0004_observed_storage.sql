-- 0004_observed_storage.sql
--
-- Storage buckets and storage.objects policies as they exist in the live
-- project (issue #5). A schema-only dump of public does not include them;
-- they were read from storage.buckets and pg_policies with read-only
-- queries on 2026-10-01 (docs/database-reconciliation.md section 2b).
--
--   imageAnalysisBucket  ESP32-CAM frames written by the camera service
--                        (POND_IMAGE_BUCKET); public, JPEG only.
--   FishImages           species images; public.
--
-- Each bucket has four policies letting anon select, insert, update and
-- delete .jpg objects under a top-level "public" folder. This records
-- existing behaviour; tightening it belongs to issue #12.
--
-- On the live project every statement is a no-op or recreates an
-- identical policy.

insert into storage.buckets (id, name, public, allowed_mime_types)
values ('imageAnalysisBucket', 'imageAnalysisBucket', true, array['image/jpeg']),
       ('FishImages', 'FishImages', true, null)
on conflict (id) do nothing;

-- FishImages
drop policy if exists "Give anon users access to JPG images in folder jl11jk_0" on storage.objects;
create policy "Give anon users access to JPG images in folder jl11jk_0" on storage.objects
    for select to public
    using (bucket_id = 'FishImages' and storage.extension(name) = 'jpg'
           and lower((storage.foldername(name))[1]) = 'public' and auth.role() = 'anon');

drop policy if exists "Give anon users access to JPG images in folder jl11jk_1" on storage.objects;
create policy "Give anon users access to JPG images in folder jl11jk_1" on storage.objects
    for insert to public
    with check (bucket_id = 'FishImages' and storage.extension(name) = 'jpg'
                and lower((storage.foldername(name))[1]) = 'public' and auth.role() = 'anon');

drop policy if exists "Give anon users access to JPG images in folder jl11jk_2" on storage.objects;
create policy "Give anon users access to JPG images in folder jl11jk_2" on storage.objects
    for update to public
    using (bucket_id = 'FishImages' and storage.extension(name) = 'jpg'
           and lower((storage.foldername(name))[1]) = 'public' and auth.role() = 'anon');

drop policy if exists "Give anon users access to JPG images in folder jl11jk_3" on storage.objects;
create policy "Give anon users access to JPG images in folder jl11jk_3" on storage.objects
    for delete to public
    using (bucket_id = 'FishImages' and storage.extension(name) = 'jpg'
           and lower((storage.foldername(name))[1]) = 'public' and auth.role() = 'anon');

-- imageAnalysisBucket
drop policy if exists "Give anon users access to JPG images in folder ko7l0h_0" on storage.objects;
create policy "Give anon users access to JPG images in folder ko7l0h_0" on storage.objects
    for select to public
    using (bucket_id = 'imageAnalysisBucket' and storage.extension(name) = 'jpg'
           and lower((storage.foldername(name))[1]) = 'public' and auth.role() = 'anon');

drop policy if exists "Give anon users access to JPG images in folder ko7l0h_1" on storage.objects;
create policy "Give anon users access to JPG images in folder ko7l0h_1" on storage.objects
    for insert to public
    with check (bucket_id = 'imageAnalysisBucket' and storage.extension(name) = 'jpg'
                and lower((storage.foldername(name))[1]) = 'public' and auth.role() = 'anon');

drop policy if exists "Give anon users access to JPG images in folder ko7l0h_2" on storage.objects;
create policy "Give anon users access to JPG images in folder ko7l0h_2" on storage.objects
    for update to public
    using (bucket_id = 'imageAnalysisBucket' and storage.extension(name) = 'jpg'
           and lower((storage.foldername(name))[1]) = 'public' and auth.role() = 'anon');

drop policy if exists "Give anon users access to JPG images in folder ko7l0h_3" on storage.objects;
create policy "Give anon users access to JPG images in folder ko7l0h_3" on storage.objects
    for delete to public
    using (bucket_id = 'imageAnalysisBucket' and storage.extension(name) = 'jpg'
           and lower((storage.foldername(name))[1]) = 'public' and auth.role() = 'anon');
