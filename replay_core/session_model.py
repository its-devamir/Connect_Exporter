from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .timeline_engine import TimelineEngine
from .types import TimelineEvent
from .events import build_stream_map_from_events, parse_mainstream_events


@dataclass(frozen=True, slots=True)
class StreamInstance:
    logical_id: str
    start_time_ms: int
    path: Path
    kind: str
    publisher_id: str


@dataclass(frozen=True, slots=True)
class SessionModel:
    folder: Path
    events: list[TimelineEvent]
    timeline: TimelineEngine
    duration_ms: int
    stream_map: dict[str, dict]
    stream_instances: list[StreamInstance]

    def resolve_stream_instance(self, logical_id: str, t_ms: int) -> StreamInstance | None:
        logical_id = str(logical_id)
        t_ms = int(t_ms)
        candidates = [s for s in self.stream_instances if s.logical_id == logical_id and s.start_time_ms <= t_ms]
        if not candidates:
            return None
        return max(candidates, key=lambda s: s.start_time_ms)

    @staticmethod
    def from_folder(folder: Path) -> "SessionModel":
        folder = Path(folder)
        mainstream = folder / "mainstream.xml"
        if not mainstream.exists():
            raise FileNotFoundError(f"Missing mainstream.xml in {folder}")

        events = parse_mainstream_events(mainstream)
        stream_map = build_stream_map_from_events(folder, events)

        instances: list[StreamInstance] = []
        for ev in events:
            if ev.type != "stream_added":
                continue
            args = ev.payload.get("args")
            if not isinstance(args, list):
                continue
            for item in args:
                if not isinstance(item, dict):
                    continue
                logical_id = str(item.get("streamId") or "")
                stream_name = str(item.get("streamName") or "").lstrip("/")
                if not logical_id or not stream_name:
                    continue
                try:
                    start_time_ms = int(float(item.get("startTime") or ev.t_ms))
                except Exception:
                    start_time_ms = int(ev.t_ms)
                kind = str(stream_map.get(logical_id, {}).get("kind") or "other")
                publisher_id = str(item.get("streamPublisherID") or "")
                flv = folder / f"{stream_name}.flv"
                if flv.exists():
                    instances.append(
                        StreamInstance(
                            logical_id=logical_id,
                            start_time_ms=start_time_ms,
                            path=flv,
                            kind=kind,
                            publisher_id=publisher_id,
                        )
                    )

        instances.sort(key=lambda s: (s.logical_id, s.start_time_ms))

        for p in folder.glob("ftcontent*.flv"):
            sid = p.stem
            stream_map.setdefault(sid, {"path": p, "kind": "content", "stream_type": "FtContent", "publisher_id": ""})

        duration_ms = 0
        if events:
            meaningful = [e.t_ms for e in events if e.type in {"play_stream", "stop_stream", "screen_share", "doc_share"}]
            duration_ms = max(meaningful) if meaningful else max(e.t_ms for e in events)
            duration_ms = int(duration_ms + 5000)

        timeline = TimelineEngine(events=events, stream_map=stream_map)
        return SessionModel(
            folder=folder,
            events=events,
            timeline=timeline,
            duration_ms=duration_ms,
            stream_map=stream_map,
            stream_instances=instances,
        )

