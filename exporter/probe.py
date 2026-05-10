from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=512)
def probe_duration_ms(path: Path) -> int:
    path = Path(path)
    cmd = [
        "ffprobe",
        "-hide_banner",
        "-loglevel",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, text=True)
    if p.returncode != 0:
        return 0
    s = (p.stdout or "").strip()
    try:
        return int(float(s) * 1000.0)
    except Exception:
        return 0

