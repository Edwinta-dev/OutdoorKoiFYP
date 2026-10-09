import 'package:mobile_app/widgets/shared/pond_widgets.dart';
import 'package:mobile_app/theme/app_theme.dart';
// lib/widgets/detail_graph/algae_severity_rating_card.dart
//
// The grounding control for the Algal & Solar detail screen: shows the
// latest ESP32-CAM frame and asks the user to rate what they see.
//
// WHY THIS IS NOT JUST A SURVEY WIDGET
// ------------------------------------
// The rating is a first-class observation. On submit the engine corrects
// its modelled green level toward the rating at HUMAN_TRUST (0.85, higher
// than the camera's 0.70), because a person standing at the pond is
// better evidence than a 640x480 JPEG of one corner of it. That is the
// only mechanism in the whole system capable of catching a lying camera -
// a fouled lens reads as pond algae and is otherwise invisible.
//
// The accumulated (label, green_ratio) pairs also calibrate the alert
// thresholds, replacing baseline-relative guesses with measured class
// boundaries. So the card has to show its work: what the rating did to
// the estimate, how much calibration is still outstanding, and whether
// the camera itself looks like it is drifting.
//
// Two design rules this widget enforces:
//   1. The rating is attached to a SPECIFIC frame (imageId), never to
//      "now". Rating at 9pm against a 6pm photo without pinning the frame
//      silently corrupts the calibration set.
//   2. Obstruction is presented in the same list for UX simplicity but is
//      visually separated, because it is a data-quality flag on a
//      different axis - not a level above "severe".
//
// DATA SOURCES
// ------------
// The camera frame comes from Supabase directly (PondCameraStorage) - one
// imageTable query for the photo, frame id and green_ratio. Flask has
// nothing to add to that, and routing it through the analysis service
// would only mean a blank photo whenever that process is down.
// Calibration state does come from Flask, because only the engine has it.
// The two are merged in _load() and degrade independently.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../data/providers.dart';
import '../../data/rating_card_data.dart';
import '../../utils/digital_twin_api.dart';
import '../../utils/pond_camera_storage.dart';

class AlgaeSeverityRatingCard extends ConsumerStatefulWidget {
  final int userId;

  /// A gallery selection stays pinned even when the latest frame changes.
  final PondCameraFrame? frame;

  /// Called after a successful submit or undo so the parent can refresh
  /// the chart and the forecast card - a rating moves the model, so
  /// everything downstream is now stale.
  final VoidCallback? onRatingChanged;

  const AlgaeSeverityRatingCard({
    super.key,
    required this.userId,
    this.frame,
    this.onRatingChanged,
  });

  @override
  ConsumerState<AlgaeSeverityRatingCard> createState() =>
      _AlgaeSeverityRatingCardState();
}

