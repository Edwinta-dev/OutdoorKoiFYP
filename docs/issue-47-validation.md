# Issue #47 validation

Acceptance: `flutter analyze --fatal-infos`, `flutter test`, and the
PowerShell equivalent of the screen/widget storage-access grep pass.
The grep finds zero files. `python tools/check.py all` passes apart from
the expected tool/service skips below. Pub uses `build/pub-cache`, and
pytest uses `build/pytest47-final`; both are repository-local and ignored.

| Suite | Before | After |
|---|---|---|
| Mobile | 102 passed | 122 passed |
| Backend | 1,522 passed, 1 failed, 4 errors, 1 skipped | 1,528 passed, 1 skipped |
| Tools | 26 passed, 6 errors | 32 passed |
| Firmware host | 98 passed | 98 passed |
| Focused API/schema | Not run separately before | 415 passed |
| Focused OpenAPI/contract/dashboard | Not run separately before | 58 passed |
| Local Supabase dev profile | Skipped in baseline | 1 skipped (explicit skip-detail run) |

Baseline errors came from the denied default pytest temporary directory.
The baseline model-version assertion failed because edits began while
that run was active, changing the working-tree dirty marker between
checks. Final runs used repository-local temporary directories and a
stable working tree; no test was weakened or skipped to fix these errors.
The initial check runner also could not write Pub's default external
cache. The separate baseline mobile test run passed using the local cache.

Skips:

- `sensor_bench`, `sensor_node`, `camera_node`: arduino-cli's
  `esp32:esp32` core is not installed.
- Local Supabase dev-profile test: `supabase status` failed, so the
  subprocess reports the local stack unavailable. SQL tests that can
  reach the disposable local database ran in the backend suite. The
  Docker CLI also cannot access its configuration/engine pipe here.

Owner actions: redeploy the backend and release the updated mobile app
together; older apps still calling retired aliases need to update. No
migration, credential change or firmware flashing is needed. Rerun the
local Supabase dev-profile check when local stack status is available.
No live service or hardware verification was performed.

Unmet contract rules: none. Camera `POST /upload`, model constants,
applied migrations and the frozen reference/report files are unchanged.
The pre-existing `.claude/settings.local.json` was left untouched.

Changed files:

