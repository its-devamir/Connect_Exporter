from __future__ import annotations

import math
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from posixpath import normpath

from typing import Annotated, Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from replay_core.session_model import SessionModel
from exporter.ffmpeg_render import RenderConfig, _pick_v_encoder  # type: ignore
from replay_web.connect_download import (
    build_zip_candidate_urls,
    build_zip_relpaths_from_sco_id,
    stream_url_to_file,
    try_extract_sco_ids_from_html,
)


_FROZEN = bool(getattr(sys, "frozen", False))
_MEIPASS = getattr(sys, "_MEIPASS", None)

# ROOT is used to locate read-only assets (webui/) — in a one-file build that lives in _MEIPASS.
ROOT = Path(_MEIPASS).resolve() if _MEIPASS else Path(__file__).resolve().parents[1]
WEBUI_DIR = ROOT / "webui"

# Writable cache must live next to the exe (or the source tree in dev), not in _MEIPASS
# which is a temp dir that gets wiped when the process exits.
if _FROZEN:
    DATA_ROOT = Path(sys.executable).resolve().parent
else:
    DATA_ROOT = Path(__file__).resolve().parents[1]

_BROWSER_UPLOAD_ROOT = DATA_ROOT / ".replay_cache" / "browser_uploads"


@dataclass
class DownloadState:
    state: str = "idle"  # idle|downloading|extracting|finished|error|cancelled
    started_at: float = 0.0
    bytes_done: int = 0
    bytes_total: int | None = None
    speed_bps: float = 0.0
    url_tried: str = ""
    resolved_folder: str = ""
    error: str = ""
    cancel: bool = False


DL = DownloadState()


def _is_browser_upload_session(folder: Path) -> bool:
    """Sessions unpacked under .replay_cache/browser_uploads (zip/folder upload from the browser)."""
    try:
        fr = folder.resolve()
        br = _BROWSER_UPLOAD_ROOT.resolve()
        return fr == br or fr.is_relative_to(br)
    except (ValueError, OSError):
        return False


def _resolve_export_out_path(session_dir: Path, out_raw: str, out_dir_user: str) -> Path:
    """
    Default MP4 location:
    - Absolute `out` → use as-is.
    - Optional `out_dir` → that folder + relative `out`.
    - Browser-uploaded session → ~/Videos/ConnectExports/ + filename (avoids burying output under cache).
    - Else → next to the recording folder.
    """
    out_raw = (out_raw or "replay.mp4").strip()
    out_dir_user = (out_dir_user or "").strip()
    candidate = Path(out_raw).expanduser()

    if candidate.is_absolute():
        path = candidate.resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    if out_dir_user:
        path = (Path(out_dir_user).expanduser() / candidate).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    if _is_browser_upload_session(session_dir):
        safe = Path.home() / "Videos" / "ConnectExports"
        safe.mkdir(parents=True, exist_ok=True)
        return (safe / candidate).resolve()

    return (session_dir / candidate).resolve()


@dataclass
class ExportState:
    state: str = "idle"  # idle|running|finished|error|stopped
    pid: int | None = None
    started_at: float = 0.0
    duration_ms: int = 0
    progress_pct: float = 0.0
    eta_text: str = ""  # legacy; UI uses rendered_s / elapsed_s / eta_s
    progress_rendered_s: float = 0.0
    progress_elapsed_s: float = 0.0
    progress_eta_s: float | None = None
    log: list[str] = None  # type: ignore[assignment]
    log_cursor: int = 0
    out_path: str = ""
    encoder_selected: str = ""

    def __post_init__(self) -> None:
        if self.log is None:
            self.log = []


STATE = ExportState()
PROC: subprocess.Popen[str] | None = None
_state_lock = threading.Lock()


def _ffmpeg_encoders_text() -> str:
    try:
        p = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
        )
        return (p.stdout or "") + (p.stderr or "")
    except Exception:
        return ""


def _nvidia_gpu_names() -> list[str]:
    try:
        p = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        if p.returncode != 0:
            return []
        return [ln.strip() for ln in (p.stdout or "").splitlines() if ln.strip()]
    except Exception:
        return []


