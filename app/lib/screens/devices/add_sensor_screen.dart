// app/lib/screens/devices/add_sensor_screen.dart
import 'package:flutter/material.dart';

import '../../models/sensor_enrolment.dart';

/// Step two of adding a sensor: the QR is already scanned, this collects where
/// the sensor lives and how it is powered, then hands off to the Pi.
class AddSensorScreen extends StatefulWidget {
  const AddSensorScreen({
    super.key,
    required this.scanned,
    required this.onSubmit,
  });

  final SensorEnrolment scanned;

  /// (zone, name, sleepy) — sleepy means battery powered.
  final Future<void> Function(String zone, String name, bool sleepy) onSubmit;

  @override
  State<AddSensorScreen> createState() => _AddSensorScreenState();
}

class _AddSensorScreenState extends State<AddSensorScreen> {
  final _zone = TextEditingController();
  bool _battery = true;
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _zone.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final zone = _zone.text.trim();
    if (zone.isEmpty) {
      setState(() => _error = 'Give this sensor a place, like "Tomato bed".');
      return;
    }
    setState(() { _busy = true; _error = null; });
    try {
      await widget.onSubmit(zone, zone, _battery);
      if (mounted) Navigator.of(context).pop(true);
    } catch (_) {
      if (mounted) {
        setState(() => _error =
            "We couldn't add this sensor. Keep it near the hub and try again.");
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Add sensor')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          const Card(
            child: Padding(
              padding: EdgeInsets.all(12),
              child: Text(
                'Keep the sensor switched on and near the hub while you add it. '
                'Once it has joined you can put it wherever you like.',
              ),
            ),
          ),
          const SizedBox(height: 16),
          TextField(
            key: const Key('zoneField'),
            controller: _zone,
            decoration: const InputDecoration(
              labelText: 'Where is this sensor?',
              hintText: 'Tomato bed',
            ),
          ),
          SwitchListTile(
            key: const Key('batteryToggle'),
            value: _battery,
            onChanged: (v) => setState(() => _battery = v),
            title: const Text('Runs on batteries'),
            subtitle: const Text(
                'Battery sensors sleep between readings to last longer. '
                'Turn this off if it is plugged in.'),
          ),
          if (_error != null) ...[
            const SizedBox(height: 8),
            Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
          ],
          const SizedBox(height: 16),
          FilledButton(
            onPressed: _busy ? null : _submit,
            child: _busy
                ? const SizedBox(
                    height: 16, width: 16, child: CircularProgressIndicator(strokeWidth: 2))
                : const Text('Add sensor'),
          ),
        ],
      ),
    );
  }
}
