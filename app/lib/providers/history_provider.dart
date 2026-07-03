import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/models/history_point.dart';
import 'package:greenhouse_app/services/history_service.dart';
import 'package:greenhouse_app/services/pairing_service.dart';

final historyServiceProvider = Provider((_) => HistoryService());

class HistoryQuery {
  final String zone;
  final String metric;
  const HistoryQuery({required this.zone, required this.metric});

  @override
  bool operator ==(Object other) =>
      other is HistoryQuery && other.zone == zone && other.metric == metric;
  @override
  int get hashCode => Object.hash(zone, metric);
}

final historyPointsProvider =
    FutureProvider.family<List<HistoryPoint>, HistoryQuery>((ref, query) async {
  final config = await ref.read(pairingServiceProvider).loadConfig();
  if (config == null) return [];
  final service = ref.read(historyServiceProvider);
  return service.fetchPoints(
    lanHost: config.lanHost,
    zone: query.zone,
    metric: query.metric,
    hours: 24,
  );
});
