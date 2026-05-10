from __future__ import annotations

import os
import sys
from pathlib import Path

from replay_web.run_server import main as run_main


def _prepend_bundled_bin_to_path() -> None:
    """
    For Windows release zips we ship ffmpeg/ffprobe under ./bin next to the exe.
    Prepend that folder to PATH so subprocess calls to `ffmpeg` / `ffprobe` resolve.
    """
    try:
        exe_dir = Path(sys.executable).resolve().parent
    except Exception:
        exe_dir = Path.cwd()
    bin_dir = exe_dir / "bin"
    if bin_dir.is_dir():
        os.environ["PATH"] = os.fspath(bin_dir) + os.pathsep + os.environ.get("PATH", "")


def main() -> int:
    _prepend_bundled_bin_to_path()
    return int(run_main() or 0)


if __name__ == "__main__":
    raise SystemExit(main())

