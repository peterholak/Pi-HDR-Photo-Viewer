"""HDR pixel pipeline: SDR + gain map → 10-bit PQ BT.2020 pixels.

All operations use NumPy for vectorized processing of full-resolution images.

Pipeline:
  1. sRGB EOTF → linear light
  2. Apply gain map → HDR linear
  3. sRGB → BT.2020 gamut transform
  4. Scale to PQ absolute luminance
  5. PQ OETF → perceptual 0..1
  6. Quantize to 10-bit
  7. Pack into XRGB2101010 uint32
"""

import numpy as np
from PIL import Image

from ultrahdr import UltraHDRImage

# sRGB to BT.2020 color matrix
SRGB_TO_BT2020 = np.array([
    [0.6274, 0.3293, 0.0433],
    [0.0691, 0.9195, 0.0114],
    [0.0164, 0.0880, 0.8956],
], dtype=np.float32)

# PQ constants (SMPTE ST 2084)
_PQ_M1 = 0.1593017578125
_PQ_M2 = 78.84375
_PQ_C1 = 0.8359375
_PQ_C2 = 18.8515625
_PQ_C3 = 18.6875

# SDR reference white in nits
SDR_WHITE_NITS = 203.0
# PQ peak luminance
PQ_PEAK_NITS = 10000.0


def _build_srgb_to_pq10_lut():
    """Build a 256×3 LUT: sRGB byte → 10-bit PQ BT.2020 per channel.

    Returns three uint32 arrays of shape (256,) containing pre-shifted
    10-bit values: R is shifted <<20, G <<10, B <<0.
    """
    srgb = np.arange(256, dtype=np.float64) / 255.0

    # sRGB EOTF → linear
    linear = np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)

    # For each input channel, compute BT.2020 contribution and PQ encode
    # Since sRGB→BT.2020 is a matrix multiply, each output channel is a
    # weighted sum of all 3 input channels. We can't do a per-channel 1D LUT
    # for the full pipeline. But we CAN precompute the contribution of each
    # input channel to each output channel.
    #
    # out_R = M[0,0]*in_R + M[0,1]*in_G + M[0,2]*in_B  (in linear)
    # out_G = M[1,0]*in_R + M[1,1]*in_G + M[1,2]*in_B
    # out_B = M[2,0]*in_R + M[2,1]*in_G + M[2,2]*in_B
    #
    # Then PQ(out * scale) for each output channel.
    # Since PQ is nonlinear, we can't separate the channels.
    #
    # However, for typical sRGB content the cross-channel terms are small
    # (BT.2020 R = 0.627*R + 0.329*G + 0.043*B), so as a fast approximation
    # that's still exact for grays, we use a 256³ → uint32 3D LUT would be
    # 64MB. Instead, use a 256×256×3 approach... that's still too big.
    #
    # Best approach: precompute per-sRGB-byte linear values, then do the
    # matrix multiply and PQ as vectorized operations on the image, but
    # use a LUT for the expensive sRGB→linear and PQ steps.
    #
    # Actually, the fastest: precompute sRGB byte → linear float LUT (256 entries),
    # and a linear→PQ 10-bit LUT (fine-grained, e.g. 4096 entries).

    return linear  # Return linear LUT, see below


# Precomputed LUTs for fast conversion
# sRGB byte [0..255] → linear float [0..1]
_SRGB_TO_LINEAR_LUT = np.zeros(256, dtype=np.float32)
for _i in range(256):
    _v = _i / 255.0
    _SRGB_TO_LINEAR_LUT[_i] = _v / 12.92 if _v <= 0.04045 else ((_v + 0.055) / 1.055) ** 2.4

# Linear float → PQ 10-bit code LUT (16384 entries for the SDR range)
# SDR range: linear [0, 1] maps to PQ input [0, 203/10000]
_LIN_TO_PQ_LUT_SIZE = 16384
_LIN_TO_PQ_LUT = np.zeros(_LIN_TO_PQ_LUT_SIZE, dtype=np.uint16)
_lin_vals = np.linspace(0.0, 1.0, _LIN_TO_PQ_LUT_SIZE, dtype=np.float64)
_pq_input = _lin_vals * (SDR_WHITE_NITS / PQ_PEAK_NITS)
_ym1 = np.power(np.clip(_pq_input, 0, 1), _PQ_M1)
_pq_out = np.power((_PQ_C1 + _PQ_C2 * _ym1) / (1.0 + _PQ_C3 * _ym1), _PQ_M2)
_LIN_TO_PQ_LUT = np.clip(_pq_out * 1023.0 + 0.5, 0, 1023).astype(np.uint16)

