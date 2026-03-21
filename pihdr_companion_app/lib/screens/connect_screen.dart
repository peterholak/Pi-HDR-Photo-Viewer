import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../providers/connection_provider.dart';
import '../services/websocket_service.dart';
import 'home_screen.dart';

const _storageKeyHost = 'pi_host';
const _storageKeyPort = 'pi_port';

class ConnectScreen extends ConsumerStatefulWidget {
  const ConnectScreen({super.key});

  @override
  ConsumerState<ConnectScreen> createState() => _ConnectScreenState();
}

class _ConnectScreenState extends ConsumerState<ConnectScreen> {
  final _hostController = TextEditingController();
  final _portController = TextEditingController(text: '8765');
  final _storage = const FlutterSecureStorage();
  bool _connecting = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadSaved();
  }

  Future<void> _loadSaved() async {
    final host = await _storage.read(key: _storageKeyHost);
    final port = await _storage.read(key: _storageKeyPort);
    if (host != null) _hostController.text = host;
    if (port != null) _portController.text = port;
  }

  Future<void> _connect() async {
    final host = _hostController.text.trim();
    final port = int.tryParse(_portController.text.trim()) ?? 8765;

    if (host.isEmpty) {
      setState(() => _error = 'Enter a host address');
      return;
    }

    setState(() {
      _connecting = true;
      _error = null;
    });

    await _storage.write(key: _storageKeyHost, value: host);
    await _storage.write(key: _storageKeyPort, value: port.toString());

    final ws = ref.read(wsServiceProvider);
    await ws.connect(host, port: port);

    // Wait briefly for connection
    bool connected = false;
    for (int i = 0; i < 20; i++) {
      await Future.delayed(const Duration(milliseconds: 150));
      if (ws.connectionState == WsConnectionState.connected) {
        connected = true;
        break;
      }
      if (ws.connectionState == WsConnectionState.error) {
        break;
      }
    }

    if (!mounted) return;

    if (connected) {
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => const HomeScreen()),
      );
    } else {
      setState(() {
        _connecting = false;
        _error = 'Could not connect to $host:$port';
      });
    }
  }

  @override
  void dispose() {
    _hostController.dispose();
    _portController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Connect to Pi Viewer')),
      body: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            TextField(
              controller: _hostController,
              decoration: const InputDecoration(
                labelText: 'Pi Host',
                hintText: 'e.g. 192.168.1.100 or pi5.local',
                border: OutlineInputBorder(),
              ),
              keyboardType: TextInputType.url,
              textInputAction: TextInputAction.next,
            ),
            const SizedBox(height: 16),
            TextField(
              controller: _portController,
              decoration: const InputDecoration(
                labelText: 'Port',
                border: OutlineInputBorder(),
              ),
              keyboardType: TextInputType.number,
              textInputAction: TextInputAction.done,
              onSubmitted: (_) => _connect(),
            ),
            const SizedBox(height: 24),
            if (_error != null)
              Padding(
                padding: const EdgeInsets.only(bottom: 16),
                child: Text(_error!, style: const TextStyle(color: Colors.red)),
              ),
            SizedBox(
              width: double.infinity,
              height: 48,
              child: FilledButton(
                onPressed: _connecting ? null : _connect,
                child: _connecting
                    ? const SizedBox(
                        width: 24,
                        height: 24,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Text('Connect'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