def _gather_system_payload() -> dict[str, Any]:
    enc_blob = _ffmpeg_encoders_text()
    has_nvenc = "h264_nvenc" in enc_blob
    ffmpeg_path = shutil.which("ffmpeg")
    ffprobe_path = shutil.which("ffprobe")
    gpus = _nvidia_gpu_names()
    un = platform.uname()
    return {
        "os": f"{un.system} {un.release}",
        "machine": un.machine,
        "python": platform.python_version(),
        "cpu_count_logical": os.cpu_count() or 0,
        "ffmpeg_on_path": bool(ffmpeg_path),
        "ffprobe_on_path": bool(ffprobe_path),
        "ffmpeg_h264_nvenc": has_nvenc,
        "nvidia_gpus": gpus,
        "notes": [] if (ffmpeg_path and ffprobe_path) else ["Install FFmpeg + FFprobe and add them to PATH."],
    }


def _estimate_payload(
    *,
    duration_ms: int,
    w: int,
    h: int,
    fps: int,
    crf: int,
    preset: str,
    encoder_chosen: str,
    burn_chat: bool,
) -> dict[str, Any]:
    """Very rough ballparks for web UI only."""
    dur_s = max(0.1, duration_ms / 1000.0)
    px = max(1, w) * max(1, h) * max(1, fps)
    base_720_15 = 1280 * 720 * 15
    load = px / base_720_15

    enc = (encoder_chosen or "libx264").lower()
    preset_l = (preset or "ultrafast").lower()
    slow_presets = {"medium", "slow", "slower", "veryslow"}
    preset_mul = 1.8 if preset_l in slow_presets else 1.15 if preset_l in {"fast", "faster", "veryfast"} else 1.0

    if enc == "h264_nvenc":
        enc_sec_per_src_sec = 0.12 * load * preset_mul
    else:
        enc_sec_per_src_sec = 0.55 * load * preset_mul

    if burn_chat:
        enc_sec_per_src_sec *= 1.35

    crf_clamp = min(40, max(18, int(crf)))
    crf_mul = 1.0 + (35 - crf_clamp) * 0.05

    est_encode_s = dur_s * enc_sec_per_src_sec * crf_mul
    est_total_s = dur_s * 0.08 + est_encode_s

    mb_per_min_720 = 7.0 + (28 - min(30, crf_clamp)) * 0.35
    res_mul = (max(1, w) * max(1, h)) / (1280.0 * 720.0)
    est_mb = (dur_s / 60.0) * mb_per_min_720 * math.sqrt(res_mul) * (1.15 if burn_chat else 1.0)

    warnings: list[str] = []
    if load > 2.2 and enc == "libx264":
        warnings.append("High resolution + CPU encoding can be very slow. Try Auto/NVENC or a lower preset resolution.")
    if load > 3.0 and burn_chat:
        warnings.append("Burning chat at high resolution is heavy — consider turning it off for a faster pass.")
    if preset_l in slow_presets and enc == "libx264":
        warnings.append("CPU encoder with a slow preset will take much longer; try ultrafast / veryfast for preview exports.")

    return {
        "encode_seconds_approx": int(round(est_encode_s)),
        "total_seconds_approx": int(round(est_total_s)),
        "output_size_mb_approx": round(est_mb, 1),
        "load_score": round(load, 2),
        "warnings": warnings,
    }


def _tail_log(max_lines: int = 4000) -> None:
    if len(STATE.log) > max_lines:
        STATE.log = STATE.log[-max_lines:]
        STATE.log_cursor = min(STATE.log_cursor, len(STATE.log))


def _parse_progress_line(line: str) -> None:
    # exporter prints: "t=606.5s elapsed=84s eta=824s: 12.3%"
    line = line.strip()
    if not line:
        return
    if "t=" in line and "elapsed=" in line and "eta=" in line:
        try:
            t_part = line.split("t=", 1)[1].split("s", 1)[0]
            t_s = float(t_part)
            if STATE.duration_ms > 0:
                STATE.progress_pct = max(0.0, min(100.0, (t_s * 1000.0) / STATE.duration_ms * 100.0))
            elapsed_part = line.split("elapsed=", 1)[1].split("s", 1)[0]
            eta_part = line.split("eta=", 1)[1].split(":", 1)[0].split("s", 1)[0]
            STATE.progress_rendered_s = t_s
            STATE.progress_elapsed_s = float(elapsed_part)
            STATE.progress_eta_s = float(eta_part)
        except Exception:
            pass


def _append_export_stdout_line(text: str) -> None:
    with _state_lock:
        STATE.log.append(text)
        _parse_progress_line(text)
        _tail_log()


