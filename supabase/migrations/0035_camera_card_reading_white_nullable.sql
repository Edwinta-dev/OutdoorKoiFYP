-- Issue #151: a frame whose white reference cannot be read (cards covered or
-- out of view) has no white_rgb. Its reading is still stored, as `unusable`,
-- so the covered-card streak and its alert can count it. Rows written before
-- this migration all carry a white_rgb and are unchanged.
alter table public.camera_card_readings alter column white_rgb drop not null;
