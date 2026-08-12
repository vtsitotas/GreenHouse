import 'package:flutter/material.dart';
import 'package:greenhouse_app/utils/friendly_error.dart';

/// The standard way this app shows a failure.
///
/// Plain title, plain explanation, then what to try — with the raw exception
/// kept under a collapsed "Technical details". Nothing is hidden from whoever
/// is debugging; it is just not the first thing a grower has to read past.
class FriendlyErrorView extends StatelessWidget {
  final Object error;

  /// Optional retry. Most callers are Riverpod providers, where retrying is
  /// `ref.invalidate(...)`.
  final VoidCallback? onRetry;

  const FriendlyErrorView({required this.error, this.onRetry, super.key});

  @override
  Widget build(BuildContext context) {
    final friendly = describeError(error);
    final theme = Theme.of(context);

    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Icon(Icons.cloud_off_outlined,
                size: 40, color: theme.colorScheme.outline),
            const SizedBox(height: 12),
            Text(
              friendly.title,
              style: theme.textTheme.titleMedium,
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 8),
            Text(
              friendly.message,
              style: theme.textTheme.bodyMedium,
              textAlign: TextAlign.center,
            ),
            if (friendly.steps.isNotEmpty) ...[
              const SizedBox(height: 16),
              Text('What to try', style: theme.textTheme.titleSmall),
              const SizedBox(height: 6),
              for (final step in friendly.steps)
                Padding(
                  padding: const EdgeInsets.only(bottom: 6),
                  child: Text('•  $step', style: theme.textTheme.bodySmall),
                ),
            ],
            if (onRetry != null) ...[
              const SizedBox(height: 16),
              FilledButton.icon(
                onPressed: onRetry,
                icon: const Icon(Icons.refresh),
                label: const Text('Try again'),
              ),
            ],
            const SizedBox(height: 8),
            ExpansionTile(
              title: Text('Technical details',
                  style: theme.textTheme.bodySmall),
              tilePadding: EdgeInsets.zero,
              childrenPadding: const EdgeInsets.only(bottom: 8),
              expandedCrossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SelectableText(
                  error.toString(),
                  style: theme.textTheme.bodySmall
                      ?.copyWith(fontFamily: 'monospace'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
