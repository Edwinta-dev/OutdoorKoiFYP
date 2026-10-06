# Mobile design system

`MobileUI/mobile_app/lib/theme/app_theme.dart` holds the palette, spacing,
radii and type scale. `AppTheme.light` and `AppTheme.dark` use the same
purpose-based roles. The normal and configuration-error apps follow system
brightness. Native bars use the matching app-bar overlay style.

Use `AppColors.of(context)` for colours and `AppType` for text sizes. Colour
roles include canvas, navigation, surface, surfaceRaised, surfaceInset,
text, textSecondary, textMuted, outline, primary, info, water, healthy,
warning, danger, feeding and intervention. Image overlays retain a fixed
dark background with `onImage` text. Filled controls use `foregroundOn` to
choose a contrasting foreground.

`lib/widgets/shared/pond_widgets.dart` provides `MetricTile`, `StatusChip`,
`SectionHeader`, `OutcomeCardShell`, `PondEmptyState` and `PondErrorState`.
A status always includes a distinct icon and a word, plus an accessible
label. Mark model-derived metrics as estimates; camera coverage and frame
counts remain observations.

Text uses the system scaler without a cap or shrink-to-fit text. Instrument
panels stack above 1.2 text scale at phone widths; narrow panels also stack
below their minimum width. `AdaptiveRow` unwraps flexible children when it
stacks. Give short icon-and-copy rows an expanded text child where possible.
Sheets scroll and buttons use minimum heights. Tests cover 320 dp screens
at 1.0, 1.3 and 1.6 scale in both themes, populated species profiles and
all intervention forms.

## Golden images

`test/goldens_test.dart` compares the three dashboard outcome cards, the
feeding log sheet, and the shared components (light and dark themes at text
scale 1.0 and 1.6) with the PNGs in `test/goldens/`. They are recorded and
checked on Linux only, as CI runs them: Windows draws text differently by
3-6 % of pixels, so the file is skipped on Windows. A golden passes when at
most 0.5 % of its pixels differ (`test/flutter_test_config.dart`).

After an intended visual change, regenerate them on Linux with Docker from
the repository root:

```
python tools/update_goldens.py            # re-record test/goldens/*.png
python tools/update_goldens.py --check    # compare only, as CI does
```

The script runs the Flutter version CI pins in a Linux container on a copy
of the app, so nothing in the working tree but the PNGs changes. Review the
changed PNGs before committing. See docs/dev.md for details.
