"""Viewer screen: fullscreen HDR photo display with debug overlay."""

import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from photo_source import PhotoSource
from hdr_pipeline import (
    process_to_xrgb2101010,
    process_sdr_to_xrgb2101010,
    process_sdr_native_to_xrgb2101010,
    process_gainmap_sdr_to_xrgb2101010,
)
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
        self._cached_gain_map = None
        self._cached_tv_hdr = None
        self.debug_mode = False
        self.gain_map_enabled = True
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

    def toggle_gain_map(self):
        """Toggle gain map usage on/off."""
        self.gain_map_enabled = not self.gain_map_enabled
        self._cached_index = -1
        print(f"Gain map: {'ON' if self.gain_map_enabled else 'OFF'}")

    def set_tv_mode(self, hdr_active: bool):
        """Update TV mode state. Always invalidates cache since render path changes."""
        self._tv_hdr_active = hdr_active
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

    def _render_mode_label(self) -> str:
        """Describe current effective render path."""
        if self._tv_hdr_active:
            return "HDR PQ" if self.gain_map_enabled else "SDR PQ"
        else:
            return "SDR Tonemap" if self.gain_map_enabled else "SDR Native"

    def render_hdr(self) -> np.ndarray:
        """Render current photo as XRGB2101010 pixels."""
        cache_key_match = (
            self._cached_index == self.current_index
            and self._cached_pixels is not None
            and self._cached_gain_map == self.gain_map_enabled
            and self._cached_tv_hdr == self._tv_hdr_active
        )
        if cache_key_match:
            return self._cached_pixels

        photo = self.source[self.current_index]
        print(f"Rendering: {photo.filename} ({self._render_mode_label()})")

        output = np.zeros((self.display_height, self.display_width), dtype=np.uint32)

        if not self._tv_hdr_active and self.gain_map_enabled and photo.is_ultrahdr:
            # SDR TV + gain map: tone-mapped gain map → sRGB → native 10-bit
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

            photo_pixels = process_gainmap_sdr_to_xrgb2101010(fitted_img)

        elif not self._tv_hdr_active:
            # SDR TV mode, no gain map: raw sRGB packed to 10-bit
            sdr = self.source.load_sdr(self.current_index)
            fitted = self._fit_image(sdr)
            photo_pixels = process_sdr_native_to_xrgb2101010(fitted)

        elif self.gain_map_enabled and photo.is_ultrahdr:
            # HDR TV + gain map: full HDR pipeline
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
            # HDR TV + no gain map (or non-Ultra-HDR photo): sRGB->linear->BT.2020->PQ
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
        self._cached_gain_map = self.gain_map_enabled
        self._cached_tv_hdr = self._tv_hdr_active
        return output

    def render_to_gpu(self, gpu):
        """Render current photo using GPU pipeline.

        Uploads raw PIL images to GPU and dispatches compute shader.
        The output is in the GPU pipeline's SSBO, ready for copy_to_framebuffer().
        """
        cache_key_match = (
            self._cached_index == self.current_index
            and self._cached_pixels is not None
            and self._cached_gain_map == self.gain_map_enabled
            and self._cached_tv_hdr == self._tv_hdr_active
        )
        if cache_key_match:
            return  # Already rendered and SSBO still has the data

        photo = self.source[self.current_index]
        print(f"Rendering [GPU]: {photo.filename} ({self._render_mode_label()})")

        use_gainmap = self.gain_map_enabled and photo.is_ultrahdr

        if use_gainmap:
            img = self.source.load_ultrahdr(self.current_index)
            fitted_sdr = self._fit_image(img.sdr)
            if img.gain_map is not None:
                fitted_gm = img.gain_map.resize(fitted_sdr.size, Image.BILINEAR)
            else:
                fitted_gm = None

            pw, ph = fitted_sdr.size
            x_offset = (self.display_width - pw) // 2
            y_offset = (self.display_height - ph) // 2

            if self._tv_hdr_active:
                gpu.render_hdr(fitted_sdr, fitted_gm, img.params,
                               pw, ph, x_offset, y_offset)
            else:
                gpu.render_tonemap(fitted_sdr, fitted_gm, img.params,
                                   pw, ph, x_offset, y_offset)
        else:
            sdr = self.source.load_sdr(self.current_index)
            fitted = self._fit_image(sdr)

            pw, ph = fitted.size
            x_offset = (self.display_width - pw) // 2
            y_offset = (self.display_height - ph) // 2

            if self._tv_hdr_active:
                gpu.render_sdr(fitted, pw, ph, x_offset, y_offset)
            else:
                gpu.render_native(fitted, pw, ph, x_offset, y_offset)

        self._cached_index = self.current_index
        self._cached_pixels = True  # Sentinel: SSBO has the data
        self._cached_gain_map = self.gain_map_enabled
        self._cached_tv_hdr = self._tv_hdr_active

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
        render_str = self._render_mode_label()
        gm_str = "ON" if self.gain_map_enabled else "OFF"
        tv_str = "HDR10" if self._tv_hdr_active else "SDR"

        text = (f"{photo.filename}  |  {uhdr_str}  |  Profile: {icc_str}"
                f"  |  Gain map: {gm_str}  |  Render: {render_str}  |  TV: {tv_str}")

        draw.text((20, 14), text, fill=(200, 200, 200), font=self._font)

        # Overlay rendering follows TV mode so text is always legible
        if self._tv_hdr_active:
            overlay_pixels = process_sdr_to_xrgb2101010(overlay)
        else:
            overlay_pixels = process_sdr_native_to_xrgb2101010(overlay)

        y = self.display_height - bar_height
        output[y:, :] = overlay_pixels
