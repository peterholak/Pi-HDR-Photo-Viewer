"""Ultra HDR JPEG decoder.

Extracts the gain map and XMP parameters from Ultra HDR (JPEG_R) files.

Ultra HDR file structure:
- Primary JPEG: standard SDR image with XMP metadata containing gain map params
- Secondary JPEG: gain map image, referenced via MPF (Multi-Picture Format) marker

XMP namespace: hdrgm: (http://ns.adobe.com/hdr-gainmap/1.0/)
"""

import io
import re
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional

from PIL import Image


@dataclass
class GainMapParams:
    """Parameters from XMP hdrgm namespace for HDR reconstruction."""
    gain_map_min: float = 0.0       # log2 of ratioMin (hdr_min_nits / sdr_nits)
    gain_map_max: float = 1.0       # log2 of ratioMax
    gamma: float = 1.0              # gain map encoding gamma
    offset_sdr: float = 1.0 / 64    # epsilonSdr
    offset_hdr: float = 1.0 / 64    # epsilonHdr
    hdr_capacity_min: float = 0.0   # log2 min display boost
    hdr_capacity_max: float = 1.0   # log2 max display boost (displayRatioForFullHdr)
    base_rendition_is_hdr: bool = False


@dataclass
class UltraHDRImage:
    """Decoded Ultra HDR image with SDR, gain map, and parameters."""
    sdr: Image.Image = None
    gain_map: Image.Image = None
    params: GainMapParams = field(default_factory=GainMapParams)
    width: int = 0
    height: int = 0


def _find_marker(data: bytes, marker_id: int, signature: bytes = b"") -> list[int]:
    """Find all JPEG APP markers with given ID and optional signature."""
    results = []
    i = 0
    while i < len(data) - 4:
        if data[i] != 0xFF:
            i += 1
            continue
        if data[i + 1] == marker_id:
            length = struct.unpack(">H", data[i + 2:i + 4])[0]
            segment_data = data[i + 4:i + 2 + length]
            if not signature or segment_data.startswith(signature):
                results.append(i)
            i += 2 + length
        elif data[i + 1] == 0xDA:  # SOS - start of scan, stop searching headers
            break
        else:
            if data[i + 1] in (0xD8, 0xD9, 0x00):  # SOI, EOI, stuffed byte
                i += 2
            else:
                length = struct.unpack(">H", data[i + 2:i + 4])[0]
                i += 2 + length
    return results


def _parse_mpf(data: bytes, mpf_offset: int) -> list[tuple[int, int]]:
    """Parse MPF (Multi-Picture Format) marker to find image offsets.

    Returns list of (offset, size) tuples for each image entry.
    """
    # MPF marker: FF E2 [length] "MPF\0" [MPF data]
    length = struct.unpack(">H", data[mpf_offset + 2:mpf_offset + 4])[0]
    mpf_data = data[mpf_offset + 4:mpf_offset + 2 + length]

    if not mpf_data.startswith(b"MPF\0"):
        raise ValueError("Not an MPF marker")

    # MPF data starts after "MPF\0" with a TIFF-like IFD structure
    mpf_payload = mpf_data[4:]

    # Determine byte order
    if mpf_payload[:2] == b"II":
        endian = "<"
    elif mpf_payload[:2] == b"MM":
        endian = ">"
    else:
        raise ValueError(f"Unknown MPF byte order: {mpf_payload[:2]}")

    # Read IFD offset (at byte 4 of the TIFF header)
    ifd_offset = struct.unpack(endian + "I", mpf_payload[4:8])[0]

    # Read IFD entries
    num_entries = struct.unpack(endian + "H", mpf_payload[ifd_offset:ifd_offset + 2])[0]

    images = []
    mp_entry_offset = None
    num_images = 0

    for i in range(num_entries):
        entry_start = ifd_offset + 2 + i * 12
        tag = struct.unpack(endian + "H", mpf_payload[entry_start:entry_start + 2])[0]
        typ = struct.unpack(endian + "H", mpf_payload[entry_start + 2:entry_start + 4])[0]
        count = struct.unpack(endian + "I", mpf_payload[entry_start + 4:entry_start + 8])[0]
        value_offset = struct.unpack(endian + "I", mpf_payload[entry_start + 8:entry_start + 12])[0]

        if tag == 0xB001:  # NumberOfImages
            num_images = value_offset  # value is inline for count=1
        elif tag == 0xB002:  # MP Entry (type=UNDEFINED, count=total bytes)
            mp_entry_offset = value_offset

    if mp_entry_offset is None:
        raise ValueError("No MP Entry found in MPF")

    # Derive number of images from byte count if NumberOfImages tag wasn't found
    if num_images == 0:
        # Find the 0xB002 entry again to get byte count
        for i in range(num_entries):
            entry_start = ifd_offset + 2 + i * 12
            tag = struct.unpack(endian + "H", mpf_payload[entry_start:entry_start + 2])[0]
            if tag == 0xB002:
                byte_count = struct.unpack(endian + "I", mpf_payload[entry_start + 4:entry_start + 8])[0]
                num_images = byte_count // 16
                break

    # Offsets in MP entries are relative to the TIFF header start
    # (i.e., after the "MPF\0" signature in the APP2 segment)
    # tiff_header_abs = mpf_offset + 4 (FFE2 + length) + 4 (MPF\0)
    tiff_header_abs = mpf_offset + 4 + 4

    # Each MP Entry is 16 bytes
    for i in range(num_images):
        entry_start = mp_entry_offset + i * 16
        if entry_start + 16 > len(mpf_payload):
            break
        img_size = struct.unpack(endian + "I", mpf_payload[entry_start + 4:entry_start + 8])[0]
        img_offset = struct.unpack(endian + "I", mpf_payload[entry_start + 8:entry_start + 12])[0]

        if i == 0:
            images.append((0, img_size))
        else:
            # Offset relative to TIFF header start in MPF segment
            abs_offset = tiff_header_abs + img_offset
            images.append((abs_offset, img_size))

    return images


