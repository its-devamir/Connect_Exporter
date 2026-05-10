from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from replay_core.session_model import SessionModel


@dataclass(frozen=True, slots=True)
class Clip:
    kind: str  # "audio" | "video" | "break" | "doc_marker"
    src: Path | None
    start_ms: int
    end_ms: int
    label: str = ""
    src_start_ms: int = 0  # local offset into src stream (ms)


def _ms(x) -> int:
    return int(max(0, int(x)))


def build_av_clips(session: SessionModel) -> tuple[list[Clip], list[Clip]]:
    """
    Build audio clips from voip play_stream switches.
    Build video clips from screenshare start/stop.
    If no screenshare, video is empty (caller can synthesize black).
    """
    audio: list[Clip] = []
    video: list[Clip] = []

    # Audio: we do NOT rely on playStream for file selection.
    # Correct model: each streamAdded chunk is a time-stamped audio slice on the global timeline.
    # We place the whole chunk at its startTime and mix them.
    for inst in session.stream_instances:
        if inst.kind != "camera":
            continue
        audio.append(
            Clip(
                kind="audio",
                src=inst.path,
                start_ms=_ms(inst.start_time_ms),
                end_ms=_ms(session.duration_ms),
                label=f"{inst.logical_id}:{inst.path.name}",
                src_start_ms=0,
            )
        )

    # Video: screenshare from screen_share events.
    ss = [e for e in session.events if e.type == "screen_share"]
    ss.sort(key=lambda e: e.t_ms)
    active_sid: str | None = None
    active_start = 0
    for ev in ss:
        sid = str(ev.payload.get("streamId") or "")
        op = str(ev.payload.get("op") or "")
        if op == "start":
            active_sid = sid
            active_start = ev.t_ms
        elif op == "stop" and active_sid:
            info = session.stream_map.get(active_sid, {})
            if info:
                video.append(
                    Clip(
                        kind="video",
                        src=Path(info["path"]),
                        start_ms=_ms(active_start),
                        end_ms=_ms(ev.t_ms),
                        label=active_sid,
                    )
                )
            active_sid = None

    # If a screenshare was started but never explicitly stopped,
    # keep it visible until the end of the session.
    if active_sid:
        info = session.stream_map.get(active_sid, {})
        if info:
            video.append(
                Clip(
                    kind="video",
                    src=Path(info["path"]),
                    start_ms=_ms(active_start),
                    end_ms=_ms(session.duration_ms),
                    label=active_sid,
                )
            )

    return audio, video


def build_doc_markers(session: SessionModel) -> list[Clip]:
    out: list[Clip] = []
    for ev in session.events:
        if ev.type != "doc_share":
            continue
        share_type = str(ev.payload.get("shareType") or "")
        name = str(ev.payload.get("docName") or "")
        if share_type == "document" and name:
            out.append(Clip(kind="doc_marker", src=None, start_ms=_ms(ev.t_ms), end_ms=_ms(ev.t_ms + 4000), label=name))
    return out

