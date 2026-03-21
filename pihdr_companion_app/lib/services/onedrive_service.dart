import 'dart:convert';

import 'package:flutter_appauth/flutter_appauth.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;

import '../models/onedrive_item.dart';

const _redirectUri = 'com.minislop.pihdr://oauth';
const _scopes = ['Files.Read', 'User.Read', 'offline_access'];
const _authority = 'https://login.microsoftonline.com/common';
const _graphBase = 'https://graph.microsoft.com/v1.0';

const _storageKeyAccess = 'onedrive_access_token';
const _storageKeyRefresh = 'onedrive_refresh_token';

class OneDriveService {
  final _appAuth = const FlutterAppAuth();
  final _storage = const FlutterSecureStorage();
  String _clientId = '';
  String? _accessToken;
  String? _refreshToken;
  String? _userName;
  DateTime _tokenExpiry = DateTime(2000);

  bool get isAuthenticated => _refreshToken != null;
  String? get userName => _userName;
  String? get accessToken => _accessToken;
  String? get refreshToken => _refreshToken;

  void configure({required String clientId}) {
    _clientId = clientId;
  }

  Future<void> loadSavedTokens() async {
    _accessToken = await _storage.read(key: _storageKeyAccess);
    _refreshToken = await _storage.read(key: _storageKeyRefresh);
    if (_refreshToken != null) {
      await _ensureValidToken();
      await _fetchUserName();
    }
  }

  Future<bool> signIn() async {
    if (_clientId.isEmpty) return false;

    try {
      final result = await _appAuth.authorizeAndExchangeCode(
        AuthorizationTokenRequest(
          _clientId,
          _redirectUri,
          discoveryUrl: '$_authority/v2.0/.well-known/openid-configuration',
          scopes: _scopes,
          promptValues: ['select_account'],
        ),
      );

      _accessToken = result.accessToken;
      _refreshToken = result.refreshToken;
      _tokenExpiry = result.accessTokenExpirationDateTime ?? DateTime.now();

      await _storage.write(key: _storageKeyAccess, value: _accessToken);
      await _storage.write(key: _storageKeyRefresh, value: _refreshToken);

      await _fetchUserName();
      return true;
    } catch (e) {
      return false;
    }
  }

  Future<void> signOut() async {
    _accessToken = null;
    _refreshToken = null;
    _userName = null;
    await _storage.delete(key: _storageKeyAccess);
    await _storage.delete(key: _storageKeyRefresh);
  }

  Future<void> _ensureValidToken() async {
    if (_accessToken != null &&
        DateTime.now().isBefore(_tokenExpiry.subtract(const Duration(minutes: 1)))) {
      return;
    }
    if (_refreshToken == null || _clientId.isEmpty) return;

    try {
      final result = await _appAuth.token(
        TokenRequest(
          _clientId,
          _redirectUri,
          discoveryUrl: '$_authority/v2.0/.well-known/openid-configuration',
          refreshToken: _refreshToken,
          scopes: _scopes,
        ),
      );

      _accessToken = result.accessToken;
      if (result.refreshToken != null) {
        _refreshToken = result.refreshToken;
      }
      _tokenExpiry = result.accessTokenExpirationDateTime ?? DateTime.now();
      await _storage.write(key: _storageKeyAccess, value: _accessToken);
      await _storage.write(key: _storageKeyRefresh, value: _refreshToken);
    } catch (_) {
      // Token refresh failed, user needs to re-auth
      _accessToken = null;
    }
  }

  Future<Map<String, String>> _authHeaders() async {
    await _ensureValidToken();
    return {
      'Authorization': 'Bearer $_accessToken',
      'Content-Type': 'application/json',
    };
  }

  Future<void> _fetchUserName() async {
    try {
      final headers = await _authHeaders();
      final resp = await http.get(Uri.parse('$_graphBase/me'), headers: headers);
      if (resp.statusCode == 200) {
        final data = jsonDecode(resp.body);
        _userName = data['displayName'] ?? data['userPrincipalName'];
      }
    } catch (_) {}
  }

  // -- Browsing --

  Future<List<OneDriveItem>> listFolder({String? folderId}) async {
    final path = (folderId == null || folderId == 'root')
        ? '/me/drive/root/children'
        : '/me/drive/items/$folderId/children';

    final query = '\$select=id,name,folder,image,size,lastModifiedDateTime&\$top=200';
    final headers = await _authHeaders();
    final resp = await http.get(
      Uri.parse('$_graphBase$path?$query'),
      headers: headers,
    );

    if (resp.statusCode != 200) return [];

    final data = jsonDecode(resp.body);
    final items = <OneDriveItem>[];
    for (final item in data['value'] ?? []) {
      final isFolder = item.containsKey('folder');
      final isPhoto = item.containsKey('image');
      if (isFolder || isPhoto) {
        items.add(OneDriveItem.fromGraphJson(item));
      }
    }
    return items;
  }

  Future<String?> getThumbnailUrl(String itemId, {String size = 'large'}) async {
    try {
      final headers = await _authHeaders();
      final resp = await http.get(
        Uri.parse('$_graphBase/me/drive/items/$itemId/thumbnails/0/$size'),
        headers: headers,
      );
      if (resp.statusCode == 200) {
        final data = jsonDecode(resp.body);
        return data['url'] as String?;
      }
    } catch (_) {}
    return null;
  }

  Future<String?> getDownloadUrl(String itemId) async {
    try {
      final headers = await _authHeaders();
      final resp = await http.get(
        Uri.parse('$_graphBase/me/drive/items/$itemId'),
        headers: headers,
      );
      if (resp.statusCode == 200) {
        final data = jsonDecode(resp.body);
        return data['@microsoft.graph.downloadUrl'] as String?;
      }
    } catch (_) {}
    return null;
  }
}
