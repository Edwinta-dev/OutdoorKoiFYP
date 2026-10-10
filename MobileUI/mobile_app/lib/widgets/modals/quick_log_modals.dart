import 'package:mobile_app/widgets/shared/pond_widgets.dart';
import 'package:mobile_app/theme/app_theme.dart';
// lib/widgets/modals/quick_log_modals.dart

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../data/providers.dart';
import '../../data/repositories.dart';
import '../../utils/digital_twin_api.dart';
import '../../utils/event_id.dart';

/// Main Entry Point: Opens the Quick Action Option Selector
void showQuickActionSelector(BuildContext context) {
  showModalBottomSheet(
    context: context,
    isScrollControlled: true,
    backgroundColor: AppColors.of(context).surface,
    shape: const RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(
        top: Radius.circular(AppRadius.panel),
      ),
    ),
    builder: (ctx) => SingleChildScrollView(
      child: Padding(
        padding: const EdgeInsets.all(AppSpace.xl),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Log Pond Intervention',
              style: AppType.style(
                color: AppColors.of(context).text,
                fontSize: AppType.body,
                fontWeight: FontWeight.bold,
              ),
            ),
            const SizedBox(height: AppSpace.md),
            _actionTile(
              ctx,
              Icons.grain,
              AppColors.of(context).intervention,
              'Salt Addition',
              'Logs added salt in grams',
              'SALT',
            ),
            _actionTile(
              ctx,
              Icons.filter_alt_outlined,
              AppColors.of(context).warning,
              'Filter Cleaning',
              'Marks pH and TDS after maintenance for 24 hours',
              'FILTER_CLEAN',
            ),
            _actionTile(
              ctx,
              Icons.water_drop,
              AppColors.of(context).info,
              'Water Change',
              'Resets pH & TDS mineral accumulation',
              'WATER_CHANGE',
            ),
            _actionTile(
              ctx,
              Icons.opacity,
              AppColors.of(context).info,
              'Water Top-Up',
              'Restores volume lost to evaporation',
              'WATER_TOPUP',
            ),
            _actionTile(
              ctx,
              Icons.cleaning_services_outlined,
              AppColors.of(context).water,
              'Algae Scrub',
              'Resets visual greenery & photo solar buildup',
              'ALGAE_SCRUB',
            ),
            _actionTile(
              ctx,
              Icons.set_meal_outlined,
              AppColors.of(context).feeding,
              'Feeding Session',
              'Logs food grams & protein content',
              'FEEDING',
            ),
          ],
        ),
      ),
    ),
  );
}

Widget _actionTile(
  BuildContext ctx,
  IconData icon,
  Color color,
  String title,
  String sub,
  String type,
) {
  return ListTile(
    contentPadding: EdgeInsets.zero,
    leading: CircleAvatar(
      backgroundColor: color.withValues(alpha: 0.15),
      child: Icon(icon, color: color, size: 20),
    ),
    title: Text(
      title,
      style: AppType.style(
        color: AppColors.of(ctx).text,
        fontWeight: FontWeight.w600,
        fontSize: AppType.body,
      ),
    ),
    subtitle: Text(
      sub,
      style: AppType.style(
        color: AppColors.of(ctx).textMuted,
        fontSize: AppType.caption,
      ),
    ),
    onTap: () {
      Navigator.pop(ctx);
      showInterventionLogSheet(
        ctx,
        eventType: type,
        title: title,
        accentColor: color,
      );
    },
  );
}

/// Opens the log sheet for one event type, as the selector's tiles do.
Future<void> showInterventionLogSheet(
  BuildContext context, {
  required String eventType,
  required String title,
  required Color accentColor,
}) => showModalBottomSheet<void>(
  context: context,
  isScrollControlled: true,
  backgroundColor: AppColors.of(context).surface,
  shape: const RoundedRectangleBorder(
    borderRadius: BorderRadius.vertical(top: Radius.circular(AppRadius.panel)),
  ),
  builder: (_) => InterventionLogSheet(
    eventType: eventType,
    title: title,
    accentColor: accentColor,
  ),
);

// ============================================================================
// UNIFIED, DRY INTERVENTION LOG SHEET (~100 LINES FOR ALL 4 EVENT TYPES)
// ============================================================================
class InterventionLogSheet extends ConsumerStatefulWidget {
  final String eventType;
  final String title;
  final Color accentColor;

  /// The time the sheet opens on; tests pass a fixed one so the rendered
  /// timestamp does not depend on the wall clock.
  final DateTime? initialTimestamp;

  const InterventionLogSheet({
    super.key,
    required this.eventType,
    required this.title,
    required this.accentColor,
    this.initialTimestamp,
  });

