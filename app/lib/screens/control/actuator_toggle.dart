import 'package:flutter/material.dart';
import 'package:greenhouse_app/models/actuator_state.dart';
import 'package:greenhouse_app/theme/app_colors.dart';
import 'package:greenhouse_app/utils/display_name.dart';

class ActuatorToggle extends StatelessWidget {
  final ActuatorState state;

  /// User-chosen names, from `deviceNamesProvider`. Without one, an actuator
  /// shows the id the automation rules use (`pump1`), which means nothing to
  /// whoever is standing in the greenhouse.
  final Map<String, String> names;

  final ValueChanged<bool> onToggle;

  /// Long-press to rename. Null disables the affordance.
  final VoidCallback? onRename;

  const ActuatorToggle({
    required this.state,
    required this.onToggle,
    this.names = const {},
    this.onRename,
    super.key,
  });

  @override
  Widget build(BuildContext context) => ListTile(
        leading: Icon(Icons.power_settings_new,
            color: state.isOn ? AppColors.online : AppColors.pending),
        title: Text(displayNameFor(state.actuatorId, names)),
        subtitle: state.isPending
            // "Pending" reads as a status the device is in; what is actually
            // happening is that we asked and haven't heard back yet.
            ? const Text('Waiting for it to respond…',
                style: TextStyle(color: AppColors.pending))
            : Text(state.isOn ? 'On' : 'Off'),
        onLongPress: onRename,
        trailing: Switch(
          value: state.isOn,
          onChanged: state.isPending ? null : onToggle,
        ),
      );
}
