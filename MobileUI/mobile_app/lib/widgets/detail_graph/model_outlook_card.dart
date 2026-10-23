// lib/widgets/detail_graph/model_outlook_card.dart
//
// Shared collapsible "Digital Twin Model" card used by the three detail
// graph screens (evaporation, water buffer, algae). Status is carried by
// colour only (card border, a thin accent bar beside the header and the
// headline colour); a Semantics label keeps it accessible to screen readers.
// Collapsed: tag + headline + chevron. Expanded: subtitle, optional
// progress bar, one compact label/value table, then short muted notes.

import 'package:flutter/material.dart';
import 'package:mobile_app/theme/app_theme.dart';
import 'package:mobile_app/widgets/shared/info_popup.dart';
import 'package:mobile_app/widgets/shared/pond_widgets.dart';

class ModelOutlookRow {
  const ModelOutlookRow(this.label, this.value, this.color);
  final String label;
  final String value;
  final Color color;
}

class ModelOutlookNote {
  const ModelOutlookNote(this.text, {this.icon, this.color});
  final String text;
  final IconData? icon;

  /// Icon/text colour override (e.g. warning). Null uses the muted style.
  final Color? color;
}

class ModelOutlookProgress {
  const ModelOutlookProgress({
    required this.label,
    required this.valueText,
    required this.value,
    this.footnote,
  });
  final String label;
  final String valueText;
  final double value;
  final String? footnote;
}

class ModelOutlookCard extends StatefulWidget {
  const ModelOutlookCard({
    super.key,
    required this.headline,
    required this.statusColor,
    required this.statusLabel,
    required this.subtitle,
    required this.rows,
    this.progress,
    this.alerts = const [],
    this.infoNotes = const [],
    this.initiallyExpanded = false,
  });

  static const String tag = 'DIGITAL TWIN MODEL';

  final String headline;
  final Color statusColor;

  /// Spoken status ("On track", "Action needed now"...) for screen readers.
  final String statusLabel;
  final String subtitle;
  final ModelOutlookProgress? progress;
  final List<ModelOutlookRow> rows;

  /// Actionable/warning lines kept visible below the table.
  final List<ModelOutlookNote> alerts;

  /// Caveats, assumptions and provenance shown only in the (i) popup.
  final List<String> infoNotes;
  final bool initiallyExpanded;

  @override
  State<ModelOutlookCard> createState() => _ModelOutlookCardState();
}

class _ModelOutlookCardState extends State<ModelOutlookCard> {
  late bool _expanded = widget.initiallyExpanded;

  void _toggle() => setState(() => _expanded = !_expanded);