def _start_export_stdout_reader(proc: subprocess.Popen[str]) -> None:
    """Drain child stdout on a thread so /api/export/status never blocks on readline()."""

    def _drain() -> None:
        out = proc.stdout
        if out is None:
            return
        try:
            while True:
                raw = out.readline()
                if raw == "":
                    break
                _append_export_stdout_line(raw.rstrip("\n"))
        except Exception:
            pass

    threading.Thread(target=_drain, name="export-stdout-drain", daemon=True).start()


def _pump_output_nonblocking() -> None:
    """Stdout is drained by _start_export_stdout_reader; keep hook for compatibility."""
    return


def _stop_process_tree_windows(pid: int) -> None:
    # Force kill entire tree (python -> ffmpeg)
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


app = FastAPI()


def _effective_duration_ms(folder: Path, session: SessionModel, skip_breaks: bool) -> int:
    """Match exporter's break-compression when skip_breaks is on (best-effort)."""
    dur = session.duration_ms
    if not skip_breaks:
        return dur
    from exporter.probe import probe_duration_ms
    from exporter.edl import build_av_clips
    from exporter.export import _merge_intervals
    from replay_core.chat import parse_ftchat

    audio0, _v0 = build_av_clips(session)
    msgs = parse_ftchat(folder)
    wins: list[tuple[int, int]] = []
    for c in audio0:
        if c.src is None:
            continue
        dms = probe_duration_ms(c.src)
        if dms <= 0:
            continue
        wins.append((c.start_ms, c.start_ms + dms + 250))
    merged = _merge_intervals(wins)
    if merged:
        best_len = 0
        gap_threshold_ms = 15 * 60 * 1000
        best = None
        for (_, a1), (b0, _) in zip(merged, merged[1:]):
            gap = b0 - a1
            if gap < gap_threshold_ms:
                continue
            if any(a1 <= m.t_ms <= b0 for m in msgs):
                continue
            if gap > best_len:
                best_len = gap
                best = (a1, b0)
        if best is not None:
            gap_ms = best[1] - best[0]
            slate_ms = 3000
            dur = dur - gap_ms + slate_ms
    return dur


def _safe_rel_posix(rel: str) -> str:
    """Reject path traversal in multipart uploads; return normalized posix path."""
    if not rel or rel.startswith(("/", "\\")):
        raise HTTPException(status_code=400, detail="Invalid uploaded path")
    n = normpath(rel.replace("\\", "/"))
    parts = []
    for p in Path(n).as_posix().split("/"):
        if not p or p == ".":
            continue
        if p == "..":
            raise HTTPException(status_code=400, detail="Invalid path segment in upload")
        parts.append(p)
    if not parts:
        raise HTTPException(status_code=400, detail="Empty relative path")
    return "/".join(parts)


async def _stream_upload_to_file(upload: UploadFile, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as out:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)


def connect_session_root(start: Path, max_depth: int = 12) -> Path:
    """
    Locate the folder containing mainstream.xml (Connect export).
    Prefer `start` if it qualifies; otherwise limited BFS for typical zip nesting.
    """
    start = start.resolve()
    if (start / "mainstream.xml").is_file():
        return start

    queue: deque[tuple[Path, int]] = deque([(start, 0)])
    found: Path | None = None
    best_depth = None
    seen: set[Path] = set()
    while queue:
        cur, depth = queue.popleft()
        if cur in seen or depth > max_depth:
            continue
        seen.add(cur)
        mainstream = cur / "mainstream.xml"
        try:
            if mainstream.is_file():
                if found is None or (best_depth is not None and depth < best_depth):
                    found = cur
                    best_depth = depth
        except OSError:
            continue
        if depth == max_depth:
            continue
        try:
            for child in cur.iterdir():
                if child.is_dir():
                    queue.append((child, depth + 1))
        except OSError:
            continue

    if found is None:
        raise HTTPException(
            status_code=400,
            detail="Could not find mainstream.xml — pick the folder or zip that contains the Connect recording export.",
        )
    return found.resolve()


