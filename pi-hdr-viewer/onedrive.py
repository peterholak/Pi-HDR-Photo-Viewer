"""OneDrive client for browsing and downloading photos via Microsoft Graph API.

Supports two auth flows:
  1. Device code flow (standalone Pi, no companion app needed)
  2. Token injection (companion app shares its OAuth tokens)

All HTTP calls are async via aiohttp, intended to run in the server thread's asyncio loop.
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

try:
    import aiohttp
except ImportError:
    aiohttp = None

log = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
AUTH_BASE = "https://login.microsoftonline.com/common/oauth2/v2.0"
SCOPES = "Files.Read User.Read offline_access"

CONFIG_DIR = os.path.expanduser("~/.config/hdr-viewer")
TOKEN_PATH = os.path.join(CONFIG_DIR, "onedrive_tokens.json")
THUMB_CACHE_DIR = os.path.expanduser("~/.cache/hdr-viewer/thumbs")
PHOTO_CACHE_DIR = os.path.expanduser("~/.cache/hdr-viewer/photos")


@dataclass
class OneDriveItem:
    id: str
    name: str
    is_folder: bool = False
    is_photo: bool = False
    thumbnail_url: Optional[str] = None
    size: int = 0
    modified: str = ""


@dataclass
class OneDriveTokens:
    access_token: str = ""
    refresh_token: str = ""
    expires_at: float = 0.0

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at - 60  # 60s buffer

    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "OneDriveTokens":
        return cls(
            access_token=d.get("access_token", ""),
            refresh_token=d.get("refresh_token", ""),
            expires_at=d.get("expires_at", 0.0),
        )


class OneDriveClient:
    """Async OneDrive client for Microsoft Graph API."""

    def __init__(self, client_id: str):
        self.client_id = client_id
        self._tokens = OneDriveTokens()
        self._session: Optional[aiohttp.ClientSession] = None
        self._user_display_name: Optional[str] = None
        self._load_tokens()

    def _ensure_dirs(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        os.makedirs(THUMB_CACHE_DIR, exist_ok=True)
        os.makedirs(PHOTO_CACHE_DIR, exist_ok=True)

    def _load_tokens(self):
        try:
            with open(TOKEN_PATH) as f:
                self._tokens = OneDriveTokens.from_dict(json.load(f))
            log.info("OneDrive: loaded saved tokens")
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            pass

    def _save_tokens(self):
        self._ensure_dirs()
        with open(TOKEN_PATH, "w") as f:
            json.dump(self._tokens.to_dict(), f)

    @property
    def is_authenticated(self) -> bool:
        return bool(self._tokens.refresh_token)

    @property
    def user_display_name(self) -> Optional[str]:
        return self._user_display_name

    async def _get_session(self) -> "aiohttp.ClientSession":
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # -- Authentication --

    async def start_device_code_flow(self) -> dict:
        """Start device code flow. Returns {user_code, verification_uri} for display on TV."""
        session = await self._get_session()
        async with session.post(
            f"{AUTH_BASE}/devicecode",
            data={
                "client_id": self.client_id,
                "scope": SCOPES,
            },
        ) as resp:
            data = await resp.json()
            return {
                "user_code": data["user_code"],
                "verification_uri": data["verification_uri"],
                "device_code": data["device_code"],
                "interval": data.get("interval", 5),
                "expires_in": data.get("expires_in", 900),
            }

    async def poll_device_code(self, device_code: str, interval: int = 5) -> bool:
        """Poll for device code completion. Returns True when auth succeeds."""
        session = await self._get_session()
        while True:
            await asyncio.sleep(interval)
            async with session.post(
                f"{AUTH_BASE}/token",
                data={
                    "client_id": self.client_id,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": device_code,
                },
            ) as resp:
                data = await resp.json()
                if "access_token" in data:
                    self._tokens = OneDriveTokens(
                        access_token=data["access_token"],
                        refresh_token=data.get("refresh_token", ""),
                        expires_at=time.time() + data.get("expires_in", 3600),
                    )
                    self._save_tokens()
                    await self._fetch_user_info()
                    return True
                error = data.get("error", "")
                if error == "authorization_pending":
                    continue
                elif error == "slow_down":
                    interval += 5
                else:
                    log.error(f"Device code auth failed: {data}")
                    return False

    def set_tokens(self, access_token: str, refresh_token: str):
        """Inject tokens from companion app."""
        self._tokens = OneDriveTokens(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=time.time() + 3500,  # Assume ~1 hour
        )
        self._save_tokens()
        log.info("OneDrive: tokens injected from companion app")

    def sign_out(self):
        """Clear stored tokens."""
        self._tokens = OneDriveTokens()
        self._user_display_name = None
        try:
            os.unlink(TOKEN_PATH)
        except OSError:
            pass

    async def _ensure_valid_token(self):
        """Refresh access token if expired."""
        if not self._tokens.expired:
            return
        if not self._tokens.refresh_token:
            raise RuntimeError("No refresh token available")

        session = await self._get_session()
        async with session.post(
            f"{AUTH_BASE}/token",
            data={
                "client_id": self.client_id,
                "grant_type": "refresh_token",
                "refresh_token": self._tokens.refresh_token,
                "scope": SCOPES,
            },
        ) as resp:
            data = await resp.json()
            if "access_token" in data:
                self._tokens.access_token = data["access_token"]
                self._tokens.expires_at = time.time() + data.get("expires_in", 3600)
                if "refresh_token" in data:
                    self._tokens.refresh_token = data["refresh_token"]
                self._save_tokens()
            else:
                raise RuntimeError(f"Token refresh failed: {data}")

    async def _graph_get(self, path: str, **kwargs) -> dict:
        """Make an authenticated GET to Microsoft Graph."""
        await self._ensure_valid_token()
        session = await self._get_session()
        headers = {"Authorization": f"Bearer {self._tokens.access_token}"}
        async with session.get(f"{GRAPH_BASE}{path}", headers=headers, **kwargs) as resp:
            if resp.status == 401:
                # Token might have been invalidated, try refresh once
                self._tokens.expires_at = 0
                await self._ensure_valid_token()
                headers = {"Authorization": f"Bearer {self._tokens.access_token}"}
                async with session.get(f"{GRAPH_BASE}{path}", headers=headers, **kwargs) as resp2:
                    resp2.raise_for_status()
                    return await resp2.json()
            resp.raise_for_status()
            return await resp.json()

    async def _graph_get_bytes(self, path: str) -> bytes:
        """Make an authenticated GET and return raw bytes."""
        await self._ensure_valid_token()
        session = await self._get_session()
        headers = {"Authorization": f"Bearer {self._tokens.access_token}"}
        async with session.get(f"{GRAPH_BASE}{path}", headers=headers) as resp:
            resp.raise_for_status()
            return await resp.read()

    # -- User info --

    async def _fetch_user_info(self):
        try:
            data = await self._graph_get("/me")
            self._user_display_name = data.get("displayName", data.get("userPrincipalName"))
        except Exception as e:
            log.warning(f"Could not fetch user info: {e}")

    async def ensure_user_info(self):
        """Fetch user info if authenticated but name not yet loaded."""
        if self.is_authenticated and not self._user_display_name:
            await self._fetch_user_info()

    # -- Browsing --

    async def list_folder(self, folder_id: Optional[str] = None) -> list[OneDriveItem]:
        """List items in a OneDrive folder. None = root."""
        if folder_id is None or folder_id == "root":
            path = "/me/drive/root/children"
        else:
            path = f"/me/drive/items/{folder_id}/children"

        # Request thumbnails inline and filter for images + folders
        path += "?$select=id,name,folder,image,size,lastModifiedDateTime"
        path += "&$top=200"

        data = await self._graph_get(path)
        items = []
        for item in data.get("value", []):
            is_folder = "folder" in item
            is_photo = "image" in item
            # Only include folders and image files
            if is_folder or is_photo:
                items.append(OneDriveItem(
                    id=item["id"],
                    name=item["name"],
                    is_folder=is_folder,
                    is_photo=is_photo,
                    size=item.get("size", 0),
                    modified=item.get("lastModifiedDateTime", ""),
                ))
        return items

    async def get_thumbnail(self, item_id: str, size: str = "large") -> Optional[bytes]:
        """Get thumbnail JPEG bytes for an item. Returns cached version if available."""
        self._ensure_dirs()
        cache_path = os.path.join(THUMB_CACHE_DIR, f"{item_id}.jpg")

        # Check disk cache
        if os.path.exists(cache_path):
            with open(cache_path, "rb") as f:
                return f.read()

        # Fetch from Graph API
        try:
            jpeg_bytes = await self._graph_get_bytes(
                f"/me/drive/items/{item_id}/thumbnails/0/{size}/content")
            with open(cache_path, "wb") as f:
                f.write(jpeg_bytes)
            return jpeg_bytes
        except Exception as e:
            log.warning(f"Thumbnail fetch failed for {item_id}: {e}")
            return None

    async def download_photo(self, item_id: str, filename: str,
                             progress_callback=None) -> Optional[str]:
        """Download a full photo to local cache. Returns local file path."""
        self._ensure_dirs()
        cache_path = os.path.join(PHOTO_CACHE_DIR, filename)

        # Check if already cached
        if os.path.exists(cache_path):
            return cache_path

        try:
            # Get download URL
            data = await self._graph_get(f"/me/drive/items/{item_id}")
            download_url = data.get("@microsoft.graph.downloadUrl")
            if not download_url:
                log.error(f"No download URL for {item_id}")
                return None

            total_size = data.get("size", 0)

            # Download the file
            session = await self._get_session()
            async with session.get(download_url) as resp:
                resp.raise_for_status()
                downloaded = 0
                with open(cache_path + ".tmp", "wb") as f:
                    async for chunk in resp.content.iter_chunked(65536):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback and total_size > 0:
                            await progress_callback(downloaded / total_size)

            os.rename(cache_path + ".tmp", cache_path)
            return cache_path

        except Exception as e:
            log.error(f"Download failed for {item_id}: {e}")
            # Clean up partial download
            try:
                os.unlink(cache_path + ".tmp")
            except OSError:
                pass
            return None
