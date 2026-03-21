import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../providers/connection_provider.dart';
import '../services/websocket_service.dart';
import '../widgets/remote_controls.dart';
import '../widgets/photo_grid.dart';
import 'connect_screen.dart';
import 'onedrive_browser_screen.dart';

class HomeScreen extends ConsumerStatefulWidget {
  const HomeScreen({super.key});

  @override
  ConsumerState<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends ConsumerState<HomeScreen> {
  int _currentTab = 0;

  @override
  Widget build(BuildContext context) {
    final connectionAsync = ref.watch(connectionStateProvider);
    final isConnected = connectionAsync.when(
      data: (state) => state == WsConnectionState.connected,
      loading: () => false,
      error: (_, _) => false,
    );

    // Navigate back to connect screen if disconnected
    if (!isConnected &&
        connectionAsync.hasValue &&
        connectionAsync.value == WsConnectionState.disconnected) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) {
          Navigator.of(context).pushReplacement(
            MaterialPageRoute(builder: (_) => const ConnectScreen()),
          );
        }
      });
    }

    return Scaffold(
      appBar: AppBar(
        title: const Text('Pi HDR Viewer'),
        actions: [
          _connectionIndicator(connectionAsync),
          IconButton(
            icon: const Icon(Icons.link_off),
            tooltip: 'Disconnect',
            onPressed: () {
              ref.read(wsServiceProvider).disconnect();
            },
          ),
        ],
      ),
      body: IndexedStack(
        index: _currentTab,
        children: const [
          RemoteControls(),
          PhotoGridTab(),
          OneDriveBrowserScreen(),
        ],
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _currentTab,
        onDestinationSelected: (index) => setState(() => _currentTab = index),
        destinations: const [
          NavigationDestination(
            icon: Icon(Icons.gamepad),
            label: 'Remote',
          ),
          NavigationDestination(
            icon: Icon(Icons.photo_library),
            label: 'Library',
          ),
          NavigationDestination(
            icon: Icon(Icons.cloud),
            label: 'OneDrive',
          ),
        ],
      ),
    );
  }

  Widget _connectionIndicator(AsyncValue<WsConnectionState> state) {
    final color = state.when(
      data: (s) => switch (s) {
        WsConnectionState.connected => Colors.green,
        WsConnectionState.connecting => Colors.orange,
        WsConnectionState.error => Colors.red,
        WsConnectionState.disconnected => Colors.grey,
      },
      loading: () => Colors.grey,
      error: (_, _) => Colors.red,
    );

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 8),
      child: Icon(Icons.circle, color: color, size: 12),
    );
  }
}
