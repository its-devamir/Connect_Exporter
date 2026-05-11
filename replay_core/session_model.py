from __future__ import annotations

from dataclasses import dataclass, field
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
class SharedDocument:
    """A document/PDF that was shared at some point during the session."""

    name: str
    ct_id: str
    who_started: str
    first_seen_ms: int
    last_seen_ms: int
    active_ms: int  # cumulative time the doc was the active share


@dataclass(frozen=True, slots=True)
class DocPageSegment:
    """Range [start_ms, end_ms) during which a specific PDF page was on screen."""

    ct_id: str
    doc_name: str
    page_index: int  # 0-based
    start_ms: int
    end_ms: int


@dataclass(frozen=True, slots=True)
class SessionModel:
    folder: Path
    events: list[TimelineEvent]
    timeline: TimelineEngine
    duration_ms: int
    stream_map: dict[str, dict]
    stream_instances: list[StreamInstance]
    documents: list[SharedDocument] = field(default_factory=list)
    doc_page_segments: list[DocPageSegment] = field(default_factory=list)

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

        documents, doc_page_segments = _summarize_documents(events, duration_ms)

        timeline = TimelineEngine(events=events, stream_map=stream_map)
        return SessionModel(
            folder=folder,
            events=events,
            timeline=timeline,
            duration_ms=duration_ms,
            stream_map=stream_map,
            stream_instances=instances,
            documents=documents,
            doc_page_segments=doc_page_segments,
        )


def _summarize_documents(
    events: list[TimelineEvent], duration_ms: int
) -> tuple[list[SharedDocument], list[DocPageSegment]]:
    """Reduce raw doc_share / doc_page events into per-document summaries.

    A document is keyed by ``(ctID, name)``. We track when it became active and the
    cumulative time it was the active share. Page segments are closed when:
    - the same doc switches to another page,
    - the doc is replaced by another share, or
    - the session ends.
    """

    docs_acc: dict[tuple[str, str], dict] = {}
    page_segs: list[DocPageSegment] = []

    active_key: tuple[str, str] | None = None
    active_start_ms: int = 0
    active_page: int = 0
    active_page_start_ms: int = 0

    def _close_page(end_ms: int) -> None:
        nonlocal active_page_start_ms
        if active_key is None:
            return
        ct, name = active_key
        if end_ms > active_page_start_ms:
            page_segs.append(
                DocPageSegment(
                    ct_id=ct,
                    doc_name=name,
                    page_index=int(active_page),
                    start_ms=int(active_page_start_ms),
                    end_ms=int(end_ms),
                )
            )
        active_page_start_ms = end_ms

    def _close_active(end_ms: int) -> None:
        nonlocal active_key, active_start_ms, active_page, active_page_start_ms
        if active_key is None:
            return
        _close_page(end_ms)
        ct, name = active_key
        acc = docs_acc[(ct, name)]
        acc["active_ms"] += max(0, end_ms - active_start_ms)
        acc["last_seen_ms"] = max(acc["last_seen_ms"], end_ms)
        active_key = None

    for ev in sorted(events, key=lambda e: e.t_ms):
        if ev.type == "doc_share":
            name = str(ev.payload.get("docName") or "")
            ct = str(ev.payload.get("ctID") or "")
            share_type = str(ev.payload.get("shareType") or "")
            op = str(ev.payload.get("op") or "set")
            if share_type != "document" or not name:
                if op == "stop" and active_key is not None:
                    _close_active(ev.t_ms)
                continue
            key = (ct, name)
            if op == "stop":
                if active_key == key or active_key is not None:
                    _close_active(ev.t_ms)
                continue
            # "set" — a new active doc. Close any prior active doc first.
            if active_key is not None and active_key != key:
                _close_active(ev.t_ms)
            if active_key != key:
                acc = docs_acc.setdefault(
                    key,
                    {
                        "name": name,
                        "ct_id": ct,
                        "who_started": str(ev.payload.get("whoStartedIt") or ""),
                        "first_seen_ms": ev.t_ms,
                        "last_seen_ms": ev.t_ms,
                        "active_ms": 0,
                    },
                )
                acc["last_seen_ms"] = max(acc["last_seen_ms"], ev.t_ms)
                active_key = key
                active_start_ms = ev.t_ms
                active_page = 0
                active_page_start_ms = ev.t_ms

        elif ev.type == "doc_page":
            if active_key is None:
                continue
            try:
                new_page = int(ev.payload.get("page") or 0)
            except (TypeError, ValueError):
                continue
            if new_page == active_page:
                continue
            _close_page(ev.t_ms)
            active_page = new_page

    _close_active(duration_ms)

    documents = [
        SharedDocument(
            name=acc["name"],
            ct_id=acc["ct_id"],
            who_started=acc["who_started"],
            first_seen_ms=int(acc["first_seen_ms"]),
            last_seen_ms=int(acc["last_seen_ms"]),
            active_ms=int(acc["active_ms"]),
        )
        for acc in docs_acc.values()
    ]
    documents.sort(key=lambda d: (d.first_seen_ms, d.name.lower()))

    page_segs.sort(key=lambda s: (s.start_ms, s.page_index))
    return documents, page_segs

