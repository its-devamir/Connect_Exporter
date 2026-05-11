from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from tqdm import tqdm


# When exporter runs under the web UI subprocess, tqdm's in-place redraw uses \r
# and rarely emits full lines — the server never receives progress.
_BACKEND_PROGRESS = os.environ.get("CONNECT_EXPORT_BACKEND") == "1"

from exporter.edl import Clip
from exporter.probe import has_audio_stream, has_video_stream


# Screenshare clips shorter than this are typically the result of a host
# briefly switching streams; they wouldn't be visible anyway and they balloon
# the filtergraph (or even break it when the duration rounds to 0).
_MIN_SCREENSHARE_CLIP_MS = 200


@dataclass(frozen=True, slots=True)
class RenderConfig:
    width: int = 1280
    height: int = 720
    fps: int = 30
    crf: int = 28
    preset: str = "ultrafast"
    encoder: str = "auto"  # auto|libx264|h264_nvenc
    break_threshold_s: int = 8 * 60
    break_slate_s: int = 3


def _has_encoder(name: str) -> bool:
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return name in (p.stdout or "")


def _pick_v_encoder(cfg: RenderConfig) -> str:
    enc = (cfg.encoder or "auto").lower()
    if enc in {"libx264", "h264_nvenc"}:
        return enc
    if enc == "auto" and _has_encoder("h264_nvenc"):
        return "h264_nvenc"
    return "libx264"


def _nvenc_preset(libx_preset: str) -> str:
    """
    libx264 presets (ultrafast, veryfast, …) are invalid for h264_nvenc.
    Map to NVENC presets: hp / fast / medium / slow / hq (FFmpeg NVENC).
    """
    p = (libx_preset or "medium").strip().lower()
    if p in {"ultrafast", "superfast", "veryfast"}:
        return "hp"
    if p in {"faster", "fast"}:
        return "fast"
    if p in {"slow", "slower", "veryslow"}:
        return "slow"
    if p == "medium":
        return "medium"
    # Unknown UI string — bias toward fast encode
    return "hp"


def _sec(ms: int) -> float:
    return ms / 1000.0


def _ffmpeg_font_path_literal() -> str:
    """Single-quoted path segment for drawtext=fontfile='…' on Windows."""
    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    for name in ("arial.ttf", "segoeui.ttf", "calibri.ttf"):
        p = windir / "Fonts" / name
        if p.is_file():
            s = p.resolve().as_posix()
            if len(s) > 1 and s[1] == ":":
                return f"{s[0]}\\:{s[2:]}"
            return s.replace(":", "\\:")
    return r"C\:/Windows/Fonts/arial.ttf"


