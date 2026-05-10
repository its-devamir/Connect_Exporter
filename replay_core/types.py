from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

EventType = Literal[
    "stream_added",
    "play_stream",
    "stop_stream",
    "screen_share",
    "doc_share",
    "voip_status",
    "chat",
    "unknown",
]
StreamKind = Literal["screenshare", "camera", "content", "other"]


@dataclass(frozen=True, slots=True)
class TimelineEvent:
    t_ms: int
    type: EventType
    payload: dict


@dataclass(frozen=True, slots=True)
class ActiveVideo:
    path: Path
    start_ms: int
    stream_id: str
    kind: StreamKind

