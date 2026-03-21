import 'dart:async';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../providers/connection_provider.dart';

class ZoomPanPad extends ConsumerStatefulWidget {
  const ZoomPanPad({super.key});

  @override
  ConsumerState<ZoomPanPad> createState() => _ZoomPanPadState();
}

class _ZoomPanPadState extends ConsumerState<ZoomPanPad> {
  final TransformationController _transformController =
      TransformationController();
  Timer? _throttleTimer;
  bool _dirty = false;

  Size? _imageNaturalSize;
  int? _decodingForIndex; // prevents duplicate async calls
  int? _displayedIndex; // tracks which photo we're showing
  Size? _currentFittedSize;

  static const _throttleInterval = Duration(milliseconds: 33); // ~30fps

  @override
  void initState() {
    super.initState();
    _transformController.addListener(_onTransformChanged);
  }

  @override
  void dispose() {
    _transformController.removeListener(_onTransformChanged);
    _transformController.dispose();
    _throttleTimer?.cancel();
    super.dispose();
  }

  void _onTransformChanged() {
    _scheduleUpdate();
  }

  void _sendViewport() {
    if (!_dirty || _currentFittedSize == null) return;
    _dirty = false;

    final matrix = _transformController.value;
    final zoom = matrix.getMaxScaleOnAxis();
    final tx = matrix.entry(0, 3);
    final ty = matrix.entry(1, 3);

    final w = _currentFittedSize!.width;
    final h = _currentFittedSize!.height;

    // Center of visible region in child coordinates, normalized to 0..1
    final cx = ((w / 2 - tx) / zoom / w).clamp(0.0, 1.0);
    final cy = ((h / 2 - ty) / zoom / h).clamp(0.0, 1.0);

    ref.read(wsServiceProvider).sendViewport(cx, cy, zoom);
  }

  void _scheduleUpdate() {
    _dirty = true;
    if (_throttleTimer == null || !_throttleTimer!.isActive) {
      _sendViewport();
      _throttleTimer = Timer(_throttleInterval, _sendViewport);
    }
  }

  void _resetViewport() {
    _transformController.value = Matrix4.identity();
    _dirty = true;
    _sendViewport();
  }

  Future<void> _decodeImageSize(int index) async {
    _decodingForIndex = index;
    final thumbnails = ref.read(thumbnailCacheProvider);
    final bytes = thumbnails[index];
    if (bytes == null) return;

    final codec = await ui.instantiateImageCodec(bytes);
    final frame = await codec.getNextFrame();
    final size = Size(
      frame.image.width.toDouble(),
      frame.image.height.toDouble(),
    );
    frame.image.dispose();
    codec.dispose();

    // Only apply if this is still the photo we want
    if (mounted && _decodingForIndex == index) {
      setState(() {
        _imageNaturalSize = size;
        _displayedIndex = index;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final viewerState = ref.watch(viewerStateProvider);
    final thumbnails = ref.watch(thumbnailCacheProvider);
    final currentIndex = viewerState.currentIndex;
    final thumbBytes = thumbnails[currentIndex];

    // Photo changed — reset zoom and start decoding new thumbnail
    if (_displayedIndex != null && _displayedIndex != currentIndex) {
      _transformController.value = Matrix4.identity();
      _imageNaturalSize = null;
      _dirty = true;
      _sendViewport();
    }

    // Start decoding if needed
    if (thumbBytes != null && _decodingForIndex != currentIndex) {
      _decodeImageSize(currentIndex);
    }

    return LayoutBuilder(
      builder: (context, constraints) {
        if (thumbBytes == null || _imageNaturalSize == null) {
          return Center(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                SizedBox(
                  width: 24,
                  height: 24,
                  child: CircularProgressIndicator(
                    strokeWidth: 2,
                    color: Colors.grey[600],
                  ),
                ),
                const SizedBox(height: 8),
                Text(
                  'Loading photo...',
                  style: TextStyle(color: Colors.grey[600], fontSize: 12),
                ),
              ],
            ),
          );
        }

        final fittedSize =
            _computeFittedSize(constraints, _imageNaturalSize!);
        _currentFittedSize = fittedSize;

        final zoom = _transformController.value.getMaxScaleOnAxis();

        return Center(
          child: SizedBox(
            width: fittedSize.width,
            height: fittedSize.height,
            child: GestureDetector(
              onDoubleTap: _resetViewport,
              child: ClipRect(
                child: Stack(
                  children: [
                    InteractiveViewer(
                      transformationController: _transformController,
                      minScale: 1.0,
                      maxScale: 10.0,
                      child: Image.memory(
                        thumbBytes,
                        fit: BoxFit.fill,
                        gaplessPlayback: true,
                      ),
                    ),
                    // Zoom level label
                    if (zoom > 1.01)
                      Positioned(
                        bottom: 8,
                        right: 8,
                        child: IgnorePointer(
                          child: Container(
                            padding: const EdgeInsets.symmetric(
                                horizontal: 8, vertical: 4),
                            decoration: BoxDecoration(
                              color: Colors.black54,
                              borderRadius: BorderRadius.circular(4),
                            ),
                            child: Text(
                              '${zoom.toStringAsFixed(1)}x',
                              style: const TextStyle(
                                color: Colors.white,
                                fontSize: 12,
                              ),
                            ),
                          ),
                        ),
                      ),
                  ],
                ),
              ),
            ),
          ),
        );
      },
    );
  }

  Size _computeFittedSize(BoxConstraints constraints, Size imageSize) {
    final availW = constraints.maxWidth;
    final availH = constraints.maxHeight;
    final imageAspect = imageSize.width / imageSize.height;
    final containerAspect = availW / availH;

    if (imageAspect > containerAspect) {
      return Size(availW, availW / imageAspect);
    } else {
      return Size(availH * imageAspect, availH);
    }
  }
}
