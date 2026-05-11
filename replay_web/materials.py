"""Server-side helpers for the "Materials" wizard step.

The user can attach the original PDF(s) that were shared during the meeting; we then
render the matching page as a stage overlay at export time. This module exposes the
filesystem layout and the document → attached-pdf lookup.

Layout under a session folder::

    <session>/materials/<safe-filename>.pdf
    <session>/materials/_index.json   (optional manifest written when attaching)

We deliberately keep the manifest optional: the source of truth for "which PDF
matches which detected document" is the filename. That way a user can also drop PDFs
in manually without going through the UI.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


def materials_dir(session_dir: Path) -> Path:
    """Return the folder under a session where attached PDFs live."""
    return Path(session_dir) / "materials"


_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(name: str) -> str:
    """Produce a filesystem-safe filename for a detected document.

    We preserve the basename (no directories) and replace anything outside a
    conservative whitelist with ``_``. The result always ends in ``.pdf`` so the
    runtime lookup is straightforward.
    """

    raw = (name or "").strip().replace("\\", "/").split("/")[-1]
    if not raw:
        raw = "document.pdf"
    if raw.lower().endswith(".pdf"):
        stem, ext = raw[:-4], ".pdf"
    else:
        stem, ext = raw, ".pdf"
    stem = _SAFE_RE.sub("_", stem).strip("._-") or "document"
    return f"{stem}{ext}"


@dataclass(frozen=True, slots=True)
class AttachedPdf:
    detected_name: str
    safe_name: str
    path: Path
    size_bytes: int


def attached_pdfs(session_dir: Path) -> dict[str, AttachedPdf]:
    """Map ``safe_filename(detected_name) -> AttachedPdf`` for every PDF on disk."""
    out: dict[str, AttachedPdf] = {}
    mdir = materials_dir(session_dir)
    if not mdir.is_dir():
        return out
    for p in mdir.iterdir():
        if not p.is_file() or p.suffix.lower() != ".pdf":
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        out[p.name.lower()] = AttachedPdf(
            detected_name=p.name,
            safe_name=p.name,
            path=p.resolve(),
            size_bytes=int(size),
        )
    return out


def find_pdf_for_document(session_dir: Path, detected_name: str) -> Path | None:
    """Return the attached PDF that matches ``detected_name`` (case-insensitive)."""
    if not detected_name:
        return None
    safe = safe_filename(detected_name).lower()
    attached = attached_pdfs(session_dir)
    hit = attached.get(safe)
    return hit.path if hit else None


def load_manifest(session_dir: Path) -> dict:
    """Load the optional manifest if present. Returns ``{}`` on any error."""
    f = materials_dir(session_dir) / "_index.json"
    if not f.is_file():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_manifest(session_dir: Path, payload: dict) -> None:
    mdir = materials_dir(session_dir)
    mdir.mkdir(parents=True, exist_ok=True)
    f = mdir / "_index.json"
    f.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
