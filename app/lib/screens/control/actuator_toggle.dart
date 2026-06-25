import 'package:flutter/material.dart';
import 'package:greenhouse_app/models/actuator_state.dart';
import 'package:greenhouse_app/theme/app_colors.dart';

class ActuatorToggle extends StatelessWidget {
  final ActuatorState state;
  final ValueChanged<bool> onToggle;
  const ActuatorToggle({required this.state, required this.onToggle, super.key});

  @override
  Widget build(BuildContext context) => ListTile(
        leading: Icon(Icons.power_settings_new,
            color: state.isOn ? AppColors.online : AppColors.pending),
        title: Text(state.actuatorId),
        subtitle: state.isPending
            ? const Text('Pending', style: TextStyle(color: AppColors.pending))
            : Text(state.isOn ? 'ON' : 'OFF'),
        trailing: Switch(
          value: state.isOn,
          onChanged: state.isPending ? null : onToggle,
        ),
      );
}
