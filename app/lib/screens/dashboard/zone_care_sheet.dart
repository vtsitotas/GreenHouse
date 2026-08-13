import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/models/plant_profile.dart';
import 'package:greenhouse_app/models/weather_rule.dart';
import 'package:greenhouse_app/providers/actuators_provider.dart';
import 'package:greenhouse_app/providers/connection_provider.dart';
import 'package:greenhouse_app/providers/history_provider.dart';
import 'package:greenhouse_app/providers/readings_provider.dart';
import 'package:greenhouse_app/screens/weather/rule_form_dialog.dart';
import 'package:greenhouse_app/services/zone_plant_store.dart';
import 'package:greenhouse_app/theme/app_colors.dart';
import 'package:greenhouse_app/utils/display_name.dart';
import 'package:greenhouse_app/utils/plant_status.dart';

/// Opens the plant-care sheet for one zone. A modal bottom sheet rather than
/// a route: the zone is already known from the card that was tapped, so
/// there is no separate "which zone" step and no new go_router route to add.
Future<void> showZoneCareSheet(BuildContext context, {required String zone}) {
  return showModalBottomSheet(
    context: context,
    isScrollControlled: true,
    useSafeArea: true,
    builder: (_) => ZoneCareSheet(zone: zone),
  );
}

/// The metrics a plant profile can have ranges for, in the fixed display
/// order used throughout this sheet.
const List<String> kProfileMetrics = ['soil_moisture', 'air_temperature', 'air_humidity'];

class ZoneCareSheet extends ConsumerStatefulWidget {
  final String zone;
  const ZoneCareSheet({required this.zone, super.key});

  @override
  ConsumerState<ZoneCareSheet> createState() => _ZoneCareSheetState();
}

class _ZoneCareSheetState extends ConsumerState<ZoneCareSheet> {
  String? _profileId;
  double _historyHours = 24 * 7;
  final Map<String, TextEditingController> _minCtrls = {};
  final Map<String, TextEditingController> _maxCtrls = {};
  bool _initializedFromStore = false;

  @override
  void initState() {
    super.initState();
    for (final metric in kProfileMetrics) {
      _minCtrls[metric] = TextEditingController();
      _maxCtrls[metric] = TextEditingController();
    }
  }

  @override
  void dispose() {
    for (final c in _minCtrls.values) {
      c.dispose();
    }
    for (final c in _maxCtrls.values) {
      c.dispose();
    }
    super.dispose();
  }

  /// Pre-fills state from the stored assignment exactly once -- after that,
  /// the dropdown/text fields are the source of truth until Save, so a
  /// provider rebuild (e.g. a live reading arriving) doesn't stomp on
  /// whatever the user is mid-way through typing.
  void _initFromAssignment(ZonePlantAssignment? assignment) {
    if (_initializedFromStore) return;
    _initializedFromStore = true;
    if (assignment == null) return;
    _profileId = assignment.profileId;
    if (assignment.isCustom) {
      for (final metric in kProfileMetrics) {
        final range = assignment.customRanges?[metric];
        _minCtrls[metric]!.text = range?.min?.toString() ?? '';
        _maxCtrls[metric]!.text = range?.max?.toString() ?? '';
      }
    }
  }

  Map<String, ClimateRange> get _customRangesFromFields {
    final result = <String, ClimateRange>{};
    for (final metric in kProfileMetrics) {
      final min = double.tryParse(_minCtrls[metric]!.text.trim());
      final max = double.tryParse(_maxCtrls[metric]!.text.trim());
      if (min != null || max != null) result[metric] = ClimateRange(min: min, max: max);
    }
    return result;
  }

  Future<void> _save() async {
    final profileId = _profileId;
    if (profileId == null) {
      await ref.read(zonePlantProvider.notifier).clear(widget.zone);
    } else {
      final assignment = ZonePlantAssignment(
        profileId: profileId,
        customRanges: profileId == kCustomPlantProfileId ? _customRangesFromFields : null,
      );
      await ref.read(zonePlantProvider.notifier).setAssignment(widget.zone, assignment);
    }
    if (mounted) Navigator.of(context).pop();
  }

