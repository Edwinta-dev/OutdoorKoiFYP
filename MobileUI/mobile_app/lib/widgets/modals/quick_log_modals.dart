// lib/widgets/modals/quick_log_modals.dart

import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

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
      backgroundColor: color.withOpacity(0.15),
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

  const InterventionLogSheet({
    super.key,
    required this.eventType,
    required this.title,
    required this.accentColor,
  });

  @override
  State<InterventionLogSheet> createState() => _InterventionLogSheetState();
}

class _InterventionLogSheetState extends State<InterventionLogSheet> {
  final _formKey = GlobalKey<FormState>();
  final _val1Ctrl = TextEditingController(text: '25'); // Pct or Grams
  final _val2Ctrl = TextEditingController(); // Volume or Protein %

  String _selectedOption = 'Manual Scrub'; // For Algae or Food presets
  DateTime _date = DateTime.now();
  int _hour = 12, _min = 0;
  bool _isPm = true, _isSaving = false;

  @override
  void initState() {
    super.initState();
    final now = DateTime.now();
    _isPm = now.hour >= 12;
    _hour = now.hour % 12 == 0 ? 12 : now.hour % 12;
    _min = now.minute;

    if (widget.eventType == 'FEEDING') _val1Ctrl.text = '10';
  }

  Future<void> _save() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() => _isSaving = true);

    try {
      final prefs = await SharedPreferences.getInstance();
      final userId = int.tryParse(prefs.getString('userID') ?? '0') ?? 0;
      final hour24 = (_hour % 12) + (_isPm ? 12 : 0);
      final timestamp = DateTime(
        _date.year,
        _date.month,
        _date.day,
        hour24,
        _min,
      );

      final Map<String, dynamic> payload = {
        'user_id': userId,
        'event_type': widget.eventType,
        'event_timestamp': timestamp.toIso8601String(),
      };

      // Map dynamic fields based on event type
      if (widget.eventType == 'WATER_CHANGE' ||
          widget.eventType == 'WATER_TOPUP') {
        payload['volume_percentage'] = double.parse(_val1Ctrl.text.trim());
        payload['volume_litres'] = double.tryParse(_val2Ctrl.text.trim());
      } else if (widget.eventType == 'FEEDING') {
        payload['food_grams'] = double.parse(_val1Ctrl.text.trim());
        payload['protein_percentage'] =
            double.tryParse(_val2Ctrl.text.trim()) ?? 40.0;
      } else if (widget.eventType == 'ALGAE_SCRUB') {
        payload['algae_method'] = _selectedOption;
      }

      await Supabase.instance.client.from('pondInterventions').insert(payload);

      if (mounted) {
        Navigator.pop(context);
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('${widget.title} logged successfully!'),
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

  @override
  Widget build(BuildContext context) {
    final bottomPad = MediaQuery.of(context).viewInsets.bottom;
    final isWater =
        widget.eventType == 'WATER_CHANGE' || widget.eventType == 'WATER_TOPUP';
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
              if (isWater || isFeed) ...[
                _inputField(
                  ctrl: _val1Ctrl,
                  label: isWater
                      ? 'Volume Percentage (%)'
                      : 'Food per session (g)',
                  suffix: isWater ? '%' : 'g',
                  color: widget.accentColor,
                  validator: (v) => (v == null || double.tryParse(v) == null)
                      ? 'Enter valid value'
                      : null,
                ),
                const SizedBox(height: 12),
              ],

              // DYNAMIC FIELD 2 (Litres or Protein %)
              if (isWater)
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
                  value: _selectedOption,
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
                        color: Colors.white.withOpacity(0.05),
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