| File | Reason |
|---|---|
| `Backend/README_INTEGRATION.md` | Documents current /v1 routes and the mobile repository/storage boundary. |
| `Backend/koi/api/openapi.py` | Removes deprecated aliases and their registration/OpenAPI machinery. |
| `Backend/koi/api/routes.py` | Removes deprecated aliases and their registration/OpenAPI machinery. |
| `Backend/koi/api/spec.py` | Removes deprecated aliases and their registration/OpenAPI machinery. |
| `Backend/tests/api/test_app_factory.py` | Migrates existing endpoint and ownership/validation assertions to /v1. |
| `Backend/tests/api/test_auth.py` | Migrates existing endpoint and ownership/validation assertions to /v1. |
| `Backend/tests/api/test_contract.py` | Checks typed pond-profile JSON keys against OpenAPI. |
| `Backend/tests/api/test_dashboard.py` | Migrates existing endpoint and ownership/validation assertions to /v1. |
| `Backend/tests/api/test_event_ledger_api.py` | Migrates existing endpoint and ownership/validation assertions to /v1. |
| `Backend/tests/api/test_openapi.py` | Migrates existing endpoint and ownership/validation assertions to /v1. |
| `Backend/tests/api/test_pond_profile_api.py` | Migrates existing endpoint and ownership/validation assertions to /v1. |
| `Backend/tests/api/test_request_validation.py` | Migrates existing endpoint and ownership/validation assertions to /v1. |
| `Backend/tests/api/test_salt_filter_api.py` | Migrates existing endpoint and ownership/validation assertions to /v1. |
| `Backend/tests/models/test_hypoxia.py` | Migrates existing endpoint and ownership/validation assertions to /v1. |
| `Backend/tests/storage/test_schema.py` | Keeps schema scanning over relocated writes and typed history consumers. |
| `Backend/tests/test_error_tracking.py` | Migrates existing endpoint and ownership/validation assertions to /v1. |
| `Backend/tests/test_logging.py` | Migrates existing endpoint and ownership/validation assertions to /v1. |
| `Backend/tests/test_metrics.py` | Migrates existing endpoint and ownership/validation assertions to /v1. |
| `Backend/tests/worker/test_poller_integration.py` | Migrates existing endpoint and ownership/validation assertions to /v1. |
| `Backend/tests/worker/test_worker_lease.py` | Migrates existing endpoint and ownership/validation assertions to /v1. |
| `MobileUI/mobile_app/lib/data/local_profile_repository.dart` | Centralizes device preferences and string/integer identity compatibility. |
| `MobileUI/mobile_app/lib/data/pond_data_source.dart` | Implements the repositories using /v1 and uncovered Supabase operations. |
| `MobileUI/mobile_app/lib/data/pond_profile.dart` | Defines typed profile/history data and the rating-card view model. |
| `MobileUI/mobile_app/lib/data/providers.dart` | Owns shared reads, loading/error state, polling and refreshes. |
| `MobileUI/mobile_app/lib/data/rating_card_data.dart` | Defines typed profile/history data and the rating-card view model. |
| `MobileUI/mobile_app/lib/data/repositories.dart` | Defines the eight narrow repository interfaces. |
| `MobileUI/mobile_app/lib/data/telemetry_history.dart` | Defines typed profile/history data and the rating-card view model. |
| `MobileUI/mobile_app/lib/main.dart` | Installs the production ProviderScope and local repository. |
| `MobileUI/mobile_app/lib/screens/dashboard_view.dart` | Reads repository providers and keeps only transient UI state locally. |
| `MobileUI/mobile_app/lib/screens/detail_graph_screen.dart` | Reads repository providers and keeps only transient UI state locally. |
| `MobileUI/mobile_app/lib/screens/fish_tips_view.dart` | Reads repository providers and keeps only transient UI state locally. |
| `MobileUI/mobile_app/lib/screens/onboarding_screen.dart` | Reads repository providers and keeps only transient UI state locally. |
| `MobileUI/mobile_app/lib/screens/settings_view.dart` | Reads repository providers and keeps only transient UI state locally. |
| `MobileUI/mobile_app/lib/utils/digital_twin_api.dart` | Injects HTTP transport and session tokens; uses /v1 and parses event chemistry. |
| `MobileUI/mobile_app/lib/utils/fish_image_helper.dart` | Moves identity access to the local repository and permits injected storage clients. |
| `MobileUI/mobile_app/lib/utils/image_controller.dart` | Moves identity access to the local repository and permits injected storage clients. |
| `MobileUI/mobile_app/lib/utils/pond_camera_storage.dart` | Moves identity access to the local repository and permits injected storage clients. |
| `MobileUI/mobile_app/lib/widgets/detail_graph/algae_severity_rating_card.dart` | Reads repository providers and keeps only transient UI state locally. |
| `MobileUI/mobile_app/lib/widgets/detail_graph/algae_status_card.dart` | Reads repository providers and keeps only transient UI state locally. |
| `MobileUI/mobile_app/lib/widgets/detail_graph/evaporation_status_card.dart` | Reads repository providers and keeps only transient UI state locally. |
| `MobileUI/mobile_app/lib/widgets/detail_graph/water_buffer_status_card.dart` | Reads repository providers and keeps only transient UI state locally. |
| `MobileUI/mobile_app/lib/widgets/modals/quick_log_modals.dart` | Reads repository providers and keeps only transient UI state locally. |
| `MobileUI/mobile_app/pubspec.lock` | Adds and locks flutter_riverpod through flutter pub add. |
| `MobileUI/mobile_app/pubspec.yaml` | Adds and locks flutter_riverpod through flutter pub add. |
| `MobileUI/mobile_app/test/dashboard_view_test.dart` | Moves existing flows to provider overrides and asserts the single dashboard request. |
| `MobileUI/mobile_app/test/flows/onboarding_to_feed_flow.dart` | Moves existing flows to provider overrides and asserts the single dashboard request. |
| `MobileUI/mobile_app/test/helpers/fake_pond_data_source.dart` | Implements the repositories using /v1 and uncovered Supabase operations. |
| `MobileUI/mobile_app/test/helpers/fixtures.dart` | Supplies observable repository fakes, device-storage fakes and /v1-shaped fixtures. |
| `MobileUI/mobile_app/test/helpers/pump_screen.dart` | Supplies observable repository fakes, device-storage fakes and /v1-shaped fixtures. |
| `MobileUI/mobile_app/test/main_layout_tab_persistence_test.dart` | Moves existing flows to provider overrides and asserts the single dashboard request. |
| `MobileUI/mobile_app/test/repositories_test.dart` | Tests production transports, provider sharing, compatibility and storage boundaries offline. |
| `README.md` | Documents current /v1 routes and the mobile repository/storage boundary. |
| `docs/api/README.md` | Documents current /v1 routes and the mobile repository/storage boundary. |
| `docs/api/openapi.yaml` | Regenerates the route contract without retired aliases. |
| `docs/issue-47-validation.md` | Records the requested file inventory, validation counts, skips and owner actions. |
| `docs/mobile-repositories.md` | Documents current /v1 routes and the mobile repository/storage boundary. |
