from __future__ import annotations

import os
import sys
from pathlib import Path

# These eager imports help PyInstaller pick up everything the launcher will need at runtime,
# including the exporter subprocess path (re-invoked via --exporter-cli below).
import exporter.export  # noqa: F401
import exporter.ffmpeg_render  # noqa: F401
import exporter.pdf_pages  # noqa: F401
import replay_core.session_model  # noqa: F401
import replay_web.materials  # noqa: F401


def _prepend_bundled_bin_to_path() -> None:
    """Prepend ./bin (next to the exe) to PATH so bundled ffmpeg/ffprobe are found."""
    try:
        exe_dir = Path(sys.executable).resolve().parent
    except Exception:
        exe_dir = Path.cwd()
    bin_dir = exe_dir / "bin"
    if bin_dir.is_dir():
        os.environ["PATH"] = os.fspath(bin_dir) + os.pathsep + os.environ.get("PATH", "")


def _run_exporter_cli() -> int:
    """Re-entry path used when the server spawns an export subprocess from the frozen exe.

    The server invokes `ConnectExporter.exe --exporter-cli <args>`; we strip the flag
    and dispatch to `exporter.export.main()` which reads `sys.argv` via argparse.
    """
    from exporter.export import main as export_main

    sys.argv = [sys.argv[0], *sys.argv[2:]]
    return int(export_main() or 0)


def main() -> int:
    _prepend_bundled_bin_to_path()

    if len(sys.argv) >= 2 and sys.argv[1] == "--exporter-cli":
        return _run_exporter_cli()

    from replay_web.run_server import main as run_main
    return int(run_main() or 0)


if __name__ == "__main__":
    raise SystemExit(main())