  @override
  Widget build(BuildContext context) {
    final c = AppColors.of(context);
    return OutcomeCardShell(
      accent: widget.statusColor,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [_header(c), if (_expanded) ..._body(c)],
      ),
    );
  }

  Widget _header(AppColors c) {
    return Semantics(
      button: true,
      expanded: _expanded,
      label:
          '${ModelOutlookCard.tag}. ${widget.headline}. '
          'Status: ${widget.statusLabel}',
      excludeSemantics: true,
      onTap: _toggle,
      child: InkWell(
        borderRadius: BorderRadius.circular(AppRadius.control),
        onTap: _toggle,
        child: IntrinsicHeight(
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Container(
                width: 3,
                decoration: BoxDecoration(
                  color: widget.statusColor,
                  borderRadius: BorderRadius.circular(AppRadius.small),
                ),
              ),
              const SizedBox(width: AppSpace.md),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      ModelOutlookCard.tag,
                      style: AppType.style(
                        color: c.accent,
                        fontSize: AppType.micro,
                        fontWeight: FontWeight.w800,
                        letterSpacing: 1.0,
                      ),
                    ),
                    const SizedBox(height: AppSpace.xxs),
                    Text(
                      widget.headline,
                      style: AppType.style(
                        color: widget.statusColor,
                        fontSize: AppType.body,
                        fontWeight: FontWeight.w800,
                      ),
                    ),
                  ],
                ),
              ),
              AnimatedRotation(
                turns: _expanded ? 0.5 : 0,
                duration: const Duration(milliseconds: 150),
                child: Icon(Icons.expand_more, color: c.textMuted),
              ),
            ],
          ),
        ),
      ),
    );
  }

  List<Widget> _body(AppColors c) {
    final p = widget.progress;
    return [
      const SizedBox(height: AppSpace.sm),
      Text(
        widget.subtitle,
        style: AppType.style(color: c.textMuted, fontSize: AppType.caption),
      ),
      if (p != null) ...[
        const SizedBox(height: AppSpace.md),
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Flexible(
              child: Text(
                p.label,
                style: AppType.style(
                  color: c.textMuted,
                  fontSize: AppType.micro,
                ),
              ),
            ),
            const SizedBox(width: AppSpace.sm),
            Text(
              p.valueText,
              style: AppType.style(
                color: widget.statusColor,
                fontSize: AppType.micro,
                fontWeight: FontWeight.bold,
              ),
            ),
          ],
        ),
        const SizedBox(height: AppSpace.sm),
        ClipRRect(
          borderRadius: BorderRadius.circular(AppRadius.small),
          child: LinearProgressIndicator(
            value: p.value,
            minHeight: 6,
            backgroundColor: c.text.withValues(alpha: 0.06),
            valueColor: AlwaysStoppedAnimation<Color>(widget.statusColor),
          ),
        ),
        if (p.footnote != null) ...[
          const SizedBox(height: AppSpace.xs),
          Text(
            p.footnote!,
            style: AppType.style(color: c.textMuted, fontSize: AppType.micro),
          ),
        ],
      ],
      const SizedBox(height: AppSpace.md),
      for (var i = 0; i < widget.rows.length; i++) ...[
        if (i > 0) Divider(color: c.outline, height: 1, thickness: 1),
        Padding(
          padding: const EdgeInsets.symmetric(vertical: AppSpace.sm),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(
                widget.rows[i].label,
                style: AppType.style(
                  color: c.textSecondary,
                  fontSize: AppType.label,
                ),
              ),
              Text(
                widget.rows[i].value,
                style: AppType.style(
                  color: widget.rows[i].color,
                  fontSize: AppType.label,
                  fontWeight: FontWeight.bold,
                ),
              ),
            ],
          ),
        ),
      ],
      for (final n in widget.alerts) ...[
        const SizedBox(height: AppSpace.sm),
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (n.icon != null) ...[
              Icon(n.icon, size: 13, color: n.color ?? c.textMuted),
              const SizedBox(width: AppSpace.xs),
            ],
            Expanded(
              child: Text(
                n.text,
                style: AppType.style(
                  color: n.color ?? c.textMuted,
                  fontSize: AppType.micro,
                  height: 1.4,
                ),
              ),
            ),
          ],
        ),
      ],
      if (widget.infoNotes.isNotEmpty)
        Align(
          alignment: Alignment.centerRight,
          child: IconButton(
            key: const ValueKey('model-info'),
            tooltip: 'About this estimate',
            icon: Icon(Icons.info_outline, size: 20, color: c.textMuted),
            onPressed: _showInfo,
          ),
        ),
    ];
  }

  void _showInfo() {
    showInfoPopup(
      context,
      key: const ValueKey('model-info-popup'),
      content: Builder(
        builder: (ctx) {
          final c = AppColors.of(ctx);
          final style = AppType.style(
            color: c.text,
            fontSize: AppType.label,
            height: 1.4,
          );
          return Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              for (final t in widget.infoNotes)
                Padding(
                  padding: const EdgeInsets.only(bottom: AppSpace.sm),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('•  ', style: style),
                      Expanded(child: Text(t, style: style)),
                    ],
                  ),
                ),
            ],
          );
        },
      ),
    );
  }
}
