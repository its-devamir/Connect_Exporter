from __future__ import annotations

import webbrowser
from pathlib import Path

import uvicorn


def main() -> int:
    # Start FastAPI server and open browser.
    port = 8765
    url = f"http://127.0.0.1:{port}/"
    print(f"Starting server on {url}")
    webbrowser.open(url)
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

