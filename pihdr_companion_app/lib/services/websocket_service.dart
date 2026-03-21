import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:web_socket_channel/web_socket_channel.dart';

enum WsConnectionState { disconnected, connecting, connected, error }

class WebSocketService {
  WebSocketChannel? _channel;
  final _stateController = StreamController<Map<String, dynamic>>.broadcast();
  final _thumbnailController = StreamController<ThumbnailData>.broadcast();
  final _connectionController =
      StreamController<WsConnectionState>.broadcast();
  WsConnectionState _connectionState = WsConnectionState.disconnected;
  String? _host;
  int _port = 8765;
  Timer? _reconnectTimer;

  // When we receive a thumbnail_header, we store it until the binary frame arrives
  Map<String, dynamic>? _pendingThumbnailHeader;

  Stream<Map<String, dynamic>> get stateStream => _stateController.stream;
  Stream<ThumbnailData> get thumbnailStream => _thumbnailController.stream;
  Stream<WsConnectionState> get connectionStream =>
      _connectionController.stream;
  WsConnectionState get connectionState => _connectionState;
  String? get host => _host;

  Future<void> connect(String host, {int port = 8765}) async {
    _host = host;
    _port = port;
    await _doConnect();
  }

  Future<void> _doConnect() async {
    if (_host == null) return;
    _setWsConnectionState(WsConnectionState.connecting);

    try {
      final uri = Uri.parse('ws://$_host:$_port');
      _channel = WebSocketChannel.connect(uri);
      await _channel!.ready;
      _setWsConnectionState(WsConnectionState.connected);

      _channel!.stream.listen(
        _onMessage,
        onError: (error) {
          _setWsConnectionState(WsConnectionState.error);
          _scheduleReconnect();
        },
        onDone: () {
          _setWsConnectionState(WsConnectionState.disconnected);
          _scheduleReconnect();
        },
      );

      // Request initial state
      sendJson({'type': 'get_state'});
    } catch (e) {
      _setWsConnectionState(WsConnectionState.error);
      _scheduleReconnect();
    }
  }

  void _onMessage(dynamic message) {
    if (message is String) {
      try {
        final json = jsonDecode(message) as Map<String, dynamic>;
        final type = json['type'] as String?;

        if (type == 'thumbnail_header') {
          _pendingThumbnailHeader = json;
        } else {
          _stateController.add(json);
        }
      } catch (_) {}
    } else if (message is List<int>) {
      // Binary frame — this is thumbnail JPEG data
      if (_pendingThumbnailHeader != null) {
        final header = _pendingThumbnailHeader!;
        _pendingThumbnailHeader = null;
        _thumbnailController.add(ThumbnailData(
          index: header['index'] as int,
          filename: header['filename'] as String,
          isUltraHdr: header['is_ultrahdr'] as bool? ?? false,
          jpegBytes: Uint8List.fromList(message),
        ));
      }
    }
  }

  void _scheduleReconnect() {
    _reconnectTimer?.cancel();
    _reconnectTimer = Timer(const Duration(seconds: 3), () {
      if (_connectionState != WsConnectionState.connected) {
        _doConnect();
      }
    });
  }

  void _setWsConnectionState(WsConnectionState state) {
    _connectionState = state;
    _connectionController.add(state);
  }

  void sendJson(Map<String, dynamic> message) {
    _channel?.sink.add(jsonEncode(message));
  }

  void sendCommand(String action, {Map<String, dynamic>? params}) {
    final msg = {'type': 'command', 'action': action, ...?params};
    sendJson(msg);
  }

  void sendViewport(double cx, double cy, double zoom) {
    sendJson({'type': 'viewport', 'cx': cx, 'cy': cy, 'zoom': zoom});
  }

  void requestThumbnails({int start = 0, int count = 20}) {
    sendJson({'type': 'get_thumbnails', 'start': start, 'count': count});
  }

  void disconnect() {
    _reconnectTimer?.cancel();
    _channel?.sink.close();
    _channel = null;
    _setWsConnectionState(WsConnectionState.disconnected);
  }

  void dispose() {
    disconnect();
    _stateController.close();
    _thumbnailController.close();
    _connectionController.close();
  }
}

class ThumbnailData {
  final int index;
  final String filename;
  final bool isUltraHdr;
  final Uint8List jpegBytes;

  const ThumbnailData({
    required this.index,
    required this.filename,
    required this.isUltraHdr,
    required this.jpegBytes,
  });
}
