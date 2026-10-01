# archive/pre-refactor

Code superseded when `x_working_refactor/` was promoted into the main tree.
Each file keeps its original path under this folder, and `git log --follow`
on the archived path shows its history. Nothing here is built, tested or
deployed. Do not edit these files; change the replacement instead. Where the
replacement has the same path as the old file, that path now holds the
refactored version that came from `x_working_refactor/`.

| Old path | Replacement |
|---|---|
| `Backend/DataGovAPI/data.py` | retired: broken NEA fetcher, to be replaced by the NEA ingestion issue |
| `Backend/DigitalTwin/algae_engine.py` | `Backend/DigitalTwin/algae_engine.py` |
| `Backend/DigitalTwin/app.py` | `Backend/DigitalTwin/app.py` |
| `Backend/DigitalTwin/engine.py` | `Backend/DigitalTwin/engine.py` |
| `Backend/DigitalTwin/evaporation_engine.py` | `Backend/DigitalTwin/evaporation_engine.py` |
| `Backend/DigitalTwin/forecast_utils.py` | `Backend/DigitalTwin/forecast_utils.py` |
| `Backend/DigitalTwin/poller.py` | `Backend/DigitalTwin/poller.py` |
| `Backend/DigitalTwin/pond_twin.py` | `Backend/DigitalTwin/pond_twin.py` |
| `Backend/DigitalTwin/registry.py` | `Backend/DigitalTwin/registry.py` |
| `Backend/DigitalTwin/state_store.py` | `Backend/DigitalTwin/state_store.py` |
| `Backend/DigitalTwin/test_contract.py` | `Backend/DigitalTwin/test_contract.py` |
| `Backend/DigitalTwin/test_new_engines.py` | `Backend/DigitalTwin/test_new_engines.py` |
| `Backend/DigitalTwin/test_poller_integration.py` | `Backend/DigitalTwin/test_poller_integration.py` |
| `Backend/DigitalTwin/test_pond_twin.py` | `Backend/DigitalTwin/test_pond_twin.py` |
| `Backend/DigitalTwin/test_projection.py` | `Backend/DigitalTwin/test_projection.py` |
| `Backend/DigitalTwin/test_severity_ratings.py` | `Backend/DigitalTwin/test_severity_ratings.py` |
| `Backend/SensorAPI/camera/PythonServer/esp32cam_pythonanywhere.ino` | retired: duplicate of the camera sketch; the maintained copy is `Embedded/camera_node/camera_node.ino` |
| `Embedded/CameraTest/Camera_Arduino_Sketch/CameraMain.ino` | retired: earlier LAN-server camera sketch, superseded by `Embedded/camera_node/camera_node.ino` |
| `Embedded/DS18B20Sketch/DS18B20Sketch.ino` | retired: single-sensor test; scratchpad parsing now in `Embedded/libraries/koi_sensing/src/ds18b20_parse.h` |
| `Embedded/FullSketch/FullSketch.ino` | `Embedded/sensor_bench/sensor_bench.ino` (sensor maths moved to `Embedded/libraries/koi_sensing/`) |
| `Embedded/TempSensor/TempSensor.ino` | `Embedded/sensor_node/sensor_node.ino` (networked build with deep sleep) |
| `MobileUI/mobile_app/analysis_options.yaml` | `MobileUI/mobile_app/analysis_options.yaml` |
| `MobileUI/mobile_app/lib/assets/*` | `MobileUI/mobile_app/lib/assets/*` (same file names) |
| `MobileUI/mobile_app/lib/main.dart` | `MobileUI/mobile_app/lib/main.dart` |
| `MobileUI/mobile_app/lib/screens/dashboard_view.dart` | `MobileUI/mobile_app/lib/screens/dashboard_view.dart` |
| `MobileUI/mobile_app/lib/screens/detail_graph_screen.dart` | `MobileUI/mobile_app/lib/screens/detail_graph_screen.dart` |
| `MobileUI/mobile_app/lib/screens/fish_tips_view.dart` | `MobileUI/mobile_app/lib/screens/fish_tips_view.dart` |
| `MobileUI/mobile_app/lib/screens/main_layout.dart` | `MobileUI/mobile_app/lib/screens/main_layout.dart` |
| `MobileUI/mobile_app/lib/screens/onboarding_screen.dart` | `MobileUI/mobile_app/lib/screens/onboarding_screen.dart` |
| `MobileUI/mobile_app/lib/screens/settings_view.dart` | `MobileUI/mobile_app/lib/screens/settings_view.dart` |
| `MobileUI/mobile_app/lib/utils/digital_twin_api.dart` | `MobileUI/mobile_app/lib/utils/digital_twin_api.dart` |
| `MobileUI/mobile_app/lib/utils/fish_image_helper.dart` | `MobileUI/mobile_app/lib/utils/fish_image_helper.dart` |
| `MobileUI/mobile_app/lib/utils/helpers/algal_helpers.dart` | `MobileUI/mobile_app/lib/utils/helpers/algal_helpers.dart` |
| `MobileUI/mobile_app/lib/utils/helpers/ph_helpers.dart` | `MobileUI/mobile_app/lib/utils/helpers/ph_helpers.dart` |
| `MobileUI/mobile_app/lib/utils/helpers/temp_helpers.dart` | `MobileUI/mobile_app/lib/utils/helpers/temp_helpers.dart` |
| `MobileUI/mobile_app/lib/utils/image_controller.dart` | `MobileUI/mobile_app/lib/utils/image_controller.dart` |
| `MobileUI/mobile_app/lib/utils/pond_camera_storage.dart` | `MobileUI/mobile_app/lib/utils/pond_camera_storage.dart` |
| `MobileUI/mobile_app/lib/utils/pond_heuristics.dart` | `MobileUI/mobile_app/lib/utils/pond_heuristics.dart` |
| `MobileUI/mobile_app/lib/widgets/dashboard/nea_weather_ribbon.dart` | `MobileUI/mobile_app/lib/widgets/dashboard/nea_weather_ribbon.dart` |
| `MobileUI/mobile_app/lib/widgets/dashboard/ph_outcome_card.dart` | `MobileUI/mobile_app/lib/widgets/dashboard/ph_outcome_card.dart` |
| `MobileUI/mobile_app/lib/widgets/dashboard/solar_outcome_card.dart` | `MobileUI/mobile_app/lib/widgets/dashboard/solar_outcome_card.dart` |
| `MobileUI/mobile_app/lib/widgets/dashboard/temperature_outcome_card.dart` | `MobileUI/mobile_app/lib/widgets/dashboard/temperature_outcome_card.dart` |
| `MobileUI/mobile_app/lib/widgets/detail_graph/algae_severity_rating_card.dart` | `MobileUI/mobile_app/lib/widgets/detail_graph/algae_severity_rating_card.dart` |
| `MobileUI/mobile_app/lib/widgets/detail_graph/algae_status_card.dart` | `MobileUI/mobile_app/lib/widgets/detail_graph/algae_status_card.dart` |
| `MobileUI/mobile_app/lib/widgets/detail_graph/evaporation_status_card.dart` | `MobileUI/mobile_app/lib/widgets/detail_graph/evaporation_status_card.dart` |
| `MobileUI/mobile_app/lib/widgets/detail_graph/historical_line_chart.dart` | `MobileUI/mobile_app/lib/widgets/detail_graph/historical_line_chart.dart` |
| `MobileUI/mobile_app/lib/widgets/detail_graph/intervention_legend.dart` | `MobileUI/mobile_app/lib/widgets/detail_graph/intervention_legend.dart` |
| `MobileUI/mobile_app/lib/widgets/detail_graph/scarce_data_placeholder.dart` | `MobileUI/mobile_app/lib/widgets/detail_graph/scarce_data_placeholder.dart` |
| `MobileUI/mobile_app/lib/widgets/detail_graph/timeline_header_selector.dart` | `MobileUI/mobile_app/lib/widgets/detail_graph/timeline_header_selector.dart` |
| `MobileUI/mobile_app/lib/widgets/detail_graph/water_buffer_status_card.dart` | `MobileUI/mobile_app/lib/widgets/detail_graph/water_buffer_status_card.dart` |
| `MobileUI/mobile_app/lib/widgets/fish/fish_carousel_header.dart` | `MobileUI/mobile_app/lib/widgets/fish/fish_carousel_header.dart` |
| `MobileUI/mobile_app/lib/widgets/fish/fish_image_header.dart` | `MobileUI/mobile_app/lib/widgets/fish/fish_image_header.dart` |
| `MobileUI/mobile_app/lib/widgets/fish/fish_parameter_table.dart` | `MobileUI/mobile_app/lib/widgets/fish/fish_parameter_table.dart` |
| `MobileUI/mobile_app/lib/widgets/modals/nea_full_forecast_modal.dart` | `MobileUI/mobile_app/lib/widgets/modals/nea_full_forecast_modal.dart` |
| `MobileUI/mobile_app/lib/widgets/modals/quick_log_modals.dart` | `MobileUI/mobile_app/lib/widgets/modals/quick_log_modals.dart` |
| `MobileUI/mobile_app/pubspec.lock` | `MobileUI/mobile_app/pubspec.lock` |
| `MobileUI/mobile_app/pubspec.yaml` | `MobileUI/mobile_app/pubspec.yaml` |
| `MobileUI/mobile_app/test/widget_test.dart` | retired: Flutter template test that does not compile against the current app |

