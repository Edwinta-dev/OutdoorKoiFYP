// lib/widgets/modals/quick_log_modals.dart

import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import '../../data/pond_data_source.dart';
import '../../utils/digital_twin_api.dart';
import '../../utils/event_id.dart';

/// The live write behind PondDataSource.insertIntervention. It lives next
/// to the payload built in _InterventionLogSheetState._save so the schema
/// check can match the payload keys to pondInterventions columns.
Future<void> insertPondIntervention(Map<String, dynamic> payload) async {
  await Supabase.instance.client.from('pondInterventions').insert(payload);
}

/// Main Entry Point: Opens the Quick Action Option Selector
void showQuickActionSelector(BuildContext context) {
  showModalBottomSheet(
    context: context,
    backgroundColor: const Color(0xFF131B2A),
    shape: const RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
    ),
    builder: (ctx) => Padding(
      padding: const EdgeInsets.all(20),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            'Log Pond Intervention',
            style: TextStyle(
              color: Colors.white,
              fontSize: 16,
              fontWeight: FontWeight.bold,
            ),
          ),
          const SizedBox(height: 12),
          _actionTile(
            ctx,
            Icons.grain,
            Colors.purpleAccent,
            'Salt Addition',
            'Logs added salt in grams',
            'SALT',
          ),
          _actionTile(
            ctx,
            Icons.filter_alt_outlined,
            Colors.amberAccent,
            'Filter Cleaning',
            'Marks pH and TDS after maintenance for 24 hours',
            'FILTER_CLEAN',
          ),
          _actionTile(
            ctx,
            Icons.water_drop,
            Colors.lightBlueAccent,
            'Water Change',
            'Resets pH & TDS mineral accumulation',
            'WATER_CHANGE',
          ),
          _actionTile(
            ctx,
            Icons.opacity,
            Colors.cyanAccent,
            'Water Top-Up',
            'Restores volume lost to evaporation',
            'WATER_TOPUP',
          ),
          _actionTile(
            ctx,
            Icons.cleaning_services_outlined,
            Colors.tealAccent,
            'Algae Scrub',
            'Resets visual greenery & photo solar buildup',
            'ALGAE_SCRUB',
          ),
          _actionTile(
            ctx,
            Icons.set_meal_outlined,
            Colors.orangeAccent,
            'Feeding Session',
            'Logs food grams & protein content',
            'FEEDING',
          ),
        ],
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
      style: const TextStyle(
        color: Colors.white,
        fontWeight: FontWeight.w600,
        fontSize: 13,
      ),
    ),
    subtitle: Text(
      sub,
      style: const TextStyle(color: Colors.white54, fontSize: 11),
    ),
    onTap: () {
      Navigator.pop(ctx);
      showModalBottomSheet(
        context: ctx,
        isScrollControlled: true,
        backgroundColor: const Color(0xFF131B2A),
        shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
        ),
        builder: (_) => InterventionLogSheet(
          eventType: type,
          title: title,
          accentColor: color,
        ),
      );
    },
  );
}

// ============================================================================
// UNIFIED, DRY INTERVENTION LOG SHEET (~100 LINES FOR ALL 4 EVENT TYPES)
// ============================================================================
class InterventionLogSheet extends StatefulWidget {
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
  State<InterventionLogSheet> createState() => _InterventionLogSheetState();
}

class _InterventionLogSheetState extends State<InterventionLogSheet> {
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

    final source = PondDataScope.of(context);
    try {
      final prefs = await SharedPreferences.getInstance();
      final userId = int.tryParse(prefs.getString('userID') ?? '0') ?? 0;

      // Fish stock was captured at onboarding into SharedPreferences (not
      // synced to Supabase's UserData table), so it rides along on each
      // event push for the DigitalTwin engine to use as PondConfig context.
      final ownedSpecies = prefs.getStringList('ownedFishSpecies') ?? [];
      final fishType = ownedSpecies.isEmpty ? null : ownedSpecies.join(', ');
      final fishCount = int.tryParse(prefs.getString('fishCount') ?? '');

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
      final Map<String, dynamic> payload = {
        'userID': userId,
        'event_id': eventId,
        'event_type': widget.eventType,
        'event_timestamp': timestamp.toIso8601String(),
      };

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
        payload['volume_percentage'] = volumePercent;
        payload['volume_litres'] = volumeLitres;
      } else if (widget.eventType == 'FEEDING') {
        foodGrams = double.parse(_val1Ctrl.text.trim());
        proteinPercent = double.tryParse(_val2Ctrl.text.trim()) ?? 40.0;
        payload['food_grams'] = foodGrams;
        payload['protein_percentage'] = proteinPercent;
      } else if (widget.eventType == 'SALT') {
        saltGrams = double.parse(_val1Ctrl.text.trim());
        payload['salt_grams'] = saltGrams;
      } else if (widget.eventType == 'FILTER_CLEAN') {
        notes = _val2Ctrl.text.trim().isEmpty ? null : _val2Ctrl.text.trim();
        payload['notes'] = notes;
      } else if (widget.eventType == 'ALGAE_SCRUB') {
        payload['algae_method'] = _selectedOption;
      }

