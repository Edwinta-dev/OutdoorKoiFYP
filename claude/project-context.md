# OutdoorKoiFYP — Project Context (as of 2026-08-20)

Read this first in any new session on this repo. It supersedes the fragmented
per-thread docs referenced by older session notes (`claude/digitaltwin-three-
domain-integration.md`, `claude/digital-twin-status.md`, `claude/lookahead-
algae-evaporation.md`) — those files do not exist anywhere in the repo or
filesystem as of this writing; an earlier session accidentally overwrote one
of them and the originals were never recovered. Treat any reference to them
in old notes as dead links.

## 1. The thesis

NTU FYP, supervised by Assoc Prof Ng Beng Koon. Original brief: a
microcontroller-based **indoor fish tank** monitor with automated control
(temp/pH/turbidity/ammonia/nitrate, LCD display, actuators). This was
deliberately abandoned early (see interim report §3) because it was an
over-served beginner project with many off-the-shelf competitors. The
project pivoted to:

> An affordable IoT/telemetry system for **outdoor koi ponds** that
> replicates the *ease of maintenance* of an expensive automated digital
> twin / auto-doser system, at roughly the *cost* of manual dip-testing —
> by combining cheap baseline sensors with Singapore NEA weather/forecast
> data to give directionally-correct, advisory (not actuated) guidance on
> when to feed, top up, change water, or clean algae.

Explicitly **not** trying to match professional system accuracy — the bar
is "directionally correct and actionable," not "lab-grade." Explicitly
**not** building actuators — advisory only (push notifications / app
cards), because actuation was judged to distract from the modelling goal.

Interim report submitted 2026-04-13, which stated the project conclusion is
~7 months out from that date → **conclusion ≈ mid-November 2026**. Today is
2026-08-20, so **roughly 12 weeks / 3 months remain**.

## 2. Repo map

| Path | What it is | Status |
|---|---|---|
| `Backend/DigitalTwin/` | Flask service: 3 stateful per-pond engines (chemistry, evaporation, algae) + orchestrator + poller + Supabase persistence | Mature, well-tested, **huge uncommitted WIP** (see §4) |
| `Backend/SensorAPI/camera/` | Middle layer between ESP32-CAM and Supabase: receives photo, runs HSV green-ratio analysis, decides next sleep duration | Core pipeline works; adaptive scheduling is **stubbed/disabled** |
| `Embedded/TempSensor/TempSensor.ino` | ESP32 sketch: temp (DS18B20), pH (E-201C), TDS (Erudent V1.0), pushes to Supabase `SensorData` table every 5s | Temp/TDS/pH wired but uncalibrated; **LUX hardcoded to 35.0**, no sleep |
| `Embedded/CameraTest/Camera_Arduino_Sketch/CameraMain.ino` | ESP32-CAM sketch: wake → photo → POST → sleep for server-given duration | Works end-to-end but **never identifies which pond/device it is** |
| `MobileUI/mobile_app/` | Flutter app: Dashboard / Fish Tips / Settings tabs + Detail Graph screen + log-event modals | Split-brain architecture (see §4) — newer parts are production-grade, older parts are dead/duplicated |
| `AquariumScraper/` | One-off scraper of a fish-care website → `fish_characteristics*.csv` feeding the Fish Tips screen | Done, low-risk, not revisited |
| `PythonSimulatorProject/` | Standalone desktop simulator (CustomTkinter) validating the core thesis against **real historical NEA rainfall**, independent of the live hardware/backend | Phase 1 + 1b done and validated; Phase 2 in progress (see §5) |
| `NEAForecastScraper/` + `nea_forecast_data/` | Scrapes NEA's real-time forecast API backdated to 2020-02-01, for Phase 2 | **Complete** (see §5) |
| `NEADataScraper/` + its `nea_rainfall_data/` | Scrapes NEA's real-time rainfall API (5-min readings, ~74-101 stations) to build 2020+ daily rainfall ground truth (no bulk CSV exists post-2017) | **~86% done**, running unattended on the user's own machine (see §5) |

## 3. Uncommitted work-in-progress — read before touching git state

