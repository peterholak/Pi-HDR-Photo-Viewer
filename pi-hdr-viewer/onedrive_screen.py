"""OneDrive browser screen: navigate OneDrive folders and photos on the TV."""

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from hdr_pipeline import process_sdr_to_xrgb2101010, process_sdr_native_to_xrgb2101010
from onedrive import OneDriveItem


# Grid layout constants
THUMB_PADDING = 20
BORDER_WIDTH = 4
BORDER_COLOR_NORMAL = (60, 60, 60)
BORDER_COLOR_SELECTED = (0, 150, 255)
BG_COLOR = (20, 20, 20)
TEXT_COLOR = (220, 220, 220)
FOLDER_COLOR = (100, 160, 255)
PHOTO_BADGE_COLOR = (80, 200, 80)
LOADING_COLOR = (80, 80, 80)


class OneDriveScreen:
    """Renders OneDrive folder contents in a grid on the TV."""

    def __init__(self, display_width: int, display_height: int, cols: int = 4):
        self.display_width = display_width
        self.display_height = display_height
        self.selected = 0
        self.scroll_offset = 0
        self.items: list[OneDriveItem] = []
        self.thumbnails: dict[str, Image.Image] = {}  # item_id -> PIL thumbnail
        self.folder_path: list[tuple[str, str]] = []  # [(folder_id, name), ...]
        self.loading = False
        self.loading_message = ""
        self.auth_prompt: dict | None = None  # {user_code, verification_uri}

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
        self.cols = max(1, min(cols, 10))
        self.cell_size = (self.display_width - THUMB_PADDING) // self.cols
        self._font_size = max(16, self.cell_size // 22)
        if self._font_path:
            self._font = ImageFont.truetype(self._font_path, self._font_size)
            self._title_font = ImageFont.truetype(self._font_path, self._font_size + 8)
        else:
            self._font = ImageFont.load_default()
            self._title_font = self._font
        self.label_height = self._font_size + 14
        self.thumb_size = self.cell_size - THUMB_PADDING * 2 - self.label_height
        # Reserve top for breadcrumb
        self._header_height = self._font_size + 30
        usable_height = self.display_height - self._header_height - THUMB_PADDING
        self.rows_visible = max(1, usable_height // self.cell_size)

    def set_items(self, items: list[OneDriveItem]):
        """Update the displayed items (called when folder listing arrives)."""
        self.items = items
        self.selected = 0
        self.scroll_offset = 0
        self.loading = False

    def set_thumbnail(self, item_id: str, jpeg_bytes: bytes):
        """Set a loaded thumbnail for an item."""
        try:
            from io import BytesIO
            img = Image.open(BytesIO(jpeg_bytes))
            img.thumbnail((self.thumb_size, self.thumb_size), Image.LANCZOS)
            self.thumbnails[item_id] = img.convert("RGB")
        except Exception:
            pass

    def navigate_into(self, folder_id: str, folder_name: str):
        """Enter a subfolder."""
        self.folder_path.append((folder_id, folder_name))
        self.loading = True
        self.loading_message = f"Loading {folder_name}..."

    def navigate_back(self) -> str | None:
        """Go up one level. Returns parent folder_id, or None if at root."""
        if self.folder_path:
            self.folder_path.pop()
            self.loading = True
            if self.folder_path:
                return self.folder_path[-1][0]
            return "root"
        return None

    @property
    def current_folder_id(self) -> str:
        if self.folder_path:
            return self.folder_path[-1][0]
        return "root"

    @property
    def breadcrumb(self) -> str:
        parts = ["OneDrive"] + [name for _, name in self.folder_path]
        return " > ".join(parts)

    @property
    def total_rows(self):
        return max(1, (len(self.items) + self.cols - 1) // self.cols)

    def move_selection(self, dx: int, dy: int):
        if not self.items:
            return
        col = self.selected % self.cols
        row = self.selected // self.cols
        col = max(0, min(self.cols - 1, col + dx))
        row = max(0, min(self.total_rows - 1, row + dy))
        new_index = row * self.cols + col
        if new_index < len(self.items):
            self.selected = new_index
        selected_row = self.selected // self.cols
        if selected_row < self.scroll_offset:
            self.scroll_offset = selected_row
        elif selected_row >= self.scroll_offset + self.rows_visible:
            self.scroll_offset = selected_row - self.rows_visible + 1

    def get_selected_item(self) -> OneDriveItem | None:
        if 0 <= self.selected < len(self.items):
            return self.items[self.selected]
        return None

    def visible_item_ids(self) -> list[str]:
        """Return item IDs of currently visible items that need thumbnails."""
        start = self.scroll_offset * self.cols
        end = min(len(self.items), (self.scroll_offset + self.rows_visible) * self.cols)
        return [
            self.items[i].id for i in range(start, end)
            if self.items[i].is_photo and self.items[i].id not in self.thumbnails
        ]

    def _render_canvas(self) -> Image.Image:
        canvas = Image.new("RGB", (self.display_width, self.display_height), BG_COLOR)
        draw = ImageDraw.Draw(canvas)

        # Breadcrumb header
        draw.text((THUMB_PADDING, 8), self.breadcrumb, fill=TEXT_COLOR, font=self._title_font)

        # Auth prompt
        if self.auth_prompt:
            code = self.auth_prompt.get("user_code", "")
            uri = self.auth_prompt.get("verification_uri", "")
            cy = self.display_height // 2
            draw.text((self.display_width // 2 - 200, cy - 60),
                      "Sign in to OneDrive", fill=TEXT_COLOR, font=self._title_font)
            draw.text((self.display_width // 2 - 200, cy - 10),
                      f"Go to: {uri}", fill=FOLDER_COLOR, font=self._font)
            draw.text((self.display_width // 2 - 200, cy + 30),
                      f"Enter code: {code}", fill=(255, 220, 100), font=self._title_font)
            return canvas

        # Loading state
        if self.loading:
            draw.text((self.display_width // 2 - 100, self.display_height // 2),
                      self.loading_message or "Loading...",
                      fill=TEXT_COLOR, font=self._font)
            return canvas

        if not self.items:
            draw.text((self.display_width // 2 - 80, self.display_height // 2),
                      "Empty folder", fill=TEXT_COLOR, font=self._font)
            return canvas

        thumb_box = (self.thumb_size, self.thumb_size)
        start_index = self.scroll_offset * self.cols
        end_index = min(len(self.items),
                       (self.scroll_offset + self.rows_visible) * self.cols)

        for idx in range(start_index, end_index):
            item = self.items[idx]
            grid_row = idx // self.cols - self.scroll_offset
            grid_col = idx % self.cols

            x = THUMB_PADDING + grid_col * self.cell_size
            y = self._header_height + grid_row * self.cell_size

            # Border
            is_selected = (idx == self.selected)
            border_color = BORDER_COLOR_SELECTED if is_selected else BORDER_COLOR_NORMAL
            border_w = BORDER_WIDTH + 2 if is_selected else BORDER_WIDTH
            draw.rectangle(
                [x - border_w, y - border_w,
                 x + self.thumb_size + border_w, y + self.thumb_size + border_w],
                outline=border_color, width=border_w,
            )

            if item.is_folder:
                # Folder icon (simple rectangle with tab)
                fx, fy = x + self.thumb_size // 4, y + self.thumb_size // 4
                fw, fh = self.thumb_size // 2, self.thumb_size // 2
                draw.rectangle([fx, fy, fx + fw, fy + fh], fill=(50, 80, 120), outline=FOLDER_COLOR)
                draw.rectangle([fx, fy - 8, fx + fw // 3, fy], fill=FOLDER_COLOR)
            elif item.id in self.thumbnails:
                thumb = self.thumbnails[item.id]
                tx = x + (self.thumb_size - thumb.width) // 2
                ty = y + (self.thumb_size - thumb.height) // 2
                canvas.paste(thumb, (tx, ty))
            else:
                # Placeholder
                draw.rectangle([x + 2, y + 2, x + self.thumb_size - 2, y + self.thumb_size - 2],
                              fill=LOADING_COLOR)
                draw.text((x + self.thumb_size // 4, y + self.thumb_size // 2 - 8),
                          "Loading...", fill=TEXT_COLOR, font=self._font)

            # Label
            label = item.name
            max_chars = max(20, int(self.thumb_size / (self._font_size * 0.55)))
            if len(label) > max_chars:
                label = label[:max_chars - 3] + "..."
            label_y = y + self.thumb_size + BORDER_WIDTH + 4
            draw.text((x + 4, label_y), label, fill=TEXT_COLOR, font=self._font)

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
        return process_sdr_to_xrgb2101010(self._render_canvas())

    def render_native(self) -> np.ndarray:
        return process_sdr_native_to_xrgb2101010(self._render_canvas())
