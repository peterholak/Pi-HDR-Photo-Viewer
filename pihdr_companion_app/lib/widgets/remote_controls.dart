import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../providers/connection_provider.dart';
import '../widgets/zoom_pan_pad.dart';

class RemoteControls extends ConsumerWidget {
  const RemoteControls({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final viewerState = ref.watch(viewerStateProvider);
    final ws = ref.read(wsServiceProvider);

    return SingleChildScrollView(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // Current state card
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    viewerState.currentFilename ?? 'No photo',
                    style: Theme.of(context).textTheme.titleMedium,
                    overflow: TextOverflow.ellipsis,
                  ),
                  const SizedBox(height: 4),
                  Text(
                    '${viewerState.currentIndex + 1} / ${viewerState.totalPhotos}'
                    '  |  ${viewerState.appState}'
                    '${viewerState.isUltraHdr ? '  |  Ultra HDR' : ''}',
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 16),

          // Transport controls
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceEvenly,
            children: [
              IconButton.filled(
                icon: const Icon(Icons.skip_previous),
                iconSize: 36,
                onPressed: () => ws.sendCommand('prev'),
              ),
              IconButton.filled(
                icon: Icon(viewerState.slideshowActive
                    ? Icons.pause
                    : Icons.play_arrow),
                iconSize: 48,
                onPressed: () => ws.sendCommand('slideshow_toggle'),
              ),
              IconButton.filled(
                icon: const Icon(Icons.skip_next),
                iconSize: 36,
                onPressed: () => ws.sendCommand('next'),
              ),
            ],
          ),
          const SizedBox(height: 24),

          // Toggle buttons
          Wrap(
            spacing: 8,
            runSpacing: 8,
            alignment: WrapAlignment.center,
            children: [
              FilterChip(
                label: const Text('HDR'),
                selected: viewerState.hdrActive,
                onSelected: (_) => ws.sendCommand('hdr'),
              ),
              FilterChip(
                label: const Text('Gain Map'),
                selected: viewerState.gainMapEnabled,
                onSelected: (_) => ws.sendCommand('gainmap'),
              ),
              ActionChip(
                label: const Text('Debug'),
                onPressed: () => ws.sendCommand('debug'),
              ),
              ActionChip(
                label: const Text('Grid'),
                onPressed: () => ws.sendCommand('back'),
              ),
            ],
          ),
          const SizedBox(height: 24),

          // Zoom/Pan pad
          const Text('Zoom & Pan',
              style: TextStyle(fontWeight: FontWeight.bold)),
          const SizedBox(height: 8),
          const SizedBox(
            height: 300,
            child: ZoomPanPad(),
          ),
          const SizedBox(height: 24),

          // Quit button
          OutlinedButton.icon(
            icon: const Icon(Icons.power_settings_new),
            label: const Text('Quit Viewer'),
            style: OutlinedButton.styleFrom(
              foregroundColor: Colors.red,
            ),
            onPressed: () {
              showDialog(
                context: context,
                builder: (ctx) => AlertDialog(
                  title: const Text('Quit Viewer?'),
                  content: const Text(
                      'This will stop the HDR viewer on the Pi.'),
                  actions: [
                    TextButton(
                      onPressed: () => Navigator.pop(ctx),
                      child: const Text('Cancel'),
                    ),
                    FilledButton(
                      onPressed: () {
                        ws.sendCommand('quit');
                        Navigator.pop(ctx);
                      },
                      child: const Text('Quit'),
                    ),
                  ],
                ),
              );
            },
          ),
        ],
      ),
    );
  }
}
