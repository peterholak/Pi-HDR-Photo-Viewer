"""Config screen: settings UI for the HDR photo viewer."""

import json
import os
from dataclasses import dataclass, asdict

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from hdr_pipeline import process_sdr_to_xrgb2101010, process_sdr_native_to_xrgb2101010


CONFIG_DIR = os.path.expanduser("~/.config/hdr-viewer")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")


@dataclass
class AppConfig:
    """Persistent application configuration."""
    grid_columns: int = 4
    slideshow_interval: float = 5.0
    debug_overlay: bool = False

    def save(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load(cls):
        try:
            with open(CONFIG_FILE) as f:
                data = json.load(f)
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            return cls()


SETTINGS = [
    ("grid_columns", "Grid Columns", [3, 4, 5, 6], str),
    ("slideshow_interval", "Slideshow Interval", [3.0, 5.0, 10.0, 15.0, 30.0],
     lambda v: f"{int(v)}s"),
    ("debug_overlay", "Debug Overlay", [False, True],
     lambda v: "On" if v else "Off"),
]


class ConfigScreen:
    """Settings screen rendered into HDR framebuffer."""

    def __init__(self, config: AppConfig, display_width: int, display_height: int):
        self.config = config
        self.display_width = display_width
        self.display_height = display_height
        self.selected = 0

        self._title_font = None
        self._item_font = None
        self._hint_font = None
        for fp in ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                   "/usr/share/fonts/TTF/DejaVuSans.ttf"]:
            try:
                self._title_font = ImageFont.truetype(fp, 48)
                self._item_font = ImageFont.truetype(fp, 36)
                self._hint_font = ImageFont.truetype(fp, 24)
                break
            except (OSError, IOError):
                continue
        if self._title_font is None:
            self._title_font = ImageFont.load_default()
            self._item_font = self._title_font
            self._hint_font = self._title_font

    def move_selection(self, dy: int):
        self.selected = max(0, min(len(SETTINGS) - 1, self.selected + dy))

    def change_value(self, direction: int):
        attr, _, options, _ = SETTINGS[self.selected]
        current = getattr(self.config, attr)
        try:
            idx = options.index(current)
        except ValueError:
            idx = 0
        idx = (idx + direction) % len(options)
        setattr(self.config, attr, options[idx])

    def _render_canvas(self) -> Image.Image:
        canvas = Image.new("RGB", (self.display_width, self.display_height), (20, 20, 20))
        draw = ImageDraw.Draw(canvas)

        x_label = self.display_width // 4
        x_value = self.display_width * 3 // 5
        y = self.display_height // 5

        draw.text((x_label, y), "Settings", fill=(220, 220, 220), font=self._title_font)
        y += 120

        for i, (attr, label, options, fmt) in enumerate(SETTINGS):
            is_selected = (i == self.selected)
            color = (0, 150, 255) if is_selected else (180, 180, 180)
            value = getattr(self.config, attr)
            value_str = fmt(value)

            draw.text((x_label, y), label, fill=color, font=self._item_font)

            display_str = f"< {value_str} >" if is_selected else f"  {value_str}"
            draw.text((x_value, y), display_str, fill=color, font=self._item_font)

            y += 80

        hint_y = self.display_height - self.display_height // 8
        draw.text((x_label, hint_y),
                  "Up/Down: navigate    Left/Right: change    Esc: back",
                  fill=(100, 100, 100), font=self._hint_font)

        return canvas

    def render_hdr(self) -> np.ndarray:
        return process_sdr_to_xrgb2101010(self._render_canvas())

    def render_native(self) -> np.ndarray:
        """Render config as XRGB2101010 raw sRGB (for SDR TV mode)."""
        return process_sdr_native_to_xrgb2101010(self._render_canvas())
