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


def _has_stream(path: Path, kind: str) -> bool:
    if not path.is_file():
        return False
    cmd = [
        "ffprobe",
        "-hide_banner",
        "-loglevel",
        "error",
        "-select_streams",
        kind[:1],
        "-show_entries",
        "stream=codec_type",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, text=True)
    if p.returncode != 0:
        return False
    return kind in (p.stdout or "")


@lru_cache(maxsize=512)
def has_video_stream(path: Path) -> bool:
    """Return True if ``path`` contains at least one decodable video stream.

    Connect's session sometimes points a ``screen_share`` event at a streamId
    that actually resolves to an audio-only FLV (a misclassified voip / camera
    stream). Including such a file as a screenshare input makes FFmpeg fail the
    whole filtergraph with the misleading ``Stream specifier ':v' ... matches
    no streams`` error, so we probe ahead of time and skip those clips.
    """

    return _has_stream(Path(path), "video")


@lru_cache(maxsize=512)
def has_audio_stream(path: Path) -> bool:
    """Return True if ``path`` contains at least one decodable audio stream.

    Mirrors :func:`has_video_stream` for the audio side: some cameraVoip FLVs
    end up empty or metadata-only, and passing them through the ``[N:a]adelay``
    chain produces the ``Stream specifier ':a' ... matches no streams`` error.
    """

    return _has_stream(Path(path), "audio")

