from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Chapter:
    start_ms: int
    end_ms: int
    title: str


def write_ffmetadata_chapters(chapters: list[Chapter], out_path: Path) -> Path:
    """
    Write ffmpeg ffmetadata with CHAPTER blocks.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [";FFMETADATA1"]
    for ch in chapters:
        start = max(0, int(ch.start_ms))
        end = max(start + 1, int(ch.end_ms))
        title = ch.title.replace("\n", " ").strip()
        lines += [
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={start}",
            f"END={end}",
            f"title={title}",
        ]
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path