class _AlgaeSeverityRatingCardState
    extends ConsumerState<AlgaeSeverityRatingCard> {
  Color get _accent => AppColors.of(context).water;

  AlgaeSeverity? _selected;
  bool _submitting = false;
  AlgaeRatingResult? _lastResult;
  String? _error;

  void _reload({bool notifyParent = false}) {
    ref.invalidate(cameraFrameProvider(widget.userId));
    ref.invalidate(ratingContextProvider(widget.userId));
    ref.invalidate(algaeForecastProvider(widget.userId));
    ref.invalidate(pondDashboardProvider);
    setState(() {
      _selected = null;
    });
    if (notifyParent) widget.onRatingChanged?.call();
  }

  Future<void> _submit(RatingCardData data) async {
    final choice = _selected;
    if (choice == null || _submitting) return;

    setState(() {
      _submitting = true;
      _error = null;
    });

    final res = await ref
        .read(ratingsRepositoryProvider)
        .submitAlgaeRating(
          userId: widget.userId,
          severity: choice,
          // Pin the exact frame being rated - see the header comment.
          imageId: data.frame?.id,
          greenRatio: data.frame?.greenRatio,
        );

    if (!mounted) return;
    setState(() {
      _submitting = false;
      _lastResult = res.result;
      _error = res.error;
    });

    if (res.result != null) {
      _reload(notifyParent: true);
    }
  }

  Future<void> _undo() async {
    final id = _lastResult?.ratingId;
    setState(() => _submitting = true);
    final ok = await ref
        .read(ratingsRepositoryProvider)
        .undoAlgaeRating(userId: widget.userId, ratingId: id);
    if (!mounted) return;
    setState(() {
      _submitting = false;
      if (ok) _lastResult = null;
      if (!ok) _error = 'Could not undo - this is no longer the newest rating.';
    });
    if (ok) _reload(notifyParent: true);
  }

  // ------------------------------------------------------------------

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<RatingCardData>(
      future: widget.frame == null
          ? ref.watch(ratingCardProvider(widget.userId).future)
          : ref.watch(
              selectedFrameRatingProvider((
                pond: widget.userId,
                frame: widget.frame!,
              )).future,
            ),
      builder: (context, snapshot) {
        if (snapshot.connectionState == ConnectionState.waiting) {
          return _shell(
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: 28),
              child: Center(
                child: SizedBox(
                  width: 22,
                  height: 22,
                  child: CircularProgressIndicator(
                    strokeWidth: 2.4,
                    color: _accent,
                  ),
                ),
              ),
            ),
          );
        }
        if (snapshot.hasError) {
          debugPrint('AlgaeSeverityRatingCard load failed: ${snapshot.error}');
        }
        final data = snapshot.data;
        if (data == null) {
          return _shell(child: _unavailable());
        }
        return _card(data);
      },
    );
  }

  Widget _unavailable() => PondErrorState(
    title: 'Rate What You See',
    message:
        'Could not load the pond camera or your device profile. This is '
        'not the "analysis service is down" case - that one still shows '
        'the photo - so it usually means local storage is unavailable.',
    onRetry: () => _reload(),
  );

  Widget _card(RatingCardData data) {
    final ctx = data.context;
    final cal = ctx?.calibration;
    final frame = data.frame;
    final canRate = ctx != null; // rating requires the Flask service

    return OutcomeCardShell(
      accent: _accent,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _header(),
          const SizedBox(height: AppSpace.lg),

          // --- the frame being rated ---
          _framePanel(data),

          // --- camera drift warning, if the labels imply one ---
          if (cal != null && cal.drift.isConcerning) ...[
            const SizedBox(height: AppSpace.md),
            _driftBanner(cal.drift),
          ],

          const SizedBox(height: AppSpace.lg),
          Text(
            frame?.id == null && frame?.imageUrl == null
                ? 'How does the pond look right now?'
                : 'How does the pond look in this photo?',
            style: AppType.style(
              color: AppColors.of(context).text,
              fontSize: AppType.label,
              fontWeight: FontWeight.bold,
            ),
          ),
          const SizedBox(height: AppSpace.xs),
          Text(
            frame?.id == null && frame?.imageUrl == null
                ? 'No camera frame available, so this rating will correct the '
                      'model but will not join the calibration set.'
                : 'Your answer corrects the model directly - it counts for more '
                      'than the camera reading does.',
            style: AppType.style(
              color: AppColors.of(context).textMuted,
              fontSize: AppType.caption,
              height: 1.35,
            ),
          ),
          const SizedBox(height: AppSpace.md),

          // --- the rating options ---
          ..._severityOptions(cal),

          // --- previous rating, for anchoring ---
          if (ctx?.latestRating != null) ...[
            const SizedBox(height: AppSpace.md),
            _previousRating(ctx!.latestRating!),
          ],

          const SizedBox(height: AppSpace.lg),
          if (canRate) _submitRow(data) else _ratingOfflineNotice(),

          // --- what the last rating actually did ---
          if (_lastResult != null) ...[
            const SizedBox(height: AppSpace.md),
            _resultBanner(_lastResult!),
          ],

          if (_error != null) ...[
            const SizedBox(height: AppSpace.md),
            Row(
              children: [
                Icon(
                  Icons.error_outline,
                  color: AppColors.of(context).danger,
                  size: 14,
                ),
                const SizedBox(width: AppSpace.sm),
                Expanded(
                  child: Text(
                    _error!,
                    style: AppType.style(
                      color: AppColors.of(context).danger,
                      fontSize: AppType.caption,
                    ),
                  ),
                ),
              ],
            ),
          ],

          // --- calibration progress ---
          if (cal != null) ...[
            const SizedBox(height: AppSpace.lg),
            Divider(color: AppColors.of(context).outline, height: 1),
            const SizedBox(height: AppSpace.md),
            _calibrationPanel(cal),
          ],
        ],
      ),
    );
  }

  // ------------------------------------------------------------------

  Widget _header() => Row(
    children: [
      Icon(Icons.rate_review_outlined, color: _accent, size: 18),
      const SizedBox(width: AppSpace.sm),
      Expanded(
        child: Text(
          'Rate What You See',
          style: AppType.style(
            color: AppColors.of(context).text,
            fontWeight: FontWeight.bold,
            fontSize: AppType.body,
          ),
        ),
      ),
    ],
  );

  Widget _framePanel(RatingCardData data) {
    final frame = data.frame;
    final url = frame?.imageUrl;
    return ClipRRect(
      borderRadius: BorderRadius.circular(14),
      child: Stack(
        children: [
          AspectRatio(
            aspectRatio: 4 / 3,
            child: url == null
                ? Container(
                    color: AppColors.of(context).text.withValues(alpha: 0.04),
                    child: Center(
                      child: Padding(
                        padding: const EdgeInsets.symmetric(
                          horizontal: AppSpace.xl,
                        ),
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Icon(
                              Icons.photo_camera_outlined,
                              color: AppColors.of(context).outline,
                              size: 30,
                            ),
                            const SizedBox(height: AppSpace.sm),
                            Text(
                              frame?.id == null
                                  ? 'No camera frame yet'
                                  : 'Camera image unavailable',
                              style: AppType.style(
                                color: AppColors.of(context).textMuted,
                                fontSize: AppType.label,
                              ),
                            ),
                            const SizedBox(height: AppSpace.xs),
                            Text(
                              // Say WHY. "No frames for user 455" is
                              // actionable; a bare placeholder is not,
                              // especially while the ESP32 is still
                              // writing under a different id.
                              data.frameError ??
                                  (frame?.id == null
                                      ? 'Waiting for the pond camera to report.'
                                      : 'This rating stays attached to the selected frame.'),
                              textAlign: TextAlign.center,
                              style: AppType.style(
                                color: AppColors.of(context).textMuted,
                                fontSize: AppType.micro,
                                height: 1.35,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                  )
                : Image.network(
                    url,
                    fit: BoxFit.cover,
                    // The bucket is public but a phone on mobile data may
                    // still fail - degrade to a labelled placeholder
                    // rather than a broken-image glyph.
                    errorBuilder: (_, _, _) => Container(
                      color: AppColors.of(context).text.withValues(alpha: 0.04),
                      child: Center(
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Icon(
                              Icons.broken_image_outlined,
                              color: AppColors.of(context).outline,
                              size: 28,
                            ),
                            const SizedBox(height: AppSpace.sm),
                            Text(
                              'Photo could not be loaded',
                              style: AppType.style(
                                color: AppColors.of(context).textMuted,
                                fontSize: AppType.caption,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                    loadingBuilder: (context, child, progress) {
                      if (progress == null) return child;
                      return Container(
                        color: AppColors.of(
                          context,
                        ).text.withValues(alpha: 0.04),
                        child: Center(
                          child: SizedBox(
                            width: 20,
                            height: 20,
                            child: CircularProgressIndicator(
                              strokeWidth: 2,
                              color: _accent,
                            ),
                          ),
                        ),
                      );
                    },
                  ),
          ),

          // Frame metadata overlay - makes it unambiguous WHICH reading
          // is being rated.
          if (url != null)
            Positioned(
              left: 0,
              right: 0,
              bottom: 0,
              child: Container(
                padding: const EdgeInsets.symmetric(
                  horizontal: AppSpace.md,
                  vertical: AppSpace.sm,
                ),
                decoration: BoxDecoration(
                  gradient: LinearGradient(
                    begin: Alignment.topCenter,
                    end: Alignment.bottomCenter,
                    colors: [
                      AppColors.transparent,
                      AppColors.of(
                        context,
                      ).imageOverlay.withValues(alpha: 0.75),
                    ],
                  ),
                ),
                child: Row(
                  children: [
                    Icon(
                      Icons.schedule,
                      color: AppColors.of(context).onImage,
                      size: 12,
                    ),
                    const SizedBox(width: AppSpace.xs),
                    Expanded(
                      child: Text(
                        _relativeTime(frame?.capturedAt),
                        style: AppType.style(
                          color: AppColors.of(context).onImage,
                          fontSize: AppType.caption,
                        ),
                      ),
                    ),
                    if (frame?.greenRatio != null)
                      Text(
                        'camera reads '
                        '${(frame!.greenRatio! * 100).toStringAsFixed(2)}%',
                        style: AppType.style(
                          color: AppColors.of(context).onImage,
                          fontSize: AppType.micro,
                        ),
                      ),
                  ],
                ),
              ),
            ),
          // hsvEngine already suspects this frame is blocked - surface
          // that so the user is nudged toward the right answer rather
          // than rating a leaf as severe algae.
          if (frame?.isFlaggedObstructed == true)
            Positioned(
              top: 8,
              left: 8,
              child: Container(
                padding: const EdgeInsets.symmetric(
                  horizontal: AppSpace.sm,
                  vertical: AppSpace.xs,
                ),
                decoration: BoxDecoration(
                  color: AppColors.of(
                    context,
                  ).imageOverlay.withValues(alpha: 0.65),
                  borderRadius: BorderRadius.circular(AppRadius.small),
                  border: Border.all(
                    color: AppColors.of(
                      context,
                    ).warningOnImage.withValues(alpha: 0.5),
                  ),
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(
                      Icons.visibility_off_outlined,
                      color: AppColors.of(context).warningOnImage,
                      size: 11,
                    ),
                    const SizedBox(width: AppSpace.xs),
                    Text(
                      'view may be blocked',
                      style: AppType.style(
                        color: AppColors.of(context).warningOnImage,
                        fontSize: AppType.micro,
                      ),
                    ),
                  ],
                ),
              ),
            ),
        ],
      ),
    );
  }

  Widget _ratingOfflineNotice() {
    return Container(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpace.md,
        vertical: 11,
      ),
      decoration: BoxDecoration(
        color: AppColors.of(context).text.withValues(alpha: 0.03),
        borderRadius: BorderRadius.circular(AppRadius.control),
        border: Border.all(
          color: AppColors.of(context).text.withValues(alpha: 0.08),
        ),
      ),
      child: Row(
        children: [
          Icon(
            Icons.cloud_off_outlined,
            color: AppColors.of(context).textMuted,
            size: 15,
          ),
          const SizedBox(width: AppSpace.sm),
          Expanded(
            child: Text(
              'Analysis service unreachable - ratings cannot be saved right '
              'now. The photo above is live from storage.',
              style: AppType.style(
                color: AppColors.of(context).textMuted,
                fontSize: AppType.caption,
                height: 1.35,
              ),
            ),
          ),
          TextButton(
            onPressed: () => _reload(),
            style: TextButton.styleFrom(
              foregroundColor: _accent,
              padding: const EdgeInsets.symmetric(horizontal: AppSpace.sm),
              minimumSize: const Size(0, 28),
              tapTargetSize: MaterialTapTargetSize.shrinkWrap,
            ),
            child: Text(
              'Retry',
              style: AppType.style(fontSize: AppType.caption),
            ),
          ),
        ],
      ),
    );
  }

  Widget _driftBanner(CameraDriftVerdict drift) {
    final severe = drift.verdict == 'drift_suspected';
    final color = severe
        ? AppColors.of(context).feeding
        : AppColors.of(context).warning;
    return Container(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpace.md,
        vertical: AppSpace.sm,
      ),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.10),
        borderRadius: BorderRadius.circular(AppRadius.control),
        border: Border.all(color: color.withValues(alpha: 0.4)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(Icons.cleaning_services_outlined, color: color, size: 15),
          const SizedBox(width: AppSpace.sm),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  severe
                      ? 'Camera lens may need cleaning'
                      : 'Camera baseline shifting',
                  style: AppType.style(
                    color: color,
                    fontSize: AppType.caption,
                    fontWeight: FontWeight.bold,
                  ),
                ),
                const SizedBox(height: AppSpace.xs),
                Text(
                  drift.detail,
                  style: AppType.style(
                    color: AppColors.of(context).textMuted,
                    fontSize: AppType.micro,
                    height: 1.35,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  List<Widget> _severityOptions(AlgaeCalibration? cal) {
    const severityScale = [
      AlgaeSeverity.none,
      AlgaeSeverity.minor,
      AlgaeSeverity.moderate,
      AlgaeSeverity.severe,
    ];
    final needed = cal?.labelsNeeded ?? const <String, int>{};

    final widgets = <Widget>[];
    for (final s in severityScale) {
      widgets.add(
        _optionTile(
          s,
          // Highlight classes the model is still short of examples for -
          // uncertainty sampling beats random prompting when a healthy
          // pond reads "none" almost every time.
          wanted: (needed[s.wire] ?? 0) > 0,
        ),
      );
      widgets.add(const SizedBox(height: AppSpace.sm));
    }

    // Obstruction is visually separated: it is a data-quality flag, not a
    // fifth severity level, and it is handled completely differently by
    // the engine (it removes a reading rather than describing one).
    widgets.add(const SizedBox(height: AppSpace.xs));
    widgets.add(
      Row(
        children: [
          Expanded(
            child: Container(height: 1, color: AppColors.of(context).outline),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: AppSpace.sm),
            child: Text(
              'or flag the reading',
              style: AppType.style(
                color: AppColors.of(context).textMuted,
                fontSize: AppType.micro,
              ),
            ),
          ),
          Expanded(
            child: Container(height: 1, color: AppColors.of(context).outline),
          ),
        ],
      ),
    );
    widgets.add(const SizedBox(height: AppSpace.sm));
    widgets.add(_optionTile(AlgaeSeverity.obstruction, wanted: false));

    return widgets;
  }

  Widget _optionTile(AlgaeSeverity s, {required bool wanted}) {
    final selected = _selected == s;
    final isObstruction = s == AlgaeSeverity.obstruction;
    final color = isObstruction
        ? AppColors.of(context).textMuted
        : _severityColor(s);

    return InkWell(
      onTap: _submitting ? null : () => setState(() => _selected = s),
      borderRadius: BorderRadius.circular(AppRadius.control),
      child: Container(
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpace.md,
          vertical: AppSpace.md,
        ),
        decoration: BoxDecoration(
          color: selected
              ? color.withValues(alpha: 0.14)
              : AppColors.of(context).text.withValues(alpha: 0.03),
          borderRadius: BorderRadius.circular(AppRadius.control),
          border: Border.all(
            color: selected
                ? color.withValues(alpha: 0.6)
                : AppColors.of(context).text.withValues(alpha: 0.06),
            width: selected ? 1.4 : 1,
          ),
        ),
        child: Row(
          children: [
            Icon(
              selected
                  ? Icons.radio_button_checked
                  : Icons.radio_button_unchecked,
              color: selected ? color : AppColors.of(context).outline,
              size: 17,
            ),
            const SizedBox(width: AppSpace.md),
            if (!isObstruction) ...[
              _severityDot(s),
              const SizedBox(width: AppSpace.sm),
            ] else ...[
              Icon(
                Icons.visibility_off_outlined,
                color: AppColors.of(context).textMuted,
                size: 14,
              ),
              const SizedBox(width: AppSpace.sm),
            ],
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Flexible(
                        child: Text(
                          s.label,
                          style: AppType.style(
                            color: selected
                                ? AppColors.of(context).text
                                : AppColors.of(context).textSecondary,
                            fontSize: AppType.label,
                            fontWeight: selected
                                ? FontWeight.bold
                                : FontWeight.w500,
                          ),
                        ),
                      ),
                      if (wanted) ...[
                        const SizedBox(width: AppSpace.sm),
                        Container(
                          padding: const EdgeInsets.symmetric(
                            horizontal: AppSpace.xs,
                            vertical: 1.5,
                          ),
                          decoration: BoxDecoration(
                            color: _accent.withValues(alpha: 0.15),
                            borderRadius: BorderRadius.circular(
                              AppRadius.small,
                            ),
                          ),
                          child: Text(
                            'needed',
                            style: AppType.style(
                              color: _accent,
                              fontSize: AppType.micro,
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                        ),
                      ],
                    ],
                  ),
                  const SizedBox(height: AppSpace.xxs),
                  Text(
                    s.hint,
                    style: AppType.style(
                      color: AppColors.of(context).textMuted,
                      fontSize: AppType.micro,
                      height: 1.25,
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _severityDot(AlgaeSeverity s) {
    final color = _severityColor(s);
    return Container(
      width: 10,
      height: 10,
      decoration: BoxDecoration(
        color: color,
        shape: BoxShape.circle,
        boxShadow: [
          BoxShadow(
            color: color.withValues(alpha: 0.5),
            blurRadius: 5,
            spreadRadius: 0.5,
          ),
        ],
      ),
    );
  }

  Widget _previousRating(AlgaeRating prev) {
    final label = prev.severity?.label ?? 'Blocked view';
    return Container(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpace.md,
        vertical: AppSpace.sm,
      ),
      decoration: BoxDecoration(
        color: AppColors.of(context).text.withValues(alpha: 0.03),
        borderRadius: BorderRadius.circular(AppRadius.control),
        border: Border.all(
          color: AppColors.of(context).text.withValues(alpha: 0.06),
        ),
      ),
      child: Row(
        children: [
          Icon(Icons.history, color: AppColors.of(context).textMuted, size: 13),
          const SizedBox(width: AppSpace.sm),
          Expanded(
            child: Text(
              // Anchoring against the previous judgement is the cheapest
              // defence against a single rater's scale drifting over
              // weeks - "minor" in August should mean what it meant in June.
              'Last time you rated this "$label"'
              '${prev.ratedAt != null ? ' ${_relativeTime(prev.ratedAt)}' : ''}',
              style: AppType.style(
                color: AppColors.of(context).textMuted,
                fontSize: AppType.caption,
              ),
            ),
          ),
          if (prev.greenRatioAtRating != null)
            Text(
              '${(prev.greenRatioAtRating! * 100).toStringAsFixed(2)}%',
              style: AppType.style(
                color: AppColors.of(context).textMuted,
                fontSize: AppType.micro,
              ),
            ),
        ],
      ),
    );
  }

  Widget _submitRow(RatingCardData data) {
    final canSubmit = _selected != null && !_submitting;
    return SizedBox(
      width: double.infinity,
      child: FilledButton.icon(
        onPressed: canSubmit ? () => _submit(data) : null,
        style: FilledButton.styleFrom(
          backgroundColor: _accent.withValues(alpha: 0.18),
          foregroundColor: _accent,
          disabledBackgroundColor: AppColors.of(
            context,
          ).text.withValues(alpha: 0.04),
          disabledForegroundColor: AppColors.of(context).outline,
          padding: const EdgeInsets.symmetric(vertical: 13),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(AppRadius.control),
            side: BorderSide(
              color: canSubmit
                  ? _accent.withValues(alpha: 0.4)
                  : AppColors.of(context).outline,
            ),
          ),
        ),
        icon: _submitting
            ? SizedBox(
                width: 14,
                height: 14,
                child: CircularProgressIndicator(
                  strokeWidth: 2,
                  color: _accent,
                ),
              )
            : const Icon(Icons.check_circle_outline, size: 16),
        label: Text(
          _submitting ? 'Saving...' : 'Submit rating',
          style: AppType.style(
            fontSize: AppType.label,
            fontWeight: FontWeight.bold,
          ),
        ),
      ),
    );
  }

  Widget _resultBanner(AlgaeRatingResult r) {
    final delta = r.delta;
    final obstruction = r.removedFrames > 0;

    final String message;
    if (obstruction) {
      message =
          'Reading discarded and removed from the growth estimate. '
          'The camera frame will no longer skew the forecast.';
    } else if (delta != null && r.greenBefore != null && r.greenAfter != null) {
      final dir = delta < 0 ? 'down' : 'up';
      message =
          'Estimate moved $dir from ${(r.greenBefore! * 100).toStringAsFixed(2)}% '
          'to ${(r.greenAfter! * 100).toStringAsFixed(2)}% coverage.';
    } else {
      message = 'Rating saved and applied to the model.';
    }

    return Container(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpace.md,
        vertical: AppSpace.sm,
      ),
      decoration: BoxDecoration(
        color: AppColors.of(context).healthy.withValues(alpha: 0.08),
        borderRadius: BorderRadius.circular(AppRadius.control),
        border: Border.all(
          color: AppColors.of(context).healthy.withValues(alpha: 0.3),
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(
                Icons.check_circle_outline,
                color: AppColors.of(context).healthy,
                size: 14,
              ),
              const SizedBox(width: AppSpace.sm),
              Expanded(
                child: Text(
                  message,
                  style: AppType.style(
                    color: AppColors.of(context).healthy,
                    fontSize: AppType.caption,
                    height: 1.35,
                  ),
                ),
              ),
              // Undo is not optional garnish here: a rating visibly moves
              // the forecast, so a mis-tap has to be reversible. The
              // engine restores the exact prior level, thresholds and
              // growth fit.
              TextButton(
                onPressed: _submitting ? null : _undo,
                style: TextButton.styleFrom(
                  foregroundColor: AppColors.of(context).textSecondary,
                  padding: const EdgeInsets.symmetric(horizontal: AppSpace.sm),
                  minimumSize: const Size(0, 28),
                  tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                ),
                child: Text(
                  'Undo',
                  style: AppType.style(fontSize: AppType.caption),
                ),
              ),
            ],
          ),
          if (r.warning != null) ...[
            const SizedBox(height: AppSpace.sm),
            Text(
              r.warning!,
              style: AppType.style(
                color: AppColors.of(context).feeding,
                fontSize: AppType.micro,
              ),
            ),
          ],
        ],
      ),
    );
  }

  Widget _calibrationPanel(AlgaeCalibration cal) {
    final total = cal.totalLabels;
    final remaining = cal.remainingForCalibration;
    // 2 labels per class x 4 classes is the minimum for full calibration.
    const target = 8;
    final progress = (total / target).clamp(0.0, 1.0);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Icon(
              cal.isCalibrated ? Icons.verified_outlined : Icons.tune,
              color: cal.isCalibrated
                  ? AppColors.of(context).healthy
                  : AppColors.of(context).textMuted,
              size: 14,
            ),
            const SizedBox(width: AppSpace.sm),
            Expanded(
              child: Text(
                cal.isCalibrated
                    ? 'Alert levels calibrated from your ratings'
                    : cal.isPartiallyCalibrated
                    ? 'Alert levels partly calibrated'
                    : 'Alert levels still estimated',
                style: AppType.style(
                  color: cal.isCalibrated
                      ? AppColors.of(context).healthy
                      : AppColors.of(context).textMuted,
                  fontSize: AppType.caption,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ),
            Text(
              '$total rating${total == 1 ? '' : 's'}',
              style: AppType.style(
                color: AppColors.of(context).textMuted,
                fontSize: AppType.micro,
              ),
            ),
          ],
        ),
        if (!cal.isCalibrated) ...[
          const SizedBox(height: AppSpace.sm),
          ClipRRect(
            borderRadius: BorderRadius.circular(AppRadius.small),
            child: LinearProgressIndicator(
              value: progress,
              minHeight: 5,
              backgroundColor: AppColors.of(
                context,
              ).text.withValues(alpha: 0.06),
              valueColor: AlwaysStoppedAnimation<Color>(_accent),
            ),
          ),
          const SizedBox(height: AppSpace.sm),
          Text(
            remaining > 0
                ? _neededSummary(cal)
                : 'Enough ratings collected - thresholds will calibrate on the '
                      'next update.',
            style: AppType.style(
              color: AppColors.of(context).textMuted,
              fontSize: AppType.micro,
              height: 1.35,
            ),
          ),
        ],
        if (cal.isCalibrated) ...[
          const SizedBox(height: AppSpace.sm),
          Text(
            'Watch at ${(cal.watchThreshold * 100).toStringAsFixed(2)}% coverage, '
            'action at ${(cal.actionThreshold * 100).toStringAsFixed(2)}% - both '
            'measured from where your own ratings changed.',
            style: AppType.style(
              color: AppColors.of(context).textMuted,
              fontSize: AppType.micro,
              height: 1.35,
            ),
          ),
        ],
      ],
    );
  }

  String _neededSummary(AlgaeCalibration cal) {
    final parts = <String>[];
    cal.labelsNeeded.forEach((k, v) {
      if (v > 0) {
        final s = AlgaeSeverityX.fromWire(k);
        parts.add('$v more "${s?.label ?? k}"');
      }
    });
    if (parts.isEmpty) return '';
    return 'Still needed to calibrate: ${parts.join(', ')}. Rate whenever the '
        'pond looks different from last time - varied examples are worth far '
        'more than repeats of the same state.';
  }

  // ------------------------------------------------------------------

  Widget _shell({required Widget child}) => OutcomeCardShell(child: child);

  Color _severityColor(AlgaeSeverity s) => switch (s) {
    AlgaeSeverity.none => AppColors.of(context).healthy,
    AlgaeSeverity.minor => AppColors.of(context).healthy,
    AlgaeSeverity.moderate => AppColors.of(context).warning,
    AlgaeSeverity.severe => AppColors.of(context).danger,
    AlgaeSeverity.obstruction => AppColors.of(context).textMuted,
  };

  String _relativeTime(DateTime? t) {
    if (t == null) return 'unknown time';
    final d = DateTime.now().difference(t.toLocal());
    if (d.inMinutes < 1) return 'just now';
    if (d.inMinutes < 60) return '${d.inMinutes} min ago';
    if (d.inHours < 24) return '${d.inHours} hr ago';
    if (d.inDays == 1) return 'yesterday';
    if (d.inDays < 7) return '${d.inDays} days ago';
    return '${t.toLocal().day}/${t.toLocal().month}';
  }
}
