import 'dart:async';
import 'dart:typed_data';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/viewer_state.dart';
import '../services/websocket_service.dart';

final wsServiceProvider = Provider<WebSocketService>((ref) {
  final service = WebSocketService();
  ref.onDispose(() => service.dispose());
  return service;
});

final connectionStateProvider = StreamProvider<WsConnectionState>((ref) {
  final ws = ref.watch(wsServiceProvider);
  return ws.connectionStream;
});

final viewerStateProvider =
    NotifierProvider<ViewerStateNotifier, ViewerState>(ViewerStateNotifier.new);

class ViewerStateNotifier extends Notifier<ViewerState> {
  StreamSubscription? _sub;

  @override
  ViewerState build() {
    final ws = ref.watch(wsServiceProvider);
    _sub?.cancel();
    _sub = ws.stateStream.listen((json) {
      if (json['type'] == 'state') {
        state = ViewerState.fromJson(json);
      }
    });
    ref.onDispose(() => _sub?.cancel());
    return const ViewerState();
  }
}

final thumbnailCacheProvider =
    NotifierProvider<ThumbnailCacheNotifier, Map<int, Uint8List>>(
        ThumbnailCacheNotifier.new);

class ThumbnailCacheNotifier extends Notifier<Map<int, Uint8List>> {
  StreamSubscription? _sub;

  @override
  Map<int, Uint8List> build() {
    final ws = ref.watch(wsServiceProvider);
    _sub?.cancel();
    _sub = ws.thumbnailStream.listen((thumb) {
      state = {...state, thumb.index: thumb.jpegBytes};
    });
    ref.onDispose(() => _sub?.cancel());
    return {};
  }

  void clear() {
    state = {};
  }
}
