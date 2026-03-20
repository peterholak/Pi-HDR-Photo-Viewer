"""Viewer screen: fullscreen HDR photo display with debug overlay."""

import time
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from photo_source import PhotoSource
from hdr_pipeline import process_to_xrgb2101010, process_sdr_to_xrgb8888, process_sdr_to_xrgb2101010
import ultrahdr


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
        self.debug_mode = False
        self.show_as_sdr = False

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

    def toggle_sdr_mode(self):
        """Toggle SDR/HDR rendering mode."""
        self.show_as_sdr = not self.show_as_sdr
        self._cached_index = -1
        print(f"Render mode: {'SDR (gain map off)' if self.show_as_sdr else 'HDR'}")

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

    def render_hdr(self) -> np.ndarray:
        """Render current photo as XRGB2101010 HDR pixels."""
        if self._cached_index == self.current_index and self._cached_pixels is not None:
            return self._cached_pixels

        photo = self.source[self.current_index]
        print(f"Rendering: {photo.filename} ({'SDR mode' if self.show_as_sdr else 'HDR'})")

        output = np.zeros((self.display_height, self.display_width), dtype=np.uint32)

        if photo.is_ultrahdr and not self.show_as_sdr:
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
        else:
            sdr = self.source.load_sdr(self.current_index)
            fitted = self._fit_image(sdr)

            dummy = ultrahdr.UltraHDRImage(
                sdr=fitted,
                gain_map=None,
                params=ultrahdr.GainMapParams(),
                width=fitted.width,
                height=fitted.height,
            )
            photo_pixels = process_to_xrgb2101010(dummy)

        # Center in output (letterbox/pillarbox)
        ph, pw = photo_pixels.shape
        y_offset = (self.display_height - ph) // 2
        x_offset = (self.display_width - pw) // 2
        output[y_offset:y_offset + ph, x_offset:x_offset + pw] = photo_pixels

        if self.debug_mode:
            self._render_debug_overlay(output, photo)

        self._cached_index = self.current_index
        self._cached_pixels = output
        return output

    def render_sdr(self) -> np.ndarray:
        """Render current photo as XRGB8888 SDR pixels (fallback)."""
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

        mode_str = "SDR (gain map off)" if self.show_as_sdr else "HDR"
        uhdr_str = "Ultra HDR" if photo.is_ultrahdr else "SDR photo"
        text = (f"{photo.filename}  |  {uhdr_str}  |  Mode: {mode_str}"
                f"  |  [D] overlay  [G] toggle gain map")

        draw.text((20, 14), text, fill=(200, 200, 200), font=self._font)

        overlay_pixels = process_sdr_to_xrgb2101010(overlay)
        y = self.display_height - bar_height
        output[y:, :] = overlay_pixels
