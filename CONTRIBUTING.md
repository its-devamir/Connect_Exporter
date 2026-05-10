# Contributing

Thanks for considering a contribution — this project is intentionally small and “boring” so it’s easy to maintain.

## What this project is (and isn’t)

- **Is**: an offline exporter that reconstructs a Connect recording by reading XML event logs + chunked media and rendering a single MP4 with FFmpeg.
- **Is not**: a full Adobe Connect player clone. Some Connect features do not export enough data to reproduce perfectly.

## Quick start (dev)

```bash
python -m pip install -r requirements.txt
python -m replay_web.run_server
```

Hard refresh the UI after edits (**Ctrl+F5**) since assets are served under `/static/`.

## Code layout

- `replay_core/`: parsing + session model (Connect XML → timeline events)
- `exporter/`: FFmpeg render pipeline
- `replay_web/`: FastAPI backend (upload, preflight, export control)
- `webui/`: static HTML/CSS/JS (no bundler)

## Guidelines

- **Keep it deterministic**: same input folder → same output MP4 as much as possible.
- **Prefer explicit, line-based logs**: the web UI reads stdout line-by-line.
- **Avoid big dependencies**: keep install friction low for non-dev friends.
- **Don’t commit huge recordings**: sample exports are enormous. Use `.gitignore` and share test exports privately.

## What to work on

High-impact, review-friendly ideas:

- Improve ZIP/session download UX (multiple candidate URLs, better error messages, optional auth cookie flow)
- More accurate “break skipping” detection
- Better audio mixing heuristics (per-speaker leveling, clipping prevention)
- Test fixtures (tiny synthetic export folders)

## Submitting changes

- Keep PRs small (one feature/fix at a time)
- Include:
  - what changed
  - why it changed
  - how you tested (CLI command or UI steps)