# Extended LUT for HDR range: linear [0, max_boost] → PQ 10-bit
# max_boost ~= 2^1.6 ≈ 3.0 (covers typical Ultra HDR gain)
_HDR_MAX_LINEAR = 4.0
_HDR_LIN_TO_PQ_LUT_SIZE = 32768
_hdr_lin_vals = np.linspace(0.0, _HDR_MAX_LINEAR, _HDR_LIN_TO_PQ_LUT_SIZE, dtype=np.float64)
_hdr_pq_input = _hdr_lin_vals * (SDR_WHITE_NITS / PQ_PEAK_NITS)
_hdr_ym1 = np.power(np.clip(_hdr_pq_input, 0, 1), _PQ_M1)
_hdr_pq_out = np.power((_PQ_C1 + _PQ_C2 * _hdr_ym1) / (1.0 + _PQ_C3 * _hdr_ym1), _PQ_M2)
_HDR_LIN_TO_PQ_LUT = np.clip(_hdr_pq_out * 1023.0 + 0.5, 0, 1023).astype(np.uint16)


def _linear_to_pq10_lut(linear: np.ndarray, hdr=False) -> np.ndarray:
    """Convert linear light to 10-bit PQ codes using precomputed LUT."""
    if hdr:
        indices = np.clip(linear * ((_HDR_LIN_TO_PQ_LUT_SIZE - 1) / _HDR_MAX_LINEAR),
                         0, _HDR_LIN_TO_PQ_LUT_SIZE - 1).astype(np.int32)
        return _HDR_LIN_TO_PQ_LUT[indices].astype(np.uint32)
    else:
        indices = np.clip(linear * (_LIN_TO_PQ_LUT_SIZE - 1),
                         0, _LIN_TO_PQ_LUT_SIZE - 1).astype(np.int32)
        return _LIN_TO_PQ_LUT[indices].astype(np.uint32)


def srgb_eotf(x: np.ndarray) -> np.ndarray:
    """sRGB electrical-to-optical transfer function (gamma decode).

    Input: [0, 1] sRGB
    Output: [0, 1] linear light
    """
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def pq_oetf(linear: np.ndarray) -> np.ndarray:
    """PQ (ST 2084) optical-to-electrical transfer function.

    Input: linear light normalized to [0, 1] where 1.0 = 10000 nits
    Output: PQ-encoded [0, 1]
    """
    # Clamp to valid range
    y = np.clip(linear, 0.0, 1.0)
    ym1 = np.power(y, _PQ_M1)
    return np.power((_PQ_C1 + _PQ_C2 * ym1) / (1.0 + _PQ_C3 * ym1), _PQ_M2)


def apply_gain_map(img: UltraHDRImage, display_boost: float = 0.0) -> np.ndarray:
    """Apply Ultra HDR gain map to produce HDR linear RGB.

    Args:
        img: Decoded UltraHDRImage with SDR, gain map, and params
        display_boost: log2 of display HDR capability.
                       0.0 = use hdr_capacity_max (full HDR).

    Returns:
        float32 array (H, W, 3) of linear-light HDR RGB in sRGB primaries.
    """
    p = img.params

    # SDR byte → linear via LUT (much faster than per-pixel sRGB EOTF)
    sdr_bytes = np.array(img.sdr)  # uint8
    sdr_linear = _SRGB_TO_LINEAR_LUT[sdr_bytes]  # float32

    if img.gain_map is None:
        return sdr_linear

    # Gain map to float32 [0, 1]
    gm = np.array(img.gain_map, dtype=np.float32) / 255.0

    # Resize gain map to match SDR if needed
    if gm.shape[:2] != sdr_linear.shape[:2]:
        gm_img = Image.fromarray((gm * 255).astype(np.uint8), mode="L")
        gm_img = gm_img.resize((sdr_linear.shape[1], sdr_linear.shape[0]), Image.BILINEAR)
        gm = np.array(gm_img, dtype=np.float32) / 255.0

    # Apply gain map gamma
    if p.gamma != 1.0:
        gm = np.power(gm, 1.0 / p.gamma)

    # Compute log2 gain: interpolate between min and max
    log2_gain = p.gain_map_min * (1.0 - gm) + p.gain_map_max * gm

    # Compute weight W based on display boost
    if display_boost <= 0.0:
        display_boost = p.hdr_capacity_max

    cap_range = p.hdr_capacity_max - p.hdr_capacity_min
    if cap_range > 0:
        w = np.clip((display_boost - p.hdr_capacity_min) / cap_range, 0.0, 1.0)
    else:
        w = 1.0

    # Apply weighted gain
    weighted_gain = log2_gain * w

    # Expand gain map to 3 channels if needed
    if weighted_gain.ndim == 2:
        weighted_gain = weighted_gain[:, :, np.newaxis]

    # HDR formula: (sdr_linear + offset_sdr) * 2^gain - offset_hdr
    hdr_linear = (sdr_linear + p.offset_sdr) * np.power(2.0, weighted_gain) - p.offset_hdr

    # Clamp to non-negative
    hdr_linear = np.maximum(hdr_linear, 0.0)

    return hdr_linear


