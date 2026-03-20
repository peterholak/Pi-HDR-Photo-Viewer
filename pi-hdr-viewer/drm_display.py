"""DRM/KMS display management for Raspberry Pi 5 HDMI HDR output.

Uses ctypes to call libdrm directly. Manages connectors, CRTCs,
framebuffers, and HDR metadata InfoFrames.
"""

import ctypes
import ctypes.util
import mmap
import os
import struct
from dataclasses import dataclass
from typing import Optional

# ── libdrm ctypes bindings ──────────────────────────────────────────────

_libdrm_path = ctypes.util.find_library("drm")
if _libdrm_path is None:
    _libdrm_path = "libdrm.so.2"
_libdrm = ctypes.CDLL(_libdrm_path, use_errno=True)


# ── DRM ioctl structures ───────────────────────────────────────────────

DRM_IOCTL_BASE = ord("d")


def _DRM_IOWR(nr, size):
    return (0xC0000000 | (size << 16) | (DRM_IOCTL_BASE << 8) | nr)


def _DRM_IOCTL(nr, size):
    return (0xC0000000 | (size << 16) | (DRM_IOCTL_BASE << 8) | nr)


# DRM mode constants
DRM_MODE_CONNECTED = 1
DRM_MODE_PAGE_FLIP_EVENT = 0x01

# Pixel formats (fourcc)
DRM_FORMAT_XRGB8888 = 0x34325258  # XR24
DRM_FORMAT_XRGB2101010 = 0x30335258  # XR30


class drm_mode_card_res(ctypes.Structure):
    _fields_ = [
        ("fb_id_ptr", ctypes.c_uint64),
        ("crtc_id_ptr", ctypes.c_uint64),
        ("connector_id_ptr", ctypes.c_uint64),
        ("encoder_id_ptr", ctypes.c_uint64),
        ("count_fbs", ctypes.c_uint32),
        ("count_crtcs", ctypes.c_uint32),
        ("count_connectors", ctypes.c_uint32),
        ("count_encoders", ctypes.c_uint32),
        ("min_width", ctypes.c_uint32),
        ("max_width", ctypes.c_uint32),
        ("min_height", ctypes.c_uint32),
        ("max_height", ctypes.c_uint32),
    ]


class drm_mode_get_connector(ctypes.Structure):
    _fields_ = [
        ("encoders_ptr", ctypes.c_uint64),
        ("modes_ptr", ctypes.c_uint64),
        ("props_ptr", ctypes.c_uint64),
        ("prop_values_ptr", ctypes.c_uint64),
        ("count_modes", ctypes.c_uint32),
        ("count_props", ctypes.c_uint32),
        ("count_encoders", ctypes.c_uint32),
        ("encoder_id", ctypes.c_uint32),
        ("connector_id", ctypes.c_uint32),
        ("connector_type", ctypes.c_uint32),
        ("connector_type_id", ctypes.c_uint32),
        ("connection", ctypes.c_uint32),
        ("mm_width", ctypes.c_uint32),
        ("mm_height", ctypes.c_uint32),
        ("subpixel", ctypes.c_uint32),
        ("pad", ctypes.c_uint32),
    ]


