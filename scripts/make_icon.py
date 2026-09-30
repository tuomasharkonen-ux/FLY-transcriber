"""Render the app icon (assets/FLY.icns) from static/favicon.svg.

    uv run python scripts/make_icon.py

Needs macOS (AppKit draws the SVG, iconutil packs the set). The result is
committed, so only re-run this when the logo changes.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from AppKit import NSBitmapImageRep, NSGraphicsContext, NSImage, NSPNGFileType
from Foundation import NSMakeRect, NSZeroRect

ROOT = Path(__file__).resolve().parent.parent / "src" / "fly_transcriber"
SVG = ROOT / "static" / "favicon.svg"
OUT = ROOT / "assets" / "FLY.icns"

# (point size, scale) pairs an .iconset needs.
SIZES = [(16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2), (256, 1), (256, 2), (512, 1), (512, 2)]


def render(image: NSImage, pixels: int) -> bytes:
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, pixels, pixels, 8, 4, True, False, "NSCalibratedRGBColorSpace", 0, 0
    )
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep))
    image.drawInRect_fromRect_operation_fraction_(
        NSMakeRect(0, 0, pixels, pixels), NSZeroRect, 2, 1.0  # NSCompositingOperationSourceOver
    )
    NSGraphicsContext.restoreGraphicsState()
    return bytes(rep.representationUsingType_properties_(NSPNGFileType, {}))


def main() -> None:
    image = NSImage.alloc().initWithContentsOfFile_(str(SVG))
    if image is None:
        raise SystemExit(f"AppKit could not read {SVG}")
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "FLY.iconset"
        iconset.mkdir()
        for size, scale in SIZES:
            name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
            (iconset / name).write_bytes(render(image, size * scale))
        OUT.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(OUT)], check=True)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
