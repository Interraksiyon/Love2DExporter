"""Create persistent desktop smoke builds for testing on their target OS.

The headless game writes a marker with FUSED=true and exits. Example on Linux:
  ./AppRun /absolute/path/to/marker.txt
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import exporter
from PIL import Image
from test_desktop_exports import SMOKE_CONF, SMOKE_MAIN


def main():
    output = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "verification" / "native-smoke"
    project = output / "project"
    (project / "assets").mkdir(parents=True, exist_ok=True)
    (project / "main.lua").write_text(SMOKE_MAIN, encoding="utf-8")
    (project / "conf.lua").write_text(SMOKE_CONF, encoding="utf-8")
    (project / "assets" / "payload.txt").write_text("embedded-game-ok", encoding="utf-8")
    icon = output / "icon.png"
    Image.new("RGBA", (24, 16), (50, 125, 220, 255)).save(icon)
    love = exporter.create_love_file(project, output / "Source" / "smoke.love")
    for build in (
        lambda: exporter.export_windows(love, icon, output / "Windows", "Smoke Game"),
        lambda: exporter.export_mac(love, icon, output / "macOS", "Smoke Game"),
        lambda: exporter.export_linux(love, output / "Linux", "love"),
    ):
        print(build())


if __name__ == "__main__":
    main()