def _resolve_folder(path_str: str) -> Path:
    """
    Accept either a directory or a .zip file.
    If it's a zip, extract once into .replay_cache/<stem> and return that folder.
    """
    p = Path(path_str).expanduser()
    if p.is_dir():
        return connect_session_root(p)
    if p.is_file() and p.suffix.lower() == ".zip":
        cache_root = ROOT / ".replay_cache"
        target = cache_root / p.stem
        zip_mtime = p.stat().st_mtime_ns
        need_extract = False
        if not target.exists():
            need_extract = True
        else:
            try:
                if zip_mtime > target.stat().st_mtime_ns:
                    need_extract = True
            except Exception:
                need_extract = True
        if need_extract:
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            target.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(p, "r") as zf:
                zf.extractall(target)
        return connect_session_root(target)
    raise HTTPException(status_code=400, detail="Folder must be a directory or a .zip file")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEBUI_DIR / "index.html")

# Serve JS/CSS under /static so /api/* keeps working.
app.mount("/static", StaticFiles(directory=str(WEBUI_DIR)), name="static")


@app.get("/api/system")
def api_system() -> dict[str, Any]:
    return _gather_system_payload()


@app.post("/api/upload_zip")
async def upload_zip(file: UploadFile = File(...)) -> dict[str, Any]:
    """Upload a .zip from the browser; extract and return a server path for preflight/export."""
    _BROWSER_UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)

    sess = uuid.uuid4().hex[:16]
    base = _BROWSER_UPLOAD_ROOT / f"z_{sess}"
    zip_path = base / "upload.zip"
    extract_root = base / "extracted"
    if extract_root.exists():
        shutil.rmtree(extract_root, ignore_errors=True)
    extract_root.mkdir(parents=True, exist_ok=True)

    try:
        await _stream_upload_to_file(file, zip_path)
        with zip_path.open("rb") as zin:
            hdr = zin.read(4)
        if hdr[:2] != b"PK":
            raise HTTPException(status_code=400, detail="Not a ZIP file (expected PK header)")
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_root)
        session_dir = connect_session_root(extract_root)
    finally:
        await file.close()

    return {
        "resolved_folder": os.fspath(session_dir),
        "upload_kind": "zip",
        "note": "Extracted uploaded zip on the server; large recordings may take a moment.",
    }


