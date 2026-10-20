-- Issue #82: a late sensor reading is placed at its own sample time by a
-- replay from a checkpoint (koi/models/pond_twin.py). One older than the
-- replay window (event_ledger.WINDOW) has no checkpoint left; the model
-- leaves it out and the ledger records it as
--   disposition 'out_of_window'  read, not passed to the model: it
--                                arrived later than the replay window
-- 'rebuild_required' (migration 0029) is retired: it marked rows applied
-- at the last input's time and nothing read it. Such rows were passed to
-- the model, so they become 'applied'.
update public.sensor_ingest_ledger
    set disposition = 'applied'
    where disposition = 'rebuild_required';

alter table public.sensor_ingest_ledger
    drop constraint if exists sensor_ingest_ledger_disposition_check;
alter table public.sensor_ingest_ledger
    add constraint sensor_ingest_ledger_disposition_check
    check (disposition in ('applied', 'superseded', 'unusable', 'out_of_window'));
