-- 0013_sensordata_latest_index.sql
--
-- An index for the newest SensorData row per (pond, sensor_type), the
-- read behind GET /v1/ponds/{pond}/dashboard (issue #13).
--
-- pond_dashboard_sources (0012) takes, for each sensor_type of a pond,
-- the row ordered by created_at desc, id desc. Without an index that read
-- scans every row of the pond. 0012 left the index out while the sensor
-- sketch was live-tested, because building it locks the table against
-- the node's inserts; that freeze was lifted on 2026-10-04.
--
-- The column order matches the read's order by, so the newest row of each
-- type is the first index entry under (userID, sensor_type), including
-- the id tie breaker. get_bundled_dashboard_payload's per-type reads use
-- the same prefix.
--
-- Not built concurrently: migrations run in a transaction. The build
-- holds a share lock on SensorData for its duration, so a node insert in
-- that window waits rather than fails. Columns, grants, policies,
-- triggers and sensor_type spellings are unchanged; rows written before
-- this migration are indexed as they are.

create index if not exists idx_sensordata_user_type_latest
    on public."SensorData" using btree ("userID", sensor_type, created_at desc, id desc);