`git status` currently shows **enormous uncommitted diffs** on the core
backend and firmware: `app.py` +677/-, `engine.py` +407/-, `poller.py`
+281/-, `state_store.py` +416/-, plus brand-new untracked files
(`algae_engine.py`, `evaporation_engine.py`, `forecast_utils.py`,
`pond_twin.py`, five `test_*.py` files, `README_INTEGRATION.md`) and a new
892-line `MobileUI/mobile_app/lib/utils/digital_twin_api.dart`. This is the
"three-domain integration" — extending the digital twin from
water-chemistry-only to chemistry + evaporation + algae, all three wired
through a new `PondTwin` orchestrator, with a parallel Flutter API client.
**This is real, current, working code, not an abandoned branch** — it has
its own passing test suite (see §4.1). Do not `git checkout .` / `stash
-u` / discard it without confirming with the user first; nothing here has
been committed yet.

`Backend/DigitalTwin/README_INTEGRATION.md` documents this integration's
intended design, deploy order, and its own "Still open" list — read it, but
verify claims against code; it is already stale in at least one place (see
finding B2 below — it describes a firmware bug that turned out to be worse
than documented).

## 4. Known issues (verified 2026-08-20, file:line cited)

Severity: **CRITICAL** = silently produces wrong output the user would
trust; **HIGH** = confirmed bug with real runtime impact; **MEDIUM** =
design/honesty concern, not yet broken; **LOW** = hygiene/dead code.

### 4.1 Backend/DigitalTwin (`engine.py`, `algae_engine.py`,
`evaporation_engine.py`, `poller.py`, `app.py`, `state_store.py`)

Test suite is genuinely strong: `test_pond_twin.py` (61 checks),
`test_new_engines.py` (49), `test_severity_ratings.py` (63),
`test_poller_integration.py` (83), `test_projection.py`, `test_contract.py`
(25 Python↔Dart JSON-contract checks) — **all ~280+ assertions pass**,
independently re-run and confirmed 2026-08-20. `test_contract.py` needs a
copy of `digital_twin_api.dart` placed beside it to run (not committed that
way; the README says so).

Physics is largely sound and better than the interim report implied: the
**TAN/NO2/NO3 nitrogen-cycle numbers are NOT derived from TDS** as the
interim report's ARF formula proposed — they're computed mechanistically
from logged feeding events (`_ammonia_mg`, `engine.py:330-331` — grams ×
protein% × 0.16 N-fraction × 0.70 excretion fraction, a standard
aquaculture textbook formula) and decayed through temperature-gated
nitrification kinetics, entirely independent of any sensor. TDS's actual
role narrowed to sensor-plausibility gating and a secondary buffering-trend
signal — a quieter, more defensible design than what was pitched in April,
but the interim report's own text is now out of date on this point.
Penman evaporation (`evaporation_engine.py`) is textbook-correct with a
documented calibration constant (`POND_SHELTER_FACTOR`) derived from a
real discrepancy against live telemetry — genuinely good engineering.
Algae growth is logistic, camera-grounded (fits the pond's own realised
growth rate from photo history, backs out an environment-independent
intrinsic rate, reprojects against forecast conditions) rather than a
first-principles guess — well-reasoned.

- **[HIGH] `engine.py:436-444`** (`ingest_sensor_sample`) has no cap on
  elapsed time before integrating chemistry forward, unlike
  `evaporation_engine.py:374` and `algae_engine.py:644`, which both
  explicitly cap at 1 day with documented reasoning. A multi-day service
  outage (Flask down over a weekend) gets fully integrated into chemistry
  using only the single sample that ends the gap — silently
  misrepresents the whole gap. Evaporation/algae already learned this
  lesson; chemistry didn't.
- **[HIGH] `engine.py:604-606`, `engine.py:422-427`** divide by
  `self.config.volume_litres` with no positivity guard, unlike
  `evaporation_engine.py:454-458` which explicitly guards
  `volume_litres <= 0`. `state_store.py:307-319` only checks volume is not
  `None`, not that it's positive. **This chains directly to Mobile UI
  finding M4 below** — onboarding lets a user submit `0` or blank tank
  volume, which would raise an uncaught `ZeroDivisionError` server-side.
