import 'package:flutter/material.dart';
import 'package:greenhouse_app/models/weather_rule.dart';

const _weatherMetrics = <String, String>{
  'temperature': 'Temperature (°C)',
  'rain_mm_1h': 'Rain next hour (mm)',
  'humidity': 'Humidity (%)',
  'wind_kmh': 'Wind (km/h)',
  'uv_index': 'UV Index',
};

const _zoneMetrics = <String, String>{
  'soil_moisture': 'Soil moisture (%)',
  'air_humidity': 'Air humidity (%)',
  'air_temperature': 'Air temperature (°C)',
};

const _operators = ['>', '<', '>=', '<=', '=='];

/// Shows the add/edit rule form. Returns the resulting WeatherRule, or null
/// if cancelled. Pass `existing` to edit (fields pre-filled, same id kept);
/// omit it to create a new rule (a fresh id is generated).
Future<WeatherRule?> showRuleFormDialog(
  BuildContext context, {
  WeatherRule? existing,
  required List<String> zones,
  required List<String> actuatorIds,
}) {
  return showDialog<WeatherRule>(
    context: context,
    builder: (_) => _RuleFormDialog(existing: existing, zones: zones, actuatorIds: actuatorIds),
  );
}

class _RuleFormDialog extends StatefulWidget {
  final WeatherRule? existing;
  final List<String> zones;
  final List<String> actuatorIds;
  const _RuleFormDialog({required this.existing, required this.zones, required this.actuatorIds});

  @override
  State<_RuleFormDialog> createState() => _RuleFormDialogState();
}

class _RuleFormDialogState extends State<_RuleFormDialog> {
  late final TextEditingController _nameCtrl;
  late final TextEditingController _valueCtrl;
  late final TextEditingController _durationCtrl;
  String? _zone;
  late String _metric;
  late String _op;
  bool _hasAction = false;
  String? _actuatorId;
  String _command = 'ON';
  late bool _notify;

  @override
  void initState() {
    super.initState();
    final e = widget.existing;
    _nameCtrl = TextEditingController(text: e?.name ?? '');
    _valueCtrl = TextEditingController(text: e?.value.toString() ?? '');
    _durationCtrl = TextEditingController(text: e?.durationMinutes?.toString() ?? '');
    _zone = e?.zone;
    _metric = e?.metric ?? _weatherMetrics.keys.first;
    _op = e?.op ?? '>';
    _hasAction = e?.actuatorId != null;
    _actuatorId = e?.actuatorId ?? (widget.actuatorIds.isNotEmpty ? widget.actuatorIds.first : null);
    _command = e?.command ?? 'ON';
    _notify = e?.notify ?? true;
  }

  @override
  void dispose() {
    _nameCtrl.dispose();
    _valueCtrl.dispose();
    _durationCtrl.dispose();
    super.dispose();
  }

  Map<String, String> get _metricOptions => _zone == null ? _weatherMetrics : _zoneMetrics;

  void _onZoneChanged(String? zone) {
    setState(() {
      _zone = zone;
      // Reset metric to a valid option for the new zone/weather set.
      _metric = (zone == null ? _weatherMetrics : _zoneMetrics).keys.first;
    });
  }

  void _save() {
    final value = double.tryParse(_valueCtrl.text);
    final name = _nameCtrl.text.trim();
    if (value == null || name.isEmpty) return;

    int? duration;
    if (_durationCtrl.text.trim().isNotEmpty) {
      duration = int.tryParse(_durationCtrl.text.trim());
    }
    if (_zone != null && duration == null) return; // duration required for zone-specific rules

    final id = widget.existing?.id ??
        '${_zone ?? "weather"}-$_metric-${DateTime.now().millisecondsSinceEpoch}';

    Navigator.pop(
      context,
      WeatherRule(
        id: id,
        name: name,
        enabled: widget.existing?.enabled ?? true,
        notify: _notify,
        zone: _zone,
        metric: _metric,
        op: _op,
        value: value,
        durationMinutes: duration,
        actuatorId: _hasAction ? _actuatorId : null,
        command: _hasAction ? _command : null,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: Text(widget.existing == null ? 'Add rule' : 'Edit rule'),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            TextField(
              controller: _nameCtrl,
              decoration: const InputDecoration(labelText: 'Name', border: OutlineInputBorder()),
            ),
            const SizedBox(height: 12),
            DropdownButtonFormField<String?>(
              key: const Key('rule-form-zone-dropdown'),
              value: _zone,
              decoration: const InputDecoration(labelText: 'Zone', border: OutlineInputBorder()),
              items: [
                const DropdownMenuItem<String?>(value: null, child: Text('Weather')),
                for (final z in widget.zones)
                  DropdownMenuItem<String?>(
                      value: z, child: Text('Zone ${z.replaceFirst('zone', '')}')),
              ],
              onChanged: _onZoneChanged,
            ),
            const SizedBox(height: 12),
            DropdownButtonFormField<String>(
              key: const Key('rule-form-metric-dropdown'),
              value: _metricOptions.containsKey(_metric) ? _metric : _metricOptions.keys.first,
              decoration: const InputDecoration(labelText: 'Metric', border: OutlineInputBorder()),
              items: [
                for (final entry in _metricOptions.entries)
                  DropdownMenuItem(value: entry.key, child: Text(entry.value)),
              ],
              onChanged: (v) => setState(() => _metric = v!),
            ),
            const SizedBox(height: 12),
            Row(children: [
              Expanded(
                child: DropdownButtonFormField<String>(
                  value: _op,
                  decoration: const InputDecoration(labelText: 'Operator', border: OutlineInputBorder()),
                  items: [for (final o in _operators) DropdownMenuItem(value: o, child: Text(o))],
                  onChanged: (v) => setState(() => _op = v!),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: TextField(
                  controller: _valueCtrl,
                  keyboardType: const TextInputType.numberWithOptions(decimal: true, signed: true),
                  decoration:
                      const InputDecoration(labelText: 'Threshold value', border: OutlineInputBorder()),
                ),
              ),
            ]),
            const SizedBox(height: 12),
            TextField(
              controller: _durationCtrl,
              keyboardType: TextInputType.number,
              decoration: InputDecoration(
                labelText: 'Duration (minutes)',
                helperText: _zone != null ? 'Required for zone-specific metrics' : 'Optional',
                border: const OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 12),
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: const Text('Also control a device'),
              value: _hasAction,
              onChanged: (v) => setState(() => _hasAction = v),
            ),
            if (_hasAction) ...[
              DropdownButtonFormField<String>(
                value: _actuatorId,
                decoration: const InputDecoration(labelText: 'Actuator', border: OutlineInputBorder()),
                items: [
                  for (final a in widget.actuatorIds) DropdownMenuItem(value: a, child: Text(a)),
                ],
                onChanged: (v) => setState(() => _actuatorId = v),
              ),
              const SizedBox(height: 12),
              DropdownButtonFormField<String>(
                value: _command,
                decoration: const InputDecoration(labelText: 'Command', border: OutlineInputBorder()),
                items: const [
                  DropdownMenuItem(value: 'ON', child: Text('ON')),
                  DropdownMenuItem(value: 'OFF', child: Text('OFF')),
                ],
                onChanged: (v) => setState(() => _command = v!),
              ),
            ],
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: const Text('Send a notification when this fires'),
              value: _notify,
              onChanged: (v) => setState(() => _notify = v),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
        FilledButton(onPressed: _save, child: const Text('Save')),
      ],
    );
  }
}
