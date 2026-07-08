import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/models/history_point.dart';
import 'package:greenhouse_app/services/history_service.dart';
import 'package:greenhouse_app/services/pairing_service.dart';

final historyServiceProvider = Provider((_) => HistoryService());

class HistoryQuery {
  final String? zone;
  final String kind;
  final String metric;
  final double hours;

  const HistoryQuery({
    this.zone,
    String? kind,
    required this.metric,
    this.hours = 24,
  }) : kind = kind ?? (zone != null ? 'zone' : 'weather');

  @override
  bool operator ==(Object other) =>
      other is HistoryQuery &&
      other.zone == zone &&
      other.kind == kind &&
      other.metric == metric &&
      other.hours == hours;

  @override
  int get hashCode => Object.hash(zone, kind, metric, hours);
}

final historyPointsProvider =
    FutureProvider.family<List<HistoryPoint>, HistoryQuery>((ref, query) async {
  final config = await ref.read(pairingServiceProvider).loadConfig();
  if (config == null) return [];
  final service = ref.read(historyServiceProvider);
  return service.fetchPoints(
    lanHost: config.lanHost,
    zone: query.zone,
    kind: query.kind,
    metric: query.metric,
    hours: query.hours,
  );
});