def _parse_xmp_gainmap(data: bytes) -> Optional[GainMapParams]:
    """Extract gain map parameters from XMP metadata in JPEG data."""
    # Find XMP APP1 markers
    xmp_markers = _find_marker(data, 0xE1, b"http://ns.adobe.com/xap/1.0/\0")

    for marker_offset in xmp_markers:
        length = struct.unpack(">H", data[marker_offset + 2:marker_offset + 4])[0]
        segment = data[marker_offset + 4:marker_offset + 2 + length]

        # Skip the XMP signature
        xmp_sig = b"http://ns.adobe.com/xap/1.0/\0"
        if not segment.startswith(xmp_sig):
            continue
        xmp_xml = segment[len(xmp_sig):].decode("utf-8", errors="replace")

        # Check if this contains gain map data
        if "hdrgm" not in xmp_xml and "hdr-gainmap" not in xmp_xml and "hdr-gain-map" not in xmp_xml:
            continue

        return _parse_gainmap_xmp(xmp_xml)

    # Also check for GContainer/RecoveryMap style (older format)
    for marker_offset in xmp_markers:
        length = struct.unpack(">H", data[marker_offset + 2:marker_offset + 4])[0]
        segment = data[marker_offset + 4:marker_offset + 2 + length]
        xmp_sig = b"http://ns.adobe.com/xap/1.0/\0"
        if not segment.startswith(xmp_sig):
            continue
        xmp_xml = segment[len(xmp_sig):].decode("utf-8", errors="replace")
        if "RecoveryMap" in xmp_xml or "GContainer" in xmp_xml:
            # This is an older Ultra HDR format, use defaults
            return GainMapParams()

    return None


def _parse_gainmap_xmp(xmp_xml: str) -> GainMapParams:
    """Parse gain map parameters from XMP XML string."""
    params = GainMapParams()

    # Try structured XML parsing first
    try:
        root = ET.fromstring(xmp_xml)
        hdrgm_ns = [
            "http://ns.adobe.com/hdr-gainmap/1.0/",
            "http://ns.adobe.com/hdr-gain-map/1.0/",
        ]

        # Search for hdrgm attributes in RDF Description elements
        for desc in root.iter("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Description"):
            attrs = desc.attrib
            for key, value in attrs.items():
                # Strip namespace prefix
                local = key.split("}")[-1] if "}" in key else key.split(":")[-1]
                _set_gainmap_param(params, local, value)

            # Also check child elements
            for child in desc:
                tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag.split(":")[-1]
                if child.text:
                    _set_gainmap_param(params, tag, child.text.strip())

    except ET.ParseError:
        pass

    # Fallback: regex extraction for common attributes
    _regex_extract_params(xmp_xml, params)

    return params


def _set_gainmap_param(params: GainMapParams, name: str, value: str):
    """Set a gain map parameter by name."""
    name_lower = name.lower()
    try:
        if name_lower in ("gainmapmin", "gain_map_min"):
            params.gain_map_min = _parse_rational(value)
        elif name_lower in ("gainmapmax", "gain_map_max"):
            params.gain_map_max = _parse_rational(value)
        elif name_lower == "gamma":
            params.gamma = _parse_rational(value)
        elif name_lower in ("offsetsdr", "offset_sdr", "epsilonsdr"):
            params.offset_sdr = _parse_rational(value)
        elif name_lower in ("offsethdr", "offset_hdr", "epsilonhdr"):
            params.offset_hdr = _parse_rational(value)
        elif name_lower in ("hdrcapacitymin", "hdr_capacity_min"):
            params.hdr_capacity_min = _parse_rational(value)
        elif name_lower in ("hdrcapacitymax", "hdr_capacity_max", "displayratioforhdr"):
            params.hdr_capacity_max = _parse_rational(value)
        elif name_lower in ("baserenditionishdr", "base_rendition_is_hdr"):
            params.base_rendition_is_hdr = value.lower() in ("true", "1")
    except (ValueError, ZeroDivisionError):
        pass


