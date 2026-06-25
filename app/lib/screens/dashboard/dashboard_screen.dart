import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/providers/connection_provider.dart';
import 'package:greenhouse_app/providers/readings_provider.dart';
import 'package:greenhouse_app/screens/dashboard/connection_banner.dart';
import 'package:greenhouse_app/screens/dashboard/zone_card.dart';

class DashboardScreen extends ConsumerWidget {
  const DashboardScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final statusAsync  = ref.watch(connectionStatusProvider);
    final readingsAsync = ref.watch(readingsProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('Greenhouse')),
      body: Column(children: [
        statusAsync.when(
          data: (s) => ConnectionBanner(status: s),
          loading: () => const SizedBox.shrink(),
          error: (_, __) => const SizedBox.shrink(),
        ),
        Expanded(child: readingsAsync.when(
          loading: () => ListView.builder(itemCount: 3, itemBuilder: (_, __) => const _Skeleton()),
          error: (e, _) => Center(child: Text('Error: $e')),
          data: (r) => r.isEmpty
              ? const Center(child: Text('Waiting for sensor data…'))
              : ListView(children: r.entries.map((e) => ZoneCard(zone: e.key, readings: e.value)).toList()),
        )),
      ]),
    );
  }
}

class _Skeleton extends StatelessWidget {
  const _Skeleton();
  @override
  Widget build(BuildContext context) => Card(
    child: Padding(padding: const EdgeInsets.all(16), child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Container(width: 80, height: 14, color: Colors.grey[300]),
        const SizedBox(height: 10),
        Container(width: double.infinity, height: 10, color: Colors.grey[200]),
      ],
    )),
  );
}