@app.post("/api/upload_folder")
async def upload_folder(files: Annotated[list[UploadFile], File(...)]) -> dict[str, Any]:
    """
    Upload a loose folder picked in the browser (webkitdirectory / drag-drop of a directory).
    Files must use relative paths (forward slashes).
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    _BROWSER_UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)

    sess = uuid.uuid4().hex[:16]
    dest_root = _BROWSER_UPLOAD_ROOT / f"f_{sess}" / "folder"
    if dest_root.exists():
        shutil.rmtree(dest_root.parent, ignore_errors=True)
    dest_root.mkdir(parents=True, exist_ok=True)

    try:
        for uf in files:
            rel = _safe_rel_posix(uf.filename or "")
            dest = dest_root / Path(rel.replace("/", os.sep))
            dest.parent.mkdir(parents=True, exist_ok=True)
            await _stream_upload_to_file(uf, dest)
    finally:
        for uf in files:
            await uf.close()

    session_dir = connect_session_root(dest_root)
    return {
        "resolved_folder": os.fspath(session_dir),
        "upload_kind": "folder",
        "file_count": len(files),
        "note": "Folder uploaded from the browser. Very large recordings are faster if you zip and upload the .zip instead.",
    }


def _reset_download_state() -> None:
    DL.state = "idle"
    DL.started_at = 0.0
    DL.bytes_done = 0
    DL.bytes_total = None
    DL.speed_bps = 0.0
    DL.url_tried = ""
    DL.resolved_folder = ""
    DL.error = ""
    DL.cancel = False


@app.get("/api/connect/download_status")
def connect_download_status() -> dict[str, Any]:
    with _state_lock:
        wall_elapsed_s = 0.0
        if DL.started_at > 0:
            wall_elapsed_s = max(0.0, time.time() - DL.started_at)
        return {
            "state": DL.state,
            "bytes_done": DL.bytes_done,
            "bytes_total": DL.bytes_total,
            "speed_bps": DL.speed_bps,
            "url_tried": DL.url_tried,
            "resolved_folder": DL.resolved_folder,
            "error": DL.error,
            "wall_elapsed_s": wall_elapsed_s,
        }


@app.post("/api/connect/download_cancel")
def connect_download_cancel() -> dict[str, Any]:
    with _state_lock:
        if DL.state not in {"downloading", "extracting"}:
            return {"ok": True, "state": DL.state}
        DL.cancel = True
        DL.state = "cancelled"
        return {"ok": True, "state": DL.state}


@app.post("/api/connect/download_start")
def connect_download_start(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Download a Connect export .zip from a room/session URL (best-effort).

    This does not try to authenticate; for protected servers the user may need to
    paste a Cookie header string.
    """
    session_url = str(payload.get("session_url") or "").strip()
    if not session_url:
        raise HTTPException(status_code=400, detail="Missing session_url")

    zip_url = str(payload.get("zip_url") or "").strip() or None
    zip_relpath = str(payload.get("zip_relpath") or "").strip() or None
    cookie = str(payload.get("cookie") or "").strip() or None

    with _state_lock:
        if DL.state in {"downloading", "extracting"}:
            raise HTTPException(status_code=409, detail="Download already running")
        _reset_download_state()
        DL.state = "downloading"
        DL.started_at = time.time()

    sess = uuid.uuid4().hex[:16]
    base = _BROWSER_UPLOAD_ROOT / f"dl_{sess}"
    zip_path = base / "download.zip"
    extract_root = base / "extracted"

    candidates = build_zip_candidate_urls(session_url, zip_url_full=zip_url, zip_relpath=zip_relpath)

    def _worker() -> None:
        nonlocal candidates
        try:
            _BROWSER_UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
            if base.exists():
                shutil.rmtree(base, ignore_errors=True)
            extract_root.mkdir(parents=True, exist_ok=True)

            # If the user gave a session URL, try to fetch it and derive sco-id guesses.
            try:
                import urllib.request

                req = urllib.request.Request(
                    session_url,
                    headers={"User-Agent": "Mozilla/5.0", **({"Cookie": cookie} if cookie else {})},
                    method="GET",
                )
                with urllib.request.urlopen(req, timeout=25) as resp:  # nosec - user-controlled URL expected
                    txt = resp.read(400_000).decode("utf-8", "ignore")
                sco_ids = try_extract_sco_ids_from_html(txt)
                if sco_ids:
                    derived: list[str] = []
                    for sid in sco_ids[:3]:
                        derived.extend(build_zip_relpaths_from_sco_id(sid))
                    # Prepend derived candidates so we try them first.
                    if derived:
                        # They are relpaths; prepend absolute candidates derived from the meeting base path.
                        from urllib.parse import urljoin

                        base_norm = session_url.split("?", 1)[0]
                        if not base_norm.endswith("/"):
                            # Keep meeting path prefix, e.g. /pvu.../
                            base_norm = base_norm.rsplit("/", 1)[0] + "/"
                        prepend = [urljoin(base_norm, r.lstrip("/")) for r in derived]
                        # De-dupe while preserving order.
                        seen: set[str] = set()
                        new_list: list[str] = []
                        for u in prepend + candidates:
                            if u not in seen:
                                seen.add(u)
                                new_list.append(u)
                        candidates = new_list
            except Exception:
                pass

            last_err: str | None = None
            for u in candidates:
                with _state_lock:
                    if DL.cancel:
                        return
                    DL.url_tried = u
                    DL.bytes_done = 0
                    DL.bytes_total = None
                    DL.speed_bps = 0.0

                def _on_chunk(done: int, total: int | None, speed: float) -> None:
                    with _state_lock:
                        if DL.cancel:
                            return
                        DL.bytes_done = int(done)
                        DL.bytes_total = int(total) if total is not None else None
                        DL.speed_bps = float(speed)

                try:
                    stream_url_to_file(
                        u,
                        zip_path,
                        cookie=cookie,
                        referer=session_url,
                        on_chunk=_on_chunk,
                        should_cancel=lambda: bool(DL.cancel),
                    )
                    # Quick signature check: many failures download an HTML login page.
                    try:
                        with zip_path.open("rb") as zin:
                            sig = zin.read(4)
                        if sig[:2] != b"PK":
                            # Include a small snippet to help debug (often a login page).
                            with zip_path.open("rb") as zin:
                                head = zin.read(3000)
                            snippet = head.decode("utf-8", "ignore").strip().replace("\r", "")
                            raise RuntimeError(
                                "Downloaded file is not a ZIP (missing PK header). "
                                "This usually means the server returned an HTML login page or an error. "
                                f"First bytes (decoded): {snippet[:220]!r}"
                            )
                    except Exception as e:
                        raise
                    last_err = None
                    break
                except Exception as e:
                    last_err = str(e)
                    continue

            if last_err is not None:
                raise RuntimeError(f"All ZIP URLs failed. Last error: {last_err}")

            with _state_lock:
                if DL.cancel:
                    return
                DL.state = "extracting"

            try:
                with zipfile.ZipFile(zip_path, "r") as zf:
                    zf.extractall(extract_root)
            except Exception as e:
                raise RuntimeError(f"ZIP extract failed: {e}") from e

            session_dir = connect_session_root(extract_root)
            with _state_lock:
                if DL.cancel:
                    return
                DL.state = "finished"
                DL.resolved_folder = os.fspath(session_dir)
        except Exception as e:
            with _state_lock:
                DL.state = "error"
                DL.error = str(e)

    threading.Thread(target=_worker, name="connect-download", daemon=True).start()
    return {"ok": True, "state": "downloading"}


