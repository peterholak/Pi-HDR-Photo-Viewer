import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/onedrive_item.dart';
import '../providers/connection_provider.dart';
import '../providers/onedrive_provider.dart';

class OneDriveBrowserScreen extends ConsumerStatefulWidget {
  const OneDriveBrowserScreen({super.key});

  @override
  ConsumerState<OneDriveBrowserScreen> createState() =>
      _OneDriveBrowserScreenState();
}

class _OneDriveBrowserScreenState
    extends ConsumerState<OneDriveBrowserScreen> {
  String? _currentFolderId;
  final List<_BreadcrumbEntry> _breadcrumb = [
    _BreadcrumbEntry('root', 'OneDrive'),
  ];
  List<OneDriveItem>? _items;
  bool _loading = false;
  String? _error;

  // Thumbnail URL cache
  final Map<String, String> _thumbnailUrls = {};

  @override
  void initState() {
    super.initState();
    _loadFolder(null);
  }

  Future<void> _loadFolder(String? folderId) async {
    setState(() {
      _loading = true;
      _error = null;
      _currentFolderId = folderId;
    });

    try {
      final service = ref.read(oneDriveServiceProvider);
      final items = await service.listFolder(folderId: folderId);
      if (!mounted) return;
      setState(() {
        _items = items;
        _loading = false;
      });
      // Fetch thumbnail URLs for photos
      _fetchThumbnails(items);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  Future<void> _fetchThumbnails(List<OneDriveItem> items) async {
    final service = ref.read(oneDriveServiceProvider);
    for (final item in items) {
      if (item.isPhoto && !_thumbnailUrls.containsKey(item.id)) {
        final url = await service.getThumbnailUrl(item.id);
        if (url != null && mounted) {
          setState(() => _thumbnailUrls[item.id] = url);
        }
      }
    }
  }

  void _navigateInto(OneDriveItem folder) {
    _breadcrumb.add(_BreadcrumbEntry(folder.id, folder.name));
    _loadFolder(folder.id);
  }


  void _selectPhoto(OneDriveItem item) {
    // Send to Pi for download and display
    final ws = ref.read(wsServiceProvider);
    ws.sendJson({
      'type': 'onedrive_select',
      'item_id': item.id,
      'filename': item.name,
    });

    // Also share tokens with Pi
    final service = ref.read(oneDriveServiceProvider);
    if (service.accessToken != null && service.refreshToken != null) {
      ws.sendJson({
        'type': 'onedrive_auth',
        'access_token': service.accessToken,
        'refresh_token': service.refreshToken,
      });
    }

    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text('Sending ${item.name} to TV...')),
    );
  }

  @override
  Widget build(BuildContext context) {
    final authState = ref.watch(oneDriveAuthStateProvider);

    if (!authState.isAuthenticated) {
      return _buildSignInView(authState);
    }

    return Column(
      children: [
        // Breadcrumb
        SizedBox(
          height: 48,
          child: ListView(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.symmetric(horizontal: 8),
            children: [
              for (int i = 0; i < _breadcrumb.length; i++) ...[
                if (i > 0)
                  const Padding(
                    padding: EdgeInsets.symmetric(horizontal: 4),
                    child: Icon(Icons.chevron_right, size: 16, color: Colors.grey),
                  ),
                TextButton(
                  onPressed: () {
                    if (i < _breadcrumb.length - 1) {
                      _breadcrumb.removeRange(i + 1, _breadcrumb.length);
                      final entry = _breadcrumb.last;
                      _loadFolder(entry.id == 'root' ? null : entry.id);
                    }
                  },
                  child: Text(_breadcrumb[i].name),
                ),
              ],
            ],
          ),
        ),
        Expanded(child: _buildContent()),
      ],
    );
  }

  Widget _buildSignInView(OneDriveAuthState authState) {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Icon(Icons.cloud_outlined, size: 64, color: Colors.grey),
          const SizedBox(height: 16),
          const Text('Sign in to OneDrive to browse your photos'),
          const SizedBox(height: 24),
          FilledButton.icon(
            icon: const Icon(Icons.login),
            label: authState.isLoading
                ? const SizedBox(
                    width: 20,
                    height: 20,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Text('Sign in with Microsoft'),
            onPressed: authState.isLoading
                ? null
                : () => ref.read(oneDriveAuthStateProvider.notifier).signIn(),
          ),
        ],
      ),
    );
  }

  Widget _buildContent() {
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }

    if (_error != null) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text('Error: $_error'),
            const SizedBox(height: 16),
            FilledButton(
              onPressed: () => _loadFolder(_currentFolderId),
              child: const Text('Retry'),
            ),
          ],
        ),
      );
    }

    if (_items == null || _items!.isEmpty) {
      return const Center(child: Text('Empty folder'));
    }

    return GridView.builder(
      padding: const EdgeInsets.all(8),
      gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
        crossAxisCount: 3,
        crossAxisSpacing: 8,
        mainAxisSpacing: 8,
        childAspectRatio: 0.85,
      ),
      itemCount: _items!.length,
      itemBuilder: (context, index) {
        final item = _items![index];
        return _buildItemTile(item);
      },
    );
  }

  Widget _buildItemTile(OneDriveItem item) {
    return GestureDetector(
      onTap: () {
        if (item.isFolder) {
          _navigateInto(item);
        } else if (item.isPhoto) {
          _selectPhoto(item);
        }
      },
      child: Column(
        children: [
          Expanded(
            child: ClipRRect(
              borderRadius: BorderRadius.circular(8),
              child: item.isFolder
                  ? Container(
                      color: Colors.blueGrey[800],
                      child: const Center(
                        child: Icon(Icons.folder, size: 48, color: Colors.blue),
                      ),
                    )
                  : _buildPhotoThumbnail(item),
            ),
          ),
          const SizedBox(height: 4),
          Text(
            item.name,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: const TextStyle(fontSize: 12),
          ),
        ],
      ),
    );
  }

  Widget _buildPhotoThumbnail(OneDriveItem item) {
    final url = _thumbnailUrls[item.id];
    if (url != null) {
      return CachedNetworkImage(
        imageUrl: url,
        fit: BoxFit.cover,
        placeholder: (_, _) => Container(
          color: Colors.grey[850],
          child: const Center(
            child: SizedBox(
              width: 24, height: 24,
              child: CircularProgressIndicator(strokeWidth: 2),
            ),
          ),
        ),
        errorWidget: (_, _, _) => Container(
          color: Colors.grey[850],
          child: const Icon(Icons.broken_image, color: Colors.grey),
        ),
      );
    }

    return Container(
      color: Colors.grey[850],
      child: const Center(
        child: SizedBox(
          width: 24, height: 24,
          child: CircularProgressIndicator(strokeWidth: 2),
        ),
      ),
    );
  }
}

class _BreadcrumbEntry {
  final String id;
  final String name;
  const _BreadcrumbEntry(this.id, this.name);
}
