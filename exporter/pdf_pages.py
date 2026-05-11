"""Optional PDF page rasterizer used to overlay shared documents at export time.

Implementation notes:
- We depend on ``pypdfium2`` (Apache-2.0, PDFium-based, ships native wheels) so the
  export pipeline keeps working without Adobe-licensed code. The module is imported
  lazily; callers must check :func:`available` before invoking :func:`render_page`.
- We deliberately do **not** depend on Pillow. ``pypdfium2`` produces an RGBA buffer
  when ``rev_byteorder=True`` is set, and we encode that to PNG with the stdlib
  (``struct``/``zlib``). That keeps the bundled exe small.
- Page renders are cached on disk under ``<session>/.replay_cache/materials/`` keyed
  on (pdf_mtime, page_index, target_width). On a cache hit we skip both PDF parsing
  and PNG encoding.

The module is safe to import even when ``pypdfium2`` is missing; in that case
:func:`available` returns False and :func:`render_page` raises ``RuntimeError``.
"""

from __future__ import annotations

import hashlib
import struct
import zlib
from pathlib import Path


def available() -> bool:
    """Return True iff PDF rasterization is currently usable."""
    try:
        import pypdfium2  # noqa: F401
    except Exception:
        return False
    return True


def page_count(pdf_path: Path) -> int:
    """Best-effort page count; returns 0 when the file can't be opened."""
    if not available():
        return 0
    try:
        import pypdfium2 as pdfium  # type: ignore

        doc = pdfium.PdfDocument(str(pdf_path))
        try:
            return int(len(doc))
        finally:
            doc.close()
    except Exception:
        return 0


def _write_png_rgba(path: Path, width: int, height: int, rgba: bytes) -> None:
    """Minimal PNG-RGBA writer (truecolor + alpha, no interlacing). Pure stdlib.

    PNG layout: 8-byte signature, then any number of length+type+data+crc32 chunks.
    We emit exactly IHDR, IDAT, IEND. Per-scanline filter byte is fixed at 0 (None).
    """

    assert len(rgba) == width * height * 4, "rgba buffer size mismatch"
    sig = b"\x89PNG\r\n\x1a\n"

    def _chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", int(width), int(height), 8, 6, 0, 0, 0)
    raw = bytearray(height * (1 + width * 4))
    stride = width * 4
    src = memoryview(rgba)
    for y in range(height):
        out = 1 + y * (1 + stride)
        raw[out - 1] = 0  # filter: None
        raw[out : out + stride] = src[y * stride : (y + 1) * stride]
    idat = zlib.compress(bytes(raw), level=6)

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        f.write(sig)
        f.write(_chunk(b"IHDR", ihdr))
        f.write(_chunk(b"IDAT", idat))
        f.write(_chunk(b"IEND", b""))


def _cache_key(pdf_path: Path, page_index: int, target_width: int) -> str:
    try:
        mtime_ns = pdf_path.stat().st_mtime_ns
    except OSError:
        mtime_ns = 0
    base = f"{pdf_path.resolve()}|{mtime_ns}|{page_index}|{target_width}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


def render_page(
    pdf_path: Path,
    page_index: int,
    *,
    target_width: int,
    cache_dir: Path,
) -> Path:
    """Render ``page_index`` (0-based) of ``pdf_path`` to a PNG at the given width.

    Returns the path of the cached PNG. Raises ``RuntimeError`` if rendering is
    unavailable or the page index is out of range.
    """

    if not available():
        raise RuntimeError("pypdfium2 is not installed; PDF page rendering is disabled.")

    pdf_path = Path(pdf_path)
    cache_dir = Path(cache_dir)

    key = _cache_key(pdf_path, page_index, target_width)
    out_path = cache_dir / f"{pdf_path.stem}-p{int(page_index):03d}-w{int(target_width)}-{key}.png"
    if out_path.is_file() and out_path.stat().st_size > 0:
        return out_path

    import pypdfium2 as pdfium  # type: ignore

    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        if page_index < 0 or page_index >= len(doc):
            raise RuntimeError(
                f"Page {page_index} is out of range for {pdf_path.name} (has {len(doc)} pages)."
            )
        page = doc[page_index]
        try:
            # pypdfium2 uses 72 DPI baseline; scale = target_width / page_width_in_pts.
            pw = float(page.get_width())
            scale = max(0.25, float(target_width) / pw) if pw > 0 else 2.0
            bitmap = page.render(scale=scale, rev_byteorder=True)
            try:
                buf = bytes(bitmap.buffer)
                _write_png_rgba(out_path, int(bitmap.width), int(bitmap.height), buf)
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        doc.close()

    return out_path
