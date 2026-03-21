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

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        // Photo viewer takes available space
        const Expanded(
          child: Padding(
            padding: EdgeInsets.fromLTRB(16, 16, 16, 8),
            child: ZoomPanPad(),
          ),
        ),

        // Controls below, scrollable if needed
        SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              // Photo info + transport controls
              Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          viewerState.currentFilename ?? 'No photo',
                          style: Theme.of(context).textTheme.titleSmall,
                          overflow: TextOverflow.ellipsis,
                        ),
                        Text(
                          '${viewerState.currentIndex + 1} / ${viewerState.totalPhotos}'
                          '  |  ${viewerState.appState}'
                          '${viewerState.isUltraHdr ? '  |  Ultra HDR' : ''}',
                          style: Theme.of(context).textTheme.bodySmall,
                        ),
                      ],
                    ),
                  ),
                  IconButton(
                    icon: const Icon(Icons.skip_previous),
                    iconSize: 32,
                    onPressed: () => ws.sendCommand('prev'),
                  ),
                  IconButton.filled(
                    icon: Icon(viewerState.slideshowActive
                        ? Icons.pause
                        : Icons.play_arrow),
                    iconSize: 36,
                    onPressed: () => ws.sendCommand('slideshow_toggle'),
                  ),
                  IconButton(
                    icon: const Icon(Icons.skip_next),
                    iconSize: 32,
                    onPressed: () => ws.sendCommand('next'),
                  ),
                ],
              ),
              const SizedBox(height: 8),

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
                  ActionChip(
                    avatar: const Icon(Icons.power_settings_new,
                        size: 18, color: Colors.red),
                    label: const Text('Quit'),
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
            ],
          ),
        ),
      ],
    );
  }
}