Moved into the new layout rather than archived (they had no refactored copy):

| Old path | New path |
|---|---|
| `Backend/SensorAPI/camera/PythonServer/{camera.py,hsvEngine.py,imageSchedule.py,requirements.txt,.env.example}` | `Backend/Camera/` |
| `Embedded/CameraTest/Camera_Arduino_Sketch/esp32cam_pythonanywhere.ino` | `Embedded/camera_node/camera_node.ino` |
| `Embedded/TestSketches/ph_bench_test.ino` | `Embedded/bench_tests/ph_bench_test/ph_bench_test.ino` |
| `Embedded/TestSketches/ph_hw828_ads1115.ino` | `Embedded/bench_tests/ph_hw828_ads1115/ph_hw828_ads1115.ino` |
| `x_working_refactor/Embedded/lib/*.h` | `Embedded/libraries/koi_sensing/src/*.h` |
| `x_working_refactor/Embedded/tests/test_all.cpp` | `Embedded/tests/test_all.cpp` |
| `x_working_refactor/Embedded/FullSketch_refactored.ino` | `Embedded/sensor_bench/sensor_bench.ino` |
| `x_working_refactor/Embedded/SensorNode_networked_PROPOSAL.ino` | `Embedded/sensor_node/sensor_node.ino` |
| `x_working_refactor/Backend/DigitalTwin/*` | `Backend/DigitalTwin/*` |
| `x_working_refactor/MobileUI/mobile_app/{lib,test,pubspec.yaml,pubspec.lock,analysis_options.yaml}` | `MobileUI/mobile_app/` |

`Backend/DigitalTwin/requirements.txt`, `Backend/DigitalTwin/README_INTEGRATION.md`,
`MobileUI/mobile_app/README.md` and the Flutter platform folders stayed where they were.
