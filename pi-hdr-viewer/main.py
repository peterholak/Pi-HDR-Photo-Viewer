#!/usr/bin/env python3
"""Pi HDR Viewer -- Fullscreen Ultra HDR photo viewer for Raspberry Pi 5.

Kiosk-style app using DRM/KMS for direct HDMI output.
Always-HDR mode: grid, viewer, and config all render to HDR10.

Starts in standby mode (WebSocket server only, no TV output).
TV activates when a companion app connects, deactivates on disconnect.

Usage:
    python main.py [--cpu] [--no-standby] [--port=PORT] [--client-id=ID] [photo_directory]

Options:
    --cpu           Force CPU (numpy) rendering pipeline instead of GPU
    --no-standby    Start with display active immediately (no companion app needed)
    --disable-hdmi  Disable HDMI output completely in standby (TV shows 'No Signal')
    --port=PORT     WebSocket server port for companion app (default: 8765)
    --client-id=ID  Azure AD client ID for OneDrive integration

Controls:
    Arrow keys  -- navigate grid / prev/next photo
    Enter       -- open selected photo
    Escape      -- back to grid / quit from grid
    Space       -- toggle slideshow
    C           -- open config screen
    D           -- toggle debug overlay (viewer)
    G           -- toggle gain map on/off (viewer)
    H           -- toggle TV HDR10/SDR (switches render pipeline too)
    M           -- show raw gain map as grayscale (viewer)
    Q           -- quit
"""

import fcntl
import logging
import os
import select
import signal
import struct
import sys
import time
from enum import Enum, auto

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from drm_display import DRMDisplay, DRM_FORMAT_XRGB2101010
from photo_source import PhotoSource
from grid_screen import GridScreen
from viewer_screen import ViewerScreen
from config_screen import AppConfig, ConfigScreen
from hdr_pipeline import process_sdr_to_xrgb2101010, process_sdr_native_to_xrgb2101010
from server import CompanionServer
from onedrive import OneDriveClient
from onedrive_screen import OneDriveScreen


# -- Linux input event constants --

INPUT_EVENT_SIZE = 24
INPUT_EVENT_FORMAT = "llHHi"

EV_KEY = 0x01

KEY_ESC = 1
KEY_Q = 16
KEY_H = 35
KEY_D = 32
KEY_G = 34
KEY_M = 50
KEY_O = 24
KEY_C = 46
KEY_ENTER = 28
KEY_SPACE = 57
KEY_UP = 103
KEY_LEFT = 105
KEY_RIGHT = 106
KEY_DOWN = 108
KEY_OK = 352
KEY_EXIT = 174
KEY_PLAYPAUSE = 164

KEY_PRESS = 1
KEY_REPEAT = 2


class AppState(Enum):
    STANDBY = auto()
    GRID = auto()
    VIEWER = auto()
    CONFIG = auto()
    ONEDRIVE_BROWSER = auto()


def find_keyboard_devices():
    """Find keyboard input devices under /dev/input/."""
    devices = []
    input_dir = "/dev/input"

    if not os.path.isdir(input_dir):
        return devices

    for name in sorted(os.listdir(input_dir)):
        if not name.startswith("event"):
            continue
        path = os.path.join(input_dir, name)
        try:
            event_num = name[5:]
            caps_path = f"/sys/class/input/event{event_num}/device/capabilities/key"
            if os.path.exists(caps_path):
                with open(caps_path) as f:
                    caps = f.read().strip()
                if len(caps) > 10:
                    devices.append(path)
        except (OSError, ValueError):
            continue

    if not devices:
        for name in sorted(os.listdir(input_dir)):
            if name.startswith("event"):
                devices.append(os.path.join(input_dir, name))

    return devices


CMD_TO_KEY = {
    "left": KEY_LEFT, "right": KEY_RIGHT, "up": KEY_UP, "down": KEY_DOWN,
    "enter": KEY_ENTER, "esc": KEY_ESC, "escape": KEY_ESC,
    "space": KEY_SPACE, "q": KEY_Q, "quit": KEY_Q,
    "n": KEY_RIGHT, "p": KEY_LEFT, "next": KEY_RIGHT, "prev": KEY_LEFT,
    "select": KEY_ENTER, "back": KEY_ESC, "slideshow": KEY_SPACE,
    "c": KEY_C, "config": KEY_C,
    "d": KEY_D, "debug": KEY_D,
    "g": KEY_G, "gainmap": KEY_G,
    "h": KEY_H, "hdr": KEY_H,
    "m": KEY_M, "map": KEY_M,
    "o": KEY_O, "onedrive": KEY_O,
}


