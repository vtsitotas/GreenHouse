import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/models/actuator_state.dart';
import 'package:greenhouse_app/providers/actuators_provider.dart';
import 'package:greenhouse_app/providers/connection_provider.dart';
import 'package:greenhouse_app/screens/common/friendly_error_view.dart';
import 'package:greenhouse_app/screens/common/rename_device_dialog.dart';
import 'package:greenhouse_app/screens/control/actuator_toggle.dart';
import 'package:greenhouse_app/services/device_names_store.dart';
import 'package:greenhouse_app/utils/display_name.dart';

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
      final names = ref.read(deviceNamesProvider).valueOrNull ?? const {};
      final label = displayNameFor(actuatorId, names);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            '$label did not respond. Check it has power and is switched on.',
          ),
        ),
      );
    });
  }

  @override
  void dispose() {
    for (final t in _timers.values) { t.cancel(); }
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
    final names = ref.watch(deviceNamesProvider).valueOrNull ?? const {};
    return Scaffold(
      appBar: AppBar(title: const Text('Switches')),
      body: actuators.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => FriendlyErrorView(
          error: e,
          onRetry: () => ref.invalidate(actuatorsProvider),
        ),
        data: (map) => map.isEmpty
            ? const Center(
                child: Padding(
                  padding: EdgeInsets.all(32),
                  child: Text(
                    'Nothing to switch yet.\n\nPumps, fans and lights appear '
                    'here once your greenhouse reports them.',
                    textAlign: TextAlign.center,
                  ),
                ),
              )
            : ListView(children: [
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
                  child: Text(
                    'Hold a switch to give it a name.',
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                ),
                ...map.values.map((s) => ActuatorToggle(
                      state: s,
                      names: names,
                      onToggle: (on) => _toggle(s.actuatorId, on),
                      onRename: () => showRenameDeviceDialog(
                        context,
                        ref,
                        deviceId: s.actuatorId,
                        currentAutoLabel:
                            displayNameFor(s.actuatorId, const {}),
                      ),
                    )),
              ]),
      ),
    );
  }
}