def _parse_rational(value: str) -> float:
    """Parse a value that might be a rational number (e.g., '1/64')."""
    value = value.strip()
    if "/" in value:
        num, den = value.split("/", 1)
        return float(num) / float(den)
    return float(value)


def _regex_extract_params(xmp_xml: str, params: GainMapParams):
    """Fallback regex extraction for gain map parameters."""
    patterns = {
        "gain_map_min": r'hdrgm:GainMapMin="([^"]+)"',
        "gain_map_max": r'hdrgm:GainMapMax="([^"]+)"',
        "gamma": r'hdrgm:Gamma="([^"]+)"',
        "offset_sdr": r'hdrgm:OffsetSDR="([^"]+)"',
        "offset_hdr": r'hdrgm:OffsetHDR="([^"]+)"',
        "hdr_capacity_min": r'hdrgm:HDRCapacityMin="([^"]+)"',
        "hdr_capacity_max": r'hdrgm:HDRCapacityMax="([^"]+)"',
        "base_rendition_is_hdr": r'hdrgm:BaseRenditionIsHDR="([^"]+)"',
    }

    for param_name, pattern in patterns.items():
        m = re.search(pattern, xmp_xml)
        if m:
            _set_gainmap_param(params, param_name.replace("_", ""), m.group(1))


def _extract_gain_map_data(data: bytes) -> Optional[bytes]:
    """Find the gain map JPEG data within an Ultra HDR file.

    Tries MPF first, then falls back to scanning for the last SOI marker.
    """
    mpf_markers = _find_marker(data, 0xE2, b"MPF\0")

    if mpf_markers:
        try:
            images = _parse_mpf(data, mpf_markers[0])
            if len(images) >= 2:
                offset, size = images[1]
                if size == 0:
                    return data[offset:]
                gm = data[offset:offset + size]
                # Verify it starts with JPEG SOI
                if len(gm) >= 2 and gm[0] == 0xFF and gm[1] == 0xD8:
                    return gm
        except (ValueError, struct.error) as e:
            print(f"Warning: failed to parse MPF: {e}")

    # Fallback: find the LAST SOI marker (skip EXIF thumbnail SOIs in the middle)
    last_soi = -1
    i = 2  # skip first SOI at offset 0
    while i < len(data) - 1:
        if data[i] == 0xFF and data[i + 1] == 0xD8:
            last_soi = i
        i += 1

    if last_soi > 0:
        return data[last_soi:]

    return None


def decode(file_path: str) -> UltraHDRImage:
    """Decode an Ultra HDR JPEG file.

    Returns UltraHDRImage with SDR image, gain map, and reconstruction parameters.
    """
    with open(file_path, "rb") as f:
        data = f.read()

    result = UltraHDRImage()

    # Decode primary (SDR) image
    result.sdr = Image.open(io.BytesIO(data)).convert("RGB")
    result.width = result.sdr.width
    result.height = result.sdr.height

    # Extract gain map JPEG
    gm_data = _extract_gain_map_data(data)
    if gm_data:
        try:
            result.gain_map = Image.open(io.BytesIO(gm_data)).convert("L")
        except Exception as e:
            print(f"Warning: failed to decode gain map: {e}")

        # Check gain map image's XMP for parameters (Google Pixel format)
        gm_params = _parse_xmp_gainmap(gm_data)
        if gm_params:
            result.params = gm_params

    # If no params from gain map, check primary image XMP
    if result.params.gain_map_max == GainMapParams().gain_map_max:
        primary_params = _parse_xmp_gainmap(data)
        if primary_params:
            result.params = primary_params

    if result.gain_map is None:
        print("Warning: no gain map found, will use SDR only")

    return result


# ── Self-test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import os

    if len(sys.argv) > 1:
        paths = sys.argv[1:]
    else:
        demo_dir = os.path.join(os.path.dirname(__file__), "..", "demo-pics")
        if os.path.isdir(demo_dir):
            paths = [os.path.join(demo_dir, f) for f in sorted(os.listdir(demo_dir))
                     if f.lower().endswith((".jpg", ".jpeg"))]
        else:
            print("Usage: python ultrahdr.py <photo.jpg> [photo2.jpg ...]")
            sys.exit(1)

    for path in paths:
        print(f"\n{'='*60}")
        print(f"File: {path}")
        img = decode(path)
        print(f"SDR size: {img.sdr.size}")
        if img.gain_map:
            print(f"Gain map size: {img.gain_map.size}")
        else:
            print("Gain map: NOT FOUND")
        print(f"Parameters:")
        print(f"  gain_map_min:  {img.params.gain_map_min}")
        print(f"  gain_map_max:  {img.params.gain_map_max}")
        print(f"  gamma:         {img.params.gamma}")
        print(f"  offset_sdr:    {img.params.offset_sdr}")
        print(f"  offset_hdr:    {img.params.offset_hdr}")
        print(f"  hdr_cap_min:   {img.params.hdr_capacity_min}")
        print(f"  hdr_cap_max:   {img.params.hdr_capacity_max}")
        print(f"  base_is_hdr:   {img.params.base_rendition_is_hdr}")