def setup_cec():
    """Register as CEC playback device so TV remote events arrive via evdev."""
    import subprocess
    try:
        # Step 1: Register as playback device (gets a logical address)
        subprocess.run(
            ["cec-ctl", "--device", "/dev/cec0", "--playback"],
            capture_output=True, timeout=5,
        )
        # Step 2: Become active source (so TV routes remote to us)
        subprocess.run(
            ["cec-ctl", "--device", "/dev/cec0",
             "--active-source", "phys-addr=3.0.0.0"],
            capture_output=True, timeout=5,
        )
        print("CEC: registered as active source")
    except FileNotFoundError:
        print("CEC: cec-ctl not available, TV remote may not work")
    except subprocess.TimeoutExpired:
        print("CEC: setup timed out")


# EVIOCGRAB ioctl: _IOW('E', 0x90, int) = 0x40044590
EVIOCGRAB = 0x40044590


class InputHandler:
    """Reads keyboard events from /dev/input/event* and a command pipe."""

    def __init__(self, cmd_pipe_path=None, grab=False):
        self.fds = []
        self.files = []
        self._input_files = []  # keyboard device files (subset of self.files)
        self._grabbed = []  # file objects with exclusive grab
        self._pipe_file = None

        setup_cec()

        devices = find_keyboard_devices()
        for path in devices:
            try:
                f = open(path, "rb", buffering=0)
                self.fds.append(f.fileno())
                self.files.append(f)
                self._input_files.append(f)
                print(f"Opened input device: {path}")
            except PermissionError:
                print(f"Warning: no permission for {path} (run as root or add to 'input' group)")
            except OSError as e:
                print(f"Warning: could not open {path}: {e}")

        pipe_path = cmd_pipe_path or "/tmp/hdr-viewer-cmd"
        try:
            try:
                os.unlink(pipe_path)
            except OSError:
                pass
            if not os.path.exists(pipe_path):
                old_umask = os.umask(0)
                os.mkfifo(pipe_path, 0o666)
                os.umask(old_umask)
            pipe_fd = os.open(pipe_path, os.O_RDONLY | os.O_NONBLOCK)
            self._pipe_file = os.fdopen(pipe_fd, "r")
            self.fds.append(self._pipe_file.fileno())
            self.files.append(self._pipe_file)
            print(f"Command pipe: {pipe_path}")
            print(f"  Send commands: echo right > {pipe_path}")
        except OSError as e:
            print(f"Warning: could not create command pipe: {e}")

        if not self.files:
            print("Warning: no input sources opened.")

    def poll(self, timeout=0.05) -> list[tuple[int, int]]:
        """Poll for key events. Returns list of (key_code, key_state)."""
        events = []
        if not self.fds:
            time.sleep(timeout)
            return events

        ready, _, _ = select.select(self.fds, [], [], timeout)
        for fd in ready:
            idx = self.fds.index(fd)
            f = self.files[idx]

            if f is self._pipe_file:
                try:
                    line = f.readline()
                    if line:
                        cmd = line.strip().lower()
                        if cmd in CMD_TO_KEY:
                            events.append((CMD_TO_KEY[cmd], KEY_PRESS))
                    else:
                        self._reopen_pipe(f)
                except OSError:
                    pass
            else:
                try:
                    data = f.read(INPUT_EVENT_SIZE)
                    if data and len(data) == INPUT_EVENT_SIZE:
                        _, _, ev_type, ev_code, ev_value = struct.unpack(
                            INPUT_EVENT_FORMAT, data)
                        if ev_type == EV_KEY and ev_value in (KEY_PRESS, KEY_REPEAT):
                            events.append((ev_code, ev_value))
                except OSError:
                    pass

        return events

    def _reopen_pipe(self, old_file):
        """Reopen the command pipe after EOF (writer disconnected)."""
        idx = self.files.index(old_file)
        pipe_path = "/tmp/hdr-viewer-cmd"
        try:
            old_file.close()
        except OSError:
            pass
        try:
            pipe_fd = os.open(pipe_path, os.O_RDONLY | os.O_NONBLOCK)
            new_file = os.fdopen(pipe_fd, "r")
            self.fds[idx] = new_file.fileno()
            self.files[idx] = new_file
            self._pipe_file = new_file
        except OSError:
            self.fds.pop(idx)
            self.files.pop(idx)
            self._pipe_file = None

    def grab(self):
        """Grab exclusive access to input devices (prevents console key leakage)."""
        for f in self._input_files:
            if f not in self._grabbed:
                try:
                    fcntl.ioctl(f.fileno(), EVIOCGRAB, 1)
                    self._grabbed.append(f)
                except OSError:
                    pass

    def ungrab(self):
        """Release exclusive access to input devices."""
        for f in self._grabbed:
            try:
                fcntl.ioctl(f.fileno(), EVIOCGRAB, 0)
            except OSError:
                pass
        self._grabbed.clear()

    def close(self):
        for f in self._grabbed:
            try:
                fcntl.ioctl(f.fileno(), EVIOCGRAB, 0)
            except OSError:
                pass
        self._grabbed.clear()
        for f in self.files:
            try:
                f.close()
            except OSError:
                pass
        self.files.clear()
        self.fds.clear()
        try:
            os.unlink("/tmp/hdr-viewer-cmd")
        except OSError:
            pass