- **[MEDIUM] `algae_engine.py` forecast path (`app.py:145-161`,
  `poller.py:274-291`)** feeds raw NEA `air_temp_c` into algae's
  `temperature_factor`, while `evaporation_engine.py:561-563` derives a
  fitted water-temp offset (`predict_water_temp`) from the same sensor
  history for its own forecast. The algae engine doesn't reuse that
  already-fitted offset, even though `temperature_factor` is sensitive in
  exactly the 28-33°C band where the difference matters.
- **[MEDIUM] Timezone bug: `engine.py:319-320`** (`_day_key`) buckets
  daily aggregates by UTC calendar day; Singapore is UTC+8, so each "day"
  bucket actually spans 8am–8am SGT, not midnight–midnight — reactivity
  and TDS-trend calculations are silently offset from the day a user
  would recognize.
- **[MEDIUM] Proxy-honesty asymmetry:** `AlgaeAssessment`
  (`algae_engine.py:390-413`) reports a `confidence`/`rate_source` field
  on every output. `WaterChemistryAssessment` (`engine.py:300-317`) does
  not — TAN/NO2/NO3 ppm values are reported at full float precision with
  no equivalent flag, even though (per the point above) they rest
  entirely on self-reported feeding logs with zero sensor grounding. Only
  the nitrite-override advisory text mentions "this is a model estimate."
  A mobile user could easily read "NO3 34ppm" as a measurement.
- **[LOW] `app.py:713-715`** — a blanket `@app.errorhandler(ValueError)` →
  404 conflates "no persisted pond config" with unrelated `ValueError`s
  (e.g. malformed `int(body.get("image_id"))` at `app.py:349`), both
  returning indistinguishable 404s to the client.

### 4.2 Backend/SensorAPI/camera + Embedded camera firmware

- **[CRITICAL] `Backend/SensorAPI/camera/imageSchedule.py:29-52`** — all
  three schedule functions are testing stubs with the real logic
  commented out immediately above the stub `return`: `get_base_schedule_
  sleep_seconds()` computes `sleep_duration` correctly but line 35's
  `return int(sleep_duration)` is commented out, replaced by an
  unconditional `return 100` at line 44; `get_obstructionstate_sleep_
  seconds()` returns hardcoded `80` instead of the documented 7200s (2hr);
  `get_dynamicstate_sleep_seconds()` returns hardcoded `60` with a dead
  `smart_dynamic_state` variable. **Net effect: the ESP32-CAM currently
  wakes and photographs roughly every 60-100 seconds, 24/7, regardless of
  day/night or algae risk** — directly contradicting both stated goals
  (skip nighttime, adapt sampling to risk) and the "low power, outdoor
  battery/solar" framing. There is also no LUX/sunrise-sunset check
  anywhere in `camera.py`, `hsvEngine.py`, `imageSchedule.py`, or
  `CameraMain.ino` — night-skipping was never anything but a hardcoded
  clock-hour list (`BASE_FIXED_TIMINGS`, line 15) inside the currently-dead
  base-schedule path. **Fix is small**: uncomment the real returns, and
  add a `break` to the `for` loop at `imageSchedule.py:29-34` (it currently
  has no break, so once un-stubbed it will keep overwriting
  `sleep_duration` through every remaining slot and sleep until 18:00
  regardless of actual current time).
