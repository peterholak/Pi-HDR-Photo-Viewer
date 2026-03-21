import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/onedrive_item.dart';
import '../services/onedrive_service.dart';

final oneDriveServiceProvider = Provider<OneDriveService>((ref) {
  return OneDriveService();
});

final oneDriveAuthStateProvider =
    NotifierProvider<OneDriveAuthNotifier, OneDriveAuthState>(
        OneDriveAuthNotifier.new);

class OneDriveAuthState {
  final bool isAuthenticated;
  final String? userName;
  final bool isLoading;

  const OneDriveAuthState({
    this.isAuthenticated = false,
    this.userName,
    this.isLoading = false,
  });
}

class OneDriveAuthNotifier extends Notifier<OneDriveAuthState> {
  @override
  OneDriveAuthState build() {
    return const OneDriveAuthState();
  }

  Future<void> initialize(String clientId) async {
    final service = ref.read(oneDriveServiceProvider);
    service.configure(clientId: clientId);
    state = const OneDriveAuthState(isLoading: true);
    await service.loadSavedTokens();
    state = OneDriveAuthState(
      isAuthenticated: service.isAuthenticated,
      userName: service.userName,
    );
  }

  Future<bool> signIn() async {
    final service = ref.read(oneDriveServiceProvider);
    state = OneDriveAuthState(
      isAuthenticated: state.isAuthenticated,
      userName: state.userName,
      isLoading: true,
    );
    final success = await service.signIn();
    state = OneDriveAuthState(
      isAuthenticated: service.isAuthenticated,
      userName: service.userName,
    );
    return success;
  }

  Future<void> signOut() async {
    final service = ref.read(oneDriveServiceProvider);
    await service.signOut();
    state = const OneDriveAuthState();
  }
}

final oneDriveFolderProvider =
    FutureProvider.family<List<OneDriveItem>, String?>((ref, folderId) async {
  final service = ref.read(oneDriveServiceProvider);
  return service.listFolder(folderId: folderId);
});