# -- Toast notification --

TOAST_DURATION = 2.0


def _find_toast_font():
    for fp in ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
               "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
               "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
               "/usr/share/fonts/TTF/DejaVuSans.ttf"]:
        try:
            return ImageFont.truetype(fp, 60)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def render_toast(message: str, width: int, font: ImageFont.ImageFont,
                 hdr_mode: bool) -> np.ndarray:
    """Render a toast bar (~50px tall) centered at the top of the screen.

    Returns XRGB2101010 pixels of shape (bar_height, width).
    """
    bar_height = 100
    img = Image.new("RGB", (width, bar_height), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Measure text to center it
    bbox = draw.textbbox((0, 0), message, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    # Draw dark background pill
    pad_x = 40
    pad_y = 12
    pill_x0 = (width - tw) // 2 - pad_x
    pill_x1 = (width + tw) // 2 + pad_x
    pill_y0 = (bar_height - th) // 2 - pad_y
    pill_y1 = (bar_height + th) // 2 + pad_y
    draw.rounded_rectangle([pill_x0, pill_y0, pill_x1, pill_y1],
                           radius=16, fill=(40, 40, 40))
    # Draw text
    tx = (width - tw) // 2
    ty = (bar_height - th) // 2
    draw.text((tx, ty), message, fill=(230, 230, 230), font=font)

    if hdr_mode:
        return process_sdr_to_xrgb2101010(img)
    else:
        return process_sdr_native_to_xrgb2101010(img)


def main():
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

    # Parse arguments
    force_cpu = "--cpu" in sys.argv
    no_standby = "--no-standby" in sys.argv
    disable_hdmi = "--disable-hdmi" in sys.argv
    server_port = 8765
    client_id = None
    for a in sys.argv[1:]:
        if a.startswith("--port="):
            server_port = int(a.split("=", 1)[1])
        elif a.startswith("--client-id="):
            client_id = a.split("=", 1)[1]
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if args:
        photo_dir = args[0]
    else:
        candidates = [
            os.path.join(os.path.dirname(__file__), "..", "demo-pics"),
            os.path.join(os.path.dirname(__file__), "photos"),
            os.path.expanduser("~/photos"),
        ]
        photo_dir = next((d for d in candidates if os.path.isdir(d)), candidates[0])

    print(f"Photo directory: {photo_dir}")

    config = AppConfig.load()

    source = PhotoSource(photo_dir)
    if len(source) == 0:
        if no_standby:
            print("No photos found. Exiting.")
            sys.exit(1)
        else:
            print("No photos found (will wait for OneDrive or companion app).")

    input_handler = InputHandler()

    # OneDrive client (optional, needs --client-id)
    onedrive = None
    if client_id:
        onedrive = OneDriveClient(client_id)
        print(f"OneDrive: client_id={client_id[:8]}... "
              f"({'authenticated' if onedrive.is_authenticated else 'not signed in'})")

    # Start companion WebSocket server
    server = CompanionServer(port=server_port, onedrive=onedrive)
    server.start()
    last_viewport_seq = 0

    # -- Display state (None when in standby) --
    display = None
    gpu_ctx = None
    gpu = None
    grid = None
    viewer = None
    config_screen = None
    onedrive_screen = None
    fb = None
    toast_font = None

    state = AppState.STANDBY
    needs_render = False

    # Toast state
    toast_message = ""
    toast_time = 0.0

    def show_toast(msg: str):
        nonlocal toast_message, toast_time, needs_render
        toast_message = msg
        toast_time = time.monotonic()
        if display is not None:
            needs_render = True

    def activate_display():
        """Initialize DRM display, GPU, screens, and framebuffer."""
        nonlocal display, gpu_ctx, gpu, grid, viewer, config_screen
        nonlocal onedrive_screen, fb, toast_font, state, needs_render

        if state != AppState.STANDBY:
            return  # Already active

        print("Activating display...")
        input_handler.grab()
        if display is None:
            display = DRMDisplay()
            display.open()

        # GPU pipeline
        if not force_cpu:
            try:
                from gpu_context import GPUContext
                from gpu_pipeline import GPUPipeline
                gpu_ctx = GPUContext(display.fd)
                gpu = GPUPipeline(gpu_ctx, display.width, display.height)
                print("GPU: pipeline ready")
            except Exception as e:
                print(f"GPU: init failed ({e}), using CPU path")
                gpu_ctx = None
                gpu = None
        else:
            print("GPU: disabled (--cpu flag)")

        # Screen objects
        grid = GridScreen(source, display.width, display.height,
                         cols=config.grid_columns)
        viewer = ViewerScreen(source, display.width, display.height)
        viewer.slideshow_interval = config.slideshow_interval
        viewer.debug_mode = config.debug_overlay
        config_screen = ConfigScreen(config, display.width, display.height)

        if client_id:
            onedrive_screen = OneDriveScreen(display.width, display.height,
                                              cols=config.grid_columns)

        # Framebuffer — try 10-bit, fall back to 8-bit
        try:
            fb = display.create_framebuffer(DRM_FORMAT_XRGB2101010)
        except OSError:
            print("10-bit framebuffer not supported, falling back to 8-bit")
            fb = display.create_framebuffer()  # default XRGB8888
        fb.mmap_buffer()

        # HDR metadata (may not be supported on all drivers)
        try:
            display.enable_hdr()
            display.set_mode(fb, hdr_blob_id=display._hdr_blob_id)
        except (OSError, AttributeError):
            print("HDR metadata not supported, using SDR output")
            display._hdr_active = False
            display.set_mode(fb)

        toast_font = _find_toast_font()
        state = AppState.GRID
        needs_render = True
        print("Display active.")

    def deactivate_display():
        """Tear down display and all display-dependent objects."""
        nonlocal display, gpu_ctx, gpu, grid, viewer, config_screen
        nonlocal onedrive_screen, fb, toast_font, state, needs_render

        if state == AppState.STANDBY:
            return  # Already in standby

        print("Deactivating display...")
        input_handler.ungrab()
        if gpu:
            gpu.close()
            gpu = None
        if gpu_ctx:
            gpu_ctx.close()
            gpu_ctx = None
        if fb:
            fb.close()
            fb = None

        if disable_hdmi and display is not None:
            # Keep DRM fd open but disable HDMI output (TV shows 'No Signal')
            display.disable_output()
        else:
            if display is not None:
                display.close()
            display = None

        grid = None
        viewer = None
        config_screen = None
        onedrive_screen = None
        toast_font = None

        state = AppState.STANDBY
        needs_render = False
        print("Display deactivated, standby mode.")

    def enter_grid():
        nonlocal state, needs_render
        state = AppState.GRID
        needs_render = True

    def enter_viewer(photo_index):
        nonlocal state, needs_render
        state = AppState.VIEWER
        needs_render = True
        viewer.set_photo(photo_index)

    def enter_config():
        nonlocal state, needs_render
        state = AppState.CONFIG
        needs_render = True

    def leave_config():
        nonlocal state, needs_render
        state = AppState.GRID
        needs_render = True
        config.save()
        grid.set_columns(config.grid_columns)
        viewer.slideshow_interval = config.slideshow_interval
        viewer.debug_mode = config.debug_overlay

    def enter_onedrive():
        nonlocal state, needs_render
        if onedrive_screen is None:
            show_toast("OneDrive not configured (--client-id)")
            return
        state = AppState.ONEDRIVE_BROWSER
        needs_render = True
        if not onedrive.is_authenticated:
            # Start device code auth
            onedrive_screen.auth_prompt = None
            server.request_device_code_auth()
            onedrive_screen.loading = True
            onedrive_screen.loading_message = "Starting sign-in..."
        elif not onedrive_screen.items and not onedrive_screen.loading:
            # Load root folder
            onedrive_screen.loading = True
            onedrive_screen.loading_message = "Loading OneDrive..."
            server.request_folder_list("root")

    # Handle SIGTERM/SIGHUP for clean shutdown (e.g., pkill, SSH disconnect)
    def _signal_exit(signum, frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, _signal_exit)
    signal.signal(signal.SIGHUP, _signal_exit)

    # Map server command actions to key codes
    SERVER_ACTION_TO_KEY = {
        "next": KEY_RIGHT, "prev": KEY_LEFT,
        "slideshow_toggle": KEY_SPACE, "quit": KEY_Q,
        "grid": KEY_ESC, "back": KEY_ESC,
        "enter": KEY_ENTER, "onedrive": KEY_O,
        "debug": KEY_D, "gainmap": KEY_G, "hdr": KEY_H,
        "gainmap_view": KEY_M, "config": KEY_C,
    }

    def handle_server_commands():
        """Drain server command queue, translate to key events or direct actions."""
        nonlocal needs_render
        events = []
        while True:
            cmd = server.get_command()
            if cmd is None:
                break
            cmd_type = cmd.get("type")
            if cmd_type == "command":
                if state == AppState.STANDBY:
                    # In standby, only quit is accepted
                    action = cmd.get("action", "")
                    if action in ("quit", "q"):
                        events.append((KEY_Q, KEY_PRESS))
                    continue
                action = cmd.get("action", "")
                if action == "select" and "index" in cmd:
                    idx = int(cmd["index"])
                    if state == AppState.GRID:
                        enter_viewer(idx)
                    elif state == AppState.VIEWER:
                        viewer.set_photo(idx)
                        needs_render = True
                elif action in SERVER_ACTION_TO_KEY:
                    events.append((SERVER_ACTION_TO_KEY[action], KEY_PRESS))
            elif cmd_type == "get_state":
                broadcast_viewer_state()
            elif cmd_type == "get_thumbnails":
                send_thumbnails(cmd.get("start", 0), cmd.get("count", 20))
            elif cmd_type == "config":
                if viewer is not None:
                    key = cmd.get("key")
                    value = cmd.get("value")
                    if key == "slideshow_interval" and value is not None:
                        config.slideshow_interval = float(value)
                        viewer.slideshow_interval = config.slideshow_interval
                    elif key == "grid_columns" and value is not None:
                        config.grid_columns = int(value)
                        if grid:
                            grid.set_columns(config.grid_columns)
                        needs_render = True
                    config.save()
            elif cmd_type == "onedrive_auth":
                # Token injection from companion app
                if onedrive:
                    onedrive.set_tokens(
                        cmd.get("access_token", ""),
                        cmd.get("refresh_token", ""))
                    show_toast("OneDrive: signed in via companion")
            elif cmd_type == "onedrive_browse":
                folder_id = cmd.get("folder_id", "root")
                server.request_folder_list(folder_id)
            elif cmd_type == "onedrive_select":
                item_id = cmd.get("item_id", "")
                filename = cmd.get("filename", "photo.jpg")
                server.request_download(item_id, filename)
            elif cmd_type == "onedrive_folder_result":
                if onedrive_screen is not None:
                    onedrive_screen.set_items(cmd["items"])
                    needs_render = True
                    needed = onedrive_screen.visible_item_ids()
                    if needed:
                        server.request_thumbnails_onedrive(needed)
            elif cmd_type == "onedrive_thumbnail_ready":
                if onedrive_screen is not None:
                    onedrive_screen.set_thumbnail(cmd["item_id"], cmd["jpeg_bytes"])
                    needs_render = True
            elif cmd_type == "onedrive_device_code":
                if onedrive_screen is not None:
                    onedrive_screen.auth_prompt = {
                        "user_code": cmd["user_code"],
                        "verification_uri": cmd["verification_uri"],
                    }
                    onedrive_screen.loading = False
                    needs_render = True
            elif cmd_type == "onedrive_auth_complete":
                if onedrive_screen is not None:
                    onedrive_screen.auth_prompt = None
                    show_toast(f"Signed in as {cmd.get('user', 'Unknown')}")
                    onedrive_screen.loading = True
                    onedrive_screen.loading_message = "Loading OneDrive..."
                    server.request_folder_list("root")
                    needs_render = True
            elif cmd_type == "onedrive_download_complete":
                path = cmd.get("path")
                filename = cmd.get("filename", "")
                if path:
                    source.add_photo(path, filename)
                    if display is not None:
                        enter_viewer(len(source) - 1)
                    show_toast(f"Loaded: {filename}")
            elif cmd_type == "onedrive_download_progress":
                progress = cmd.get("progress", 0)
                show_toast(f"Downloading... {int(progress * 100)}%")
            elif cmd_type == "onedrive_error":
                show_toast(f"OneDrive: {cmd.get('message', 'Error')}")
                if onedrive_screen is not None:
                    onedrive_screen.loading = False
                needs_render = True
            elif cmd_type == "companion_connected":
                print("Companion connected")
                activate_display()
                show_toast("Companion connected")
                broadcast_viewer_state()
            elif cmd_type == "companion_disconnected":
                print("Companion disconnected")
                deactivate_display()
        return events

    def broadcast_viewer_state():
        """Send current state to companion app."""
        current_photo = None
        is_uhdr = False
        if state in (AppState.VIEWER, AppState.GRID) and len(source) > 0:
            idx = viewer.current_index if state == AppState.VIEWER and viewer else 0
            if grid and state == AppState.GRID:
                idx = grid.selected
            if 0 <= idx < len(source):
                current_photo = source[idx].filename
                is_uhdr = source[idx].is_ultrahdr
        vp, _ = server.get_viewport()
        server.broadcast_state({
            "type": "state",
            "app_state": state.name,
            "current_index": (viewer.current_index if viewer and state == AppState.VIEWER
                             else grid.selected if grid else 0),
            "total_photos": len(source),
            "slideshow_active": viewer.slideshow_active if viewer else False,
            "zoom": vp["zoom"],
            "pan_cx": vp["cx"],
            "pan_cy": vp["cy"],
            "current_filename": current_photo,
            "is_ultrahdr": is_uhdr,
            "hdr_active": display._hdr_active if display else False,
            "gain_map_enabled": viewer.gain_map_enabled if viewer else True,
            "onedrive_authed": onedrive.is_authenticated if onedrive else False,
            "onedrive_user": onedrive.user_display_name if onedrive else None,
        })

    def send_thumbnails(start, count):
        """Generate and send JPEG thumbnails to companion app."""
        import io
        end = min(start + count, len(source))
        for i in range(start, end):
            photo = source[i]
            try:
                thumb = photo.get_thumbnail((300, 300))
                buf = io.BytesIO()
                thumb.save(buf, format="JPEG", quality=80)
                jpeg_bytes = buf.getvalue()
                server.send_binary(
                    {"type": "thumbnail_header", "index": i,
                     "filename": photo.filename,
                     "is_ultrahdr": photo.is_ultrahdr,
                     "size": len(jpeg_bytes)},
                    jpeg_bytes)
            except Exception as e:
                print(f"  thumbnail error [{i}]: {e}")

    # -- Startup --

    if no_standby:
        activate_display()
        print("\nReady. Arrow keys: navigate, Enter: view, Esc: back, Q: quit")
        print("  C: config   D: debug   G: gain map   H: HDR/SDR   M: show gain map")
        if onedrive:
            print("  O: OneDrive browser")
    else:
        print("\nStandby mode. Waiting for companion app to connect...")

    print(f"  Companion server: ws://0.0.0.0:{server_port}")

    try:
        while True:
            if state == AppState.VIEWER and viewer and viewer.tick():
                needs_render = True

            # Check toast expiry (only when display active)
            if display is not None and toast_message:
                if (time.monotonic() - toast_time) >= TOAST_DURATION:
                    toast_message = ""
                    needs_render = True

            key_events = input_handler.poll(timeout=0.05)

            # Drain server command queue
            server_events = handle_server_commands()
            key_events.extend(server_events)

            # Check for viewport updates from companion app
            if state == AppState.VIEWER and viewer:
                vp, vp_seq = server.get_viewport()
                if vp_seq != last_viewport_seq:
                    last_viewport_seq = vp_seq
                    viewer.set_viewport(vp["cx"], vp["cy"], vp["zoom"])
                    needs_render = True

            for key_code, key_state in key_events:
                if state == AppState.STANDBY:
                    if key_code == KEY_Q:
                        raise KeyboardInterrupt

                elif state == AppState.GRID:
                    if key_code == KEY_LEFT:
                        grid.move_selection(-1, 0)
                        needs_render = True
                    elif key_code == KEY_RIGHT:
                        grid.move_selection(1, 0)
                        needs_render = True
                    elif key_code == KEY_UP:
                        grid.move_selection(0, -1)
                        needs_render = True
                    elif key_code == KEY_DOWN:
                        grid.move_selection(0, 1)
                        needs_render = True
                    elif key_code in (KEY_ENTER, KEY_OK):
                        if len(source) > 0:
                            enter_viewer(grid.selected)
                    elif key_code == KEY_C:
                        enter_config()
                    elif key_code == KEY_O:
                        enter_onedrive()
                    elif key_code == KEY_H:
                        new_hdr = not display._hdr_active
                        display.set_hdr_enabled(new_hdr)
                        viewer.set_tv_mode(new_hdr)
                        needs_render = True
                        show_toast(f"TV: {'HDR10' if new_hdr else 'SDR'}")
                    elif key_code in (KEY_ESC, KEY_EXIT, KEY_Q):
                        raise KeyboardInterrupt

                elif state == AppState.VIEWER:
                    if key_code in (KEY_ESC, KEY_EXIT):
                        enter_grid()
                    elif key_code == KEY_LEFT:
                        viewer.prev_photo()
                        needs_render = True
                    elif key_code == KEY_RIGHT:
                        viewer.next_photo()
                        needs_render = True
                    elif key_code in (KEY_SPACE, KEY_PLAYPAUSE):
                        viewer.toggle_slideshow()
                        show_toast(f"Slideshow: {'ON' if viewer.slideshow_active else 'OFF'}")
                    elif key_code == KEY_D:
                        viewer.toggle_debug()
                        needs_render = True
                        show_toast(f"Debug: {'ON' if viewer.debug_mode else 'OFF'}")
                    elif key_code == KEY_G:
                        viewer.toggle_gain_map()
                        needs_render = True
                        show_toast(f"Gain map: {'ON' if viewer.gain_map_enabled else 'OFF'}")
                    elif key_code == KEY_M:
                        viewer.toggle_gainmap_view()
                        needs_render = True
                        show_toast(f"Gain map view: {'ON' if viewer.show_gainmap_view else 'OFF'}")
                    elif key_code == KEY_H:
                        new_hdr = not display._hdr_active
                        display.set_hdr_enabled(new_hdr)
                        viewer.set_tv_mode(new_hdr)
                        needs_render = True
                        show_toast(f"TV: {'HDR10' if new_hdr else 'SDR'}")
                    elif key_code == KEY_Q:
                        raise KeyboardInterrupt

                elif state == AppState.CONFIG:
                    if key_code in (KEY_ESC, KEY_EXIT):
                        leave_config()
                    elif key_code == KEY_UP:
                        config_screen.move_selection(-1)
                        needs_render = True
                    elif key_code == KEY_DOWN:
                        config_screen.move_selection(1)
                        needs_render = True
                    elif key_code == KEY_LEFT:
                        config_screen.change_value(-1)
                        needs_render = True
                    elif key_code == KEY_RIGHT:
                        config_screen.change_value(1)
                        needs_render = True
                    elif key_code == KEY_H:
                        new_hdr = not display._hdr_active
                        display.set_hdr_enabled(new_hdr)
                        viewer.set_tv_mode(new_hdr)
                        needs_render = True
                        show_toast(f"TV: {'HDR10' if new_hdr else 'SDR'}")
                    elif key_code == KEY_Q:
                        raise KeyboardInterrupt

                elif state == AppState.ONEDRIVE_BROWSER and onedrive_screen is not None:
                    if key_code in (KEY_ESC, KEY_EXIT):
                        parent = onedrive_screen.navigate_back()
                        if parent is not None:
                            server.request_folder_list(parent)
                            needs_render = True
                        else:
                            enter_grid()
                    elif key_code == KEY_LEFT:
                        onedrive_screen.move_selection(-1, 0)
                        needs_render = True
                    elif key_code == KEY_RIGHT:
                        onedrive_screen.move_selection(1, 0)
                        needs_render = True
                    elif key_code == KEY_UP:
                        onedrive_screen.move_selection(0, -1)
                        needs_render = True
                    elif key_code == KEY_DOWN:
                        onedrive_screen.move_selection(0, 1)
                        needs_render = True
                    elif key_code in (KEY_ENTER, KEY_OK):
                        item = onedrive_screen.get_selected_item()
                        if item and item.is_folder:
                            onedrive_screen.navigate_into(item.id, item.name)
                            server.request_folder_list(item.id)
                            needs_render = True
                        elif item and item.is_photo:
                            show_toast(f"Downloading {item.name}...")
                            server.request_download(item.id, item.name)
                            needs_render = True
                    elif key_code == KEY_Q:
                        raise KeyboardInterrupt

            # Request OneDrive thumbnails for visible items after any changes
            if (state == AppState.ONEDRIVE_BROWSER and onedrive_screen is not None
                    and needs_render):
                needed = onedrive_screen.visible_item_ids()
                if needed:
                    server.request_thumbnails_onedrive(needed)

            # -- Render (only when display is active) --
            if display is not None and needs_render:
                t0 = time.monotonic()
                hdr_active = display._hdr_active

                if gpu:
                    # GPU render path
                    if state == AppState.VIEWER:
                        viewer.render_to_gpu(gpu)
                    elif state == AppState.GRID:
                        canvas = grid._render_canvas()
                        if hdr_active:
                            gpu.render_fullscreen(canvas)
                        else:
                            gpu.render_native_fullscreen(canvas)
                    elif state == AppState.CONFIG:
                        canvas = config_screen._render_canvas()
                        if hdr_active:
                            gpu.render_fullscreen(canvas)
                        else:
                            gpu.render_native_fullscreen(canvas)
                    elif state == AppState.ONEDRIVE_BROWSER and onedrive_screen is not None:
                        canvas = onedrive_screen._render_canvas()
                        if hdr_active:
                            gpu.render_fullscreen(canvas)
                        else:
                            gpu.render_native_fullscreen(canvas)
                    t1 = time.monotonic()

                    # Debug overlay: read back pixels, apply overlay, write
                    if state == AppState.VIEWER and viewer.debug_mode:
                        pixels = gpu.get_pixels()
                        viewer._render_debug_overlay(
                            pixels, viewer.source[viewer.current_index])
                        mm = fb.mmap_buffer()
                        mm.seek(0)
                        if fb.pitch == display.width * 4:
                            mm.write(pixels.tobytes())
                        else:
                            for y in range(display.height):
                                mm.seek(y * fb.pitch)
                                mm.write(pixels[y].tobytes())
                    else:
                        # Fast path: direct SSBO -> mmap copy
                        gpu.copy_to_framebuffer(fb)
                    t2 = time.monotonic()
                else:
                    # CPU render path (fallback when no GPU)
                    if state == AppState.GRID:
                        pixels = grid.render_hdr() if hdr_active else grid.render_native()
                    elif state == AppState.VIEWER:
                        pixels = viewer.render_hdr()
                    elif state == AppState.CONFIG:
                        pixels = config_screen.render_hdr() if hdr_active else config_screen.render_native()
                    elif state == AppState.ONEDRIVE_BROWSER and onedrive_screen is not None:
                        pixels = onedrive_screen.render_hdr() if hdr_active else onedrive_screen.render_native()
                    t1 = time.monotonic()

                    mm = fb.mmap_buffer()
                    mm.seek(0)
                    if fb.pitch == display.width * 4:
                        mm.write(pixels.tobytes())
                    else:
                        for y in range(display.height):
                            mm.seek(y * fb.pitch)
                            mm.write(pixels[y].tobytes())
                    t2 = time.monotonic()

                # Overlay toast if active
                if toast_message and (time.monotonic() - toast_time) < TOAST_DURATION:
                    toast_pixels = render_toast(toast_message, display.width,
                                                toast_font, hdr_active)
                    toast_h = toast_pixels.shape[0]
                    mm = fb.mmap_buffer()
                    mm.seek(0)
                    if fb.pitch == display.width * 4:
                        mm.write(toast_pixels.tobytes())
                    else:
                        for y in range(toast_h):
                            mm.seek(y * fb.pitch)
                            mm.write(toast_pixels[y].tobytes())

                print(f"  render: {t1-t0:.2f}s, write: {t2-t1:.2f}s")

                needs_render = False
                broadcast_viewer_state()

    except (KeyboardInterrupt, SystemExit):
        print("\nExiting...")

    finally:
        deactivate_display()
        # Close DRM fd if still open (disable-hdmi keeps it open in standby)
        if display is not None:
            display.close()
            display = None
        input_handler.close()


if __name__ == "__main__":
    main()
