# fieldcheck2: water-balance and feeding datasets (2026-10-23)

Quick check only. Downloads untrusted, not executed; helpers: `xl.py` (stdlib xlsx dump), `checks.py`, `checks_c.py`. Hashes/licences in MANIFEST.md.

## Verdicts

| | Dataset | Access / licence | (1) Evaporation / water balance | (2) Chemistry feeding / TDS prompts |
|---|---|---|---|---|
| A | USDA Ty Ty irrigation pond | open, CC BY 4.0, md5 verified | NO-GO as A-tier (see below); GO-with-caveats only as a B-tier plausibility check with external met | n/a |
| B | Tamil Nadu pan evaporation | open, 95 MB, licence not stated | GO-with-caveats for the Penman wind/vapour term only if paired with external met (pan is not a pond); NO-GO for shelter_factor/top-up fit | n/a |
| C | FWI Google sheet (+ farm-data repo) | open CSV; sheet licence not stated; farm-data repo CC BY 4.0 | n/a | NO-GO for the feeding test; NO-GO for TDS water-change prompts |
| D | WorldFish Vietnam 2018/2019 | metadata open CC BY 4.0, files access-gated (guestbook response required) | n/a | access-gated: not assessed; do not proceed without owner accepting terms |
| E | Guppy Nannochloropsis (Mendeley) | open, CC BY 4.0 | n/a | NO-GO (tank-scale, no feed mass/volume); TAN trend only anecdotal |

## A. Ty Ty pond (figshare 26142568), 1 pond, Oct 2021 to Oct 2023
Files: bathymetry (369 soundings, ft, RTK) + shoreline (100 pts) => area/stage-volume derivable; daily pump flow meter 2022 (acre-inch, 3 meters: R67960 in, Q64397 and R67963 out); staff-gauge pressure level 5-min 2021-09-29..2023-08-15 (ft); 1-min tipping rain 2022 (event rows only, 2513 rows, 108 rain days, 38.9 in); UAS tifs (479/17/51 MB, not fetched).
Integrity: pump file 1047 rows, no nulls/negatives, 6 duplicate date+meter rows (needs summing/dedup decision); gauge 196,997 non-null of 197,006 but contains one -6999 sentinel row (min -6999; mask it, it wrecks means) and 9 NAN rows, no gaps over 15 min, 2 jumps over 0.3 ft/5 min; 2022 daily level 0 to 2.78 ft. Rain times valid.
Engine mapping: surface area derivable (shoreline polygon); rain and pumping present; level present. Air temp, RH, wind, radiation and water temperature: MISSING (no met in the dataset; would need a nearby Georgia station, i.e. an external join). No seepage or runoff term.
Separability: on 166 2022 days with no rain gauge hits and zero logged pumping, the median daily level change is only -0.03 in/day (q10 to q90 -0.21 to +0.35), and July/Nov/Dec medians are positive. Expected summer evaporation is about 0.15-0.2 in/day, so the signal is buried in seepage, groundwater supplementation by unmetered paths, runoff after rain and gauge noise. Evaporation cannot be separated from seepage/runoff here: not A-tier. Caveat: time zones differ (rain/gauge EST, GPS EDT); pumping is daily so day-level only.

## B. Tamil Nadu twice-daily pan (NWDP)
935,828 rows, 43 stations (up to 43 since 2013, 1 in 1972), 1972-01-01..2026-06-30, obs 08:30 and 17:30 local, columns: station, lat/lon, time, pan_evaporation (mm). Integrity: no nulls/negatives, max 10 mm, 61 duplicate station+time rows, zero share 12% (AM 08:30 records 24 h total in some networks, PM 17:30 is often 0 so the two are not equal half-days: needs the convention confirmed). Pan only: no rain, temperature, RH, wind, radiation, water level or pumping. Pan coefficient (~0.7) and shelter differ from koi pond; it can test the order of magnitude of evaporation_mm_per_day against climate only after joining external met (e.g. IMD/ERA5), which makes it a weak, indirect test. It cannot exercise fit_shelter_factor (needs top-up litres and hourly met) or cross_check_against_tds.

## C. FWI sheet (Google CSV)
Not "fortified feed": it is a 2024-06-25..2024-11-13 pond monitoring campaign, 6 ponds (P1-P6), 1260 rows (~210 per pond, 270 rows have no timestamp), ProDSS sonde (temp, ODO, pH, cond, SpCond, TDS, salinity, chlorophyll), TDS in 990 rows and 96 days per pond, ammonia (mg/L) in 269 rows (about 44 days per pond), rain Yes/No flag, one "Feed data" value per pond-day in 810 rows (0 to 6, 144 zeros), ABW (fish weight, g) in 1160 rows. TDS/SpCond ratio is constant 0.65 (computed, so one channel only). Missing: feed unit (kg assumed, not documented in the sheet), feed protein %, pond volume/area/depth, any water-exchange or salt event, NO2, NO3, hourly cadence (about daily). Consecutive-day TDS: 446 pairs, only 1 drop and 1 rise over 100 ppm, so no usable water-change steps; detect_steps/TdsPrompts has nothing to score. The sheet matches the earlier src/fwi Data Campaign family. Separate lead: github.com/fish-welfare-initiative/fwi-farm-data-india (CC BY 4.0, 323 ponds, Jun 2021..Aug 2026): per-visit water quality with TAN (morning only), TDS in ppt, `Feed amount (kg)`, enrolled_ponds pond area and depth, feed type; but visits are sparse, no water-exchange log found, Nellore data after 2024-11-01 retracted for fabrication. Not downloaded (3.3 MB water_quality.csv); worth a 30-minute follow-up only for TAN vs feed kg at pond-visit cadence. Feed protein would be a category (DORB/mash/pellet), not %.
Engine mapping: food_grams derivable (kg x 1000), protein_percent missing, volume derivable from area x depth in the repo only, events missing => NO-GO.

## D. WorldFish Nutritious Pond Vietnam (2018, 2019)
Metadata public, CC BY 4.0; file list per year: Input_output_data (water in/out likely), Input_water_daily, Water_growth_weekly, protocols. All downloads return "You may not download this file without the required Guestbook response" (Dataverse guestbook). Recorded as access-gated: not downloaded, not accepted. Potentially the best fit (daily input/output water, feed inputs, weekly water quality) but unverified.

## E. Guppy Nannochloropsis (Mendeley xnpj6353yw v1)
Three small xlsx summaries (Fig1 oxidative stress; Fig2 nitrite and TAN at Day 0/2/4/6 for Control, N5, N10, N15, CMF with 3 replicates; Table1 temp, DO, pH single values). Integrity: values plausible (TAN 0 at day 0 for all, rising to ~0.64; nitrite 0 to 0.13), summary tables incl. avg/SD/SE rows, 3 replicates, no timestamps, no feed mass, protein, tank volume or exchanges, no NO3/TDS. Verdict NO-GO.

## Bottom line
(1) Evaporation: no dataset gives evaporation separable from seepage plus met drivers. Best realistic next step is A + an external Georgia met station as a B-tier plausibility test, with the -6999 mask and duplicate-pump handling. (2) Chemistry/TDS: nothing usable open; D is the only candidate and is access-gated (owner decision on the guestbook); fwi-farm-data-india is a low-cadence follow-up.