def process_to_xrgb2101010(img: UltraHDRImage, target_width: int = 0,
                           target_height: int = 0) -> np.ndarray:
    """Full pipeline: Ultra HDR → 10-bit PQ BT.2020 packed pixels.

    Args:
        img: Decoded UltraHDRImage
        target_width: Output width (0 = use image width)
        target_height: Output height (0 = use image height)

    Returns:
        uint32 array (H, W) with XRGB2101010 packed pixels.
    """
    # Step 1: Apply gain map → HDR linear (sRGB primaries)
    hdr_linear = apply_gain_map(img)

    # Step 2: sRGB → BT.2020 gamut transform
    h, w = hdr_linear.shape[:2]
    bt2020_linear = hdr_linear.reshape(-1, 3) @ SRGB_TO_BT2020.T
    bt2020_linear = bt2020_linear.reshape(h, w, 3)

    # Step 3-5: Linear → PQ 10-bit via LUT, pack into XRGB2101010
    pq10 = _linear_to_pq10_lut(bt2020_linear, hdr=True)

    return (pq10[:, :, 0] << 20) | (pq10[:, :, 1] << 10) | pq10[:, :, 2]


def process_sdr_to_xrgb2101010(image: Image.Image) -> np.ndarray:
    """Convert an SDR PIL Image to PQ BT.2020 XRGB2101010 packed pixels.

    Uses precomputed LUTs for fast conversion.
    """
    if image.mode != "RGB":
        image = image.convert("RGB")

    arr = np.array(image)  # uint8 (H, W, 3)

    # sRGB byte → linear via LUT
    linear = _SRGB_TO_LINEAR_LUT[arr]  # float32 (H, W, 3)

    # sRGB → BT.2020 matrix multiply
    h, w = linear.shape[:2]
    bt2020 = linear.reshape(-1, 3) @ SRGB_TO_BT2020.T
    bt2020 = bt2020.reshape(h, w, 3)

    # Linear → PQ 10-bit via LUT
    pq10 = _linear_to_pq10_lut(bt2020)

    return (pq10[:, :, 0] << 20) | (pq10[:, :, 1] << 10) | pq10[:, :, 2]


def process_sdr_native_to_xrgb2101010(image: Image.Image) -> np.ndarray:
    """Pack raw sRGB 8-bit values into 10-bit XRGB2101010 with NO color transform.

    No EOTF, no BT.2020 matrix, no PQ. Just 8→10 bit expansion:
    (val << 2) | (val >> 6) maps 0→0, 255→1023.
    This is what a normal SDR viewer does, just bit-expanded to fit XRGB2101010.
    """
    if image.mode != "RGB":
        image = image.convert("RGB")

    arr = np.array(image, dtype=np.uint32)  # (H, W, 3)

    # 8→10 bit expansion: maps 0→0, 255→1023
    r10 = (arr[:, :, 0] << 2) | (arr[:, :, 0] >> 6)
    g10 = (arr[:, :, 1] << 2) | (arr[:, :, 1] >> 6)
    b10 = (arr[:, :, 2] << 2) | (arr[:, :, 2] >> 6)

    return (r10 << 20) | (g10 << 10) | b10


def process_sdr_to_xrgb8888(image: Image.Image, target_width: int = 0,
                             target_height: int = 0) -> np.ndarray:
    """Convert an SDR PIL Image to XRGB8888 packed pixels.

    Args:
        image: PIL Image in RGB mode
        target_width: Output width (0 = use image width)
        target_height: Output height (0 = use image height)

    Returns:
        uint32 array (H, W) with XRGB8888 packed pixels (BGRX byte order in memory).
    """
    if image.mode != "RGB":
        image = image.convert("RGB")

    arr = np.array(image, dtype=np.uint32)
    r = arr[:, :, 0]
    g = arr[:, :, 1]
    b = arr[:, :, 2]

    # XRGB8888: byte order in memory is B, G, R, X (little-endian uint32 = 0x00RRGGBB)
    packed = (r << 16) | (g << 8) | b

    return packed


# ── Self-test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import os

    # Test PQ roundtrip
    linear_values = np.array([0.0, 0.001, 0.01, 0.1, 0.5, 1.0], dtype=np.float32)
    pq_values = pq_oetf(linear_values)
    print("PQ OETF test:")
    for lin, pq in zip(linear_values, pq_values):
        nits = lin * PQ_PEAK_NITS
        code = int(pq * 1023 + 0.5)
        print(f"  {nits:8.1f} nits (linear={lin:.4f}) → PQ={pq:.4f} → 10-bit code={code}")

    # Test with demo photos if available
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        demo_dir = os.path.join(os.path.dirname(__file__), "..", "demo-pics")
        jpgs = sorted(f for f in os.listdir(demo_dir) if f.lower().endswith((".jpg", ".jpeg")))
        if jpgs:
            path = os.path.join(demo_dir, jpgs[0])
        else:
            print("No demo photos found")
            sys.exit(0)

    from ultrahdr import decode
    print(f"\nProcessing: {path}")
    img = decode(path)

    # Process through pipeline
    packed = process_to_xrgb2101010(img)
    print(f"Output shape: {packed.shape}")
    print(f"Output dtype: {packed.dtype}")

    # Analyze pixel values
    r = (packed >> 20) & 0x3FF
    g = (packed >> 10) & 0x3FF
    b = packed & 0x3FF
    print(f"R range: [{r.min()}, {r.max()}]")
    print(f"G range: [{g.min()}, {g.max()}]")
    print(f"B range: [{b.min()}, {b.max()}]")
