from __future__ import annotations

from bisect import bisect_right
from pathlib import Path

from .types import ActiveVideo, TimelineEvent


class TimelineEngine:
    def __init__(self, events: list[TimelineEvent], stream_map: dict[str, dict]):
        self._events = sorted(events, key=lambda e: e.t_ms)
        self._times = [e.t_ms for e in self._events]
        self._stream_map = dict(stream_map)

        self._screen_after: list[ActiveVideo | None] = []
        self._camera_after: list[ActiveVideo | None] = []
        self._doc_after: list[dict | None] = []
        screen: ActiveVideo | None = None
        camera: ActiveVideo | None = None
        doc: dict | None = None
        for ev in self._events:
            if ev.type == "play_stream":
                sid = str(ev.payload.get("streamId") or ev.payload.get("stream_id") or "")
                if sid and sid in self._stream_map:
                    info = self._stream_map[sid]
                    av = ActiveVideo(
                        path=Path(info["path"]),
                        start_ms=ev.t_ms,
                        stream_id=sid,
                        kind=str(info.get("kind", "other")),  # type: ignore[arg-type]
                    )
                    if av.kind == "screenshare":
                        screen = av
                    elif av.kind == "camera":
                        camera = av
                    else:
                        screen = av
            elif ev.type == "screen_share":
                sid = str(ev.payload.get("streamId") or "")
                op = str(ev.payload.get("op") or "start")
                if op == "stop":
                    if screen and (not sid or sid == screen.stream_id):
                        screen = None
                else:
                    if sid and sid in self._stream_map:
                        info = self._stream_map[sid]
                        screen = ActiveVideo(path=Path(info["path"]), start_ms=ev.t_ms, stream_id=sid, kind="screenshare")
            elif ev.type == "stop_stream":
                sid = str(ev.payload.get("streamId") or ev.payload.get("stream_id") or "")
                if not sid:
                    screen = None
                    camera = None
                else:
                    if screen and sid == screen.stream_id:
                        screen = None
                    if camera and sid == camera.stream_id:
                        camera = None
            elif ev.type == "doc_share":
                op = str(ev.payload.get("op") or "set")
                if op == "stop":
                    doc = None
                else:
                    doc = dict(ev.payload)
            self._screen_after.append(screen)
            self._camera_after.append(camera)
            self._doc_after.append(doc)

    @property
    def events(self) -> list[TimelineEvent]:
        return self._events

    def get_state_at(self, t_ms: int) -> dict:
        t_ms = int(t_ms)
        idx = bisect_right(self._times, t_ms) - 1
        screen = self._screen_after[idx] if idx >= 0 else None
        camera = self._camera_after[idx] if idx >= 0 else None
        doc = self._doc_after[idx] if idx >= 0 else None

        def pack(av: ActiveVideo | None):
            if av is None:
                return None
            return {"path": av.path, "start_ms": av.start_ms, "stream_id": av.stream_id, "kind": av.kind}

        stage = screen
        audio = camera or stage
        return {"screen": pack(stage), "camera": pack(camera), "audio": pack(audio), "doc": doc}

