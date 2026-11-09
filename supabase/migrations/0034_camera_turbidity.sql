-- Issue #152: additive calibration and alert state for camera card readings.
alter table public.camera_card_readings
    add column turbidity_index double precision,
    add column turbidity_channels jsonb,
    add column turbidity_a jsonb,
    add column turbidity_b jsonb,
    add column turbidity_r2 jsonb,
    add column turbidity_reason text,
    add column turbidity_patches_used integer not null default 0 check (turbidity_patches_used >= 0),
    add column agreement jsonb,
    add column frame_agreement text check (frame_agreement in ('clear', 'disagree')),
    add column unusable_streak integer not null default 0 check (unusable_streak >= 0),
    add column unusable_alerted boolean not null default false;
