import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/models/actuator_state.dart';
import 'package:greenhouse_app/providers/actuators_provider.dart';
import 'package:greenhouse_app/providers/connection_provider.dart';
import 'package:greenhouse_app/screens/control/actuator_toggle.dart';

class ControlScreen extends ConsumerStatefulWidget {
  const ControlScreen({super.key});
  @override ConsumerState<ControlScreen> createState() => _ControlScreenState();
}

class _ControlScreenState extends ConsumerState<ControlScreen> {
  final Map<String, Timer> _timers = {};

  Future<void> _toggle(String actuatorId, bool on) async {
    await ref.read(repositoryProvider).sendCommand(actuatorId, on);
    _timers[actuatorId]?.cancel();
    _timers[actuatorId] = Timer(const Duration(seconds: 5), () {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('No response from $actuatorId')),
      );
    });
  }

  @override
  void dispose() {
    for (final t in _timers.values) t.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    ref.listen<AsyncValue<Map<String, ActuatorState>>>(actuatorsProvider, (_, next) {
      next.whenData((map) {
        for (final e in map.entries) {
          if (!e.value.isPending) { _timers[e.key]?.cancel(); _timers.remove(e.key); }
        }
      });
    });

    final actuators = ref.watch(actuatorsProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('Control')),
      body: actuators.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => Center(child: Text('Error: $e')),
        data: (map) => map.isEmpty
            ? const Center(child: Text('No actuators discovered yet'))
            : ListView(children: map.values
                .map((s) => ActuatorToggle(state: s, onToggle: (on) => _toggle(s.actuatorId, on)))
                .toList()),
      ),
    );
  }
}
