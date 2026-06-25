import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:greenhouse_app/screens/shell_screen.dart';
import 'package:greenhouse_app/screens/pairing/pairing_screen.dart';
import 'package:greenhouse_app/screens/pairing/qr_scan_screen.dart';
import 'package:greenhouse_app/screens/dashboard/dashboard_screen.dart';
import 'package:greenhouse_app/screens/devices/devices_screen.dart';
import 'package:greenhouse_app/screens/control/control_screen.dart';
import 'package:greenhouse_app/screens/settings/settings_screen.dart';
import 'package:greenhouse_app/services/pairing_service.dart';
import 'package:greenhouse_app/theme/app_theme.dart';

final _router = GoRouter(
  initialLocation: '/dashboard',
  redirect: (context, state) async {
    final pairing = ProviderScope.containerOf(context).read(pairingServiceProvider);
    if (!await pairing.isPaired && state.matchedLocation != '/pair') return '/pair';
    return null;
  },
  routes: [
    GoRoute(path: '/pair', builder: (_, __) => const PairingScreen()),
    GoRoute(path: '/pair/qr', builder: (_, __) => const QrScanScreen()),
    ShellRoute(
      builder: (_, __, child) => ShellScreen(child: child),
      routes: [
        GoRoute(path: '/dashboard', builder: (_, __) => const DashboardScreen()),
        GoRoute(path: '/devices',   builder: (_, __) => const DevicesScreen()),
        GoRoute(path: '/control',   builder: (_, __) => const ControlScreen()),
        GoRoute(path: '/settings',  builder: (_, __) => const SettingsScreen()),
      ],
    ),
  ],
);

class GreenhouseApp extends StatelessWidget {
  const GreenhouseApp({super.key});
  @override
  Widget build(BuildContext context) => MaterialApp.router(
        title: 'Greenhouse',
        theme: AppTheme.light(),
        darkTheme: AppTheme.dark(),
        themeMode: ThemeMode.system,
        routerConfig: _router,
      );
}