@app.post("/api/preflight")
def preflight(payload: dict[str, Any]) -> dict[str, Any]:
    folder_raw = str(payload.get("folder") or "")
    if not folder_raw:
        raise HTTPException(status_code=400, detail="Missing folder")
    folder = _resolve_folder(folder_raw)
    session = SessionModel.from_folder(folder)
    cfg_from_ui = RenderConfig(
        width=int(payload.get("w") or 1280),
        height=int(payload.get("h") or 720),
        fps=int(payload.get("fps") or 15),
        crf=int(payload.get("crf") or 30),
        preset=str(payload.get("preset") or "ultrafast"),
        encoder=str(payload.get("encoder") or "auto"),
    )
    enc = _pick_v_encoder(cfg_from_ui)
    breaks_on = bool(payload.get("skip_breaks", True))
    dur = _effective_duration_ms(folder, session, breaks_on)

    ui_enc = str(payload.get("encoder") or "auto").lower()
    burn_chat = bool(payload.get("burn_chat", True))

    warns: list[str] = []
    syspl = _gather_system_payload()
    if ui_enc == "h264_nvenc" or enc == "h264_nvenc":
        if not syspl["ffmpeg_h264_nvenc"]:
            warns.append("GPU encoder NVENC was requested but FFmpeg does not list h264_nvenc — export may fail or fallback is needed.")
        if not syspl["nvidia_gpus"]:
            warns.append("No NVIDIA GPU was detected via nvidia-smi; NVENC may still work if drivers are installed.")
    if enc == "libx264" and ui_enc == "auto" and syspl["ffmpeg_h264_nvenc"]:
        warns.append("Auto picked CPU (libx264) even though NVENC appears in FFmpeg — check drivers or pick GPU explicitly.")

    est = _estimate_payload(
        duration_ms=dur,
        w=int(payload.get("w") or 1280),
        h=int(payload.get("h") or 720),
        fps=int(payload.get("fps") or 15),
        crf=int(payload.get("crf") or 30),
        preset=str(payload.get("preset") or "ultrafast"),
        encoder_chosen=enc,
        burn_chat=burn_chat,
    )
    warns.extend(est.get("warnings") or [])

    return {
        "resolved_folder": str(folder),
        "duration_ms": dur,
        "duration_ms_original": session.duration_ms,
        "stream_instances": len(session.stream_instances),
        "encoder_selected": enc,
        "note": "ETA and file size are rough guesses; real results depend on content and hardware.",
        "estimates": {
            "encode_seconds_approx": est["encode_seconds_approx"],
            "total_seconds_approx": est["total_seconds_approx"],
            "output_size_mb_approx": est["output_size_mb_approx"],
            "load_score": est["load_score"],
        },
        "warnings": warns,
        "system": syspl,
    }


@app.get("/api/export/status")
def export_status() -> dict[str, Any]:
    """Snapshot state under one lock so PROC/STATE stay consistent vs export_start races."""
    global PROC
    _pump_output_nonblocking()
    with _state_lock:
        if PROC is not None:
            rc = PROC.poll()
            if rc is not None and STATE.state == "running":
                STATE.state = "finished" if rc == 0 else "error"
                if STATE.state == "finished":
                    STATE.progress_pct = 100.0
                    STATE.progress_eta_s = 0.0
                STATE.pid = None
                PROC = None
        new_log = STATE.log[STATE.log_cursor :]
        STATE.log_cursor = len(STATE.log)
        wall_elapsed_s = 0.0
        if STATE.state == "running" and STATE.started_at > 0:
            wall_elapsed_s = max(0.0, time.time() - STATE.started_at)

        return {
            "state": STATE.state,
            "pid": STATE.pid,
            "progress_pct": STATE.progress_pct,
            "eta_text": STATE.eta_text,
            "rendered_s": STATE.progress_rendered_s,
            "elapsed_s": STATE.progress_elapsed_s,
            "eta_s": STATE.progress_eta_s,
            "wall_elapsed_s": wall_elapsed_s,
            "new_log": new_log,
            "out_path": STATE.out_path,
            "encoder_selected": STATE.encoder_selected,
        }