- **[CRITICAL] Firmware never sends a device/pond identity.**
  `CameraMain.ino` (whole file, grepped) contains no `X-User-ID` header or
  any user/device identifier at all — worse than `README_INTEGRATION.md`'s
  own claim that it "hardcodes 15" (verified false; there's no such
  literal in the file). `camera.py:92` falls back to the literal string
  `'default_user'` whenever the header is missing, i.e. always right now.
  **Every physical pond's photos collide into one shared history/baseline
  in Supabase.** This single-handedly defeats the algae engine's entire
  camera-grounding design (§4.1) and the per-pond EMA baseline in
  `hsvEngine.py`, which are otherwise the strongest, most-defensible part
  of the algae model. Fixing this (add one header to the firmware's POST)
  is the highest-leverage embedded fix in the whole project.
- **[HIGH] "Darker first photo" bug is still fully live**, unaddressed in
  both layers: `CameraMain.ino:132-148` (`takePhoto`) calls
  `esp_camera_fb_get()` exactly once with no discard/warm-up frame, and
  `hsvEngine.py:86-114` has no darkness/exposure sanity check or
  multi-frame averaging.
- **[MEDIUM] HSV green range (`hsvEngine.py:76-77`,
  `LOWER_GREEN=[18,40,40]`/`UPPER_GREEN=[42,255,255]`)** can misclassify
  yellow koi (e.g. Yamabuki Ogon, hue ≈30) as algae-green; no fish/
  reflection masking exists. Compounding: an ROI-cropping parameter exists
  in `hsvEngine.analyze_image_bytes` specifically to exclude deck/plants/
  sky (its own docstring says so) but `camera.py:96` never passes it —
  full-frame analysis ships regardless.
- **[MEDIUM]** `camera.py:96`'s call into `hsvEngine.analyze_image_bytes`
  sits outside the `try/except` starting at line 121 — a corrupt JPEG
  raises an unhandled `ValueError`, producing a bare Flask 500 instead of
  the JSON envelope every other failure path returns.
- **Well done**: the EMA-relative (not absolute-global) threshold design
  for cross-pond normalization is legitimately good and is undermined only
  by the missing device ID above, not by its own logic. Deep-sleep
  sequencing in firmware is correct (WiFi explicitly torn down before
  `esp_deep_sleep_start()`). `hsvEngine.py`'s docstring self-documents
  three earlier real bugs it fixed, with before/after arithmetic against
  live Supabase data — unusually rigorous for an FYP.

### 4.3 Embedded/TempSensor/TempSensor.ino

- **[CRITICAL] Line 103: `float luxValue = 35.0;` — hardcoded "Testing
  value", not real sensor data.** This isn't just a missing feature: it
  actively corrupts the algae model. `poller.py:81` pulls
  `lux=float(raw["LUX"])` straight from this table with **no fallback
  guard**, feeding `algae_engine.py`'s `favourability()` /
  `light_factor(lux) = lux/(lux+8000)` (line 157-160). At `lux=35`, that
  term evaluates to ≈0.004 — i.e. the model currently believes the pond is
  in near-total darkness at all times, regardless of actual sunlight.
  Real tropical noon lux is in the tens of thousands (the chosen TSL2591
  sensor's rated range tops out at 88,000 lux per the interim report).
  Until the LUX sensor is actually soldered in and wired to replace this
  line, **the algae growth model's light term is silently wrong, not just
  absent.**
- **[MEDIUM] TDS formula's final `* 0.5` (line 75) is an unexplained
  constant.** The polynomial `133.42x³ - 255.86x² + 857.39x` is the
  standard DFRobot/Gravity TDS-sensor curve, empirically fit against a 5V/
  1023-step (Arduino) ADC. The code correctly rescales the *input* voltage
  for the ESP32's 3.3V/4095-step ADC (line 69, matches interim report's
  stated approach), but simply rescaling the input does not make the
  cubic polynomial's *coefficients* valid for the new voltage range — the
  polynomial was empirically fit, not derived from first principles, so
  swapping the input scale without refitting will not reproduce the
  correct output curve. The trailing `* 0.5` looks like an ad hoc attempt
  to compensate for exactly this, but there's no comment or calibration
  data explaining where `0.5` comes from — it reads like a guess tuned to
  make one test reading look plausible, not a real calibration. This adds
  to (not replaces) the report's own acknowledged ±10% sensor accuracy
  caveat.
- **[MEDIUM] No power-saving, no reconnect logic.** `loop()` runs a
  continuous 5-second poll (`delay(5000)`, line 117) with no
  `esp_deep_sleep`, and no WiFi-reconnect check — if the connection drops
  after `setup()`, `db.insert()` (line 113) will just keep failing
  silently every 5s with no retry/backoff. Contradicts the "low power,
  outdoor solar/battery" framing that motivated ESP32 selection.
  Confirmed still true per the developer's own note (this file is one of
  the ones under active uncommitted edit, +84/-lines, but the sleep
  function referenced in past notes is not present in current content).
- pH is still using the "approximate calibration curve" (slope -1.49,
  intercept 12.95, line 55-56) flagged as unresolved in interim report
  §9 — recalibration against buffer solutions is still the documented
  next step and has evidently not happened yet as of this file's current
  content.
