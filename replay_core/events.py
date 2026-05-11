from __future__ import annotations

from pathlib import Path

from .types import StreamKind, TimelineEvent
from .connect_xml import parse_connect_xml


# Keys (case-insensitive) we treat as a "page-like" cursor under setContentSo payloads.
# Connect doesn't document these stably across versions; we accept the common ones and
# clamp to a sane range so unrelated counters can't masquerade as page changes.
_PAGE_KEYS = frozenset(
    {
        "currentpage",
        "currpage",
        "page",
        "pageindex",
        "pageno",
        "pagenum",
        "pagenumber",
        "currentslide",
        "slide",
        "slideindex",
        "slideno",
        "slidenum",
        "frame",
        "frameindex",
        "frameno",
        "framenumber",
    }
)


def _find_page_hint(value: object, depth: int = 0) -> int | None:
    """Best-effort recursive scan for a page index under a setContentSo payload.

    We accept ints (or numeric strings) for keys matching :data:`_PAGE_KEYS`. The value
    must be a small non-negative int — page counters in the wild start at 0 or 1 and
    realistic decks rarely exceed a few hundred slides.
    """

    if depth > 8:
        return None
    if isinstance(value, dict):
        for k, v in value.items():
            key = str(k).lower()
            if key in _PAGE_KEYS:
                try:
                    n = int(float(v))  # tolerate "3", "3.0"
                except (TypeError, ValueError):
                    continue
                if 0 <= n < 1000:
                    return n
        for v in value.values():
            hit = _find_page_hint(v, depth + 1)
            if hit is not None:
                return hit
    elif isinstance(value, list):
        for item in value:
            hit = _find_page_hint(item, depth + 1)
            if hit is not None:
                return hit
    return None


def parse_mainstream_events(mainstream_xml: Path) -> list[TimelineEvent]:
    msgs = parse_connect_xml(mainstream_xml)
    events: list[TimelineEvent] = []
    # The most recent document we've seen become active. Used to attribute later
    # page-change deltas (which don't always repeat the document descriptor).
    last_active_doc: dict | None = None
    for m in msgs:
        if m.method != "playEvent" or not m.args:
            continue

        if (
            len(m.args) >= 3
            and isinstance(m.args[0], dict)
            and str(m.args[0].get("name") or "") == "userVoipStatusChanged"
        ):
            publisher_id = m.args[1]
            is_talking_raw = m.args[2]
            pid = str(publisher_id)
            is_talking = str(is_talking_raw).lower() == "true"
            events.append(TimelineEvent(t_ms=m.t_ms, type="voip_status", payload={"publisherId": pid, "talking": is_talking}))
            continue

        action = None
        payload: dict = {}

        if len(m.args) >= 2 and isinstance(m.args[1], str):
            action = m.args[1]
            if len(m.args) >= 3:
                payload["args"] = m.args[2]
            if isinstance(m.args[0], dict):
                payload["meta"] = m.args[0]
        elif len(m.args) >= 1 and isinstance(m.args[0], str):
            action = m.args[0]

        if action == "streamAdded":
            events.append(TimelineEvent(t_ms=m.t_ms, type="stream_added", payload=payload))
        elif action == "playStream":
            stream_id = ""
            args = payload.get("args")
            if isinstance(args, list) and args and isinstance(args[0], str):
                stream_id = args[0]
            payload["streamId"] = stream_id
            events.append(TimelineEvent(t_ms=m.t_ms, type="play_stream", payload=payload))
        elif action == "stopStream":
            stream_id = ""
            args = payload.get("args")
            if isinstance(args, list) and args and isinstance(args[0], str):
                stream_id = args[0]
            payload["streamId"] = stream_id
            events.append(TimelineEvent(t_ms=m.t_ms, type="stop_stream", payload=payload))
        elif action == "setContentSo":
            args = payload.get("args")
            if isinstance(args, list):
                for item in args:
                    if not isinstance(item, dict):
                        continue
                    code = str(item.get("code") or "")
                    newv = item.get("newValue")
                    oldv = item.get("oldValue")

                    def extract_stream_id(v):
                        if not isinstance(v, dict):
                            return ""
                        sd = v.get("screenDescriptor")
                        if isinstance(sd, dict):
                            return str(sd.get("streamID") or "")
                        return ""

                    def extract_document(v):
                        if not isinstance(v, dict):
                            return None
                        share_type = str(v.get("shareType") or "")
                        if share_type not in {"document", "wb"}:
                            return None
                        ct_id = str(v.get("ctID") or "")
                        who = str(v.get("whoStartedIt") or "")
                        dd = v.get("documentDescriptor")
                        name = ""
                        if isinstance(dd, dict):
                            name = str(dd.get("theName") or "")
                        return {"shareType": share_type, "ctID": ct_id, "whoStartedIt": who, "docName": name}

                    sid_new = extract_stream_id(newv)
                    sid_old = extract_stream_id(oldv)
                    if sid_new or sid_old:
                        events.append(
                            TimelineEvent(
                                t_ms=m.t_ms,
                                type="screen_share",
                                payload={"code": code, "streamId": sid_new or sid_old, "op": "stop" if code == "delete" else "start"},
                            )
                        )
                        continue

                    doc = extract_document(newv) or extract_document(oldv)
                    if doc:
                        op = "stop" if code == "delete" else "set"
                        events.append(
                            TimelineEvent(
                                t_ms=m.t_ms,
                                type="doc_share",
                                payload={"code": code, "op": op, **doc},
                            )
                        )
                        if op == "stop":
                            last_active_doc = None
                        else:
                            last_active_doc = dict(doc)
                        continue

                    # No screen/document descriptor in this delta — look for a page
                    # cursor. We can only attribute the page to the currently active
                    # document; otherwise drop the hint.
                    if last_active_doc is not None:
                        page_hit = _find_page_hint(newv) or _find_page_hint(oldv)
                        if page_hit is not None:
                            events.append(
                                TimelineEvent(
                                    t_ms=m.t_ms,
                                    type="doc_page",
                                    payload={
                                        "ctID": str(last_active_doc.get("ctID") or ""),
                                        "docName": str(last_active_doc.get("docName") or ""),
                                        "page": int(page_hit),
                                    },
                                )
                            )
        else:
            events.append(TimelineEvent(t_ms=m.t_ms, type="unknown", payload={"action": action, **payload}))

    return events


def _kind_from_stream_type(stream_type: str) -> StreamKind:
    st = (stream_type or "").lower()
    if "screenshare" in st:
        return "screenshare"
    if "cameravoip" in st or "camera" in st or "voip" in st:
        return "camera"
    return "other"


def build_stream_map_from_events(folder: Path, events: list[TimelineEvent]) -> dict[str, dict]:
    mapping: dict[str, dict] = {}
    for ev in events:
        if ev.type != "stream_added":
            continue
        args = ev.payload.get("args")
        if not isinstance(args, list):
            continue
        for item in args:
            if not isinstance(item, dict):
                continue
            sid = str(item.get("streamId") or "")
            sname = str(item.get("streamName") or "")
            skind = _kind_from_stream_type(str(item.get("streamType") or ""))
            if not sid or not sname:
                continue
            base = sname.lstrip("/")
            flv = folder / f"{base}.flv"
            if flv.exists():
                mapping[sid] = {
                    "path": flv,
                    "kind": skind,
                    "stream_type": str(item.get("streamType") or ""),
                    "publisher_id": str(item.get("streamPublisherID") or ""),
                }
    return mapping

