"""WebSocket server for companion app communication.

Runs in a background daemon thread with its own asyncio event loop.
Communicates with the main thread via a thread-safe command queue
and a lock-protected viewport state (latest-wins for zoom/pan).
"""

import asyncio
import json
import queue
import threading
import logging

try:
    import websockets
    import websockets.asyncio.server
except ImportError:
    websockets = None

log = logging.getLogger(__name__)


class CompanionServer:
    """WebSocket server for companion app control of the viewer."""

    def __init__(self, port=8765, onedrive=None):
        self.port = port
        self.onedrive = onedrive  # OneDriveClient instance (optional)
        self._command_queue = queue.Queue()
        self._viewport_lock = threading.Lock()
        self._viewport = {"cx": 0.5, "cy": 0.5, "zoom": 1.0}
        self._viewport_seq = 0  # incremented on each update
        self._client = None
        self._loop = None
        self._thread = None

    def start(self):
        """Start the WebSocket server in a background daemon thread."""
        if websockets is None:
            log.warning("websockets not installed, companion server disabled")
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="ws-server")
        self._thread.start()

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())

    async def _serve(self):
        async with websockets.asyncio.server.serve(
            self._handle_client, "0.0.0.0", self.port,
            ping_interval=20, ping_timeout=10,
        ):
            log.info(f"Companion server listening on ws://0.0.0.0:{self.port}")
            await asyncio.Future()  # run forever

    async def _handle_client(self, ws):
        remote = ws.remote_address
        log.info(f"Companion connected: {remote}")

        # Disconnect previous client
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                pass

        self._client = ws
        self._command_queue.put({"type": "companion_connected"})

        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue

                msg_type = msg.get("type")

                if msg_type == "viewport":
                    # High-frequency: update shared state, don't queue
                    with self._viewport_lock:
                        self._viewport["cx"] = float(msg.get("cx", 0.5))
                        self._viewport["cy"] = float(msg.get("cy", 0.5))
                        self._viewport["zoom"] = float(msg.get("zoom", 1.0))
                        self._viewport_seq += 1
                elif msg_type in ("command", "get_state", "get_thumbnails",
                                  "config", "onedrive_browse", "onedrive_select",
                                  "onedrive_auth", "load_onedrive"):
                    self._command_queue.put(msg)
                else:
                    log.warning(f"Unknown message type: {msg_type}")

        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as e:
            log.error(f"WebSocket handler error: {e}")
        finally:
            if self._client is ws:
                self._client = None
            self._command_queue.put({"type": "companion_disconnected"})
            log.info(f"Companion disconnected: {remote}")

    # -- Main-thread interface --

    def get_command(self):
        """Non-blocking: get next command from queue. Returns dict or None."""
        try:
            return self._command_queue.get_nowait()
        except queue.Empty:
            return None

    def get_viewport(self):
        """Get latest viewport state. Returns (dict, seq)."""
        with self._viewport_lock:
            return dict(self._viewport), self._viewport_seq

    def broadcast_state(self, state_dict):
        """Send state update to connected client (thread-safe)."""
        if self._client is None or self._loop is None:
            return
        data = json.dumps(state_dict)
        asyncio.run_coroutine_threadsafe(self._send_text(data), self._loop)

    def send_binary(self, header_dict, data_bytes):
        """Send a JSON header frame followed by a binary data frame."""
        if self._client is None or self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(
            self._send_header_and_binary(header_dict, data_bytes), self._loop)

    async def _send_text(self, data):
        if self._client:
            try:
                await self._client.send(data)
            except Exception:
                pass

    async def _send_header_and_binary(self, header_dict, data_bytes):
        if self._client:
            try:
                await self._client.send(json.dumps(header_dict))
                await self._client.send(data_bytes)
            except Exception:
                pass

    # -- OneDrive async operations (dispatched to server thread's asyncio loop) --

    def request_folder_list(self, folder_id="root"):
        """Request OneDrive folder listing (async, result comes via command queue)."""
        if self.onedrive is None or self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(
            self._do_list_folder(folder_id), self._loop)

    async def _do_list_folder(self, folder_id):
        try:
            items = await self.onedrive.list_folder(folder_id)
            self._command_queue.put({
                "type": "onedrive_folder_result",
                "folder_id": folder_id,
                "items": items,
            })
        except Exception as e:
            log.error(f"OneDrive folder list failed: {e}")
            self._command_queue.put({
                "type": "onedrive_error",
                "message": str(e),
            })

    def request_thumbnails_onedrive(self, item_ids: list[str]):
        """Request OneDrive thumbnails (async, results come via command queue)."""
        if self.onedrive is None or self._loop is None:
            return
        for item_id in item_ids:
            asyncio.run_coroutine_threadsafe(
                self._do_get_thumbnail(item_id), self._loop)

    async def _do_get_thumbnail(self, item_id):
        try:
            jpeg_bytes = await self.onedrive.get_thumbnail(item_id)
            if jpeg_bytes:
                self._command_queue.put({
                    "type": "onedrive_thumbnail_ready",
                    "item_id": item_id,
                    "jpeg_bytes": jpeg_bytes,
                })
        except Exception as e:
            log.warning(f"OneDrive thumbnail failed for {item_id}: {e}")

    def request_download(self, item_id: str, filename: str):
        """Request OneDrive photo download (async, result via command queue)."""
        if self.onedrive is None or self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(
            self._do_download(item_id, filename), self._loop)

    async def _do_download(self, item_id, filename):
        async def progress_cb(frac):
            self._command_queue.put({
                "type": "onedrive_download_progress",
                "item_id": item_id,
                "progress": frac,
            })
            # Also broadcast to companion app
            self.broadcast_state({
                "type": "download_progress",
                "item_id": item_id,
                "progress": frac,
            })

        try:
            path = await self.onedrive.download_photo(item_id, filename, progress_cb)
            if path:
                self._command_queue.put({
                    "type": "onedrive_download_complete",
                    "item_id": item_id,
                    "filename": filename,
                    "path": path,
                })
                self.broadcast_state({
                    "type": "download_complete",
                    "item_id": item_id,
                })
            else:
                self._command_queue.put({
                    "type": "onedrive_error",
                    "message": f"Download failed: {filename}",
                })
        except Exception as e:
            log.error(f"OneDrive download failed: {e}")
            self._command_queue.put({
                "type": "onedrive_error",
                "message": str(e),
            })

    def request_device_code_auth(self):
        """Start OneDrive device code auth flow (async)."""
        if self.onedrive is None or self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._do_device_code_auth(), self._loop)

    async def _do_device_code_auth(self):
        try:
            flow = await self.onedrive.start_device_code_flow()
            self._command_queue.put({
                "type": "onedrive_device_code",
                "user_code": flow["user_code"],
                "verification_uri": flow["verification_uri"],
            })
            # Also send to companion app
            self.broadcast_state({
                "type": "device_code",
                "user_code": flow["user_code"],
                "verification_uri": flow["verification_uri"],
            })
            success = await self.onedrive.poll_device_code(
                flow["device_code"], flow["interval"])
            if success:
                await self.onedrive.ensure_user_info()
                self._command_queue.put({
                    "type": "onedrive_auth_complete",
                    "user": self.onedrive.user_display_name or "Unknown",
                })
                self.broadcast_state({
                    "type": "auth_complete",
                    "user": self.onedrive.user_display_name or "Unknown",
                })
            else:
                self._command_queue.put({
                    "type": "onedrive_error",
                    "message": "Device code auth failed",
                })
        except Exception as e:
            log.error(f"Device code auth error: {e}")
            self._command_queue.put({
                "type": "onedrive_error",
                "message": str(e),
            })
