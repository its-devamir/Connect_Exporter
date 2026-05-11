from __future__ import annotations

import sys
import webbrowser

import uvicorn


def main() -> int:
    """Start the FastAPI server and open the browser.

    When frozen (PyInstaller), pass the ASGI app object directly because
    `uvicorn.run("module:app", ...)` relies on import-by-string which fails
    inside a one-file bundle.
    """
    port = 8765
    url = f"http://127.0.0.1:{port}/"
    print(f"Starting server on {url}", flush=True)
    try:
        webbrowser.open(url)
    except Exception:
        pass

    if getattr(sys, "frozen", False):
        from replay_web.server import app
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
    else:
        uvicorn.run(
            "replay_web.server:app",
            host="127.0.0.1",
            port=port,
            reload=False,
            workers=1,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

