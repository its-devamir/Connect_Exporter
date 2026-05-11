from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from replay_core.session_model import SessionModel
from replay_web.materials import find_pdf_for_document


# Files whose name we don't want to render as a stage text marker.
# Hosts often pre-load images/screenshots/videos into the share pod; turning each
# one into a drawtext overlay both looks bad (six labels stacked on top of each
# other) and used to crash FFmpeg's filter parser via the chained-overlay path.
_NON_DOC_MARKER_EXTS = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".mp4", ".mov", ".webm", ".mkv", ".m4v"}
)

# Doc-share events whose start times are within this window of one another get
# collapsed into a single text marker so the drawtext filter chain stays short.
_DOC_MARKER_COLLAPSE_MS = 1500


@dataclass(frozen=True, slots=True)
class Clip:
    kind: str  # "audio" | "video" | "break" | "doc_marker" | "doc_image"
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
    """Short text overlays announcing a new document share.

    Only documents that the user did NOT attach as a real PDF get a marker — if
    they did, :func:`build_doc_image_clips` produces a richer page overlay and we
    don't want both at the same time.

    We also keep the marker set small and well-behaved so the FFmpeg
    ``filter_complex`` graph stays short:

    - Skip images / videos that Connect sometimes classifies as "documents"; their
      filenames aren't useful to display and they tend to be loaded in batches.
    - Collapse markers whose start times are within
      :data:`_DOC_MARKER_COLLAPSE_MS` of one another into a single marker (the
      most recent name wins). Long chains of simultaneous drawtext filters used
      to break FFmpeg's filter parser ("Stream specifier ':v' ... matches no
      streams").
    """

    raw: list[Clip] = []
    folder = Path(session.folder)
    for ev in session.events:
        if ev.type != "doc_share":
            continue
        share_type = str(ev.payload.get("shareType") or "")
        name = str(ev.payload.get("docName") or "")
        if share_type != "document" or not name:
            continue
        if find_pdf_for_document(folder, name) is not None:
            continue
        ext = Path(name).suffix.lower()
        if ext in _NON_DOC_MARKER_EXTS:
            continue
        raw.append(Clip(kind="doc_marker", src=None, start_ms=_ms(ev.t_ms), end_ms=_ms(ev.t_ms + 4000), label=name))

    raw.sort(key=lambda c: c.start_ms)
    out: list[Clip] = []
    for c in raw:
        if out and c.start_ms - out[-1].start_ms < _DOC_MARKER_COLLAPSE_MS:
            out[-1] = replace(out[-1], label=c.label, end_ms=max(out[-1].end_ms, c.end_ms))
        else:
            out.append(c)
    return out


def build_doc_image_clips(
    session: SessionModel,
    *,
    stage_w: int,
    stage_h: int,
) -> list[Clip]:
    """For each detected document that the user attached as a PDF, return clips that
    overlay the matching page as an image.

    The image source paths are populated lazily by the exporter (it owns the
    rasterizer and the cache directory) — we only emit ``src=None`` clips here with
    enough information in :attr:`Clip.label` to look up the page. The exporter calls
    :func:`materialize_doc_image_clips` next to fill the real PNG paths.
    """

    out: list[Clip] = []
    folder = Path(session.folder)

    # Build a lookup of (ct_id, doc_name) -> attached PDF path.
    name_to_pdf: dict[str, Path] = {}
    for d in session.documents:
        pdf = find_pdf_for_document(folder, d.name)
        if pdf is not None:
            name_to_pdf[(d.ct_id, d.name)] = pdf

    if not name_to_pdf:
        return out

    has_segments_for: set[tuple[str, str]] = set()
    for seg in session.doc_page_segments:
        key = (seg.ct_id, seg.doc_name)
        if key not in name_to_pdf:
            continue
        has_segments_for.add(key)
        # Encode (page_index, pdf_path) into the label; src filled in later.
        label = f"{seg.doc_name}|page={seg.page_index}|pdf={name_to_pdf[key]}"
        out.append(
            Clip(
                kind="doc_image",
                src=None,
                start_ms=_ms(seg.start_ms),
                end_ms=_ms(seg.end_ms),
                label=label,
            )
        )

    # For documents that were attached but had no page-change events captured, fall
    # back to "page 1 for the whole time it was active".
    for d in session.documents:
        key = (d.ct_id, d.name)
        if key not in name_to_pdf or key in has_segments_for:
            continue
        end = d.first_seen_ms + max(0, d.active_ms) if d.active_ms > 0 else d.last_seen_ms + 2000
        if end <= d.first_seen_ms:
            end = d.first_seen_ms + 2000
        label = f"{d.name}|page=0|pdf={name_to_pdf[key]}"
        out.append(
            Clip(
                kind="doc_image",
                src=None,
                start_ms=_ms(d.first_seen_ms),
                end_ms=_ms(end),
                label=label,
            )
        )

    out.sort(key=lambda c: c.start_ms)
    return out


def materialize_doc_image_clips(
    clips: list[Clip], *, stage_w: int, cache_dir: Path
) -> list[Clip]:
    """Rasterize the PDF page for each ``doc_image`` clip; drop clips whose render fails.

    This is split out from :func:`build_doc_image_clips` so the cheap "did the user
    attach anything" check can run during the build phase, and the (relatively
    expensive) PDF rendering only runs once we know the exporter is committed.
    """

    from .pdf_pages import available as _pdf_available, render_page

    if not clips:
        return clips
    if not _pdf_available():
        return [c for c in clips if c.kind != "doc_image"]

    materialized: list[Clip] = []
    for c in clips:
        if c.kind != "doc_image":
            materialized.append(c)
            continue
        # Decode the label encoded in build_doc_image_clips.
        try:
            parts = dict(part.split("=", 1) for part in c.label.split("|")[1:])
            page_index = int(parts["page"])
            pdf_path = Path(parts["pdf"])
        except Exception:
            continue
        try:
            png = render_page(
                pdf_path, page_index, target_width=int(stage_w), cache_dir=cache_dir
            )
        except Exception:
            continue
        materialized.append(
            Clip(
                kind="doc_image",
                src=png,
                start_ms=c.start_ms,
                end_ms=c.end_ms,
                label=c.label.split("|", 1)[0],
            )
        )
    return materialized

