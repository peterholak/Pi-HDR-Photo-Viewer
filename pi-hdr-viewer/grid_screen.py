"""Grid screen: thumbnail browser rendered as PQ BT.2020."""

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from photo_source import PhotoSource
from hdr_pipeline import process_sdr_to_xrgb2101010, process_sdr_native_to_xrgb2101010


# Grid layout constants
THUMB_PADDING = 20
BORDER_WIDTH = 4
BORDER_COLOR_NORMAL = (60, 60, 60)
BORDER_COLOR_SELECTED = (0, 150, 255)
BG_COLOR = (20, 20, 20)
TEXT_COLOR = (220, 220, 220)
UHDR_BADGE_COLOR = (255, 180, 0)


class GridScreen:
    """Renders a photo thumbnail grid into an XRGB2101010 framebuffer."""

    def __init__(self, source: PhotoSource, display_width: int, display_height: int,
                 cols: int = 4):
        self.source = source
        self.display_width = display_width
        self.display_height = display_height
        self.selected = 0
        self.scroll_offset = 0

        # Find available font path
        self._font_path = None
        for fp in ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                   "/usr/share/fonts/TTF/DejaVuSans.ttf"]:
            try:
                ImageFont.truetype(fp, 16)
                self._font_path = fp
                break
            except (OSError, IOError):
                continue

        self._set_layout(cols)

    def _set_layout(self, cols: int):
        """Recompute grid layout for the given column count."""
        self.cols = max(1, min(cols, 10))
        self.cell_size = (self.display_width - THUMB_PADDING) // self.cols

        # Scale font to cell size
        self._font_size = max(16, self.cell_size // 22)
        if self._font_path:
            self._font = ImageFont.truetype(self._font_path, self._font_size)
        else:
            self._font = ImageFont.load_default()

        self.label_height = self._font_size + 14
        self.thumb_size = self.cell_size - THUMB_PADDING * 2 - self.label_height
        self.rows_visible = max(1, (self.display_height - THUMB_PADDING) // self.cell_size)

    def set_columns(self, cols: int):
        """Change column count and recompute layout."""
        self._set_layout(cols)
        if self.selected >= len(self.source):
            self.selected = max(0, len(self.source) - 1)

    @property
    def total_rows(self):
        return max(1, (len(self.source) + self.cols - 1) // self.cols)

    def move_selection(self, dx: int, dy: int):
        """Move selection by (dx, dy) in grid coordinates."""
        col = self.selected % self.cols
        row = self.selected // self.cols

        col = max(0, min(self.cols - 1, col + dx))
        row = max(0, min(self.total_rows - 1, row + dy))

        new_index = row * self.cols + col
        if new_index < len(self.source):
            self.selected = new_index

        # Auto-scroll to keep selection visible
        selected_row = self.selected // self.cols
        if selected_row < self.scroll_offset:
            self.scroll_offset = selected_row
        elif selected_row >= self.scroll_offset + self.rows_visible:
            self.scroll_offset = selected_row - self.rows_visible + 1

    def _render_canvas(self) -> Image.Image:
        """Render the grid to a PIL RGB canvas."""
        canvas = Image.new("RGB", (self.display_width, self.display_height), BG_COLOR)
        draw = ImageDraw.Draw(canvas)

        if len(self.source) == 0:
            draw.text((self.display_width // 2 - 80, self.display_height // 2),
                      "No photos found", fill=TEXT_COLOR, font=self._font)
            return canvas

        thumb_box = (self.thumb_size, self.thumb_size)

        # Render visible cells
        start_index = self.scroll_offset * self.cols
        end_index = min(len(self.source),
                       (self.scroll_offset + self.rows_visible) * self.cols)

        for idx in range(start_index, end_index):
            photo = self.source[idx]
            grid_row = idx // self.cols - self.scroll_offset
            grid_col = idx % self.cols

            # Cell position
            x = THUMB_PADDING + grid_col * self.cell_size
            y = THUMB_PADDING + grid_row * self.cell_size

            # Draw border
            is_selected = (idx == self.selected)
            border_color = BORDER_COLOR_SELECTED if is_selected else BORDER_COLOR_NORMAL
            border_w = BORDER_WIDTH + 2 if is_selected else BORDER_WIDTH
            draw.rectangle(
                [x - border_w, y - border_w,
                 x + self.thumb_size + border_w, y + self.thumb_size + border_w],
                outline=border_color, width=border_w,
            )

            # Draw thumbnail
            try:
                thumb = photo.get_thumbnail(thumb_box)
                tx = x + (self.thumb_size - thumb.width) // 2
                ty = y + (self.thumb_size - thumb.height) // 2
                canvas.paste(thumb, (tx, ty))
            except Exception as e:
                draw.text((x + 10, y + self.thumb_size // 2), "Error",
                          fill=(255, 80, 80), font=self._font)

            # Draw filename label
            label = photo.filename
            max_chars = max(20, int(self.thumb_size / (self._font_size * 0.55)))
            if len(label) > max_chars:
                label = label[:max_chars - 3] + "..."
            label_y = y + self.thumb_size + BORDER_WIDTH + 4
            draw.text((x + 4, label_y), label, fill=TEXT_COLOR, font=self._font)

            # Ultra HDR badge
            if photo.is_ultrahdr:
                badge_text = "HDR"
                badge_w = max(40, self._font_size * 3)
                badge_h = max(18, self._font_size + 4)
                draw.rectangle([x + self.thumb_size - badge_w, y + 4,
                               x + self.thumb_size - 2, y + badge_h + 4],
                              fill=UHDR_BADGE_COLOR)
                draw.text((x + self.thumb_size - badge_w + 2, y + 4), badge_text,
                          fill=(0, 0, 0), font=self._font)

        # Scroll indicator
        if self.total_rows > self.rows_visible:
            bar_height = max(20, int(self.display_height * self.rows_visible / self.total_rows))
            bar_y = int((self.display_height - bar_height) * self.scroll_offset /
                       max(1, self.total_rows - self.rows_visible))
            bar_x = self.display_width - 8
            draw.rectangle([bar_x, bar_y, bar_x + 6, bar_y + bar_height],
                          fill=(100, 100, 100))

        return canvas

    def render_hdr(self) -> np.ndarray:
        """Render the grid as XRGB2101010 PQ BT.2020."""
        return process_sdr_to_xrgb2101010(self._render_canvas())

    def render_native(self) -> np.ndarray:
        """Render the grid as XRGB2101010 raw sRGB (for SDR TV mode)."""
        return process_sdr_native_to_xrgb2101010(self._render_canvas())
