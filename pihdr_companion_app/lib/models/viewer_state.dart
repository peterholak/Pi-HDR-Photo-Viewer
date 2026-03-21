class ViewerState {
  final String appState;
  final int currentIndex;
  final int totalPhotos;
  final bool slideshowActive;
  final double zoom;
  final double panCx;
  final double panCy;
  final String? currentFilename;
  final bool isUltraHdr;
  final bool hdrActive;
  final bool gainMapEnabled;

  const ViewerState({
    this.appState = 'GRID',
    this.currentIndex = 0,
    this.totalPhotos = 0,
    this.slideshowActive = false,
    this.zoom = 1.0,
    this.panCx = 0.5,
    this.panCy = 0.5,
    this.currentFilename,
    this.isUltraHdr = false,
    this.hdrActive = true,
    this.gainMapEnabled = true,
  });

  factory ViewerState.fromJson(Map<String, dynamic> json) {
    return ViewerState(
      appState: json['app_state'] as String? ?? 'GRID',
      currentIndex: json['current_index'] as int? ?? 0,
      totalPhotos: json['total_photos'] as int? ?? 0,
      slideshowActive: json['slideshow_active'] as bool? ?? false,
      zoom: (json['zoom'] as num?)?.toDouble() ?? 1.0,
      panCx: (json['pan_cx'] as num?)?.toDouble() ?? 0.5,
      panCy: (json['pan_cy'] as num?)?.toDouble() ?? 0.5,
      currentFilename: json['current_filename'] as String?,
      isUltraHdr: json['is_ultrahdr'] as bool? ?? false,
      hdrActive: json['hdr_active'] as bool? ?? true,
      gainMapEnabled: json['gain_map_enabled'] as bool? ?? true,
    );
  }
}
