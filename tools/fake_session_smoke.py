"""Generate a synthetic Connect session folder + drive the materials endpoint.

This is a maintainer-only smoke test for the v0.2 materials wiring. It writes a
tiny ``mainstream.xml`` that contains two document shares (one with a page change)
and one screen share, then asks the dev server to list detected documents.

Usage::

    python -m tools.fake_session_smoke
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote


MAINSTREAM = """<?xml version='1.0' encoding='UTF-8'?>
<root>
  <Message time="1000">
    <Method>playEvent</Method>
    <Object>
      <name><String>UntypedAction</String></name>
    </Object>
    <String>setContentSo</String>
    <Array>
      <Object>
        <code><String>change</String></code>
        <newValue>
          <Object>
            <shareType><String>document</String></shareType>
            <ctID><String>ct-100</String></ctID>
            <whoStartedIt><String>host-1</String></whoStartedIt>
            <documentDescriptor>
              <Object>
                <theName><String>Lecture-04.pdf</String></theName>
              </Object>
            </documentDescriptor>
          </Object>
        </newValue>
      </Object>
    </Array>
  </Message>
  <Message time="12000">
    <Method>playEvent</Method>
    <Object>
      <name><String>UntypedAction</String></name>
    </Object>
    <String>setContentSo</String>
    <Array>
      <Object>
        <code><String>child_change</String></code>
        <newValue>
          <Object>
            <state>
              <Object>
                <currentPage><Number>3</Number></currentPage>
              </Object>
            </state>
          </Object>
        </newValue>
      </Object>
    </Array>
  </Message>
  <Message time="30000">
    <Method>playEvent</Method>
    <Object>
      <name><String>UntypedAction</String></name>
    </Object>
    <String>setContentSo</String>
    <Array>
      <Object>
        <code><String>change</String></code>
        <newValue>
          <Object>
            <shareType><String>document</String></shareType>
            <ctID><String>ct-200</String></ctID>
            <whoStartedIt><String>host-1</String></whoStartedIt>
            <documentDescriptor>
              <Object>
                <theName><String>Appendix-Notes.pdf</String></theName>
              </Object>
            </documentDescriptor>
          </Object>
        </newValue>
      </Object>
    </Array>
  </Message>
</root>
"""


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    sess_dir = root / ".replay_cache" / "fake_v2_session"
    if sess_dir.exists():
        for p in sess_dir.rglob("*"):
            if p.is_file():
                p.unlink()
    sess_dir.mkdir(parents=True, exist_ok=True)
    (sess_dir / "mainstream.xml").write_text(MAINSTREAM, encoding="utf-8")
    print(f"wrote fake session at {sess_dir}")

    # Boot the server.
    env = os.environ.copy()
    proc = subprocess.Popen(
        [sys.executable, "-m", "replay_web.run_server"],
        cwd=str(root),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        # Wait for /api/system to come up.
        for _ in range(20):
            try:
                with urllib.request.urlopen("http://127.0.0.1:8765/api/system", timeout=1) as r:
                    if r.status == 200:
                        break
            except Exception:
                time.sleep(0.5)
        else:
            print("server failed to start")
            return 1

        url = f"http://127.0.0.1:8765/api/session/materials?folder={quote(str(sess_dir))}"
        with urllib.request.urlopen(url, timeout=8) as r:
            payload = json.loads(r.read())
        print(json.dumps(payload, indent=2))
        docs = payload.get("documents", [])
        if len(docs) != 2:
            print(f"FAIL: expected 2 documents, got {len(docs)}")
            return 2
        names = sorted(d["name"] for d in docs)
        if names != ["Appendix-Notes.pdf", "Lecture-04.pdf"]:
            print(f"FAIL: unexpected names {names}")
            return 3
        first = next(d for d in docs if d["name"] == "Lecture-04.pdf")
        if first["page_changes_detected"] < 1:
            print("FAIL: expected at least one detected page change for Lecture-04")
            return 4
        print("OK: materials endpoint returns expected payload.")
        return 0
    finally:
        proc.kill()
        proc.wait(timeout=4)


if __name__ == "__main__":
    raise SystemExit(main())
