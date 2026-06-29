import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

class ShellScreen extends StatelessWidget {
  final Widget child;
  const ShellScreen({required this.child, super.key});

  static const _routes = ['/dashboard', '/devices', '/control', '/weather', '/settings'];

  @override
  Widget build(BuildContext context) {
    final loc = GoRouterState.of(context).matchedLocation;
    final idx = _routes.indexOf(loc).clamp(0, _routes.length - 1);
    return Scaffold(
      body: child,
      bottomNavigationBar: NavigationBar(
        selectedIndex: idx,
        onDestinationSelected: (i) => context.go(_routes[i]),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.dashboard),    label: 'Dashboard'),
          NavigationDestination(icon: Icon(Icons.sensors),      label: 'Devices'),
          NavigationDestination(icon: Icon(Icons.toggle_on),    label: 'Control'),
          NavigationDestination(icon: Icon(Icons.cloud),        label: 'Weather'),
          NavigationDestination(icon: Icon(Icons.settings),     label: 'Settings'),
        ],
      ),
    );
  }
}