def _stage_dimensions(cfg: RenderConfig, vcodec: str) -> tuple[int, int]:
    """NVENC often rejects widths/heights not divisible by 4."""
    w0, h0 = max(16, int(cfg.width)), max(16, int(cfg.height))
    if vcodec == "h264_nvenc":
        w = max(16, (w0 + 3) // 4 * 4)
        h = max(16, (h0 + 3) // 4 * 4)
        return w, h
    return w0, h0


def _esc_drawtext(s: str) -> str:
    """Escape a string for use inside a drawtext ``text='...'`` value.

    FFmpeg's filtergraph parser has two escaping levels: the outer one (used to
    delimit filters with ``,``/``;``) and the inner one (used to separate filter
    options with ``:``). Single-quote wrapping is the standard way to protect the
    value at the outer level, but older FFmpeg builds in the wild are not always
    consistent about it. Explicitly escaping every metacharacter we control makes
    the chain robust across ffmpeg 4.x/5.x/6.x.
    """

    return (
        s.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace(",", "\\,")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace(";", "\\;")
    )


def _enable_window(start_s: float, end_s: float) -> str:
    """Return an FFmpeg ``enable=`` expression for ``[start_s, end_s]``.

    We deliberately avoid ``between(t,X,Y)`` because its inner commas have
    historically tripped FFmpeg's filter parser when several filters are chained
    together. ``gte(t\\,X)*lte(t\\,Y)`` is equivalent and only carries escaped
    commas, which every supported FFmpeg parses correctly.
    """

    return f"gte(t\\,{start_s:.3f})*lte(t\\,{end_s:.3f})"


def _drawtext(label: str, start_s: float, end_s: float, *, stage_h: int) -> str:
    label = _esc_drawtext(label)
    fs = max(10, min(44, int(stage_h / 10)))
    bw = max(4, min(18, int(stage_h / 22)))
    fp = _ffmpeg_font_path_literal()
    return (
        "drawtext="
        f"fontfile='{fp}':"
        f"fontcolor=white:fontsize={fs}:"
        f"box=1:boxcolor=black@0.55:boxborderw={bw}:"
        "x=(w-text_w)/2:y=(h-text_h)/2:"
        f"text='{label}':"
        f"enable={_enable_window(start_s, end_s)}"
    )


def render_fast_mp4(
    *,
    out_mp4: Path,
    duration_ms: int,
    audio_clips: list[Clip],
    video_clips: list[Clip],
    overlays: list[Clip],
    chat_ass: Path | None = None,
    ffmetadata: Path | None = None,
    cfg: RenderConfig = RenderConfig(),
) -> None:
    """
    Render a single MP4 with:
    - Stage video: screenshare clips when present, otherwise black
    - Audio: concatenated clip strategy (best-effort)
    - Overlays: doc markers + break markers as drawtext

    Notes:
    - This is a correctness-first exporter that remains fast by avoiding Python decoding.
    - For very large segment counts, we’ll move to a filter_complex_script file.
    """

    out_mp4 = Path(out_mp4)
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = out_mp4.with_name(out_mp4.name + ".partial.mp4")

    vcodec = _pick_v_encoder(cfg)
    stage_w, stage_h = _stage_dimensions(cfg, vcodec)

    # Drop screenshare clips that would break the filtergraph:
    # - sources that don't actually carry a video stream (Connect occasionally
    #   points a screen_share event at an audio-only FLV);
    # - extremely short clips (< _MIN_SCREENSHARE_CLIP_MS) that wouldn't render
    #   anyway and that have been observed to make older FFmpeg builds choke.
    _v_checked: dict[str, bool] = {}
    safe_video_clips: list[Clip] = []
    for vc in video_clips:
        if vc.src is None:
            continue
        dur_ms = max(0, int(vc.end_ms) - int(vc.start_ms))
        if dur_ms < _MIN_SCREENSHARE_CLIP_MS:
            print(
                f"[export] skip degenerate screenshare clip ({dur_ms} ms) src={vc.src.name}",
                flush=True,
            )
            continue
        k = str(vc.src)
        ok = _v_checked.get(k)
        if ok is None:
            ok = has_video_stream(vc.src)
            _v_checked[k] = ok
        if not ok:
            print(
                f"[export] skip screenshare clip (no video stream) src={vc.src.name}",
                flush=True,
            )
            continue
        safe_video_clips.append(vc)
    video_clips = safe_video_clips

    # Same dance for audio clips: cameraVoip FLVs are sometimes empty / metadata
    # only. We have to filter before building the input list because every
    # adelay node addresses its input by absolute index ``[N:a]``.
    _a_checked: dict[str, bool] = {}
    safe_audio_clips: list[Clip] = []
    for ac in audio_clips:
        if ac.src is None:
            continue
        k = str(ac.src)
        ok = _a_checked.get(k)
        if ok is None:
            ok = has_audio_stream(ac.src)
            _a_checked[k] = ok
        if not ok:
            print(
                f"[export] skip audio clip (no audio stream) src={ac.src.name}",
                flush=True,
            )
            continue
        safe_audio_clips.append(ac)
    audio_clips = safe_audio_clips

    # Video: black stage + optional screenshare overlays.
    # Audio: mix VoIP chunks placed by startTime, then normalize/boost.
    base = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-progress",
        "pipe:2",
        "-nostats",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=black:s={stage_w}x{stage_h}:r={cfg.fps}:d={_sec(duration_ms):.3f}",
    ]

    if ffmetadata is not None:
        base += ["-i", str(ffmetadata)]

    # Audio inputs: one per chunk (we want unique chunk placement).
    for c in audio_clips:
        if c.src is not None:
            base += ["-i", str(c.src)]

    # Video inputs: one per screenshare clip source (dedup).
    vid_src_to_input: dict[str, int] = {}
    unique_vid: list[Path] = []
    for vc in video_clips:
        if vc.src is None:
            continue
        k = str(vc.src)
        if k not in vid_src_to_input:
            vid_src_to_input[k] = len(base)
            unique_vid.append(vc.src)
    for p in unique_vid:
        base += ["-i", str(p)]

    # PDF page overlays: one image input per unique PNG (deduped). Each is fed as a
    # looped single-frame stream so we can `overlay=enable=between(...)` them.
    doc_image_clips = [
        o for o in overlays if o.kind == "doc_image" and o.src is not None
    ]
    unique_doc_pngs: list[Path] = []
    doc_png_to_idx: dict[str, int] = {}
    for o in doc_image_clips:
        k = str(o.src)
        if k not in doc_png_to_idx:
            doc_png_to_idx[k] = len(unique_doc_pngs)
            unique_doc_pngs.append(o.src)  # type: ignore[arg-type]
    for p in unique_doc_pngs:
        # `-loop 1` repeats the still image; framerate small to keep filter graph light.
        base += ["-loop", "1", "-framerate", "1", "-i", str(p)]

    filters: list[str] = []

    # Overlays drawn as text on the base black layer.
    for o in overlays:
        if o.kind == "doc_marker" and o.label:
            filters.append(_drawtext(f"{o.label} is being shown", _sec(o.start_ms), _sec(o.end_ms), stage_h=stage_h))
        if o.kind == "break" and o.label:
            filters.append(_drawtext(o.label, _sec(o.start_ms), _sec(o.end_ms), stage_h=stage_h))

    # Build video graph: base black + drawtext markers + screenshare overlays + PDF page overlays + optional subtitles.
    vf_chain = ",".join(filters) if filters else "null"
    # Input indices: 0 base, [1 ffmetadata], audio inputs..., screenshare inputs..., PDF inputs...
    meta_inputs = 1 if ffmetadata is not None else 0
    audio_input_count = sum(1 for c in audio_clips if c.src is not None)
    video_base_idx = 1 + meta_inputs + audio_input_count
    doc_image_base_idx = video_base_idx + len(unique_vid)

    overlay_steps: list[str] = []
    current_label = "v0"
    overlay_steps.append(f"[0:v]{vf_chain}[{current_label}]")
    for i, vc in enumerate(video_clips):
        if vc.src is None:
            continue
        idx = video_base_idx + unique_vid.index(vc.src)
        start_s = _sec(vc.start_ms)
        end_s = _sec(vc.end_ms)
        dur_s = max(0.001, end_s - start_s)
        vlab = f"sv{i}"
        nlab = f"v{i+1}"
        overlay_steps.append(
            f"[{idx}:v]scale={stage_w}:{stage_h}:force_original_aspect_ratio=decrease,"
            f"pad={stage_w}:{stage_h}:(ow-iw)/2:(oh-ih)/2:black,"
            f"trim=start=0:duration={dur_s:.3f},setpts=PTS-STARTPTS+{start_s:.3f}/TB[{vlab}]"
        )
        overlay_steps.append(
            f"[{current_label}][{vlab}]overlay=enable={_enable_window(start_s, end_s)}[{nlab}]"
        )
        current_label = nlab

    # PDF page overlays: scale the (still) page to fit the stage, then overlay only
    # during the segment we computed. Each image input is independent of other
    # overlays — the stage is the latest current_label.
    for j, oc in enumerate(doc_image_clips):
        idx = doc_image_base_idx + doc_png_to_idx[str(oc.src)]
        start_s = _sec(oc.start_ms)
        end_s = _sec(oc.end_ms)
        plab = f"dp{j}"
        nlab = f"vd{j+1}"
        overlay_steps.append(
            f"[{idx}:v]scale={stage_w}:{stage_h}:force_original_aspect_ratio=decrease,"
            f"pad={stage_w}:{stage_h}:(ow-iw)/2:(oh-ih)/2:black,format=yuv420p[{plab}]"
        )
        overlay_steps.append(
            f"[{current_label}][{plab}]overlay=enable={_enable_window(start_s, end_s)}[{nlab}]"
        )
        current_label = nlab

    if chat_ass is not None:
        # Burn-in ASS subtitles. Escape backslashes for Windows path.
        sp = str(chat_ass).replace("\\", "\\\\").replace(":", "\\:")
        overlay_steps.append(f"[{current_label}]subtitles='{sp}'[{current_label}sub]")
        current_label = f"{current_label}sub"

    # Build audio mix filter:
    # Place each chunk at its absolute start time using adelay, then mix all.
    af_parts: list[str] = []
    mix_inputs: list[str] = []
    for i, c in enumerate(audio_clips):
        if c.src is None:
            continue
        delay_ms = max(0, int(c.start_ms))
        lab = f"m{i}"
        # adelay wants per-channel delays: "d|d"
        ain = 1 + meta_inputs + i
        af_parts.append(f"[{ain}:a]adelay={delay_ms}|{delay_ms},asetpts=PTS-STARTPTS[{lab}]")
        mix_inputs.append(f"[{lab}]")

    if mix_inputs:
        # dynaudnorm boosts quiet audio smoothly; then volume bump.
        af_parts.append(
            "".join(mix_inputs)
            + f"amix=inputs={len(mix_inputs)}:dropout_transition=0,"
            + "dynaudnorm=f=150:g=15,volume=2.2[aout]"
        )
        af = ";".join(af_parts)
    else:
        af = "aevalsrc=0:d={:.3f}[aout]".format(_sec(duration_ms))

    cmd = base + [
        "-filter_complex",
        f"{';'.join(overlay_steps)};{af}",
        "-map",
        f"[{current_label}]",
        "-map",
        "[aout]",
    ]

    if ffmetadata is not None:
        cmd += ["-map_metadata", "1", "-map_chapters", "1"]

    eff_preset = _nvenc_preset(str(cfg.preset)) if vcodec == "h264_nvenc" else str(cfg.preset)
    cmd += ["-c:v", vcodec, "-preset", eff_preset]
    if vcodec == "libx264":
        cmd += ["-crf", str(int(cfg.crf))]
    else:
        cmd += ["-cq", str(int(cfg.crf)), "-b:v", "0"]

    cmd += [
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(cfg.fps),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "44100",
        "-movflags",
        "+faststart",
        str(tmp_out),
    ]

    # Drop a sidecar log with the exact ffmpeg invocation. When a filtergraph
    # breaks in the wild it's almost impossible to diagnose without seeing the
    # whole command, and the live stderr only shows a truncated tail.
    try:
        debug_log = tmp_out.with_suffix(tmp_out.suffix + ".ffmpeg-cmd.log")
        debug_log.write_text(
            "\n".join(["ffmpeg argv:", *cmd, "", "filter_complex:", cmd[cmd.index("-filter_complex") + 1] if "-filter_complex" in cmd else "(none)"]),
            encoding="utf-8",
        )
    except Exception:
        pass

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    out_time_ms = 0
    total_us = max(1, int(duration_ms * 1000))
    start_wall = time.time()
    stderr_lines: list[str] = []
    bar = tqdm(
        total=total_us,
        unit="us",
        unit_scale=True,
        smoothing=0.1,
        disable=_BACKEND_PROGRESS,
        file=sys.stderr,
        mininterval=0.5,
        dynamic_ncols=True,
    )
    last_plain_log = 0.0

    try:
        assert proc.stderr is not None
        for raw in proc.stderr:
            line = raw.strip()
            stderr_lines.append(raw.rstrip("\n"))
            if line.startswith("out_time_ms="):
                try:
                    out_time_ms = int(line.split("=", 1)[1])
                except Exception:
                    continue
                if out_time_ms < 0:
                    continue
                bar.n = min(total_us, out_time_ms)
                elapsed = max(0.001, time.time() - start_wall)
                rate = bar.n / elapsed
                eta_s = (total_us - bar.n) / rate if rate > 1e-6 else 0.0
                pct = min(99.9, max(0.0, (100.0 * bar.n / total_us) if total_us else 0.0))
                desc = f"t={out_time_ms / 1e6:.1f}s elapsed={elapsed:.0f}s eta={eta_s:.0f}s"
                bar.set_description(desc)
                bar.refresh()
                now = time.monotonic()
                if _BACKEND_PROGRESS and now - last_plain_log >= 0.35:
                    # Line-based stdout for subprocess consumers (replay_web parses this).
                    print(
                        f"t={out_time_ms / 1e6:.1f}s elapsed={elapsed:.0f}s eta={eta_s:.0f}s: {pct:.1f}%",
                        flush=True,
                    )
                    last_plain_log = now
            elif line.startswith("progress=") and line.endswith("end"):
                break
    finally:
        bar.close()
        rc = proc.wait()

    if rc != 0:
        tail = "\n".join(stderr_lines[-50:])
        raise RuntimeError(f"ffmpeg exited with code {rc}. Last stderr lines:\n{tail}")

    # Atomic-ish finalize.
    if tmp_out.exists():
        if out_mp4.exists():
            out_mp4.unlink(missing_ok=True)  # py3.13
        tmp_out.replace(out_mp4)

