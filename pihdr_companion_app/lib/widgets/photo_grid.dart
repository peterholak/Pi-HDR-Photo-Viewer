import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../providers/connection_provider.dart';

class PhotoGridTab extends ConsumerStatefulWidget {
  const PhotoGridTab({super.key});

  @override
  ConsumerState<PhotoGridTab> createState() => _PhotoGridTabState();
}

class _PhotoGridTabState extends ConsumerState<PhotoGridTab> {
  bool _requested = false;

  @override
  void initState() {
    super.initState();
    _requestThumbnails();
  }

  void _requestThumbnails() {
    if (_requested) return;
    _requested = true;
    // Request thumbnails after frame to ensure provider is ready
    WidgetsBinding.instance.addPostFrameCallback((_) {
      ref.read(wsServiceProvider).requestThumbnails(start: 0, count: 100);
    });
  }

  @override
  Widget build(BuildContext context) {
    final viewerState = ref.watch(viewerStateProvider);
    final thumbnails = ref.watch(thumbnailCacheProvider);
    final ws = ref.read(wsServiceProvider);

    if (viewerState.totalPhotos == 0) {
      return const Center(child: Text('No photos on Pi'));
    }

    return RefreshIndicator(
      onRefresh: () async {
        ref.read(thumbnailCacheProvider.notifier).clear();
        ws.requestThumbnails(start: 0, count: 100);
        await Future.delayed(const Duration(seconds: 1));
      },
      child: GridView.builder(
        padding: const EdgeInsets.all(8),
        gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
          crossAxisCount: 3,
          crossAxisSpacing: 8,
          mainAxisSpacing: 8,
          childAspectRatio: 1.0,
        ),
        itemCount: viewerState.totalPhotos,
        itemBuilder: (context, index) {
          final thumb = thumbnails[index];
          final isSelected = viewerState.appState == 'VIEWER' &&
              viewerState.currentIndex == index;

          return GestureDetector(
            onTap: () {
              ws.sendCommand('select', params: {'index': index});
            },
            child: Container(
              decoration: BoxDecoration(
                border: Border.all(
                  color: isSelected ? Colors.blue : Colors.transparent,
                  width: 3,
                ),
                borderRadius: BorderRadius.circular(8),
              ),
              child: ClipRRect(
                borderRadius: BorderRadius.circular(5),
                child: thumb != null
                    ? Image.memory(
                        thumb,
                        fit: BoxFit.cover,
                        gaplessPlayback: true,
                      )
                    : Container(
                        color: Colors.grey[850],
                        child: const Center(
                          child: SizedBox(
                            width: 24,
                            height: 24,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          ),
                        ),
                      ),
              ),
            ),
          );
        },
      ),
    );
  }
}
