import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/providers/connection_provider.dart';

final readingsProvider = StreamProvider<Map<String, Map<String, double>>>((ref) {
  ref.watch(connectOnStartProvider);
  return ref.watch(repositoryProvider).readings;
});