- Minor hygiene: Supabase anon key and WiFi SSID/password are committed
  directly in this tracked `.ino` file (lines 9, 11-12) — mostly a
  portability problem (won't work on a different network without editing
  source) rather than a security concern worth dwelling on for an FYP.

### 4.4 MobileUI/mobile_app (Flutter)

The app shows two coexisting engineering eras. The newer layer
(`digital_twin_api.dart`, the four `widgets/detail_graph/*` cards,
`quick_log_modals.dart`, `pond_camera_storage.dart`) is genuinely
production-grade: defensive JSON parsing throughout, distinct loading/
unreachable/"not enough history" (422) states with retry affordances, and
field names verified almost exactly against `Backend/DigitalTwin/app.py`'s
actual endpoints. The older layer (`pond_heuristics.dart` +
`utils/helpers/*`) is a self-contained client-side re-implementation of
pond risk logic that predates the Flask engine.

- **[CRITICAL] The Dashboard screen (the app's primary view) never calls
  the backend digital twin at all.** `dashboard_view.dart` imports only
  `pond_heuristics.dart`; `widgets/dashboard/ph_outcome_card.dart:46`,
  `temperature_outcome_card.dart:73`, `solar_outcome_card.dart:34` all
  call `PondHeuristics.*` (pure client-side logic). Meanwhile
  `widgets/detail_graph/water_buffer_status_card.dart`, one tap away,
  fetches the real, tested, server-computed `WaterChemistryAssessment`
  for the exact same pond and domain. **These are two independent
  implementations of "is the pond's buffer failing" that can disagree**,
  with nothing in the UI indicating to the user that the number on the
  Dashboard and the number on the Detail screen come from different
  pipelines. This is the single most important mobile-app finding: it
  means all the well-tested backend physics in §4.1 currently has no
  effect on what the user sees first.
- **[HIGH, confirmed dead code, duplicated]** `utils/rain_vulnerability.dart`
  (259 lines) is byte-for-byte the same algorithm as
  `utils/helpers/ph_helpers.dart:81-327` — identical function/class names
  (`evaluateRainVulnerabilityRefined`, `PondSample`, `BufferAssessment`,
  `_groupByDay`, `_slope`, `_recentNightMinima`). Confirmed zero imports of
  `rain_vulnerability.dart` anywhere in `lib/` — it's the abandoned
  original that got copy-pasted into `ph_helpers.dart`.
- **[HIGH, confirmed dead code]** `utils/pond_simulator.dart`
  (`UnifiedPondSimulator`) — confirmed zero calls anywhere in `lib/`. Its
  TAN→NO3 stoichiometry (4.43mg NO3 per mg TAN-as-N) is correct, but it
  looks like an earlier client-side draft of what `engine.py` now does
  server-side, never removed.
- **[HIGH, confirmed stub/bug] Onboarding volume field has zero
  validation.** `screens/onboarding_screen.dart:456-464` is a bare
  `TextField` for tank volume; `_saveAndContinue()` (line 335) writes
  `_volumeController.text` straight to `SharedPreferences` and Supabase
  with no empty/zero/negative check — contrast the fish-count/weight
  inputs a few sections later, which do validate (line 310). **Chains
  directly to backend finding in §4.1** (`engine.py`/`evaporation_
  engine.py` divide by volume with inconsistent guards) — a blank or "0"
  volume can reach a live `ZeroDivisionError` server-side.
- **[MEDIUM, confirmed] No surface-area field anywhere in onboarding** —
  confirms the developer's own suspicion. The backend doesn't ask for it
  either; `evaporation_engine.py:193-197` derives
  `surface_area_m2 = (volume_litres/1000) / DEFAULT_POND_DEPTH_M` (a
  hardcoded 1.2m assumed depth), which the engine's own docstring
  (line 51-53) calls "the single largest source of error in the volume
  projection." **To the app's credit, this assumption *is* honestly
  surfaced to the user** — `evaporation_status_card.dart:250-251`
  displays "Assumes 1.2m depth (X m² surface)..." — verified directly.
  The gap is that there's still no way for a user to enter the real
  number and remove the assumption.
- **[MEDIUM, confirmed stub]** Settings screen is not functional,
  confirming the developer's assessment: `screens/settings_view.dart:23-
  25` is a literal `Placeholder`; two `ListTile`s (lines 37-52) have empty
  `onTap` bodies. The one wired action, `deletePondData()` (line 69,
  bound at line 58), **deletes the user's entire pond record with no
  confirmation dialog** — a single mis-tap is destructive and
  irreversible.
- **[MEDIUM]** `userID` is collected as a free-text numeric-keyboard field
  with no format check (`onboarding_screen.dart:675-682`), then parsed
  everywhere else via `int.tryParse(...) ?? 0` — a typo or blank entry
  silently becomes user id `0`, with every subsequent read/write posted to
  that fallback bucket with no error surfaced.
- **[LOW, confirmed dead code]** `widgets/dashboard/old/{at_a_glance,
  long_term_forecast, localized_nea, fish_tips}_widget.dart` — grepped,
  genuinely unreferenced anywhere in `lib/`.
- **[LOW]** `main.dart:12-16` — `Supabase.initialize` uses
  `String.fromEnvironment(...)` defaulting to empty string, unguarded by
  try/catch; a build without the right `--dart-define` values crashes at
  startup with no user-facing message.
- **Well done**: `digital_twin_api.dart` + the four detail-graph cards are
  careful, production-grade work. `quick_log_modals.dart` fans a logged
  event out to both the Supabase history table and the live Flask engine,
  treating the Flask push as best-effort so a slow backend never blocks
  the save. Controller disposal is handled properly throughout the newer
  layer.

## 5. NEA validation experiment track (Python Simulator, separate from live hardware)

This is a standalone empirical-validation effort, deliberately independent
of the live ESP32/Flask/Flutter stack, testing the thesis against **real
historical NEA data** instead of live sensors. Consolidated from an
external handoff note (`SESSION_CONTEXT.md`, not repo-tracked) with two
corrections below from checking actual current file state.

**Phase 1 (done, validated)** — `PythonSimulatorProject/simulator1.py` (a
CustomTkinter desktop app; this is the actively-maintained file, renamed/
evolved from the git-tracked `simulator.py` — `simulator2.py` is a small,
separate 4KB file, not the main line) driven by
`HistoricWeatherData/HistoricalDailyWeatherRecords.csv` (NEA Admiralty
station, daily rainfall, 2009-2017, 2,838 days). Simulates pond health
under three policies: CONTROL (ignores rain), REACTIVE (emergency reset
after a crash), ACTIVE MONITORING (same-day sensing, pre-emptive top-up —
deliberately not called "Predictive," since it isn't forecast-based yet).
**Headline finding**: Control never drops below 85% health across 8 years;
Reactive/Active both hit the -100% floor repeatedly (0 vs 46 vs 31
unhealthy days at defaults) — rain materially matters.

**Phase 1b (done)** — parameter sweep, `phase1_parameter_sweep_report.md`.
Three empirically-run findings: (1) monitoring's relative payoff grows
with pond size (33-46% → 77-86% reduction in unhealthy days as threshold
goes 10mm→100mm); (2) **small ponds have a hard ceiling same-day sensing
cannot break** — at a 10mm threshold, 20% of all historical days have
single-day rainfall alone exceeding the entire threshold, so warning and
crash land in the same 24h tick and no same-day trigger can act early
enough (only 0.1% of days at 100mm) — **this is the load-bearing argument
for why forecast lead time specifically, not just tighter sensing,
matters**; (3) efficiency frontier: trigger at 75-90% of threshold + 10-20%
top-up captures most benefit before costs jump 10-100x.

**Phase 2 (in progress) — data collection status, corrected 2026-08-20:**
- `NEAForecastScraper/` + `nea_forecast_data/`: **complete.**
  `run_summary.json` shows the full 2020-02-01→2026-08-19 range scraped;
  142 dates permanently 404 from NEA itself (genuine gaps, not a scraper
  bug — confirmed via `failed_dates.log`), everything else present.
- `NEADataScraper/` (rainfall) + `nea_rainfall_data/`: **~86% done by date
  coverage, running unattended**, NOT "not started" as the external
  session note claimed — that note is now stale. 2,063 of ~2,392 expected
  date-folders exist (2020-02-01 through 2025-09-23 so far), 2,031/2,063
  fully complete at the page level. No collated daily-mm CSV yet
  (`collate_rainfall.py` hasn't been run against the finished portion).
- **Not yet built**: the actual "Forecast-Predictive" simulator arm (act
  N days ahead using forecast `lead_days`, compare against Active
  Monitoring's same-day trigger), and empirical calibration of
  `confidence(tier, lead_days)` against the rainfall ground truth once
  both scrapes finish. This is the single highest-value piece of
  remaining work on this track — see §6.

## 6. Recommendation: shift priority to validation, not more UI/backend features

Given ~3 months to project conclusion (§1), the balance of remaining work
has inverted from what the interim report anticipated:

- **Software (backend + app) has outpaced validation.** The three-domain
  digital twin (§4.1) is sophisticated, well-tested (~280+ passing
  assertions), and arguably feature-complete for FYP scope — Penman
  evaporation with a real calibration check, a camera-grounded (not
  first-principles-guessed) algae model, full undo/versioning. The mobile
  app's newer layer (§4.4) is production-grade. **Building more features
  in either place has diminishing return right now.**
- **The physical/hardware layer — already the interim report's own
  flagged risk — is now the binding constraint on the whole project**,
  and has gotten *more* concerning, not less, since April: the LUX sensor
  is still not integrated and is actively feeding a wrong constant into
  the algae model (§4.3); the camera's adaptive scheduling is fully
  disabled and its device identity is entirely unset, defeating the
  camera-grounding design that is the algae engine's main selling point
  over "pure guessing" (§4.2); pH/TDS calibration against real buffer
  solutions — the report's own stated immediate next step in April — has
  still not happened. **All the well-tested backend sophistication in
  §4.1 currently has no real sensor data to validate itself against.**
- **The Dashboard/Detail-graph split-brain (§4.4)** means even the mobile
  app doesn't yet surface the validated backend to the user in the
  primary view — but fixing this is a small, mechanical wiring task
  (point 3 dashboard cards at the same calls the detail screen already
  makes; delete 3 confirmed-dead files), not a reason to invest further in
  new UI development.
- **The NEA/simulator validation track (§5) is the closest thing to
  "proof the thesis works" evidence, and is cheap to finish**: forecast
  data is fully scraped, rainfall ground truth is ~86% there on its own
  unattended timeline, and the remaining work (build the forecast-arm
  simulator, calibrate confidence-by-lead-time) is scoped and doesn't
  depend on any hardware fix above.

**Concrete suggested order for the remaining ~3 months**, roughly in
priority order and each individually small:
1. Fix the ~4 bounded hardware/firmware issues that block real data:
   solder + wire the LUX sensor (removes the fake-35.0 corruption of the
   algae model), un-stub `imageSchedule.py` (uncomment real returns + add
   the missing `break`), add a device/user-ID header to `CameraMain.ino`'s
   upload (unlocks per-pond camera grounding for every physical pond, not
   just one), recalibrate pH/TDS against real buffer/standard solutions.
2. Finish Phase 2 of the simulator/NEA validation track — this produces
   the strongest quantitative claim for the final report and is largely
   unblocked already.
3. Collapse the Dashboard/Detail-graph split-brain and delete the
   confirmed-dead Flutter files — small, removes a real correctness risk,
   not "new development."
4. Only after 1-3: consider new mobile UI features (e.g. Settings) or new
   backend capability. Right now more of either would widen, not close,
   the gap between "what the system claims" and "what's been validated
   with real data" — which is the actual deliverable of an interim-vs-
   final FYP report.

## 7. Notes for future sessions

- Don't trust `README_INTEGRATION.md`'s "Still open" list uncritically —
  it correctly flags "no auth" and "single-process only" (both fine to
  deprioritize per the user's explicit standing guidance, see memory), but
  its claim about `CameraMain.ino` hardcoding user_id `15` is wrong; the
  real bug is worse (no ID sent at all) — verified directly 2026-08-20.
- The user has said to go light on cybersecurity/auth/gating critique for
  this project — it's an FYP evaluated by a non-technical-security-focused
  supervisor; don't spend review effort there unless asked.
- Simulator filenames drifted: `simulator1.py` (not `simulator.py`) is the
  actively-maintained Phase 1 file as of 2026-08-19.
