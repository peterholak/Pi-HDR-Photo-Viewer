"""Viewer screen: fullscreen HDR photo display with debug overlay."""

import time
from enum import Enum, auto

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from photo_source import PhotoSource
from hdr_pipeline import (
    process_to_xrgb2101010,
    process_sdr_to_xrgb2101010,
    process_sdr_native_to_xrgb2101010,
)
import ultrahdr


class RenderMode(Enum):
    HDR_PQ = auto()      # gain map + sRGB->linear->BT.2020->PQ (full HDR pipeline)
    SDR_PQ = auto()      # no gain map, sRGB->linear->BT.2020->PQ (see PQ/gamut effect)
    SDR_NATIVE = auto()  # raw sRGB packed to 10-bit, no transform (normal viewer)


_RENDER_MODE_LABELS = {
    RenderMode.HDR_PQ: "HDR PQ",
    RenderMode.SDR_PQ: "SDR PQ",
    RenderMode.SDR_NATIVE: "SDR Native",
}

_RENDER_MODE_CYCLE = [RenderMode.HDR_PQ, RenderMode.SDR_PQ, RenderMode.SDR_NATIVE]


class ViewerScreen:
    """Renders a single photo in fullscreen HDR (XRGB2101010)."""

    def __init__(self, source: PhotoSource, display_width: int, display_height: int):
        self.source = source
        self.display_width = display_width
        self.display_height = display_height
        self.current_index = 0
        self.slideshow_active = False
        self.slideshow_interval = 5.0
        self._last_advance_time = 0.0
        self._cached_index = -1
        self._cached_pixels = None
        self._cached_render_mode = None
        self._cached_tv_hdr = None
        self.debug_mode = False
        self.render_mode = RenderMode.HDR_PQ
        self._tv_hdr_active = True

        self._font = None
        try:
            self._font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
        except (OSError, IOError):
            try:
                self._font = ImageFont.truetype("/usr/share/fonts/TTF/DejaVuSans.ttf", 24)
            except (OSError, IOError):
                self._font = ImageFont.load_default()

    def set_photo(self, index: int):
        """Set the current photo index."""
        if 0 <= index < len(self.source):
            self.current_index = index
            self._cached_index = -1
            self._last_advance_time = time.monotonic()

    def next_photo(self):
        """Advance to next photo."""
        if len(self.source) > 0:
            self.set_photo((self.current_index + 1) % len(self.source))

    def prev_photo(self):
        """Go to previous photo."""
        if len(self.source) > 0:
            self.set_photo((self.current_index - 1) % len(self.source))

    def toggle_slideshow(self):
        """Toggle slideshow mode."""
        self.slideshow_active = not self.slideshow_active
        self._last_advance_time = time.monotonic()
        print(f"Slideshow {'ON' if self.slideshow_active else 'OFF'}")

    def toggle_debug(self):
        """Toggle debug overlay."""
        self.debug_mode = not self.debug_mode
        self._cached_index = -1
        print(f"Debug overlay {'ON' if self.debug_mode else 'OFF'}")

    def cycle_render_mode(self):
        """Cycle through render modes: HDR PQ -> SDR PQ -> SDR Native."""
        idx = _RENDER_MODE_CYCLE.index(self.render_mode)
        self.render_mode = _RENDER_MODE_CYCLE[(idx + 1) % len(_RENDER_MODE_CYCLE)]
        self._cached_index = -1
        print(f"Render mode: {_RENDER_MODE_LABELS[self.render_mode]}")

    @property
    def render_mode_label(self):
        return _RENDER_MODE_LABELS[self.render_mode]

    def set_tv_mode(self, hdr_active: bool):
        """Update TV mode state. Invalidates cache if debug overlay is on."""
        self._tv_hdr_active = hdr_active
        if self.debug_mode:
            self._cached_index = -1

    def tick(self) -> bool:
        """Check if slideshow should advance. Returns True if photo changed."""
        if not self.slideshow_active:
            return False
        now = time.monotonic()
        if now - self._last_advance_time >= self.slideshow_interval:
            self.next_photo()
            return True
        return False

    def _fit_image(self, img: Image.Image) -> Image.Image:
        """Scale image to fit display while maintaining aspect ratio."""
        src_w, src_h = img.size
        dst_w, dst_h = self.display_width, self.display_height

        scale = min(dst_w / src_w, dst_h / src_h)
        new_w = int(src_w * scale)
        new_h = int(src_h * scale)

        if new_w == src_w and new_h == src_h:
            return img
        return img.resize((new_w, new_h), Image.LANCZOS)

    def _extract_icc_profile(self, photo) -> str:
        """Extract ICC profile description from photo, cached on PhotoInfo."""
        if hasattr(photo, '_icc_description'):
            return photo._icc_description

        desc = "sRGB (assumed)"
        try:
            from PIL import ImageCms
            sdr = self.source.load_sdr(self.current_index)
            icc_data = sdr.info.get("icc_profile")
            if icc_data:
                profile = ImageCms.ImageCmsProfile(ImageCms.core.profile_frombytes(icc_data))
                name = ImageCms.getProfileDescription(profile)
                if name:
                    desc = f"{name.strip()} (not applied)"
        except Exception:
            pass

        photo._icc_description = desc
        return desc

    def render_hdr(self) -> np.ndarray:
        """Render current photo as XRGB2101010 pixels."""
        cache_key_match = (
            self._cached_index == self.current_index
            and self._cached_pixels is not None
            and self._cached_render_mode == self.render_mode
            and self._cached_tv_hdr == self._tv_hdr_active
        )
        if cache_key_match:
            return self._cached_pixels

        photo = self.source[self.current_index]
        print(f"Rendering: {photo.filename} (Render: {_RENDER_MODE_LABELS[self.render_mode]})")

        output = np.zeros((self.display_height, self.display_width), dtype=np.uint32)

        if self.render_mode == RenderMode.HDR_PQ and photo.is_ultrahdr:
            # Full HDR pipeline with gain map
            img = self.source.load_ultrahdr(self.current_index)

            fitted_sdr = self._fit_image(img.sdr)
            if img.gain_map is not None:
                fitted_gm = img.gain_map.resize(fitted_sdr.size, Image.BILINEAR)
            else:
                fitted_gm = None

            fitted_img = ultrahdr.UltraHDRImage(
                sdr=fitted_sdr,
                gain_map=fitted_gm,
                params=img.params,
                width=fitted_sdr.width,
                height=fitted_sdr.height,
            )

            photo_pixels = process_to_xrgb2101010(fitted_img)

        elif self.render_mode == RenderMode.SDR_NATIVE:
            # Raw sRGB packed to 10-bit, no transform
            sdr = self.source.load_sdr(self.current_index)
            fitted = self._fit_image(sdr)
            photo_pixels = process_sdr_native_to_xrgb2101010(fitted)

        else:
            # SDR_PQ, or HDR_PQ with non-Ultra-HDR photo: sRGB->linear->BT.2020->PQ
            sdr = self.source.load_sdr(self.current_index)
            fitted = self._fit_image(sdr)
            photo_pixels = process_sdr_to_xrgb2101010(fitted)

        # Center in output (letterbox/pillarbox)
        ph, pw = photo_pixels.shape
        y_offset = (self.display_height - ph) // 2
        x_offset = (self.display_width - pw) // 2
        output[y_offset:y_offset + ph, x_offset:x_offset + pw] = photo_pixels

        if self.debug_mode:
            self._render_debug_overlay(output, photo)

        self._cached_index = self.current_index
        self._cached_pixels = output
        self._cached_render_mode = self.render_mode
        self._cached_tv_hdr = self._tv_hdr_active
        return output

    def render_sdr(self) -> np.ndarray:
        """Render current photo as XRGB8888 SDR pixels (fallback)."""
        from hdr_pipeline import process_sdr_to_xrgb8888
        photo = self.source[self.current_index]
        sdr = self.source.load_sdr(self.current_index)
        fitted = self._fit_image(sdr)

        output = np.zeros((self.display_height, self.display_width), dtype=np.uint32)
        photo_pixels = process_sdr_to_xrgb8888(fitted)

        ph, pw = photo_pixels.shape
        y_offset = (self.display_height - ph) // 2
        x_offset = (self.display_width - pw) // 2
        output[y_offset:y_offset + ph, x_offset:x_offset + pw] = photo_pixels

        return output

    def _render_debug_overlay(self, output, photo):
        """Render debug info bar at the bottom of the frame."""
        bar_height = 50
        overlay = Image.new("RGB", (self.display_width, bar_height), (30, 30, 30))
        draw = ImageDraw.Draw(overlay)

        uhdr_str = "Ultra HDR" if photo.is_ultrahdr else "SDR photo"
        icc_str = self._extract_icc_profile(photo)
        render_str = _RENDER_MODE_LABELS[self.render_mode]
        tv_str = "HDR10" if self._tv_hdr_active else "SDR"

        text = (f"{photo.filename}  |  {uhdr_str}  |  Profile: {icc_str}"
                f"  |  Render: {render_str}  |  TV: {tv_str}")

        draw.text((20, 14), text, fill=(200, 200, 200), font=self._font)

        # Overlay rendering follows TV mode so text is always legible
        if self._tv_hdr_active:
            overlay_pixels = process_sdr_to_xrgb2101010(overlay)
        else:
            overlay_pixels = process_sdr_native_to_xrgb2101010(overlay)

        y = self.display_height - bar_height
        output[y:, :] = overlay_pixels
