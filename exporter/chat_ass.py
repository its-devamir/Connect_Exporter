from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import xml.etree.ElementTree as ET


@dataclass(frozen=True, slots=True)
class ChatCue:
    t_ms: int
    who: str
    text: str


def _wrap_lines(s: str, max_cols: int = 34) -> str:
    """
    Soft wrap into multiple lines using ASS newline (\\N).
    """
    s = " ".join(s.split())
    if len(s) <= max_cols:
        return s
    words = s.split(" ")
    lines: list[str] = []
    cur = ""
    for w in words:
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= max_cols:
            cur = cur + " " + w
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return r"\N".join(lines[:4])  # cap height


def cues_from_ftchat(messages) -> list[ChatCue]:
    cues: list[ChatCue] = []
    for m in messages:
        pid = m.from_pid or ""
        who = f"user{pid}" if pid else "user"
        text = _wrap_lines(f"{who}: {m.text}")
        cues.append(ChatCue(t_ms=int(m.t_ms), who=who, text=text))
    return cues


def parse_document_metadata_chat(path: Path) -> list[ChatCue]:
    """
    Parse `document-metadata.xml` chat sections.

    Observed:
    <section type="chat" position="29289"><content>...</content></section>

    `position` is session-relative ms (works well for overlays).
    """
    path = Path(path)
    if not path.exists():
        return []
    root = ET.parse(path).getroot()
    out: list[ChatCue] = []
    for sec in root.findall("section"):
        if sec.attrib.get("type") != "chat":
            continue
        pos = sec.attrib.get("position") or "0"
        try:
            t_ms = int(float(pos))
        except Exception:
            t_ms = 0
        content_el = sec.find("content")
        text = (content_el.text or "").strip() if content_el is not None else ""
        if not text:
            continue
        out.append(ChatCue(t_ms=t_ms, who="user", text=_wrap_lines(text)))
    out.sort(key=lambda c: c.t_ms)
    return out


def _ass_time(ms: int) -> str:
    ms = max(0, int(ms))
    cs = ms // 10  # centiseconds
    s = cs // 100
    cs = cs % 100
    m = s // 60
    s = s % 60
    h = m // 60
    m = m % 60
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def write_chat_ass(cues: list[ChatCue], out_path: Path, *, hold_ms: int = 10_000) -> Path:
    """
    Create an ASS subtitle file with bottom-right chat popups.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    header = "\n".join(
        [
            "[Script Info]",
            "ScriptType: v4.00+",
            "PlayResX: 1280",
            "PlayResY: 720",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            # Alignment 3 = bottom-right
            "Style: Chat,Arial,26,&H00FFFFFF,&H000000FF,&H00111111,&H64000000,0,0,0,0,100,100,0,0,1,2,1,3,40,40,44,1",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
    )

    lines = [header]
    for c in cues:
        start = _ass_time(c.t_ms)
        end = _ass_time(c.t_ms + hold_ms)
        # Fade in/out: \fad(in_ms,out_ms)
        txt = c.text.replace("{", "(").replace("}", ")")
        # Slightly transparent box via style backcolor; use \bord for readability
        ass_text = r"{\fad(250,600)\bord2\shad0\q2}" + txt
        lines.append(f"Dialogue: 0,{start},{end},Chat,,0,0,0,,{ass_text}")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path

