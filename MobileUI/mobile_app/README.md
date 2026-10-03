# mobile_app

Flutter client for OutdoorKoi.

## Configuration

Every environment value is passed at build time from a JSON file:

```bash
cp env/dev.json.example env/dev.json     # or env/prod.json.example
flutter run --dart-define-from-file=env/dev.json
```

| Key | Value |
|---|---|
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_PUBLISHABLE_KEY` | Supabase publishable (anon) key; never the secret key |
| `DIGITAL_TWIN_BASE_URL` | Base URL of the digital twin API (`python -m koi.api`) |
| `POND_IMAGE_BUCKET` | Storage bucket for camera frames; must match `POND_IMAGE_BUCKET` in Backend/.env |

The real `env/*.json` files are gitignored. There are no built-in
defaults: a build missing any value opens on a configuration error screen
that lists what is missing (see `lib/config/app_config.dart`).

## Tests

```bash
flutter test                              # widget, golden and flow tests
flutter test integration_test -d windows  # same flow on a device; pick one with -d
```

Screens read and write through `PondDataSource`
(`lib/data/pond_data_source.dart`). The app uses the live source; tests put
a `FakePondDataSource` in a `PondDataScope` (`test/helpers/`), so no test
reaches Supabase or the digital twin API. `pumpScreen` builds any screen
with a fake source and seeded SharedPreferences.

Goldens live in `test/goldens/`. `test/flutter_test_config.dart` loads
Roboto and the Material icon font from the Flutter SDK cache for every
test and allows 0.5 % of pixels to differ, for anti-aliasing differences
between Windows and the Linux CI runner. After an intended visual change:

```bash
flutter test --update-goldens test/goldens_test.dart
```
