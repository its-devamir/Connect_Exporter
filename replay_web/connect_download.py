"""Download Adobe Connect recording ZIP from a session room URL (best-effort).

many hosts use URLs like: {room}/output/filename.zip?download=zip
Hosts may require Cookie / Referer — pass through the API.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlparse
import re

# Tried in order when no full zip URL is given (after optional custom relpath).
DEFAULT_ZIP_RELPATHS = (
    "output/filename.zip?download=zip",
    "output/recording.zip?download=zip",
    "output/stream.zip?download=zip",
)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def normalize_connect_base(session_url: str) -> str:
    u = session_url.strip()
    p = urlparse(u)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise ValueError("URL must start with http:// or https:// and include a host")
    path = p.path or "/"
    if not path.endswith("/"):
        path = path + "/"
    return f"{p.scheme}://{p.netloc}{path}"


def build_zip_candidate_urls(
    session_url: str,
    *,
    zip_url_full: str | None = None,
    zip_relpath: str | None = None,
) -> list[str]:
    """Return ordered list of absolute URLs to try."""
    if zip_url_full and zip_url_full.strip():
        z = zip_url_full.strip()
        p = urlparse(z)
        if p.scheme in ("http", "https") and p.netloc:
            return [z]
        raise ValueError("zip_url must be a full http(s) URL")

    base = normalize_connect_base(session_url)
    rels: list[str] = []
    if zip_relpath and zip_relpath.strip():
        rels.append(zip_relpath.strip().lstrip("/"))
    for r in DEFAULT_ZIP_RELPATHS:
        if r not in rels:
            rels.append(r)

    out: list[str] = []
    seen: set[str] = set()
    for r in rels:
        u = urljoin(base, r)
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _request_headers(cookie: str | None, referer: str | None) -> dict[str, str]:
    h: dict[str, str] = {
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if cookie and cookie.strip():
        h["Cookie"] = cookie.strip()
    if referer and referer.strip():
        h["Referer"] = referer.strip()
    return h


def try_extract_sco_ids_from_html(html: str) -> list[str]:
    """
    Best-effort extraction from Connect playback HTML. Common patterns:
    - appInstance=7/2109110-1/output/
    - sco-id=2109110 (sometimes URL-encoded as sco-id%3D2109110)
    """
    out: list[str] = []
    for m in re.finditer(r"appInstance=([0-9]+/[0-9]+-[0-9]+/output/)", html):
        try:
            inst = m.group(1)  # e.g. 7/2109110-1/output/
            parts = inst.split("/")
            if len(parts) >= 2:
                sco = parts[1].split("-", 1)[0]
                if sco.isdigit():
                    out.append(sco)
        except Exception:
            continue
    for m in re.finditer(r"sco-id(?:%3D|=)([0-9]{5,})", html):
        sco = m.group(1)
        if sco.isdigit():
            out.append(sco)
    # De-dupe, stable
    seen: set[str] = set()
    uniq: list[str] = []
    for s in out:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq


def build_zip_relpaths_from_sco_id(sco_id: str) -> list[str]:
    # Different Connect deployments name the export zip differently.
    base = sco_id.strip()
    if not base.isdigit():
        return []
    return [
        f"output/{base}.zip?download=zip",
        f"output/{base}-1.zip?download=zip",
        f"output/{base}_1.zip?download=zip",
        f"output/{base}.zip",
    ]


def stream_url_to_file(
    url: str,
    dest: Path,
    *,
    cookie: str | None,
    referer: str | None,
    chunk_size: int = 256 * 1024,
    on_chunk: Callable[[int, int | None, float], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    timeout_open: float = 60.0,
) -> tuple[int, int]:
    """Download URL to dest. Returns (http_status_or_0, bytes_written). Raises on failure."""
    headers = _request_headers(cookie, referer or url)
    req = urllib.request.Request(url, headers=headers, method="GET")
    dest.parent.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler())
    with opener.open(req, timeout=timeout_open) as resp:
        status = getattr(resp, "status", 200) or 200
        if status >= 400:
            raise urllib.error.HTTPError(url, status, str(status), resp.headers, None)  # pragma: no cover
        total_raw = resp.headers.get("Content-Length")
        total: int | None = None
        if total_raw and total_raw.isdigit():
            total = int(total_raw)
        written = 0
        t0 = time.monotonic()
        last_report = t0
        with dest.open("wb") as out:
            while True:
                if should_cancel and should_cancel():
                    raise RuntimeError("cancelled")
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                out.write(chunk)
                written += len(chunk)
                now = time.monotonic()
                elapsed = max(1e-6, now - t0)
                speed = written / elapsed
                if on_chunk and (now - last_report >= 0.25 or written == total):
                    on_chunk(written, total, speed)
                    last_report = now
    return status, written
