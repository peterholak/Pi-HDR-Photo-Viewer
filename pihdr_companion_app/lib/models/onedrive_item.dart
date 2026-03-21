class OneDriveItem {
  final String id;
  final String name;
  final bool isFolder;
  final bool isPhoto;
  final String? thumbnailUrl;
  final int? size;
  final String? modified;

  const OneDriveItem({
    required this.id,
    required this.name,
    this.isFolder = false,
    this.isPhoto = false,
    this.thumbnailUrl,
    this.size,
    this.modified,
  });

  factory OneDriveItem.fromGraphJson(Map<String, dynamic> json) {
    return OneDriveItem(
      id: json['id'] as String,
      name: json['name'] as String,
      isFolder: json.containsKey('folder'),
      isPhoto: json.containsKey('image'),
      thumbnailUrl: null, // fetched separately
      size: json['size'] as int?,
      modified: json['lastModifiedDateTime'] as String?,
    );
  }
}