class drm_mode_modeinfo(ctypes.Structure):
    _fields_ = [
        ("clock", ctypes.c_uint32),
        ("hdisplay", ctypes.c_uint16),
        ("hsync_start", ctypes.c_uint16),
        ("hsync_end", ctypes.c_uint16),
        ("htotal", ctypes.c_uint16),
        ("hskew", ctypes.c_uint16),
        ("vdisplay", ctypes.c_uint16),
        ("vsync_start", ctypes.c_uint16),
        ("vsync_end", ctypes.c_uint16),
        ("vtotal", ctypes.c_uint16),
        ("vscan", ctypes.c_uint16),
        ("vrefresh", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("type", ctypes.c_uint32),
        ("name", ctypes.c_char * 32),
    ]


class drm_mode_get_encoder(ctypes.Structure):
    _fields_ = [
        ("encoder_id", ctypes.c_uint32),
        ("encoder_type", ctypes.c_uint32),
        ("crtc_id", ctypes.c_uint32),
        ("possible_crtcs", ctypes.c_uint32),
        ("possible_clones", ctypes.c_uint32),
    ]


class drm_mode_create_dumb(ctypes.Structure):
    _fields_ = [
        ("height", ctypes.c_uint32),
        ("width", ctypes.c_uint32),
        ("bpp", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("handle", ctypes.c_uint32),
        ("pitch", ctypes.c_uint32),
        ("size", ctypes.c_uint64),
    ]


class drm_mode_map_dumb(ctypes.Structure):
    _fields_ = [
        ("handle", ctypes.c_uint32),
        ("pad", ctypes.c_uint32),
        ("offset", ctypes.c_uint64),
    ]


class drm_mode_destroy_dumb(ctypes.Structure):
    _fields_ = [
        ("handle", ctypes.c_uint32),
    ]


class drm_mode_fb_cmd(ctypes.Structure):
    _fields_ = [
        ("fb_id", ctypes.c_uint32),
        ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32),
        ("pitch", ctypes.c_uint32),
        ("bpp", ctypes.c_uint32),
        ("depth", ctypes.c_uint32),
        ("handle", ctypes.c_uint32),
    ]


class drm_mode_fb_cmd2(ctypes.Structure):
    _fields_ = [
        ("fb_id", ctypes.c_uint32),
        ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32),
        ("pixel_format", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("handles", ctypes.c_uint32 * 4),
        ("pitches", ctypes.c_uint32 * 4),
        ("offsets", ctypes.c_uint32 * 4),
        ("modifier", ctypes.c_uint64 * 4),
    ]


class drm_mode_crtc(ctypes.Structure):
    _fields_ = [
        ("set_connectors_ptr", ctypes.c_uint64),
        ("count_connectors", ctypes.c_uint32),
        ("crtc_id", ctypes.c_uint32),
        ("fb_id", ctypes.c_uint32),
        ("x", ctypes.c_uint32),
        ("y", ctypes.c_uint32),
        ("gamma_size", ctypes.c_uint32),
        ("mode_valid", ctypes.c_uint32),
        ("mode", drm_mode_modeinfo),
    ]


class drm_mode_get_property(ctypes.Structure):
    _fields_ = [
        ("values_ptr", ctypes.c_uint64),
        ("enum_blob_ptr", ctypes.c_uint64),
        ("prop_id", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("name", ctypes.c_char * 32),
        ("count_values", ctypes.c_uint32),
        ("count_enum_blobs", ctypes.c_uint32),
    ]


class drm_mode_connector_set_property(ctypes.Structure):
    _fields_ = [
        ("value", ctypes.c_uint64),
        ("prop_id", ctypes.c_uint32),
        ("connector_id", ctypes.c_uint32),
    ]


class drm_mode_obj_get_properties(ctypes.Structure):
    _fields_ = [
        ("props_ptr", ctypes.c_uint64),
        ("prop_values_ptr", ctypes.c_uint64),
        ("count_props", ctypes.c_uint32),
        ("obj_id", ctypes.c_uint32),
        ("obj_type", ctypes.c_uint32),
    ]


class drm_mode_obj_set_property(ctypes.Structure):
    _fields_ = [
        ("value", ctypes.c_uint64),
        ("prop_id", ctypes.c_uint32),
        ("obj_id", ctypes.c_uint32),
        ("obj_type", ctypes.c_uint32),
    ]


class drm_mode_create_blob(ctypes.Structure):
    _fields_ = [
        ("data", ctypes.c_uint64),
        ("length", ctypes.c_uint32),
        ("blob_id", ctypes.c_uint32),
    ]


class drm_mode_destroy_blob(ctypes.Structure):
    _fields_ = [
        ("blob_id", ctypes.c_uint32),
    ]


# ioctl numbers
DRM_IOCTL_MODE_GETRESOURCES = _DRM_IOWR(0xA0, ctypes.sizeof(drm_mode_card_res))
DRM_IOCTL_MODE_GETCONNECTOR = _DRM_IOWR(0xA7, ctypes.sizeof(drm_mode_get_connector))
DRM_IOCTL_MODE_GETENCODER = _DRM_IOWR(0xA6, ctypes.sizeof(drm_mode_get_encoder))
DRM_IOCTL_MODE_GETPROPERTY = _DRM_IOWR(0xAA, ctypes.sizeof(drm_mode_get_property))
DRM_IOCTL_MODE_CREATE_DUMB = _DRM_IOWR(0xB2, ctypes.sizeof(drm_mode_create_dumb))
DRM_IOCTL_MODE_MAP_DUMB = _DRM_IOWR(0xB3, ctypes.sizeof(drm_mode_map_dumb))
DRM_IOCTL_MODE_DESTROY_DUMB = _DRM_IOWR(0xB4, ctypes.sizeof(drm_mode_destroy_dumb))
DRM_IOCTL_MODE_ADDFB = _DRM_IOWR(0xAE, ctypes.sizeof(drm_mode_fb_cmd))
DRM_IOCTL_MODE_ADDFB2 = _DRM_IOWR(0xB8, ctypes.sizeof(drm_mode_fb_cmd2))
DRM_IOCTL_MODE_RMFB = _DRM_IOWR(0xAF, ctypes.sizeof(ctypes.c_uint32))
DRM_IOCTL_MODE_SETCRTC = _DRM_IOWR(0xA2, ctypes.sizeof(drm_mode_crtc))
DRM_IOCTL_MODE_GETCRTC = _DRM_IOWR(0xA1, ctypes.sizeof(drm_mode_crtc))
DRM_IOCTL_MODE_OBJ_GETPROPERTIES = _DRM_IOWR(0xB9, ctypes.sizeof(drm_mode_obj_get_properties))
DRM_IOCTL_MODE_OBJ_SETPROPERTY = _DRM_IOWR(0xBA, ctypes.sizeof(drm_mode_obj_set_property))
DRM_IOCTL_MODE_CREATEPROPBLOB = _DRM_IOWR(0xBD, ctypes.sizeof(drm_mode_create_blob))
DRM_IOCTL_MODE_DESTROYPROPBLOB = _DRM_IOWR(0xBE, ctypes.sizeof(drm_mode_destroy_blob))

# DRM client caps
class drm_set_client_cap(ctypes.Structure):
    _fields_ = [
        ("capability", ctypes.c_uint64),
        ("value", ctypes.c_uint64),
    ]

DRM_IOCTL_SET_CLIENT_CAP = _DRM_IOWR(0x0D, ctypes.sizeof(drm_set_client_cap))
DRM_CLIENT_CAP_UNIVERSAL_PLANES = 2
DRM_CLIENT_CAP_ATOMIC = 3

# Atomic modeset
class drm_mode_atomic(ctypes.Structure):
    _fields_ = [
        ("flags", ctypes.c_uint32),
        ("count_objs", ctypes.c_uint32),
        ("objs_ptr", ctypes.c_uint64),
        ("count_props_ptr", ctypes.c_uint64),
        ("props_ptr", ctypes.c_uint64),
        ("prop_values_ptr", ctypes.c_uint64),
        ("reserved", ctypes.c_uint64),
        ("user_data", ctypes.c_uint64),
    ]

DRM_IOCTL_MODE_ATOMIC = _DRM_IOWR(0xBC, ctypes.sizeof(drm_mode_atomic))
DRM_MODE_ATOMIC_ALLOW_MODESET = 0x0400
DRM_MODE_ATOMIC_NONBLOCK = 0x0200

# Plane get
class drm_mode_get_plane(ctypes.Structure):
    _fields_ = [
        ("plane_id", ctypes.c_uint32),
        ("crtc_id", ctypes.c_uint32),
        ("fb_id", ctypes.c_uint32),
        ("possible_crtcs", ctypes.c_uint32),
        ("gamma_size", ctypes.c_uint32),
        ("count_format_types", ctypes.c_uint32),
        ("format_type_ptr", ctypes.c_uint64),
    ]

class drm_mode_get_plane_res(ctypes.Structure):
    _fields_ = [
        ("plane_id_ptr", ctypes.c_uint64),
        ("count_planes", ctypes.c_uint32),
    ]

DRM_IOCTL_MODE_GETPLANERESOURCES = _DRM_IOWR(0xB5, ctypes.sizeof(drm_mode_get_plane_res))
DRM_IOCTL_MODE_GETPLANE = _DRM_IOWR(0xB6, ctypes.sizeof(drm_mode_get_plane))

# DRM object types
DRM_MODE_OBJECT_CONNECTOR = 0xC0C0C0C0
DRM_MODE_OBJECT_CRTC = 0xCCCCCCCC
DRM_MODE_OBJECT_PLANE = 0xEEEEEEEE

import fcntl


def _ioctl(fd, request, arg):
    ret = fcntl.ioctl(fd, request, arg)
    if ret < 0:
        errno = ctypes.get_errno()
        raise OSError(errno, f"ioctl 0x{request:x} failed: {os.strerror(errno)}")
    return ret


# ── HDR metadata struct (matches kernel's hdr_output_metadata) ──────────

def build_hdr_metadata_blob(
    eotf=2,  # PQ
    metadata_type=0,
    # BT.2020 primaries (x, y) scaled by 50000
    primaries=((17000, 39500), (8000, 15500), (1250, 500)),
    white_point=(15635, 16450),  # D65
    max_luminance=1000,
    min_luminance=1,  # in units of 0.0001 nits
    max_cll=1000,
    max_fall=400,
):
    """Build the binary blob for struct hdr_output_metadata.

    Layout (kernel UAPI):
      u32 metadata_type (always 0 for HDMI)
      struct hdr_metadata_infoframe:
        u8  eotf
        u8  metadata_type
        struct {
          u16 x, y   -- display_primaries[3]
        }
        struct { u16 x, y } white_point
        u16 max_display_mastering_luminance
        u16 min_display_mastering_luminance
        u16 max_cll
        u16 max_fall
    """
    buf = struct.pack("<I", 0)  # metadata_type = 0 (HDMI Static Type 1)
    buf += struct.pack("<BB", eotf, metadata_type)
    for px, py in primaries:
        buf += struct.pack("<HH", px, py)
    buf += struct.pack("<HH", white_point[0], white_point[1])
    buf += struct.pack("<HH", max_luminance, min_luminance)
    buf += struct.pack("<HH", max_cll, max_fall)
    # Pad to 32 bytes (sizeof(struct hdr_output_metadata) with alignment)
    buf += b"\x00" * (32 - len(buf))
    return buf


# ── Framebuffer wrapper ─────────────────────────────────────────────────

@dataclass
class Framebuffer:
    fd: int
    width: int
    height: int
    bpp: int
    pixel_format: int
    handle: int
    pitch: int
    size: int
    fb_id: int
    map_offset: int
    mm: Optional[mmap.mmap] = None

    def mmap_buffer(self):
        if self.mm is None:
            self.mm = mmap.mmap(self.fd, self.size, mmap.MAP_SHARED,
                                mmap.PROT_READ | mmap.PROT_WRITE,
                                offset=self.map_offset)
        return self.mm

    def close(self):
        if self.mm is not None:
            self.mm.close()
            self.mm = None
        # Remove framebuffer
        fb_id = ctypes.c_uint32(self.fb_id)
        try:
            _ioctl(self.fd, DRM_IOCTL_MODE_RMFB, fb_id)
        except OSError:
            pass
        # Destroy dumb buffer
        destroy = drm_mode_destroy_dumb(handle=self.handle)
        try:
            _ioctl(self.fd, DRM_IOCTL_MODE_DESTROY_DUMB, destroy)
        except OSError:
            pass


# ── DRM Display ─────────────────────────────────────────────────────────

class DRMDisplay:
    """Manages a DRM/KMS display output with HDR support."""

    def __init__(self, device_path=None):
        self.device_path = device_path
        self.fd = -1
        self.connector_id = 0
        self.crtc_id = 0
        self.plane_id = 0
        self.mode = None  # drm_mode_modeinfo
        self.current_fb: Optional[Framebuffer] = None
        self._hdr_blob_id = 0
        self._mode_blob_id = 0
        self._saved_crtc = None
        self._atomic = False

    def open(self):
        """Open DRM device and find connected HDMI output."""
        if self.device_path:
            paths = [self.device_path]
        else:
            paths = [f"/dev/dri/card{i}" for i in range(4)]

        for path in paths:
            try:
                self.fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)

                # Enable universal planes + atomic
                try:
                    cap = drm_set_client_cap(capability=DRM_CLIENT_CAP_UNIVERSAL_PLANES, value=1)
                    _ioctl(self.fd, DRM_IOCTL_SET_CLIENT_CAP, cap)
                    cap = drm_set_client_cap(capability=DRM_CLIENT_CAP_ATOMIC, value=1)
                    _ioctl(self.fd, DRM_IOCTL_SET_CLIENT_CAP, cap)
                    self._atomic = True
                except OSError:
                    self._atomic = False

                if self._find_hdmi_output():
                    print(f"Opened DRM device: {path} (atomic={'yes' if self._atomic else 'no'})")
                    return
                os.close(self.fd)
                self.fd = -1
            except OSError:
                continue

        raise RuntimeError("No connected HDMI output found on any DRM device")

    def _find_hdmi_output(self):
        """Find first connected HDMI connector and its CRTC."""
        # Get resource counts
        res = drm_mode_card_res()
        _ioctl(self.fd, DRM_IOCTL_MODE_GETRESOURCES, res)

        if res.count_connectors == 0 or res.count_crtcs == 0:
            return False

        # Allocate arrays and fetch again
        connector_ids = (ctypes.c_uint32 * res.count_connectors)()
        crtc_ids = (ctypes.c_uint32 * res.count_crtcs)()
        encoder_ids = (ctypes.c_uint32 * res.count_encoders)()

        res.connector_id_ptr = ctypes.addressof(connector_ids)
        res.crtc_id_ptr = ctypes.addressof(crtc_ids)
        res.encoder_id_ptr = ctypes.addressof(encoder_ids)
        _ioctl(self.fd, DRM_IOCTL_MODE_GETRESOURCES, res)

        # Find connected HDMI connector
        for i in range(res.count_connectors):
            conn = drm_mode_get_connector()
            conn.connector_id = connector_ids[i]
            _ioctl(self.fd, DRM_IOCTL_MODE_GETCONNECTOR, conn)

            if conn.connection != DRM_MODE_CONNECTED:
                continue

            # HDMI-A = 11, HDMI-B = 12 in DRM connector types
            # But we'll accept any connected connector
            if conn.count_modes == 0:
                continue

            # Get modes
            modes = (drm_mode_modeinfo * conn.count_modes)()
            conn.modes_ptr = ctypes.addressof(modes)

            # Get props (need to reallocate)
            props = (ctypes.c_uint32 * conn.count_props)() if conn.count_props else None
            prop_vals = (ctypes.c_uint64 * conn.count_props)() if conn.count_props else None
            if props:
                conn.props_ptr = ctypes.addressof(props)
                conn.prop_values_ptr = ctypes.addressof(prop_vals)

            encoders = (ctypes.c_uint32 * conn.count_encoders)()
            conn.encoders_ptr = ctypes.addressof(encoders)

            _ioctl(self.fd, DRM_IOCTL_MODE_GETCONNECTOR, conn)

            # Pick preferred mode or largest
            best_mode = None
            for j in range(conn.count_modes):
                m = modes[j]
                if m.type & 0x08:  # DRM_MODE_TYPE_PREFERRED
                    best_mode = m
                    break
            if best_mode is None and conn.count_modes > 0:
                best_mode = modes[0]

            if best_mode is None:
                continue

            # Find CRTC via encoder
            crtc_id = 0
            if conn.encoder_id:
                enc = drm_mode_get_encoder(encoder_id=conn.encoder_id)
                _ioctl(self.fd, DRM_IOCTL_MODE_GETENCODER, enc)
                crtc_id = enc.crtc_id

            if not crtc_id and conn.count_encoders > 0:
                for k in range(conn.count_encoders):
                    enc = drm_mode_get_encoder(encoder_id=encoders[k])
                    try:
                        _ioctl(self.fd, DRM_IOCTL_MODE_GETENCODER, enc)
                    except OSError:
                        continue
                    if enc.possible_crtcs:
                        # Use first possible CRTC
                        for ci in range(res.count_crtcs):
                            if enc.possible_crtcs & (1 << ci):
                                crtc_id = crtc_ids[ci]
                                break
                    if crtc_id:
                        break

            if not crtc_id:
                continue

            self.connector_id = connector_ids[i]
            self.crtc_id = crtc_id
            self.mode = best_mode

            # Save current CRTC state for restoration
            saved = drm_mode_crtc(crtc_id=self.crtc_id)
            try:
                _ioctl(self.fd, DRM_IOCTL_MODE_GETCRTC, saved)
                self._saved_crtc = saved
            except OSError:
                pass

            # Find primary plane for this CRTC
            self._find_primary_plane()

            mode_name = best_mode.name.decode("ascii", errors="replace").rstrip("\x00")
            print(f"Found connector {self.connector_id}, CRTC {self.crtc_id}, "
                  f"plane {self.plane_id}, "
                  f"mode {best_mode.hdisplay}x{best_mode.vdisplay}@{best_mode.vrefresh} ({mode_name})")
            return True

        return False

    def _find_primary_plane(self):
        """Find the primary plane associated with our CRTC."""
        plane_res = drm_mode_get_plane_res()
        _ioctl(self.fd, DRM_IOCTL_MODE_GETPLANERESOURCES, plane_res)

        if plane_res.count_planes == 0:
            return

        plane_ids = (ctypes.c_uint32 * plane_res.count_planes)()
        plane_res.plane_id_ptr = ctypes.addressof(plane_ids)
        _ioctl(self.fd, DRM_IOCTL_MODE_GETPLANERESOURCES, plane_res)

        # Get CRTC index for possible_crtcs bitmask
        res = drm_mode_card_res()
        _ioctl(self.fd, DRM_IOCTL_MODE_GETRESOURCES, res)
        crtc_ids = (ctypes.c_uint32 * res.count_crtcs)()
        res.crtc_id_ptr = ctypes.addressof(crtc_ids)
        connector_ids = (ctypes.c_uint32 * max(1, res.count_connectors))()
        res.connector_id_ptr = ctypes.addressof(connector_ids)
        encoder_ids = (ctypes.c_uint32 * max(1, res.count_encoders))()
        res.encoder_id_ptr = ctypes.addressof(encoder_ids)
        _ioctl(self.fd, DRM_IOCTL_MODE_GETRESOURCES, res)

        crtc_index = -1
        for i in range(res.count_crtcs):
            if crtc_ids[i] == self.crtc_id:
                crtc_index = i
                break

        for i in range(plane_res.count_planes):
            plane = drm_mode_get_plane(plane_id=plane_ids[i])
            _ioctl(self.fd, DRM_IOCTL_MODE_GETPLANE, plane)

            if crtc_index >= 0 and not (plane.possible_crtcs & (1 << crtc_index)):
                continue

            # Check if it's a primary plane via "type" property
            result = self._find_property_id(plane_ids[i], DRM_MODE_OBJECT_PLANE, "type")
            if result:
                _, plane_type = result
                if plane_type == 1:  # DRM_PLANE_TYPE_PRIMARY
                    self.plane_id = plane_ids[i]
                    return

        # Fallback: use first compatible plane
        for i in range(plane_res.count_planes):
            plane = drm_mode_get_plane(plane_id=plane_ids[i])
            _ioctl(self.fd, DRM_IOCTL_MODE_GETPLANE, plane)
            if crtc_index >= 0 and (plane.possible_crtcs & (1 << crtc_index)):
                self.plane_id = plane_ids[i]
                return

    @property
    def width(self):
        return self.mode.hdisplay if self.mode else 0

    @property
    def height(self):
        return self.mode.vdisplay if self.mode else 0

    def create_framebuffer(self, pixel_format=DRM_FORMAT_XRGB8888):
        """Create a dumb buffer framebuffer."""
        if pixel_format == DRM_FORMAT_XRGB2101010:
            bpp = 32
            depth = 30
        else:
            bpp = 32
            depth = 24

        # Create dumb buffer
        create = drm_mode_create_dumb()
        create.width = self.width
        create.height = self.height
        create.bpp = bpp
        _ioctl(self.fd, DRM_IOCTL_MODE_CREATE_DUMB, create)

        # Add framebuffer using addfb2 for pixel format control
        fb_cmd = drm_mode_fb_cmd2()
        fb_cmd.width = self.width
        fb_cmd.height = self.height
        fb_cmd.pixel_format = pixel_format
        fb_cmd.handles[0] = create.handle
        fb_cmd.pitches[0] = create.pitch
        fb_cmd.offsets[0] = 0
        _ioctl(self.fd, DRM_IOCTL_MODE_ADDFB2, fb_cmd)

        # Map for CPU access
        map_req = drm_mode_map_dumb(handle=create.handle)
        _ioctl(self.fd, DRM_IOCTL_MODE_MAP_DUMB, map_req)

        fb = Framebuffer(
            fd=self.fd,
            width=self.width,
            height=self.height,
            bpp=bpp,
            pixel_format=pixel_format,
            handle=create.handle,
            pitch=create.pitch,
            size=create.size,
            fb_id=fb_cmd.fb_id,
            map_offset=map_req.offset,
        )

        print(f"Created framebuffer: {self.width}x{self.height} "
              f"format=0x{pixel_format:08x} pitch={create.pitch} fb_id={fb_cmd.fb_id}")
        return fb

    def _create_mode_blob(self):
        """Create a blob for the current display mode."""
        mode_bytes = bytes(self.mode)
        blob = drm_mode_create_blob()
        data_buf = (ctypes.c_uint8 * len(mode_bytes))(*mode_bytes)
        blob.data = ctypes.addressof(data_buf)
        blob.length = len(mode_bytes)
        _ioctl(self.fd, DRM_IOCTL_MODE_CREATEPROPBLOB, blob)
        return blob.blob_id

    def _atomic_commit(self, props_by_obj, allow_modeset=True):
        """Perform an atomic commit with the given properties.

        props_by_obj: dict of {obj_id: [(prop_id, value), ...]}
        """
        obj_ids = []
        count_props_list = []
        prop_ids_list = []
        prop_values_list = []

        for obj_id, props in props_by_obj.items():
            obj_ids.append(obj_id)
            count_props_list.append(len(props))
            for pid, val in props:
                prop_ids_list.append(pid)
                prop_values_list.append(val)

        n_objs = len(obj_ids)
        n_props = len(prop_ids_list)

        objs = (ctypes.c_uint32 * n_objs)(*obj_ids)
        counts = (ctypes.c_uint32 * n_objs)(*count_props_list)
        pids = (ctypes.c_uint32 * n_props)(*prop_ids_list)
        pvals = (ctypes.c_uint64 * n_props)(*prop_values_list)

        req = drm_mode_atomic()
        req.flags = DRM_MODE_ATOMIC_ALLOW_MODESET if allow_modeset else 0
        req.count_objs = n_objs
        req.objs_ptr = ctypes.addressof(objs)
        req.count_props_ptr = ctypes.addressof(counts)
        req.props_ptr = ctypes.addressof(pids)
        req.prop_values_ptr = ctypes.addressof(pvals)

        _ioctl(self.fd, DRM_IOCTL_MODE_ATOMIC, req)

    def set_mode(self, fb: Framebuffer, hdr_blob_id=None):
        """Set CRTC mode with the given framebuffer.

        If hdr_blob_id is provided, also sets HDR metadata + max_bpc=10
        in the same atomic commit (required for modeset-triggering changes).
        """
        if self._atomic and self.plane_id:
            self._set_mode_atomic(fb, hdr_blob_id=hdr_blob_id)
        else:
            self._set_mode_legacy(fb)
        self.current_fb = fb
        print(f"Mode set: fb_id={fb.fb_id}")

    def _set_mode_legacy(self, fb: Framebuffer):
        conn_id = ctypes.c_uint32(self.connector_id)
        crtc = drm_mode_crtc()
        crtc.crtc_id = self.crtc_id
        crtc.fb_id = fb.fb_id
        crtc.set_connectors_ptr = ctypes.addressof(conn_id)
        crtc.count_connectors = 1
        crtc.mode = self.mode
        crtc.mode_valid = 1
        _ioctl(self.fd, DRM_IOCTL_MODE_SETCRTC, crtc)

    def _set_mode_atomic(self, fb: Framebuffer, hdr_blob_id=None):
        # Create mode blob
        if self._mode_blob_id:
            try:
                destroy = drm_mode_destroy_blob(blob_id=self._mode_blob_id)
                _ioctl(self.fd, DRM_IOCTL_MODE_DESTROYPROPBLOB, destroy)
            except OSError:
                pass
        self._mode_blob_id = self._create_mode_blob()

        props = {}

        # Connector: CRTC_ID + optional HDR properties
        conn_entries = []
        crtc_id_prop = self._get_prop_id(self.connector_id, DRM_MODE_OBJECT_CONNECTOR, "CRTC_ID")
        if crtc_id_prop:
            conn_entries.append((crtc_id_prop, self.crtc_id))

        if hdr_blob_id is not None:
            max_bpc_prop = self._get_prop_id(self.connector_id, DRM_MODE_OBJECT_CONNECTOR, "max bpc")
            hdr_prop = self._get_prop_id(self.connector_id, DRM_MODE_OBJECT_CONNECTOR, "HDR_OUTPUT_METADATA")
            if max_bpc_prop:
                conn_entries.append((max_bpc_prop, 10 if hdr_blob_id else 8))
            if hdr_prop:
                conn_entries.append((hdr_prop, hdr_blob_id))

        if conn_entries:
            props[self.connector_id] = conn_entries

        # CRTC: ACTIVE, MODE_ID
        active_prop = self._get_prop_id(self.crtc_id, DRM_MODE_OBJECT_CRTC, "ACTIVE")
        mode_prop = self._get_prop_id(self.crtc_id, DRM_MODE_OBJECT_CRTC, "MODE_ID")
        if active_prop:
            props.setdefault(self.crtc_id, []).append((active_prop, 1))
        if mode_prop:
            props.setdefault(self.crtc_id, []).append((mode_prop, self._mode_blob_id))

        # Plane: FB_ID, CRTC_ID, SRC_*, CRTC_*
        plane_props = {}
        for pname in ["FB_ID", "CRTC_ID", "SRC_X", "SRC_Y", "SRC_W", "SRC_H",
                       "CRTC_X", "CRTC_Y", "CRTC_W", "CRTC_H"]:
            pid = self._get_prop_id(self.plane_id, DRM_MODE_OBJECT_PLANE, pname)
            if pid:
                plane_props[pname] = pid

        plane_entries = []
        if "FB_ID" in plane_props:
            plane_entries.append((plane_props["FB_ID"], fb.fb_id))
        if "CRTC_ID" in plane_props:
            plane_entries.append((plane_props["CRTC_ID"], self.crtc_id))
        if "SRC_X" in plane_props:
            plane_entries.append((plane_props["SRC_X"], 0))
        if "SRC_Y" in plane_props:
            plane_entries.append((plane_props["SRC_Y"], 0))
        if "SRC_W" in plane_props:
            # SRC dimensions are in 16.16 fixed point
            plane_entries.append((plane_props["SRC_W"], fb.width << 16))
        if "SRC_H" in plane_props:
            plane_entries.append((plane_props["SRC_H"], fb.height << 16))
        if "CRTC_X" in plane_props:
            plane_entries.append((plane_props["CRTC_X"], 0))
        if "CRTC_Y" in plane_props:
            plane_entries.append((plane_props["CRTC_Y"], 0))
        if "CRTC_W" in plane_props:
            plane_entries.append((plane_props["CRTC_W"], self.width))
        if "CRTC_H" in plane_props:
            plane_entries.append((plane_props["CRTC_H"], self.height))

        if plane_entries:
            props[self.plane_id] = plane_entries

        self._atomic_commit(props)

    def _get_prop_id(self, obj_id, obj_type, prop_name):
        """Get just the property ID (not the value)."""
        result = self._find_property_id(obj_id, obj_type, prop_name)
        return result[0] if result else None

    def _find_property_id(self, obj_id, obj_type, prop_name):
        """Find a property ID by name on a DRM object."""
        get_props = drm_mode_obj_get_properties()
        get_props.obj_id = obj_id
        get_props.obj_type = obj_type
        _ioctl(self.fd, DRM_IOCTL_MODE_OBJ_GETPROPERTIES, get_props)

        if get_props.count_props == 0:
            return None

        prop_ids = (ctypes.c_uint32 * get_props.count_props)()
        prop_values = (ctypes.c_uint64 * get_props.count_props)()
        get_props.props_ptr = ctypes.addressof(prop_ids)
        get_props.prop_values_ptr = ctypes.addressof(prop_values)
        _ioctl(self.fd, DRM_IOCTL_MODE_OBJ_GETPROPERTIES, get_props)

        for i in range(get_props.count_props):
            prop = drm_mode_get_property(prop_id=prop_ids[i])
            _ioctl(self.fd, DRM_IOCTL_MODE_GETPROPERTY, prop)
            name = prop.name.decode("ascii", errors="replace").rstrip("\x00")
            if name == prop_name:
                return prop_ids[i], prop_values[i]

        return None

    def set_connector_property(self, prop_name, value):
        """Set a property on the HDMI connector (legacy path)."""
        result = self._find_property_id(self.connector_id, DRM_MODE_OBJECT_CONNECTOR, prop_name)
        if result is None:
            print(f"Warning: property '{prop_name}' not found on connector")
            return False

        prop_id, _ = result

        if self._atomic:
            self._atomic_commit({self.connector_id: [(prop_id, value)]})
        else:
            set_prop = drm_mode_obj_set_property()
            set_prop.value = value
            set_prop.prop_id = prop_id
            set_prop.obj_id = self.connector_id
            set_prop.obj_type = DRM_MODE_OBJECT_CONNECTOR
            _ioctl(self.fd, DRM_IOCTL_MODE_OBJ_SETPROPERTY, set_prop)

        print(f"Set connector property '{prop_name}' = {value}")
        return True

    def enable_hdr(self):
        """Enable HDR10 output: set max_bpc=10 and HDR_OUTPUT_METADATA.

        On atomic drivers, this only creates the blob. The actual property
        is set in the next set_mode() call via hdr_blob_id parameter.
        On legacy drivers, sets properties immediately.
        """
        # Build HDR metadata blob
        metadata = build_hdr_metadata_blob()
        blob = drm_mode_create_blob()
        data_buf = (ctypes.c_uint8 * len(metadata))(*metadata)
        blob.data = ctypes.addressof(data_buf)
        blob.length = len(metadata)
        _ioctl(self.fd, DRM_IOCTL_MODE_CREATEPROPBLOB, blob)
        self._hdr_blob_id = blob.blob_id
        print(f"HDR metadata blob created (blob_id={blob.blob_id})")

        if not self._atomic:
            self.set_connector_property("max bpc", 10)
            self.set_connector_property("HDR_OUTPUT_METADATA", blob.blob_id)
            print("HDR enabled (legacy)")

    def disable_hdr(self):
        """Disable HDR output: clear metadata and reset max_bpc."""
        max_bpc_prop = self._get_prop_id(self.connector_id, DRM_MODE_OBJECT_CONNECTOR, "max bpc")
        hdr_prop = self._get_prop_id(self.connector_id, DRM_MODE_OBJECT_CONNECTOR, "HDR_OUTPUT_METADATA")

        if self._atomic:
            # Clear HDR metadata and reset max_bpc via atomic commit
            conn_props = []
            if hdr_prop:
                conn_props.append((hdr_prop, 0))
            if max_bpc_prop:
                conn_props.append((max_bpc_prop, 8))
            if conn_props:
                try:
                    self._atomic_commit({self.connector_id: conn_props})
                except OSError as e:
                    print(f"Warning: atomic HDR disable failed: {e}")
        else:
            if hdr_prop:
                self.set_connector_property("HDR_OUTPUT_METADATA", 0)
            if max_bpc_prop:
                self.set_connector_property("max bpc", 8)

        # Destroy old blob
        if self._hdr_blob_id:
            destroy = drm_mode_destroy_blob(blob_id=self._hdr_blob_id)
            try:
                _ioctl(self.fd, DRM_IOCTL_MODE_DESTROYPROPBLOB, destroy)
            except OSError:
                pass
            self._hdr_blob_id = 0

        print("HDR disabled")

    def close(self):
        """Clean up DRM resources."""
        if self.fd < 0:
            return

        # Disable HDR (atomic commit with HDR_OUTPUT_METADATA=0, max_bpc=8)
        try:
            self.disable_hdr()
        except OSError as e:
            print(f"Warning: disable_hdr failed: {e}")

        # Restore saved CRTC (legacy only — atomic already did a modeset in disable_hdr)
        if not self._atomic and self._saved_crtc and self._saved_crtc.mode_valid:
            try:
                _ioctl(self.fd, DRM_IOCTL_MODE_SETCRTC, self._saved_crtc)
            except OSError:
                pass

        # Destroy mode blob
        if self._mode_blob_id:
            try:
                destroy = drm_mode_destroy_blob(blob_id=self._mode_blob_id)
                _ioctl(self.fd, DRM_IOCTL_MODE_DESTROYPROPBLOB, destroy)
            except OSError:
                pass
            self._mode_blob_id = 0

        if self.current_fb:
            self.current_fb.close()
            self.current_fb = None

        os.close(self.fd)
        self.fd = -1
        print("DRM display closed")


# ── Self-test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import numpy as np
    import time

    display = DRMDisplay()
    display.open()

    # Create 10-bit HDR framebuffer
    fb = display.create_framebuffer(DRM_FORMAT_XRGB2101010)
    mm = fb.mmap_buffer()

    # Fill with PQ gradient
    w, h = display.width, display.height
    pixels = np.zeros((h, w), dtype=np.uint32)

    for x in range(w):
        # Gradient from 0 to 1023 across screen width
        val = int(x * 1023 / (w - 1))
        r = val
        g = val
        b = val
        pixels[:, x] = (r << 20) | (g << 10) | b

    # Add color bars in bottom quarter
    bar_h = h // 4
    bar_w = w // 3
    for x in range(bar_w):
        val = int(x * 1023 / (bar_w - 1))
        # Red bar
        pixels[h - bar_h:, x] = (val << 20)
        # Green bar
        pixels[h - bar_h:, bar_w + x] = (val << 10)
        # Blue bar
        pixels[h - bar_h:, 2 * bar_w + x] = val

    mm.seek(0)
    mm.write(pixels.tobytes())

    # Enable HDR and display (combined for atomic)
    display.enable_hdr()
    display.set_mode(fb, hdr_blob_id=display._hdr_blob_id)

    print("\nHDR gradient displayed. Check TV OSD for HDR10 indicator.")
    print("Press Ctrl+C to exit.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    fb.close()
    display.close()