  Future<void> _addSuggestedRule(String metric, ClimateRange range, PlantConditionStatus status) async {
    final isLow = status == PlantConditionStatus.low;
    final threshold = isLow ? range.min : range.max;
    if (threshold == null) return;
    final suggested = WeatherRule(
      id: '${widget.zone}-$metric-${DateTime.now().millisecondsSinceEpoch}',
      name: '${metricDisplayName(metric)} ${isLow ? 'too low' : 'too high'} (${zoneLabel(widget.zone)})',
      enabled: true,
      notify: true,
      zone: widget.zone,
      metric: metric,
      op: isLow ? '<' : '>',
      value: threshold,
      durationMinutes: 60,
      actuatorId: null,
      command: null,
    );
    final actuatorIds = ref.read(actuatorsProvider).valueOrNull?.keys.toList() ?? const <String>[];
    final result = await showRuleFormDialog(
      context,
      existing: suggested,
      zones: [widget.zone],
      actuatorIds: actuatorIds,
    );
    if (result == null) return;
    final currentRules = ref.read(rulesProvider).valueOrNull ?? const <WeatherRule>[];
    await ref.read(repositoryProvider).publishRules([...currentRules, result]);
    if (mounted) {
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('Rule added — see it under Weather → Rules')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final assignments = ref.watch(zonePlantProvider).valueOrNull ?? const {};
    final storedAssignment = assignments[widget.zone];
    _initFromAssignment(storedAssignment);

    final builtIn = _profileId == null ? null : builtInPlantProfileById(_profileId!);
    final isCustom = _profileId == kCustomPlantProfileId;
    final Map<String, ClimateRange> previewRanges = isCustom
        ? _customRangesFromFields
        : (builtIn?.ranges ?? const {});

    final readings = ref.watch(readingsProvider).valueOrNull?[widget.zone] ?? const <String, double>{};

    return DraggableScrollableSheet(
      initialChildSize: 0.8,
      minChildSize: 0.4,
      maxChildSize: 0.95,
      expand: false,
      builder: (context, scrollController) => SingleChildScrollView(
        controller: scrollController,
        padding: const EdgeInsets.fromLTRB(20, 20, 20, 32),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('${zoneLabel(widget.zone)} care',
                style: Theme.of(context).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.bold)),
            const SizedBox(height: 4),
            Text(
              'Pick what is growing here to get plant-specific status and advice.',
              style: Theme.of(context).textTheme.bodySmall,
            ),
            const SizedBox(height: 16),
            DropdownButtonFormField<String?>(
              key: const Key('zone-care-plant-dropdown'),
              initialValue: _profileId,
              decoration: const InputDecoration(labelText: 'Plant', border: OutlineInputBorder()),
              items: [
                const DropdownMenuItem<String?>(value: null, child: Text('Not set')),
                for (final p in kBuiltInPlantProfiles)
                  DropdownMenuItem<String?>(value: p.id, child: Text(p.name)),
                const DropdownMenuItem<String?>(value: kCustomPlantProfileId, child: Text('Custom')),
              ],
              onChanged: (v) => setState(() => _profileId = v),
            ),
            if (isCustom) ...[
              const SizedBox(height: 16),
              Text('Custom ranges', style: Theme.of(context).textTheme.titleSmall),
              for (final metric in kProfileMetrics) _CustomRangeRow(
                key: Key('zone-care-custom-$metric'),
                label: metricDisplayName(metric),
                minCtrl: _minCtrls[metric]!,
                maxCtrl: _maxCtrls[metric]!,
                onChanged: () => setState(() {}),
              ),
            ],
            if (_profileId != null && previewRanges.isNotEmpty) ...[
              const SizedBox(height: 20),
              Text('Current status', style: Theme.of(context).textTheme.titleSmall),
              const SizedBox(height: 8),
              for (final metric in kProfileMetrics)
                if (previewRanges[metric] != null)
                  _CurrentStatusRow(
                    metric: metric,
                    range: previewRanges[metric]!,
                    value: readings[kMetricReadingsKey[metric]],
                  ),
              const SizedBox(height: 20),
              Row(children: [
                Text('History', style: Theme.of(context).textTheme.titleSmall),
                const Spacer(),
                _HistoryRangeChips(
                  hours: _historyHours,
                  onChanged: (h) => setState(() => _historyHours = h),
                ),
              ]),
              const SizedBox(height: 8),
              for (final metric in kProfileMetrics)
                if (previewRanges[metric] != null)
                  _HistoryComplianceRow(
                    zone: widget.zone,
                    metric: metric,
                    range: previewRanges[metric]!,
                    hours: _historyHours,
                  ),
              const SizedBox(height: 20),
              Text('Suggested rules', style: Theme.of(context).textTheme.titleSmall),
              const SizedBox(height: 8),
              for (final metric in kProfileMetrics)
                if (previewRanges[metric] != null)
                  _SuggestedRuleRow(
                    metric: metric,
                    range: previewRanges[metric]!,
                    value: readings[kMetricReadingsKey[metric]],
                    onAdd: (status) => _addSuggestedRule(metric, previewRanges[metric]!, status),
                  ),
            ],
            const SizedBox(height: 24),
            Row(children: [
              if (storedAssignment != null)
                TextButton(
                  onPressed: () async {
                    await ref.read(zonePlantProvider.notifier).clear(widget.zone);
                    if (context.mounted) Navigator.of(context).pop();
                  },
                  child: const Text('Remove plant'),
                ),
              const Spacer(),
              FilledButton(
                key: const Key('zone-care-save-button'),
                onPressed: _save,
                child: const Text('Save'),
              ),
            ]),
          ],
        ),
      ),
    );
  }
}

