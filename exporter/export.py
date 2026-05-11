from __future__ import annotations

import argparse
import sys
import traceback
from dataclasses import replace
from pathlib import Path

from replay_core.session_model import SessionModel
from replay_core.chat import parse_ftchat
from exporter.chat_ass import cues_from_ftchat, write_chat_ass
from exporter.edl import (
    Clip,
    build_av_clips,
    build_doc_image_clips,
    build_doc_markers,
    materialize_doc_image_clips,
)
from exporter.ffmpeg_render import RenderConfig, render_fast_mp4
from exporter.timewarp import Break, Timewarp
from exporter.probe import probe_duration_ms
from exporter.chapters import Chapter, write_ffmetadata_chapters


def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    intervals = [(int(a), int(b)) for a, b in intervals if b > a]
    intervals.sort()
    out: list[tuple[int, int]] = []
    for a, b in intervals:
        if not out:
            out.append((a, b))
            continue
        la, lb = out[-1]
        if a <= lb:
            out[-1] = (la, max(lb, b))
        else:
            out.append((a, b))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", default=".", help="Export folder containing mainstream.xml + flv/xml files")
    ap.add_argument("--out", default="replay.mp4", help="Output MP4 path")
    ap.add_argument("--w", type=int, default=1280)
    ap.add_argument("--h", type=int, default=720)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--crf", type=int, default=28)
    ap.add_argument("--preset", default="ultrafast")
    ap.add_argument(
        "--encoder",
        default="auto",
        help="Video encoder: auto|libx264|h264_nvenc (auto picks NVENC if available)",
    )
    ap.add_argument("--no-chat", dest="chat", action="store_false", default=True, help="Do not burn chat subtitles")
    ap.add_argument("--no-chapters", dest="chapters", action="store_false", default=True, help="Do not write MP4 chapter metadata")
    ap.add_argument(
        "--no-skip-breaks", dest="skip_breaks", action="store_false", default=True, help="Do not detect/skip long inactivity gaps"
    )
    args = ap.parse_args()

    folder = Path(args.folder)
    out = Path(args.out)

    session = SessionModel.from_folder(folder)
    audio, video = build_av_clips(session)
    doc = build_doc_markers(session)
    # Attached-PDF page overlays — only filled in when the user uploaded a matching
    # PDF in the Materials wizard step (or dropped one into <session>/materials/).
    doc_images_raw = build_doc_image_clips(session, stage_w=int(args.w), stage_h=int(args.h))

    # Chat from ftchat (has sender PID).
    chat_msgs = parse_ftchat(folder)
    chat_cues = cues_from_ftchat(chat_msgs)

    breaks: list[Break] = []
    tw = Timewarp(breaks)

    if args.skip_breaks:
        # Detect long breaks: no active audio chunks and no chat inside window.
        # We estimate active windows from chunk start + ffprobe duration, then merge.
        windows: list[tuple[int, int]] = []
        for c in audio:
            if c.src is None:
                continue
            dur = probe_duration_ms(c.src)
            if dur <= 0:
                continue
            # Small tail pad avoids "break starts a bit early" due to probe rounding.
            windows.append((c.start_ms, c.start_ms + dur + 250))
        merged = _merge_intervals(windows)
        if merged:
            gap_threshold_ms = 15 * 60 * 1000
            # Choose the biggest eligible gap.
            best: tuple[int, int] | None = None
            best_len = 0
            for (_, a1), (b0, _) in zip(merged, merged[1:]):
                gap = b0 - a1
                if gap < gap_threshold_ms:
                    continue
                if any(a1 <= m.t_ms <= b0 for m in chat_msgs):
                    continue
                if gap > best_len:
                    best = (a1, b0)
                    best_len = gap
            if best is not None:
                breaks.append(Break(start_ms=best[0], end_ms=best[1], slate_ms=3000))

        tw = Timewarp(breaks)

    def warp_clips(clips):
        if not breaks:
            return clips
        out = []
        for c in clips:
            if tw.is_inside_break(c.start_ms):
                continue
            start = tw.map_time(c.start_ms)
            end = tw.map_time(c.end_ms)
            if end <= start:
                continue
            out.append(replace(c, start_ms=start, end_ms=end))
        return out

    audio = warp_clips(audio)
    video = warp_clips(video)
    doc = warp_clips(doc)
    doc_images_raw = warp_clips(doc_images_raw)

    # Add break slate overlay(s).
    if breaks:
        for b in breaks:
            mins = max(1, int(round((b.end_ms - b.start_ms) / 60000.0)))
            start = tw.map_time(b.start_ms)
            doc.append(
                Clip(
                    kind="break",
                    src=None,
                    start_ms=start,
                    end_ms=start + b.slate_ms,
                    label=f"Skipping {mins} minute break time",
                )
            )

    # Apply timewarp to chat cues (skip breaks).
    if breaks:
        warped = []
        for c in chat_cues:
            if tw.is_inside_break(c.t_ms):
                continue
            warped.append(type(c)(t_ms=tw.map_time(c.t_ms), who=c.who, text=c.text))
        chat_cues = warped

    chat_ass = (
        write_chat_ass(chat_cues, folder / ".replay_cache" / "chat.ass")
        if (args.chat and chat_cues)
        else None
    )

    # Chapters ("timeline headings"): chunk starts + break slate start.
    chapters: list[Chapter] = []
    if args.chapters:
        for inst in session.stream_instances:
            if inst.kind == "camera":
                t = tw.map_time(inst.start_time_ms) if breaks else inst.start_time_ms
                chapters.append(Chapter(start_ms=t, end_ms=t + 1, title=f"Audio: {inst.path.name}"))
        for o in doc:
            if o.kind == "break":
                chapters.append(Chapter(start_ms=o.start_ms, end_ms=o.start_ms + 1, title=o.label or "Break"))
        chapters.sort(key=lambda c: c.start_ms)
    ffmeta = (
        write_ffmetadata_chapters(chapters, folder / ".replay_cache" / "chapters.ffmeta")
        if chapters
        else None
    )

    print("[export] Building timeline and filters...", flush=True)
    cfg = RenderConfig(width=args.w, height=args.h, fps=args.fps, crf=args.crf, preset=args.preset, encoder=str(args.encoder))

    # Materialize PDF pages now that the stage size is locked in.
    doc_images: list[Clip] = []
    if doc_images_raw:
        cache_dir = folder / ".replay_cache" / "materials"
        try:
            doc_images = materialize_doc_image_clips(
                doc_images_raw, stage_w=int(cfg.width), cache_dir=cache_dir
            )
        except Exception as e:
            print(f"[export] PDF page rendering disabled: {e}", flush=True)
            doc_images = []
    overlays = doc + doc_images

    try:
        render_fast_mp4(
            out_mp4=out,
            duration_ms=tw.map_time(session.duration_ms) if breaks else session.duration_ms,
            audio_clips=audio,
            video_clips=video,
            overlays=overlays,
            chat_ass=chat_ass,
            ffmetadata=ffmeta,
            cfg=cfg,
        )
    except Exception as e:
        print("Export failed:", flush=True)
        print(str(e), flush=True)
        traceback.print_exc(file=sys.stdout)
        return 1

    print(f"Wrote: {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