@app.post("/api/export/start")
def export_start(payload: dict[str, Any]) -> dict[str, Any]:
    global PROC
    with _state_lock:
        if STATE.state == "running":
            raise HTTPException(status_code=409, detail="Export already running")

    folder_raw = str(payload.get("folder") or "").strip()
    if not folder_raw:
        raise HTTPException(status_code=400, detail="Missing folder")
    folder_path = _resolve_folder(folder_raw)

    out = str(payload.get("out") or "replay.mp4").strip()
    w = int(payload.get("w") or 1280)
    h = int(payload.get("h") or 720)
    fps = int(payload.get("fps") or 15)
    crf = int(payload.get("crf") or 30)
    preset = str(payload.get("preset") or "ultrafast")
    encoder = str(payload.get("encoder") or "auto")
    burn_chat = bool(payload.get("burn_chat", True))
    chapters = bool(payload.get("chapters", True))
    skip_breaks = bool(payload.get("skip_breaks", True))

    session = SessionModel.from_folder(folder_path)
    dur_ms = _effective_duration_ms(folder_path, session, skip_breaks)
    session_dir = folder_path
    out_dir = str(payload.get("out_dir") or "").strip()
    out_path = _resolve_export_out_path(session_dir, out, out_dir)

    child_env = os.environ.copy()
    child_env["PYTHONUNBUFFERED"] = "1"
    child_env["CONNECT_EXPORT_BACKEND"] = "1"

    # In a PyInstaller bundle there is no separate python interpreter to run `python -m exporter.export`.
    # Instead the launcher re-invokes the bundled exe with `--exporter-cli` and dispatches internally.
    if _FROZEN:
        cmd = [os.fspath(Path(sys.executable)), "--exporter-cli"]
    else:
        cmd = [os.fspath(Path(sys.executable)), "-u", "-m", "exporter.export"]
    cmd += [
        "--folder",
        str(session_dir),
        "--out",
        str(out_path),
        "--w",
        str(w),
        "--h",
        str(h),
        "--fps",
        str(fps),
        "--crf",
        str(crf),
        "--preset",
        preset,
        "--encoder",
        encoder,
    ]
    if not burn_chat:
        cmd.append("--no-chat")
    if not chapters:
        cmd.append("--no-chapters")
    if not skip_breaks:
        cmd.append("--no-skip-breaks")

    cmd_line = f"cmd: {' '.join(cmd)}"

    cfg = RenderConfig(width=w, height=h, fps=fps, crf=crf, preset=preset, encoder=encoder)
    encoder_selected = _pick_v_encoder(cfg)

    proc = subprocess.Popen(
        cmd,
        cwd=str(DATA_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=child_env,
    )

    # Publish PROC + STATE together so /api/export/status never sees a child without state=running.
    with _state_lock:
        PROC = proc
        STATE.duration_ms = dur_ms
        STATE.progress_pct = 0.0
        STATE.eta_text = ""
        STATE.progress_rendered_s = 0.0
        STATE.progress_elapsed_s = 0.0
        STATE.progress_eta_s = None
        STATE.log = [cmd_line]
        STATE.log_cursor = 0
        STATE.state = "running"
        STATE.started_at = time.time()
        STATE.out_path = str(out_path)
        STATE.pid = proc.pid
        STATE.encoder_selected = encoder_selected

    _start_export_stdout_reader(proc)
    return {"pid": proc.pid}


@app.post("/api/export/stop")
def export_stop() -> dict[str, Any]:
    global PROC
    with _state_lock:
        if PROC is None or STATE.pid is None or STATE.state != "running":
            return {"ok": True, "state": STATE.state}
        pid = STATE.pid
    _stop_process_tree_windows(pid)
    with _state_lock:
        STATE.state = "stopped"
        STATE.pid = None
        PROC = None
    return {"ok": True, "state": STATE.state}