class _CustomRangeRow extends StatelessWidget {
  final String label;
  final TextEditingController minCtrl;
  final TextEditingController maxCtrl;
  final VoidCallback onChanged;
  const _CustomRangeRow({
    required this.label,
    required this.minCtrl,
    required this.maxCtrl,
    required this.onChanged,
    super.key,
  });

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(top: 8),
        child: Row(children: [
          Expanded(flex: 2, child: Text(label)),
          Expanded(
            child: TextField(
              controller: minCtrl,
              keyboardType: const TextInputType.numberWithOptions(decimal: true, signed: true),
              decoration: const InputDecoration(labelText: 'Min', isDense: true),
              onChanged: (_) => onChanged(),
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: TextField(
              controller: maxCtrl,
              keyboardType: const TextInputType.numberWithOptions(decimal: true, signed: true),
              decoration: const InputDecoration(labelText: 'Max', isDense: true),
              onChanged: (_) => onChanged(),
            ),
          ),
        ]),
      );
}

class _CurrentStatusRow extends StatelessWidget {
  final String metric;
  final ClimateRange range;
  final double? value;
  const _CurrentStatusRow({required this.metric, required this.range, required this.value});

  @override
  Widget build(BuildContext context) {
    final status = metricStatus(value, range);
    final (color, icon) = zoneStatusVisual(status);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(children: [
        Icon(icon, color: color, size: 18),
        const SizedBox(width: 8),
        Expanded(
          child: Text(
            '${metricDisplayName(metric)}: ${metricAdviceText(metric, status)}',
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ),
      ]),
    );
  }
}

class _HistoryRangeChips extends StatelessWidget {
  final double hours;
  final ValueChanged<double> onChanged;
  const _HistoryRangeChips({required this.hours, required this.onChanged});

  static const _options = {'7d': 24.0 * 7, '30d': 24.0 * 30};

  @override
  Widget build(BuildContext context) => Wrap(
        spacing: 6,
        children: [
          for (final entry in _options.entries)
            ChoiceChip(
              label: Text(entry.key),
              selected: hours == entry.value,
              onSelected: (_) => onChanged(entry.value),
            ),
        ],
      );
}

class _HistoryComplianceRow extends ConsumerWidget {
  final String zone;
  final String metric;
  final ClimateRange range;
  final double hours;
  const _HistoryComplianceRow({
    required this.zone,
    required this.metric,
    required this.range,
    required this.hours,
  });

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final query = HistoryQuery(zone: zone, metric: metric, hours: hours);
    final pointsAsync = ref.watch(historyPointsProvider(query));
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: pointsAsync.when(
        loading: () => Row(children: [
          const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2)),
          const SizedBox(width: 8),
          Text('${metricDisplayName(metric)}…', style: Theme.of(context).textTheme.bodySmall),
        ]),
        error: (_, __) => Text('${metricDisplayName(metric)}: history unavailable',
            style: Theme.of(context).textTheme.bodySmall?.copyWith(color: AppColors.pending)),
        data: (points) {
          if (points.isEmpty) {
            return Text('${metricDisplayName(metric)}: no history yet',
                style: Theme.of(context).textTheme.bodySmall);
          }
          final result = historicalCompliance(points.map((p) => p.avg).toList(), range);
          final days = (hours / 24).round();
          return Text(
            '${metricDisplayName(metric)}: averaged ${result.average.toStringAsFixed(1)} over '
            'the last ${days}d — within range ${result.compliancePct.toStringAsFixed(0)}% of the time',
            style: Theme.of(context).textTheme.bodySmall,
          );
        },
      ),
    );
  }
}

class _SuggestedRuleRow extends StatelessWidget {
  final String metric;
  final ClimateRange range;
  final double? value;
  final void Function(PlantConditionStatus status) onAdd;
  const _SuggestedRuleRow({
    required this.metric,
    required this.range,
    required this.value,
    required this.onAdd,
  });

  @override
  Widget build(BuildContext context) {
    final status = metricStatus(value, range);
    if (status != PlantConditionStatus.low && status != PlantConditionStatus.high) {
      return const SizedBox.shrink();
    }
    final threshold = status == PlantConditionStatus.low ? range.min : range.max;
    final op = status == PlantConditionStatus.low ? '<' : '>';
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(children: [
        Expanded(
          child: Text(
            'Alert when ${metricDisplayName(metric)} $op $threshold',
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ),
        TextButton(
          key: Key('zone-care-suggest-$metric'),
          onPressed: () => onAdd(status),
          child: const Text('Add'),
        ),
      ]),
    );
  }
}
