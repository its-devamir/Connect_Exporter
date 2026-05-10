from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .connect_xml import parse_connect_xml


@dataclass(frozen=True, slots=True)
class ChatMessage:
    t_ms: int
    from_name: str
    from_pid: str
    text: str
    color: str


def parse_ftchat(folder: Path) -> list[ChatMessage]:
    raw: list[tuple[float, str, str, str, str]] = []
    for p in sorted(Path(folder).glob("ftchat*.xml")):
        for m in parse_connect_xml(p):
            if m.method != "playEvent" or len(m.args) < 3:
                continue
            meta, action, arr = m.args[0], m.args[1], m.args[2]
            if not isinstance(meta, dict) or not isinstance(action, str):
                continue
            if action != "setHistory6":
                continue
            if not isinstance(arr, list):
                continue
            for it in arr:
                if not isinstance(it, dict):
                    continue
                text = str(it.get("text") or "")
                if not text.strip():
                    continue
                from_name = str(it.get("fromName") or "").strip()
                from_pid = str(it.get("fromPID") or "").strip()
                color = str(it.get("color") or "Default")
                when = it.get("when")
                try:
                    when_ms = float(when) if when is not None else 0.0
                except Exception:
                    when_ms = 0.0
                raw.append((when_ms, from_name, from_pid, text, color))

    if not raw:
        return []

    raw.sort(key=lambda x: x[0])
    base = raw[0][0]
    msgs: list[ChatMessage] = []
    for when_ms, from_name, from_pid, text, color in raw:
        t_ms = int(max(0.0, when_ms - base))
        msgs.append(ChatMessage(t_ms=t_ms, from_name=from_name, from_pid=from_pid, text=text, color=color))
    return msgs

