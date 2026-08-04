import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_mjpeg/flutter_mjpeg.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/models/cam_status.dart';
import 'package:greenhouse_app/models/connection_status.dart';
import 'package:greenhouse_app/providers/camera_provider.dart';
import 'package:greenhouse_app/providers/connection_provider.dart';
import 'package:greenhouse_app/theme/app_colors.dart';

class CameraScreen extends ConsumerStatefulWidget {
  const CameraScreen({super.key});
  @override
  ConsumerState<CameraScreen> createState() => _CameraScreenState();
}

class _CameraScreenState extends ConsumerState<CameraScreen> {
  @override
  Widget build(BuildContext context) {
    final camStatus = ref.watch(camStatusProvider).valueOrNull;
    final connectionStatus = ref.watch(connectionStatusProvider).valueOrNull;

    return Scaffold(
      appBar: AppBar(title: const Text('Camera')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          _StatusCard(status: camStatus),
          const SizedBox(height: 12),
          _LiveViewCard(
            connectionStatus: connectionStatus,
            cameraIp: camStatus?.ip,
          ),
        ],
      ),
    );
  }
}

class _StatusCard extends StatelessWidget {
  final CamStatus? status;
  const _StatusCard({required this.status});

  @override
  Widget build(BuildContext context) {
    final online = status?.online ?? false;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Icon(online ? Icons.videocam : Icons.videocam_off,
                color: online ? AppColors.brand : Colors.grey),
            const SizedBox(width: 8),
            Text(online ? 'Camera: Online' : 'Camera: Offline',
                style: const TextStyle(fontWeight: FontWeight.bold)),
          ]),
          if (status?.lastEvent != null) ...[
            const SizedBox(height: 8),
            Text('Last motion: ${status!.lastEvent!.timestamp}',
                style: const TextStyle(fontSize: 12, color: Colors.grey)),
          ],
        ]),
      ),
    );
  }
}

class _LiveViewCard extends ConsumerStatefulWidget {
  final ConnectionStatus? connectionStatus;
  final String? cameraIp;
  const _LiveViewCard({required this.connectionStatus, required this.cameraIp});

  @override
  ConsumerState<_LiveViewCard> createState() => _LiveViewCardState();
}

class _LiveViewCardState extends ConsumerState<_LiveViewCard> {
  @override
  void dispose() {
    if (widget.connectionStatus == ConnectionStatus.remote) {
      ref.read(repositoryProvider).stopLive();
    }
    super.dispose();
  }

  @override
  void didUpdateWidget(_LiveViewCard old) {
    super.didUpdateWidget(old);
    if (widget.connectionStatus == ConnectionStatus.remote &&
        old.connectionStatus != ConnectionStatus.remote) {
      ref.read(repositoryProvider).startLive();
    } else if (widget.connectionStatus != ConnectionStatus.remote &&
        old.connectionStatus == ConnectionStatus.remote) {
      ref.read(repositoryProvider).stopLive();
    }
  }

  @override
  void initState() {
    super.initState();
    if (widget.connectionStatus == ConnectionStatus.remote) {
      ref.read(repositoryProvider).startLive();
    }
  }

  @override
  Widget build(BuildContext context) {
    if (widget.connectionStatus == ConnectionStatus.local && widget.cameraIp != null) {
      return Card(
        clipBehavior: Clip.antiAlias,
        child: Mjpeg(stream: 'http://${widget.cameraIp}/stream', isLive: true),
      );
    }
    if (widget.connectionStatus == ConnectionStatus.remote) {
      return Card(
        child: Column(children: [
          StreamBuilder<Uint8List>(
            stream: ref.watch(repositoryProvider).liveFrames,
            builder: (context, snap) {
              if (!snap.hasData) {
                return const SizedBox(
                  height: 240,
                  child: Center(child: CircularProgressIndicator()),
                );
              }
              return Image.memory(snap.data!, gaplessPlayback: true);
            },
          ),
          const Padding(
            padding: EdgeInsets.all(8),
            child: Text('Refreshing ~1x/sec (remote view)',
                style: TextStyle(fontSize: 11, color: Colors.grey)),
          ),
        ]),
      );
    }
    return const Card(
      child: Padding(
        padding: EdgeInsets.all(24),
        child: Center(child: Text('Camera unavailable — not connected.')),
      ),
    );
  }
}
