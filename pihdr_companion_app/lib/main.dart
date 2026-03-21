import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'screens/connect_screen.dart';

void main() {
  runApp(const ProviderScope(child: PiHdrCompanionApp()));
}

class PiHdrCompanionApp extends StatelessWidget {
  const PiHdrCompanionApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Pi HDR Companion',
      theme: ThemeData(
        colorSchemeSeed: Colors.blue,
        brightness: Brightness.dark,
        useMaterial3: true,
      ),
      home: const ConnectScreen(),
    );
  }
}
