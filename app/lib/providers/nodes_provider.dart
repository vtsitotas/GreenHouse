import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/models/node_status.dart';
import 'package:greenhouse_app/providers/connection_provider.dart';

final nodesProvider = StreamProvider<Map<String, NodeStatus>>((ref) {
  ref.watch(connectOnStartProvider);
  return ref.watch(repositoryProvider).nodes;
});
