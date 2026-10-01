-- Synthetic rows for the dump-upgrade rehearsal (tools/db_rehearsal.py).
--
-- Loaded into a database restored from schema.sql, before the later
-- migrations run, to check those migrations keep every existing row and
-- identifier. Shapes follow live rows (read-only queries, 2026-10-01);
-- the values are invented. No postal codes, so the UserData trigger never
-- makes its OneMap HTTP request.

insert into public."WeatherStationLookup" ("Id", station__id, station__name, location__latitude, location__longitude, "Measurement")
values (1, 'S43', 'Kim Chuan Road', 1.3399, 103.8878, '["AirTemp", "Rainfall", "Wind"]'),
       (2, 'S06', 'Woodlands', 1.4332, 103.7829, '["AirTemp", "Rainfall", "Wind"]'),
       (3, 'A1', 'Serangoon', 1.357, 103.865, '["2HourForecast"]');

-- Two ponds with coordinates (the trigger fills ClosestStations) and one
-- legacy pond with neither coordinates nor a postal code.
insert into public."UserData" ("userID", volume, biomass, region, latitude, longitude)
values (455, 2500, 55.05, null, 1.35, 103.87),
       (6, 500, 35, 'Central', 1.43, 103.79),
       (1, 3600, 25, null, null, null);

insert into public."SensorData" (created_at, sensor_type, data1, "userID")
values (now() - interval '2 hours', 'pH', 7.4, 455),
       (now() - interval '2 hours', 'TDS', 160, 455),
       (now() - interval '2 hours', 'temp', 27.5, 455),
       (now() - interval '2 hours', 'LUX', 850, 455),
       (now() - interval '1 hour', 'pH', 8.1, 6),
       (now() - interval '1 hour', 'TDS', 310, 6),
       (now() - interval '3 days', 'pH', 7.2, null);  -- pre-userID firmware row

insert into public."imageTable" (created_at, green_ratio, current_state, "user_ID", "imageURL")
values ('2026-07-27 08:43:45+00', 0.0009, '["base",0.0009]', 455, 'https://example.invalid/public/455/1.jpg'),
       ('2026-08-01 08:43:45+00', 0.0569, '["obstruction",0.0569]', 455, 'https://example.invalid/public/455/2.jpg');

insert into public."pondInterventions" ("userID", event_type, volume_percentage, volume_litres, food_grams, event_timestamp)
values (455, 'FEEDING', null, null, 40, now() - interval '1 day'),
       (455, 'WATER_CHANGE', 30, 750, null, now() - interval '2 days'),
       (455, 'ALGAE_SCRUB', null, null, null, now() - interval '3 days');

insert into public.daily_sensor_averages (userid, sensor_type, avg_value, min_value, max_value, record_date)
values (455, 'pH', 7.35, 7.1, 7.6, '2026-09-30');

-- Opaque to the rehearsal, which only checks the row survives; it is not
-- a snapshot the engine can load (that needs the engine's "config" block).
insert into public.pond_chemistry_state (user_id, snapshot, updated_at)
values (455, '{"version": 1, "chemistry": {"tan_ppm": 0.12}}', '2026-08-20 00:00+00');

insert into public.pond_chemistry_evaluations (userid, status, category, tan_ppm, no2_ppm, no3_ppm, advisory)
values (455, 'ok', 'chemistry', 0.12, 0.02, 14.0, 'Water is within range.');

insert into public.pond_algae_evaluations (userid, status, category, green_ratio)
values (455, 'watch', 'algae', 0.0569);

insert into public.pond_evaporation_evaluations (userid, status, category, loss_litres)
values (455, 'ok', 'evaporation', 12.5);

insert into public.weather_telemetry (station_id, metric_type, data, valid_start, valid_end)
values ('S43', 'realtime_sensor', '{"rainfall": 0, "wind_speed": 4.1, "air_temperature": 30.7}',
        now() - interval '5 minutes', now() + interval '10 minutes');

insert into public.weather_forecasts (forecast_type, slot_id, data, valid_period)
values ('2hr', 'Serangoon', '{"forecast": "Cloudy"}', '{"end": "2026-10-01T18:30:00+08:00", "start": "2026-10-01T16:30:00+08:00"}'),
       ('uv', '12:00', '{"uv": 7}', '{"hour": "2026-10-01T12:00:00+08:00"}');

insert into public."Fish_Database" ("Title", "Common Name", "pH", "Temperature")
values ('Koi', 'Koi', '6.5 - 8.5', '15°C - 25°C');
