import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:mobile_scanner/mobile_scanner.dart';

class QrScanScreen extends StatefulWidget {
  const QrScanScreen({super.key});
  @override State<QrScanScreen> createState() => _QrScanScreenState();
}

class _QrScanScreenState extends State<QrScanScreen> {
  bool _done = false;
  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('Scan Pi QR code')),
        body: MobileScanner(
          onDetect: (capture) {
            if (_done) return;
            final val = capture.barcodes.firstOrNull?.rawValue;
            if (val == null) return;
            _done = true;
            context.pop(val);
          },
        ),
      );
}
