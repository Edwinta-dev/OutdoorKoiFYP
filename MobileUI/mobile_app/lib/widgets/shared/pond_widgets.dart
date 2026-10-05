import 'package:flutter/material.dart';
import '../../theme/app_theme.dart';

enum PondStatus {
  healthy(Icons.check_circle_outline, 'Stable'),
  warning(Icons.warning_amber_rounded, 'Caution'),
  danger(Icons.error_outline, 'Action needed'),
  unknown(Icons.help_outline, 'Unknown');

  const PondStatus(this.icon, this.word);
  final IconData icon;
  final String word;
  Color color(AppColors c) => switch (this) {
    healthy => c.healthy,
    warning => c.warning,
    danger => c.danger,
    unknown => c.textMuted,
  };
  static PondStatus fromWire(String status) => switch (status.toLowerCase()) {
    'green' => healthy,
    'amber' => warning,
    'red' => danger,
    _ => unknown,
  };
  static PondStatus forDays(int? days) => days == null
      ? healthy
      : days <= 0
      ? danger
      : days <= 3
      ? warning
      : healthy;
}

/// Status always has a word and a distinct icon, even in monochrome.
class StatusChip extends StatelessWidget {
  const StatusChip({super.key, required this.status, this.label});
  final PondStatus status;
  final String? label;
  @override
  Widget build(BuildContext context) {
    final color = status.color(AppColors.of(context));
    final text = label ?? status.word;
    return Semantics(
      label: '${status.word}: $text',
      container: true,
      child: ExcludeSemantics(
        child: Container(
          padding: const EdgeInsets.symmetric(
            horizontal: AppSpace.sm,
            vertical: AppSpace.xs,
          ),
          decoration: BoxDecoration(
            color: color.withValues(alpha: 0.12),
            borderRadius: BorderRadius.circular(AppRadius.chip),
            border: Border.all(color: color.withValues(alpha: 0.5)),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(status.icon, size: 16, color: color),
              const SizedBox(width: AppSpace.xs),
              Flexible(
                child: Text(
                  text,
                  style: AppType.style(
                    color: color,
                    fontSize: AppType.label,
                    fontWeight: FontWeight.bold,
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class MetricTile extends StatelessWidget {
  const MetricTile({
    super.key,
    required this.label,
    required this.value,
    this.color,
    this.status,
    this.estimate = false,
  });
  final String label;
  final String value;
  final Color? color;
  final PondStatus? status;
  final bool estimate;
  @override
  Widget build(BuildContext context) {
    final c = AppColors.of(context);
    return Container(
      padding: const EdgeInsets.all(AppSpace.sm),
      decoration: BoxDecoration(
        color: c.surfaceInset,
        borderRadius: BorderRadius.circular(AppRadius.control),
        border: Border.all(color: c.outline),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            label,
            style: AppType.style(color: c.textMuted, fontSize: AppType.caption),
          ),
          const SizedBox(height: AppSpace.xs),
          Text(
            value,
            style: AppType.style(
              color: color ?? c.text,
              fontSize: AppType.label,
              fontWeight: FontWeight.bold,
            ),
          ),
          if (estimate)
            Text(
              'Model estimate',
              style: AppType.style(color: c.textMuted, fontSize: AppType.micro),
            ),
          if (status != null) ...[
            const SizedBox(height: AppSpace.xs),
            StatusChip(status: status!),
          ],
        ],
      ),
    );
  }
}

class SectionHeader extends StatelessWidget {
  const SectionHeader({
    super.key,
    required this.title,
    required this.icon,
    this.color,
    this.onTap,
  });
  final String title;
  final IconData icon;
  final Color? color;
  final VoidCallback? onTap;
  @override
  Widget build(BuildContext context) {
    final foreground = color ?? AppColors.of(context).text;
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(AppRadius.chip),
      child: Padding(
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpace.xs,
          vertical: AppSpace.sm,
        ),
        child: Row(
          children: [
            Icon(icon, color: foreground, size: 18),
            const SizedBox(width: AppSpace.sm),
            Expanded(
              child: Text(
                title,
                style: AppType.style(
                  color: foreground,
                  fontSize: AppType.label,
                  fontWeight: FontWeight.w800,
                  letterSpacing: 0.6,
                ),
              ),
            ),
            if (onTap != null) ...[
              const SizedBox(width: AppSpace.sm),
              Icon(Icons.arrow_forward_ios, color: foreground, size: 12),
            ],
          ],
        ),
      ),
    );
  }
}

class OutcomeCardShell extends StatelessWidget {
  const OutcomeCardShell({
    super.key,
    required this.child,
    this.onTap,
    this.accent,
    this.transparent = false,
    this.padding = const EdgeInsets.all(AppSpace.lg),
  });
  final Widget child;
  final VoidCallback? onTap;
  final Color? accent;
  final bool transparent;
  final EdgeInsetsGeometry padding;
  @override
  Widget build(BuildContext context) {
    final c = AppColors.of(context);
    return Material(
      color: transparent ? AppColors.transparent : c.surface,
      borderRadius: BorderRadius.circular(AppRadius.card),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(AppRadius.card),
        child: Container(
          width: double.infinity,
          padding: padding,
          decoration: transparent
              ? null
              : BoxDecoration(
                  borderRadius: BorderRadius.circular(AppRadius.card),
                  border: Border.all(
                    color: accent?.withValues(alpha: 0.3) ?? c.outline,
                  ),
                ),
          child: child,
        ),
      ),
    );
  }
}

class PondEmptyState extends StatelessWidget {
  const PondEmptyState({
    super.key,
    required this.title,
    required this.message,
    this.icon = Icons.query_stats,
  });
  final String title;
  final String message;
  final IconData icon;
  @override
  Widget build(BuildContext context) => _MessageState(
    title: title,
    message: message,
    icon: icon,
    color: AppColors.of(context).info,
  );
}

class PondErrorState extends StatelessWidget {
  const PondErrorState({
    super.key,
    required this.title,
    required this.message,
    required this.onRetry,
  });
  final String title;
  final String message;
  final VoidCallback onRetry;
  @override
  Widget build(BuildContext context) => _MessageState(
    title: title,
    message: message,
    icon: Icons.error_outline,
    color: AppColors.of(context).danger,
    onRetry: onRetry,
  );
}

class _MessageState extends StatelessWidget {
  const _MessageState({
    required this.title,
    required this.message,
    required this.icon,
    required this.color,
    this.onRetry,
  });
  final String title;
  final String message;
  final IconData icon;
  final Color color;
  final VoidCallback? onRetry;
  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.symmetric(vertical: AppSpace.lg),
    child: Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SectionHeader(title: title, icon: icon, color: color),
        const SizedBox(height: AppSpace.sm),
        Text(
          message,
          style: AppType.style(
            color: AppColors.of(context).textSecondary,
            fontSize: AppType.label,
            height: 1.4,
          ),
        ),
        if (onRetry != null)
          TextButton.icon(
            onPressed: onRetry,
            icon: const Icon(Icons.refresh),
            label: const Text('Retry'),
          ),
      ],
    ),
  );
}

/// Stack instrument panels when enlarged type would leave too little width.
/// Expanded children are unwrapped in a column so content chooses its height.
class AdaptiveRow extends StatelessWidget {
  const AdaptiveRow({
    super.key,
    required this.children,
    this.crossAxisAlignment = CrossAxisAlignment.center,
    this.mainAxisAlignment = MainAxisAlignment.start,
    this.mainAxisSize = MainAxisSize.max,
    this.minWidth = 0,
    this.textBaseline,
  });
  final List<Widget> children;
  final CrossAxisAlignment crossAxisAlignment;
  final MainAxisAlignment mainAxisAlignment;
  final MainAxisSize mainAxisSize;
  final TextBaseline? textBaseline;
  final double minWidth;
  @override
  Widget build(BuildContext context) => LayoutBuilder(
    builder: (context, constraints) {
      if (constraints.maxWidth < minWidth ||
          (MediaQuery.textScalerOf(context).scale(14) > 14 * 1.2 &&
              constraints.maxWidth < 600)) {
        return Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            for (final child in children)
              if (child is! Spacer)
                Padding(
                  padding: const EdgeInsets.only(bottom: AppSpace.sm),
                  child: child is Flexible ? child.child : child,
                ),
          ],
        );
      }
      return Row(
        mainAxisAlignment: mainAxisAlignment,
        mainAxisSize: mainAxisSize,
        crossAxisAlignment: crossAxisAlignment,
        textBaseline: textBaseline,
        children: children,
      );
    },
  );
}
