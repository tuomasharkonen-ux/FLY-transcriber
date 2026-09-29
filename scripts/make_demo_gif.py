"""Render docs/demo.gif, the README demo of the menubar popover.

    uv run --with playwright python scripts/make_demo_gif.py

Drives the real popover UI in Google Chrome (headless, via Playwright's
``channel="chrome"``, so no browser download) with the scripted story in
``scripts/readme_demo.js``: one 2x screenshot per step, joined by ffmpeg with
each step's own duration and a shared palette. Needs ffmpeg on PATH.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

from fly_transcriber.server import Api, make_server

ROOT = Path(__file__).resolve().parent.parent
STAGE = ROOT / "scripts" / "readme_demo.js"
OUT = ROOT / "docs" / "demo.gif"

VIEWPORT = {"width": 720, "height": 620}
#: The part of the stage the GIF shows: the popover and a strip of desktop.
CROP = {"x": 250, "y": 0, "width": 470, "height": 620}
GIF_WIDTH = 705  # 1.5x the crop; embed at width=470 for a sharp result


def main() -> int:
    # The stage replaces the API, so the server only has to serve static files.
    empty = {"run": {"css": "idle", "label": "Idle", "detail": "", "elapsed": ""},
             "meetings": [], "projects": [], "settings": {}}
    unused = lambda *_: {}  # noqa: E731
    api = Api(snapshot=lambda: empty, file_meeting=unused, save_settings=unused, forget=unused)
    server = make_server(api, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/popover.html"

    frames = Path(tempfile.mkdtemp(prefix="fly-demo-"))
    durations: list[float] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome")
            page = browser.new_page(viewport=VIEWPORT, device_scale_factor=2, color_scheme="light")
            page.goto(url)
            page.wait_for_selector(".rec")
            page.add_script_tag(path=str(STAGE))
            page.wait_for_timeout(300)
            for i in range(page.evaluate("demo.count")):
                durations.append(page.evaluate("demo.next()") / 1000)
                page.screenshot(path=str(frames / f"{i:03}.png"), clip=CROP)
            browser.close()

        listing = frames / "frames.txt"
        lines = [f"file '{frames / f'{i:03}.png'}'\nduration {d}" for i, d in enumerate(durations)]
        # The concat demuxer ignores the last duration unless the file repeats.
        lines.append(f"file '{frames / f'{len(durations) - 1:03}.png'}'")
        listing.write_text("\n".join(lines) + "\n")
        scale = f"scale={GIF_WIDTH}:-1:flags=lanczos"
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
                "-vf", f"{scale},split[a][b];[a]palettegen=stats_mode=full[p];[b][p]paletteuse=dither=sierra2_4a",
                "-fps_mode", "vfr", str(OUT),
            ],
            check=True,
        )
    finally:
        server.shutdown()
        shutil.rmtree(frames, ignore_errors=True)
    print(f"{OUT.relative_to(ROOT)}: {len(durations)} frames, {sum(durations):.1f} s, "
          f"{OUT.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
