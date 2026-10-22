# Algae datasets field check (FAB, MESO, BRU) - 2026-10-22

Engine: `Backend/koi/models/algae_engine.py`. Inputs: `light_factor(lux)` (half-saturation 8000 lux), `temperature_factor(temp_c)` (peak 30 C, ramps 12-20 and 20-30 C), `nutrient_factor(no3_ppm)` (half-sat 5 ppm, floor 0.15, 0.6 if None), `favourability = L*T*N`, `fit_specific_growth_rate(samples)` (needs >=4 `GreenSample` with green_ratio >1e-5 spanning >=0.5 day).

## FAB (Zenodo 15669103; files live in version record 15669104): NO-GO, data absent
- The five listed files (FGA_area, FGA_final_biomass, limnotron_chlorophyll_a, limnotron_nutrients, limnotron_oxygen; 1.9-2.3 KB each) contain **only the README/metadata tab. Zero data rows** (21-29 lines each, all header and column descriptions). The record's file URLs 404 under 15669103; they download from 15669104. Zenodo md5s matched.
- What the metadata says the full data would hold (unverified): Limnotron, NIOO-KNAW, 22/04/2022-07/07/2022. Exp 1 = 8 h daylength, exp 2 = 16 h, each with Low/Medium/High PAR; exp 3 = 8-24 C in 2 C steps. 9 mesocosms. `algae area` (cm2, from imagery, top and bottom) with day/date/time; final dry biomass; chl-a and nutrients (exp 1-2 only); DO mid/max/min. PAR treatments are only classes; the ~13.2 mol m-2 d-1 DLI threshold and 16-22 C optimum appear only in the abstract.
- Would-be mapping: light -> `light_factor` only through a PAR-to-lux assumption (named scenario); temperature -> `temperature_factor` (8-24 C, never reaches the 30 C peak); nutrients -> `nutrient_factor` via dissolved N mg/L (not NO3 ppm); area -> `fit_specific_growth_rate` as a physical-area observable (cadence unknown, 9 mesocosms).
- Action: request the data tabs from the authors (README names Hannah Kemp, Suzanne McGowan) or the Aquacosm catalogue (data.aquacosm.eu, lineage in README). Reading treatment ranking off the paper abstract is hypothesis-grade only.
- WP5 verdict: **Blocked** until data rows are obtained.

## MESO (Zenodo 10520248, Lemming mesocosm Denmark): GO-with-caveats, B-tier temperature ranking only
- Files: `LemCP_metaData_v1.xlsx` (95 KB, 3 sheets) and `LemCP_DataORE_v1.zip` (28.2 MB, 13 CSVs, about 124 MB unzipped). Version 1, 2024-01-17, CC BY 4.0.
- Design: 24 outdoor tanks (A1..H3), 2 nutrient x 3 temperature levels; metadata study period Sep 2018-Aug 2020.
- Time-indexed: `TempOxyPh` (30 min, 24 tanks, 46,383 records each, 2018-01-01..2020-09-30: temperature, DO, pH); `PAR1Med5_QAQCdata` (5 min, 92,559 rows: chl-a fluorescence 24 tanks, phycocyanin 12 tanks, **in mV**); in-vitro chl-a in `Spectro_OctOnw` (840 rows, 24 tanks x 30-40 dates) and `Spectro_AugSep18` (72 rows) to calibrate the sensors.
- Treatments are **not labelled in any column or the metadata**. Empirically the temperature tier is the tank's last digit (1/2/3): all-period mean 11.0 / 14.1 / 15.5 C; summer-2019 mean 18.2-19.4 / 20.5-22.4 / 21.3-23.6 C. The nutrient tier is not recoverable (metadata only mentions "high and low nutrient tanks" for the linearity test).
- Light: **no irradiance series.** The `PAR` column in `DateNewGroup` is a day/night flag (PAR = 1 uE marks night) used for night-time medians. No lux. No nitrate.
- Mapping: temperature -> `temperature_factor` (clean per-tank series); light blocked; nitrogen blocked; pigment (night-median fluorescence calibrated to in-vitro chl-a) gives a time-indexed biomass proxy, so a ln-slope growth rate can be fitted with the same maths as `fit_specific_growth_rate` as an adapted, different observable (not green-pixel ratio).
- WP5 slice: temperature response of pigment across three temperature tiers over two seasons. Not a light treatment test.

## BRU (Zenodo 11103557, Brussels urban ponds): GO-with-caveats, ecological association only
- Files used: `DATA _Dissolved.xlsx` (sheet `diffusive`, 191 rows); `DATA -_Ebullitive.xlsx` and `DATA _Perturbed_sediments.xlsx` are methane work, irrelevant. 2024-05-02, CC BY 4.0.
- 4 ponds (Leybeek, Pecheries, Silex, Tenreuken), 48 sample dates each (47 Leybeek), about monthly, 2021-06 to 2023-12, grab samples: water temp, chl-a ug/L, %O2, TSM, SRP, NH4, NO2, NO3 (umol/L; x0.062 -> mg/L), gases. Dates are Excel serials.
- No light variable (lux needs a stated constant scenario). Two clear and two turbid ponds. Maps to `temperature_factor` and `nutrient_factor` (after unit conversion); outcome is chl-a, not coverage; monthly cadence rules out growth-rate fitting.
- Use: rank correlation of N x T favourability against chl-a across ponds and dates. No A-tier.

## Summary
| Dataset | Outcome | Light | Temp | N | Treatment labels | WP5 verdict |
|---|---|---|---|---|---|---|
| FAB | claimed area cm2, **rows absent** | claimed classes + daylength | claimed 8-24 C | claimed (dissolved N) | claimed | **Blocked** (get data from authors) |
| MESO | fluorescence mV 5-min, in-vitro chl-a ~weekly | no | yes, 30 min | no | tank ids only, mapping missing | GO-with-caveats (temperature ranking) |
| BRU | chl-a monthly x 4 ponds | no | yes | yes (umol/L) | none | GO-with-caveats (association) |
