from __future__ import annotations

from pathlib import Path

from .types import StreamKind, TimelineEvent
from .connect_xml import parse_connect_xml


def parse_mainstream_events(mainstream_xml: Path) -> list[TimelineEvent]:
    msgs = parse_connect_xml(mainstream_xml)
    events: list[TimelineEvent] = []
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
                        events.append(
                            TimelineEvent(
                                t_ms=m.t_ms,
                                type="doc_share",
                                payload={"code": code, "op": "stop" if code == "delete" else "set", **doc},
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