  @override
  ConsumerState<InterventionLogSheet> createState() =>
      _InterventionLogSheetState();
}

class _InterventionLogSheetState extends ConsumerState<InterventionLogSheet> {
  final _formKey = GlobalKey<FormState>();
  final _val1Ctrl = TextEditingController(text: '25'); // Pct or Grams
  final _val2Ctrl = TextEditingController(); // Volume or Protein %

  String _selectedOption = 'Manual Scrub'; // For Algae or Food presets
  late DateTime _date;
  int _hour = 12, _min = 0;
  bool _isPm = true, _isSaving = false;

  @override
  void initState() {
    super.initState();
    final now = widget.initialTimestamp ?? DateTime.now();
    _date = now;
    _isPm = now.hour >= 12;
    _hour = now.hour % 12 == 0 ? 12 : now.hour % 12;
    _min = now.minute;

    if (widget.eventType == 'FEEDING') _val1Ctrl.text = '10';
  }

  Future<void> _save() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() => _isSaving = true);

    final source = ref.read(eventsRepositoryProvider);
    try {
      final prefs = await ref.read(localProfileRepositoryProvider).load();
      final userId = int.tryParse(prefs.pondId?.toString() ?? '0') ?? 0;

      // Fish stock was captured at onboarding into local profile storage (not
      // synced to Supabase's UserData table), so it rides along on each
      // event push for the DigitalTwin engine to use as PondConfig context.
      final ownedSpecies = prefs.species;
      final fishType = ownedSpecies.isEmpty ? null : ownedSpecies.join(', ');
      final fishCount = prefs.fishCount;

      final hour24 = (_hour % 12) + (_isPm ? 12 : 0);
      final timestamp = DateTime(
        _date.year,
        _date.month,
        _date.day,
        hour24,
        _min,
      );

      // One id for the row and the post, so the twin applies the event
      // once however it arrives (the post, a retry or its poller).
      final eventId = newEventId();
      double? saltGrams;
      String? notes;
      double? volumePercent;
      double? volumeLitres;
      double? foodGrams;
      double? proteinPercent;

      // Map dynamic fields based on event type
      if (widget.eventType == 'WATER_CHANGE' ||
          widget.eventType == 'WATER_TOPUP') {
        volumePercent = double.parse(_val1Ctrl.text.trim());
        volumeLitres = double.tryParse(_val2Ctrl.text.trim());
      } else if (widget.eventType == 'FEEDING') {
        foodGrams = double.parse(_val1Ctrl.text.trim());
        proteinPercent = double.tryParse(_val2Ctrl.text.trim()) ?? 40.0;
      } else if (widget.eventType == 'SALT') {
        saltGrams = double.parse(_val1Ctrl.text.trim());
      } else if (widget.eventType == 'FILTER_CLEAN') {
        notes = _val2Ctrl.text.trim().isEmpty ? null : _val2Ctrl.text.trim();
      } else if (widget.eventType == 'ALGAE_SCRUB') {}

      // The repository records chart history and sends the same ID to /v1.
      final assessment = await _pushToDigitalTwin(
        source,
        userId: userId,
        timestamp: timestamp,
        eventId: eventId,
        saltGrams: saltGrams,
        notes: notes,
        volumePercent: volumePercent,
        volumeLitres: volumeLitres,
        foodGrams: foodGrams,
        proteinPercent: proteinPercent,
        fishType: fishType,
        fishCount: fishCount,
      );

      if (!mounted) return;
      ref.invalidate(pondDashboardProvider);
      ref.invalidate(assessmentProvider(userId));
      ref.invalidate(evaporationForecastProvider(userId));
      ref.invalidate(algaeForecastProvider(userId));
      ref.invalidate(historyProvider);
      if (mounted) {
        Navigator.pop(context);
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              assessment != null
                  ? '${widget.title} logged • water buffer status: ${assessment.category}'
                  : '${widget.title} logged successfully!',
            ),
            backgroundColor: widget.accentColor,
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        setState(() => _isSaving = false);
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Error: $e'),
            backgroundColor: AppColors.of(context).danger,
          ),
        );
      }
    }
  }

  Future<WaterChemistryAssessment?> _pushToDigitalTwin(
    EventsRepository source, {
    required int userId,
    required DateTime timestamp,
    required String eventId,
    double? saltGrams,
    String? notes,
    double? volumePercent,
    double? volumeLitres,
    double? foodGrams,
    double? proteinPercent,
    String? fishType,
    int? fishCount,
  }) {
    switch (widget.eventType) {
      case 'SALT':
        return source.logSalt(
          userId: userId,
          saltGrams: saltGrams!,
          timestamp: timestamp,
          eventId: eventId,
        );
      case 'FILTER_CLEAN':
        return source.logFilterClean(
          userId: userId,
          notes: notes,
          timestamp: timestamp,
          eventId: eventId,
        );
      case 'FEEDING':
        return source.logFeeding(
          userId: userId,
          foodGrams: foodGrams ?? 0.0,
          proteinPercent: proteinPercent ?? 40.0,
          timestamp: timestamp,
          eventId: eventId,
          fishType: fishType,
          fishCount: fishCount,
        );
      case 'WATER_CHANGE':
        return source.logWaterChange(
          userId: userId,
          volumePercent: volumePercent,
          volumeLitres: volumeLitres,
          timestamp: timestamp,
          eventId: eventId,
          fishType: fishType,
          fishCount: fishCount,
        );
      case 'WATER_TOPUP':
        return source.logTopUp(
          userId: userId,
          volumePercent: volumePercent,
          volumeLitres: volumeLitres,
          timestamp: timestamp,
          eventId: eventId,
          fishType: fishType,
          fishCount: fishCount,
        );
      case 'ALGAE_SCRUB':
        return source.logAlgalScrub(
          userId: userId,
          scrubType: _selectedOption,
          timestamp: timestamp,
          eventId: eventId,
          fishType: fishType,
          fishCount: fishCount,
        );
      default:
        return Future.value(null);
    }
  }

  @override
  Widget build(BuildContext context) {
    final bottomPad = MediaQuery.of(context).viewInsets.bottom;
    final isWater =
        widget.eventType == 'WATER_CHANGE' || widget.eventType == 'WATER_TOPUP';
    final isSalt = widget.eventType == 'SALT';
    final isFilter = widget.eventType == 'FILTER_CLEAN';
    final isFeed = widget.eventType == 'FEEDING';
    final isAlgae = widget.eventType == 'ALGAE_SCRUB';

    return SingleChildScrollView(
      physics: const BouncingScrollPhysics(),
      child: Padding(
        padding: EdgeInsets.only(
          left: AppSpace.xl,
          right: AppSpace.xl,
          top: AppSpace.xl,
          bottom: bottomPad + AppSpace.xl,
        ),
        child: Form(
          key: _formKey,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              AdaptiveRow(
                minWidth: 300,
                children: [
                  Icon(
                    Icons.cleaning_services,
                    color: widget.accentColor,
                    size: 20,
                  ),
                  const SizedBox(width: AppSpace.sm),
                  Text(
                    'Log ${widget.title}',
                    style: AppType.style(
                      color: AppColors.of(context).text,
                      fontSize: AppType.body,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: AppSpace.lg),

              // DYNAMIC FIELD 1 (Water % or Food Grams)
              if (isWater || isFeed || isSalt) ...[
                _inputField(
                  ctrl: _val1Ctrl,
                  label: isWater
                      ? 'Volume Percentage (%)'
                      : isSalt
                      ? 'Salt added (g)'
                      : 'Food per session (g)',
                  suffix: isWater ? '%' : 'g',
                  color: widget.accentColor,
                  validator: (v) =>
                      (v == null ||
                          double.tryParse(v) == null ||
                          (isSalt &&
                              (!double.parse(v).isFinite ||
                                  double.parse(v) <= 0)))
                      ? 'Enter valid value'
                      : null,
                ),
                const SizedBox(height: AppSpace.md),
              ],

              // DYNAMIC FIELD 2 (Litres or Protein %)
              if (isFilter)
                TextFormField(
                  controller: _val2Ctrl,
                  maxLength: 1000,
                  style: AppType.style(color: AppColors.of(context).text),
                  decoration: _inputDeco(
                    'Notes (optional)',
                    widget.accentColor,
                  ),
                )
              else if (isWater)
                _inputField(
                  ctrl: _val2Ctrl,
                  label: 'Volume (Litres) [Optional]',
                  suffix: 'L',
                  color: widget.accentColor,
                )
              else if (isFeed)
                _inputField(
                  ctrl: _val2Ctrl,
                  label: 'Protein Content (%)',
                  suffix: '%',
                  color: widget.accentColor,
                )
              else if (isAlgae)
                DropdownButtonFormField<String>(
                  initialValue: _selectedOption,
                  dropdownColor: AppColors.of(context).surface,
                  style: AppType.style(
                    color: AppColors.of(context).text,
                    fontSize: AppType.label,
                  ),
                  decoration: _inputDeco('Scrub Method', widget.accentColor),
                  items: ['Manual Scrub', 'UV Clarifier', 'Chemical Treatment']
                      .map((m) => DropdownMenuItem(value: m, child: Text(m)))
                      .toList(),
                  onChanged: (v) => setState(() => _selectedOption = v!),
                ),

              const SizedBox(height: AppSpace.lg),
              Text(
                'Event Timestamp',
                style: AppType.style(
                  color: AppColors.of(context).textMuted,
                  fontSize: AppType.caption,
                ),
              ),
              const SizedBox(height: AppSpace.sm),

              // COMPACT TIME/DATE SELECTOR
              AdaptiveRow(
                minWidth: 300,
                children: [
                  InkWell(
                    onTap: () async {
                      final d = await showDatePicker(
                        context: context,
                        initialDate: _date,
                        firstDate: DateTime(2025),
                        lastDate: DateTime.now(),
                      );
                      if (d != null) setState(() => _date = d);
                    },
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: AppSpace.md,
                        vertical: AppSpace.sm,
                      ),
                      decoration: BoxDecoration(
                        color: AppColors.of(
                          context,
                        ).text.withValues(alpha: 0.05),
                        borderRadius: BorderRadius.circular(AppRadius.chip),
                        border: Border.all(
                          color: AppColors.of(context).outline,
                        ),
                      ),
                      child: Text(
                        '${_date.day}/${_date.month}/${_date.year}',
                        style: AppType.style(
                          color: AppColors.of(context).text,
                          fontSize: AppType.label,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(width: AppSpace.sm),
                  _timeDropdown(
                    _hour,
                    12,
                    1,
                    (v) => setState(() => _hour = v!),
                  ),
                  Text(
                    ' : ',
                    style: AppType.style(
                      color: AppColors.of(context).textSecondary,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                  _timeDropdown(_min, 60, 0, (v) => setState(() => _min = v!)),
                  const SizedBox(width: AppSpace.sm),
                  ToggleButtons(
                    isSelected: [!_isPm, _isPm],
                    constraints: const BoxConstraints(
                      minWidth: 32,
                      minHeight: 32,
                    ),
                    borderRadius: BorderRadius.circular(AppRadius.small),
                    selectedColor: AppColors.of(
                      context,
                    ).foregroundOn(widget.accentColor),
                    fillColor: widget.accentColor,
                    children: [
                      Text(
                        'AM',
                        style: AppType.style(
                          fontSize: AppType.micro,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                      Text(
                        'PM',
                        style: AppType.style(
                          fontSize: AppType.micro,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                    ],
                    onPressed: (i) => setState(() => _isPm = i == 1),
                  ),
                ],
              ),
              const SizedBox(height: AppSpace.xl),

              // SAVE BUTTON
              Container(
                width: double.infinity,
                constraints: const BoxConstraints(minHeight: 46),
                child: ElevatedButton(
                  style: ElevatedButton.styleFrom(
                    backgroundColor: widget.accentColor,
                  ),
                  onPressed: _isSaving ? null : _save,
                  child: _isSaving
                      ? SizedBox(
                          width: 20,
                          height: 20,
                          child: CircularProgressIndicator(
                            strokeWidth: 2,
                            color: AppColors.of(
                              context,
                            ).foregroundOn(widget.accentColor),
                          ),
                        )
                      : Text(
                          'Save ${widget.title}',
                          style: AppType.style(
                            color: AppColors.of(
                              context,
                            ).foregroundOn(widget.accentColor),
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _inputField({
    required TextEditingController ctrl,
    required String label,
    required String suffix,
    required Color color,
    String? Function(String?)? validator,
  }) {
    return TextFormField(
      controller: ctrl,
      keyboardType: TextInputType.number,
      style: AppType.style(color: AppColors.of(context).text),
      decoration: _inputDeco(label, color, suffix: suffix),
      validator: validator,
    );
  }

  InputDecoration _inputDeco(String label, Color color, {String? suffix}) {
    return InputDecoration(
      labelText: label,
      labelStyle: AppType.style(color: AppColors.of(context).textSecondary),
      suffixText: suffix,
      suffixStyle: AppType.style(color: color),
      enabledBorder: OutlineInputBorder(
        borderSide: BorderSide(color: AppColors.of(context).outline),
      ),
      focusedBorder: OutlineInputBorder(borderSide: BorderSide(color: color)),
    );
  }

  Widget _timeDropdown(
    int val,
    int count,
    int offset,
    ValueChanged<int?> onChanged,
  ) {
    return DropdownButton<int>(
      value: val,
      dropdownColor: AppColors.of(context).surface,
      style: AppType.style(
        color: AppColors.of(context).text,
        fontSize: AppType.label,
        fontWeight: FontWeight.bold,
      ),
      items: List.generate(count, (i) => i + offset)
          .map(
            (n) => DropdownMenuItem(
              value: n,
              child: Text(n.toString().padLeft(2, '0')),
            ),
          )
          .toList(),
      onChanged: onChanged,
    );
  }
}