      // Historical intervention record - feeds the timeline graph markers.
      await source.insertIntervention(payload);

      // Push the same event into the DigitalTwin chemistry engine so its
      // TAN/NO2/NO3 pools and the water buffer status card reflect it.
      // Best-effort: the Supabase insert above already succeeded, so a
      // slow/unreachable Flask host must not fail this save.
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
            backgroundColor: Colors.redAccent,
          ),
        );
      }
    }
  }

  Future<WaterChemistryAssessment?> _pushToDigitalTwin(
    PondDataSource source, {
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
          left: 20,
          right: 20,
          top: 20,
          bottom: bottomPad + 20,
        ),
        child: Form(
          key: _formKey,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Icon(
                    Icons.cleaning_services,
                    color: widget.accentColor,
                    size: 20,
                  ),
                  const SizedBox(width: 8),
                  Text(
                    'Log ${widget.title}',
                    style: const TextStyle(
                      color: Colors.white,
                      fontSize: 16,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 16),

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
                const SizedBox(height: 12),
              ],

              // DYNAMIC FIELD 2 (Litres or Protein %)
              if (isFilter)
                TextFormField(
                  controller: _val2Ctrl,
                  maxLength: 1000,
                  style: const TextStyle(color: Colors.white),
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
                  dropdownColor: const Color(0xFF131B2A),
                  style: const TextStyle(color: Colors.white, fontSize: 12),
                  decoration: _inputDeco('Scrub Method', widget.accentColor),
                  items: ['Manual Scrub', 'UV Clarifier', 'Chemical Treatment']
                      .map((m) => DropdownMenuItem(value: m, child: Text(m)))
                      .toList(),
                  onChanged: (v) => setState(() => _selectedOption = v!),
                ),

              const SizedBox(height: 16),
              const Text(
                'Event Timestamp',
                style: TextStyle(color: Colors.white54, fontSize: 11),
              ),
              const SizedBox(height: 6),

              // COMPACT TIME/DATE SELECTOR
              Row(
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
                        horizontal: 10,
                        vertical: 8,
                      ),
                      decoration: BoxDecoration(
                        color: Colors.white.withValues(alpha: 0.05),
                        borderRadius: BorderRadius.circular(8),
                        border: Border.all(color: Colors.white24),
                      ),
                      child: Text(
                        '${_date.day}/${_date.month}/${_date.year}',
                        style: const TextStyle(
                          color: Colors.white,
                          fontSize: 12,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  _timeDropdown(
                    _hour,
                    12,
                    1,
                    (v) => setState(() => _hour = v!),
                  ),
                  const Text(
                    ' : ',
                    style: TextStyle(
                      color: Colors.white70,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                  _timeDropdown(_min, 60, 0, (v) => setState(() => _min = v!)),
                  const SizedBox(width: 8),
                  ToggleButtons(
                    isSelected: [!_isPm, _isPm],
                    constraints: const BoxConstraints(
                      minWidth: 32,
                      minHeight: 32,
                    ),
                    borderRadius: BorderRadius.circular(6),
                    selectedColor: Colors.black,
                    fillColor: widget.accentColor,
                    children: const [
                      Text(
                        'AM',
                        style: TextStyle(
                          fontSize: 10,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                      Text(
                        'PM',
                        style: TextStyle(
                          fontSize: 10,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                    ],
                    onPressed: (i) => setState(() => _isPm = i == 1),
                  ),
                ],
              ),
              const SizedBox(height: 20),

              // SAVE BUTTON
              SizedBox(
                width: double.infinity,
                height: 46,
                child: ElevatedButton(
                  style: ElevatedButton.styleFrom(
                    backgroundColor: widget.accentColor,
                  ),
                  onPressed: _isSaving ? null : _save,
                  child: _isSaving
                      ? const SizedBox(
                          width: 20,
                          height: 20,
                          child: CircularProgressIndicator(
                            strokeWidth: 2,
                            color: Colors.black,
                          ),
                        )
                      : Text(
                          'Save ${widget.title}',
                          style: const TextStyle(
                            color: Colors.black,
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
      style: const TextStyle(color: Colors.white),
      decoration: _inputDeco(label, color, suffix: suffix),
      validator: validator,
    );
  }

  InputDecoration _inputDeco(String label, Color color, {String? suffix}) {
    return InputDecoration(
      labelText: label,
      labelStyle: const TextStyle(color: Colors.white60),
      suffixText: suffix,
      suffixStyle: TextStyle(color: color),
      enabledBorder: const OutlineInputBorder(
        borderSide: BorderSide(color: Colors.white24),
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
      dropdownColor: const Color(0xFF131B2A),
      style: const TextStyle(
        color: Colors.white,
        fontSize: 12,
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
