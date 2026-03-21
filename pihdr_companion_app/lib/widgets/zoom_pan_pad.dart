import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../providers/connection_provider.dart';

class ZoomPanPad extends ConsumerStatefulWidget {
  const ZoomPanPad({super.key});

  @override
  ConsumerState<ZoomPanPad> createState() => _ZoomPanPadState();
}

class _ZoomPanPadState extends ConsumerState<ZoomPanPad> {
  double _zoom = 1.0;
  double _cx = 0.5;
  double _cy = 0.5;
  Timer? _throttleTimer;
  bool _dirty = false;

  static const double _minZoom = 1.0;
  static const double _maxZoom = 10.0;
  static const _throttleInterval = Duration(milliseconds: 33); // ~30fps

  @override
  void dispose() {
    _throttleTimer?.cancel();
    super.dispose();
  }

  void _sendViewport() {
    if (!_dirty) return;
    _dirty = false;
    ref.read(wsServiceProvider).sendViewport(_cx, _cy, _zoom);
  }

  void _scheduleUpdate() {
    _dirty = true;
    if (_throttleTimer == null || !_throttleTimer!.isActive) {
      _sendViewport();
      _throttleTimer = Timer(_throttleInterval, _sendViewport);
    }
  }

  void _onScaleUpdate(ScaleUpdateDetails details) {
    setState(() {
      // Apply zoom from pinch gesture
      if (details.scale != 1.0) {
        _zoom = (_zoom * details.scale).clamp(_minZoom, _maxZoom);
      }

      // Apply pan from drag (normalized to 0..1)
      if (_zoom > 1.0) {
        final viewFrac = 1.0 / _zoom;
        _cx = (_cx - details.focalPointDelta.dx / 300 * viewFrac)
            .clamp(viewFrac / 2, 1.0 - viewFrac / 2);
        _cy = (_cy - details.focalPointDelta.dy / 300 * viewFrac)
            .clamp(viewFrac / 2, 1.0 - viewFrac / 2);
      }
    });
    _scheduleUpdate();
  }

  void _resetViewport() {
    setState(() {
      _zoom = 1.0;
      _cx = 0.5;
      _cy = 0.5;
    });
    _dirty = true;
    _sendViewport();
  }

  @override
  Widget build(BuildContext context) {
    final viewFrac = 1.0 / _zoom;

    return GestureDetector(
      onScaleUpdate: _onScaleUpdate,
      onDoubleTap: _resetViewport,
      child: Container(
        decoration: BoxDecoration(
          color: Colors.grey[900],
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: Colors.grey[700]!),
        ),
        child: Stack(
          children: [
            // Grid lines
            CustomPaint(
              size: Size.infinite,
              painter: _GridPainter(),
            ),
            // Viewport rectangle
            if (_zoom > 1.0)
              Positioned.fill(
                child: LayoutBuilder(
                  builder: (context, constraints) {
                    final w = constraints.maxWidth;
                    final h = constraints.maxHeight;
                    final rectW = w * viewFrac;
                    final rectH = h * viewFrac;
                    final rectX = (_cx - viewFrac / 2) * w;
                    final rectY = (_cy - viewFrac / 2) * h;

                    return Stack(
                      children: [
                        Positioned(
                          left: rectX,
                          top: rectY,
                          width: rectW,
                          height: rectH,
                          child: Container(
                            decoration: BoxDecoration(
                              border: Border.all(
                                color: Colors.blue,
                                width: 2,
                              ),
                              color: Colors.blue.withValues(alpha: 0.1),
                            ),
                          ),
                        ),
                      ],
                    );
                  },
                ),
              ),
            // Zoom level label
            Positioned(
              bottom: 8,
              right: 8,
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                decoration: BoxDecoration(
                  color: Colors.black54,
                  borderRadius: BorderRadius.circular(4),
                ),
                child: Text(
                  '${_zoom.toStringAsFixed(1)}x',
                  style: const TextStyle(
                    color: Colors.white,
                    fontSize: 12,
                  ),
                ),
              ),
            ),
            // Hint text
            if (_zoom == 1.0)
              const Center(
                child: Text(
                  'Pinch to zoom\nDrag to pan\nDouble-tap to reset',
                  textAlign: TextAlign.center,
                  style: TextStyle(color: Colors.grey, fontSize: 14),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _GridPainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = Colors.grey[800]!
      ..strokeWidth = 0.5;

    // Draw 4x4 grid
    for (int i = 1; i < 4; i++) {
      final x = size.width * i / 4;
      final y = size.height * i / 4;
      canvas.drawLine(Offset(x, 0), Offset(x, size.height), paint);
      canvas.drawLine(Offset(0, y), Offset(size.width, y), paint);
    }
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}
