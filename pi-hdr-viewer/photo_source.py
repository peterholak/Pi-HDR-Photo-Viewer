"""Photo source: list and load photos from a directory."""

import os
from dataclasses import dataclass
from typing import Optional

from PIL import Image

import ultrahdr


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg"}


@dataclass
class PhotoInfo:
    """Metadata about a photo file."""
    path: str
    filename: str
    index: int
    is_ultrahdr: bool = False
    _thumbnail: Optional[Image.Image] = None
    _thumb_size: tuple = (0, 0)

    def get_thumbnail(self, size=(300, 300)) -> Image.Image:
        """Get or create a thumbnail for this photo."""
        if self._thumbnail is None or self._thumb_size != size:
            img = Image.open(self.path)
            img.thumbnail(size, Image.LANCZOS)
            self._thumbnail = img.convert("RGB")
            self._thumb_size = size
        return self._thumbnail


class PhotoSource:
    """Lists and loads photos from a directory."""

    def __init__(self, directory: str):
        self.directory = os.path.abspath(directory)
        self.photos: list[PhotoInfo] = []
        self._scan()

    def _scan(self):
        """Scan directory for supported photo files."""
        self.photos = []
        if not os.path.isdir(self.directory):
            print(f"Warning: directory not found: {self.directory}")
            return

        files = sorted(f for f in os.listdir(self.directory)
                      if os.path.splitext(f)[1].lower() in SUPPORTED_EXTENSIONS)

        for i, filename in enumerate(files):
            path = os.path.join(self.directory, filename)
            info = PhotoInfo(path=path, filename=filename, index=i)

            # Quick check for Ultra HDR: look for hdrgm or MPF markers
            try:
                with open(path, "rb") as f:
                    header = f.read(65536)  # Read first 64KB
                if b"hdrgm" in header or b"hdr-gainmap" in header or b"MPF\0" in header:
                    info.is_ultrahdr = True
            except OSError:
                pass

            self.photos.append(info)

        print(f"Found {len(self.photos)} photos in {self.directory}")
        uhdr_count = sum(1 for p in self.photos if p.is_ultrahdr)
        if uhdr_count:
            print(f"  ({uhdr_count} Ultra HDR)")

    def __len__(self):
        return len(self.photos)

    def __getitem__(self, index):
        return self.photos[index]

    def load_ultrahdr(self, index: int) -> ultrahdr.UltraHDRImage:
        """Load and decode an Ultra HDR photo."""
        photo = self.photos[index]
        return ultrahdr.decode(photo.path)

    def load_sdr(self, index: int) -> Image.Image:
        """Load a photo as a standard SDR image."""
        photo = self.photos[index]
        return Image.open(photo.path).convert("RGB")

    def rescan(self):
        """Re-scan the directory for changes."""
        self._scan()


# ── Self-test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    directory = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(__file__), "..", "demo-pics")

    source = PhotoSource(directory)
    for photo in source.photos:
        uhdr_tag = " [Ultra HDR]" if photo.is_ultrahdr else ""
        print(f"  [{photo.index}] {photo.filename}{uhdr_tag}")
        thumb = photo.get_thumbnail()
        print(f"      thumbnail: {thumb.size}")
