# Exporter (fast FFmpeg render)

Goal: produce a single MP4 from an Adobe Connect export folder using **timeline-driven** FFmpeg edits.

## What it does (MVP)

- Builds a deterministic timeline from `mainstream.xml`:
  - VoIP audio switches (`playStream`)
  - Screenshare start/stop (from `setContentSo` + `screenDescriptor.streamID`)
  - Document/whiteboard markers (from `shareType=document|wb`) as **text overlay only**
- Detects long inactivity as **session breaks** and overlays a short “Session break …” message.

## Run

From the export folder:

```bash
python -m exporter.export --folder . --out replay.mp4
```

Requirements:
- `ffmpeg` on PATH

