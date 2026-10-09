import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

/// Purpose-based colours shared by both brightness modes.
class AppColors {
  const AppColors(this.brightness);
  final Brightness brightness;
  bool get isDark => brightness == Brightness.dark;
  static AppColors of(BuildContext context) =>
      AppColors(Theme.of(context).brightness);
  Color get canvas =>
      isDark ? const Color(0xFF070B12) : const Color(0xFFF3F7F8);
  Color get navigation =>
      isDark ? const Color(0xFF0F2027) : const Color(0xFFE3EFF0);
  Color get surface =>
      isDark ? const Color(0xFF131B2A) : const Color(0xFFFFFFFF);
  Color get surfaceRaised =>
      isDark ? const Color(0xFF1A323C) : const Color(0xFFE3EFF0);
  Color get surfaceInset =>
      isDark ? const Color(0xFF0A0E17) : const Color(0xFFEDF3F5);
  Color get text => isDark ? const Color(0xFFFFFFFF) : const Color(0xFF162D37);
  Color get textSecondary =>
      isDark ? const Color(0xFFBBC3CC) : const Color(0xFF435963);
  Color get textMuted =>
      isDark ? const Color(0xFF9BA8B6) : const Color(0xFF526874);
  Color get outline =>
      isDark ? const Color(0xFF354452) : const Color(0xFFB5C6CE);
  Color get primary =>
      isDark ? const Color(0xFF00E676) : const Color(0xFF006C45);
  Color get onPrimary =>
      isDark ? const Color(0xFF071C14) : const Color(0xFFFFFFFF);
  Color get info => isDark ? const Color(0xFF38BDF8) : const Color(0xFF006B96);
  Color get onInfo => foregroundOn(info);
  Color foregroundOn(Color background) => background.computeLuminance() > 0.179
      ? const Color(0xFF000000)
      : const Color(0xFFFFFFFF);
  Color get water => isDark ? const Color(0xFF64FFDA) : const Color(0xFF006B61);
  Color get healthy =>
      isDark ? const Color(0xFF50C878) : const Color(0xFF236D3D);
  Color get warning =>
      isDark ? const Color(0xFFFFD740) : const Color(0xFF805600);
  Color get danger =>
      isDark ? const Color(0xFFFF6868) : const Color(0xFFB3261E);
  Color get feeding =>
      isDark ? const Color(0xFFFFAB40) : const Color(0xFF8B4B00);
  Color get intervention =>
      isDark ? const Color(0xFFE040FB) : const Color(0xFF813691);
  Color get shadow => const Color(0xFF000000);
  Color get imageOverlay => const Color(0xFF000000);
  Color get warningOnImage => const Color(0xFFFFAB40);
  Color get onImage => const Color(0xFFFFFFFF);
  static const transparent = Color(0x00000000);
  static const waterMaskPreview = Color(0xAA00FF00);
}

class AppSpace {
  static const none = 0.0;
  static const xxs = 2.0;
  static const xs = 4.0;
  static const sm = 8.0;
  static const md = 12.0;
  static const lg = 16.0;
  static const xl = 20.0;
  static const xxl = 24.0;
  static const xxxl = 32.0;
}

class AppRadius {
  static const small = 4.0;
  static const chip = 8.0;
  static const control = 12.0;
  static const card = 16.0;
  static const panel = 20.0;
  static const sheet = 24.0;
  static const pill = 32.0;
}

/// The compact type scale keeps the existing instrument-panel appearance.
/// All type inherits Roboto and the current theme's foreground colour.
class AppType {
  static const micro = 10.0;
  static const caption = 11.0;
  static const label = 12.0;
  static const body = 14.0;
  static const title = 18.0;
  static const heading = 20.0;
  static const metric = 26.0;
  static const display = 34.0;
  static TextStyle style({
    Color? color,
    double fontSize = body,
    FontWeight? fontWeight,
    double? height,
    double? letterSpacing,
    FontStyle? fontStyle,
    TextDecoration? decoration,
  }) => TextStyle(
    fontFamily: 'Roboto',
    color: color,
    fontSize: fontSize,
    fontWeight: fontWeight,
    height: height,
    letterSpacing: letterSpacing,
    fontStyle: fontStyle,
    decoration: decoration,
  );
}

class AppTheme {
  static ThemeData get light => _build(Brightness.light);
  static ThemeData get dark => _build(Brightness.dark);
  static ThemeData _build(Brightness brightness) {
    final c = AppColors(brightness);
    final scheme =
        ColorScheme.fromSeed(
          seedColor: c.primary,
          brightness: brightness,
        ).copyWith(
          primary: c.primary,
          onPrimary: c.onPrimary,
          secondary: c.water,
          surface: c.surface,
          onSurface: c.text,
          onSurfaceVariant: c.textSecondary,
          error: c.danger,
          outline: c.outline,
        );
    final base = ThemeData(
      useMaterial3: true,
      colorScheme: scheme,
      fontFamily: 'Roboto',
    );
    return base.copyWith(
      scaffoldBackgroundColor: c.canvas,
      textTheme: base.textTheme.copyWith(
        bodySmall: AppType.style(
          fontSize: AppType.label,
          color: c.textSecondary,
        ),
        bodyMedium: AppType.style(color: c.text),
        bodyLarge: AppType.style(fontSize: 16, color: c.text),
        titleMedium: AppType.style(
          fontSize: AppType.title,
          fontWeight: FontWeight.bold,
          color: c.text,
        ),
        titleLarge: AppType.style(
          fontSize: AppType.heading,
          fontWeight: FontWeight.bold,
          color: c.text,
        ),
      ),
      appBarTheme: AppBarTheme(
        backgroundColor: c.navigation,
        foregroundColor: c.text,
        elevation: 0,
        scrolledUnderElevation: 0,
        centerTitle: false,
        titleTextStyle: AppType.style(
          fontSize: AppType.heading,
          fontWeight: FontWeight.bold,
          color: c.text,
        ),
        systemOverlayStyle: SystemUiOverlayStyle(
          statusBarColor: AppColors.transparent,
          statusBarIconBrightness: c.isDark
              ? Brightness.light
              : Brightness.dark,
          statusBarBrightness: brightness,
          systemNavigationBarColor: c.navigation,
          systemNavigationBarIconBrightness: c.isDark
              ? Brightness.light
              : Brightness.dark,
        ),
      ),
      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: c.navigation,
        indicatorColor: c.primary.withValues(alpha: 0.2),
        labelTextStyle: WidgetStatePropertyAll(
          AppType.style(fontSize: AppType.label, color: c.textSecondary),
        ),
        iconTheme: WidgetStateProperty.resolveWith(
          (states) => IconThemeData(
            color: states.contains(WidgetState.selected)
                ? c.primary
                : c.textMuted,
          ),
        ),
      ),
      bottomNavigationBarTheme: BottomNavigationBarThemeData(
        backgroundColor: c.navigation,
        selectedItemColor: c.primary,
        unselectedItemColor: c.textMuted,
        elevation: 0,
      ),
      dividerTheme: DividerThemeData(color: c.outline),
      bottomSheetTheme: BottomSheetThemeData(backgroundColor: c.surface),
    );
  }
}
