import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/services/device_names_store.dart';

/// Prompts for a device's name and saves it to [deviceNamesProvider].
///
/// Shared by the device list, the mesh map and the control screen so a rename
/// means the same thing everywhere — including clearing: submitting an empty
/// field removes the custom name and restores the automatic label, which is
/// the only way back once a user has renamed something.
///
/// [currentAutoLabel] is what the device would be called with no custom name;
/// showing it as the hint tells the user what "leave blank" gets them.
Future<void> showRenameDeviceDialog(
  BuildContext context,
  WidgetRef ref, {
  required String deviceId,
  required String currentAutoLabel,
}) async {
  final names = ref.read(deviceNamesProvider).valueOrNull ?? const {};
  final controller = TextEditingController(text: names[deviceId] ?? '');

  final name = await showDialog<String>(
    context: context,
    builder: (dialogContext) => AlertDialog(
      title: const Text('Name this device'),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Give it a name you will recognise, like where it sits in the '
            'greenhouse.',
            style: Theme.of(dialogContext).textTheme.bodySmall,
          ),
          const SizedBox(height: 12),
          TextField(
            controller: controller,
            autofocus: true,
            textCapitalization: TextCapitalization.sentences,
            decoration: InputDecoration(
              labelText: 'Name',
              hintText: currentAutoLabel,
              helperText: 'Leave empty to use "$currentAutoLabel"',
            ),
            onSubmitted: (value) => Navigator.pop(dialogContext, value),
          ),
        ],
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(dialogContext),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: () => Navigator.pop(dialogContext, controller.text),
          child: const Text('Save'),
        ),
      ],
    ),
  );

  controller.dispose();
  // null means cancelled — distinct from '' which means "clear the name".
  if (name == null) return;
  await ref.read(deviceNamesProvider.notifier).setName(deviceId, name);
}
