# Architecture

This exporter reconstructs an Adobe Connect recording export into a single MP4.

The key constraint is that Connect does **not** give you “one video” and “one audio”. It gives you:

- **XML event streams** describing what should happen at time \(t\)
- many **chunked media files** (`*.flv`) that cover only portions of the meeting

So we build a deterministic **timeline**, then render it via FFmpeg.

## High-level flow

1. **Input**: Connect export folder (or ZIP) containing at least `mainstream.xml` and media chunks
2. `replay_core/` parses XML into **timeline events**
3. `exporter/` converts events + chunk metadata into an **EDL-like plan**
4. `exporter/ffmpeg_render.py` runs FFmpeg with:
   - black stage base
   - screenshare overlays (when present)
   - audio mix (VoIP chunks aligned to timeline)
   - optional chat burn-in via ASS subtitles
   - optional “break skipped” slates
   - optional chapter metadata
5. `replay_web/` (FastAPI) provides:
   - upload endpoints for ZIP/folder
   - preflight/estimates endpoint
   - export start/status/stop endpoints
6. `webui/` polls status and shows live log/progress

## Components

### `replay_core/`

- `connect_xml.py`: generic parser for Connect “Message / Method / Object / Array” XML
- `events.py`: extracts the subset of events we care about (streams, screenshare, document share markers)
- `session_model.py`: loads a folder into a `SessionModel` (events + stream instances)

### `exporter/`

- `edl.py`: builds audio/video clips aligned to global session time
- `ffmpeg_render.py`: the render backend
- `export.py`: CLI entrypoint, ties everything together

### `replay_web/`

- `server.py`: FastAPI app, static mount, upload/preflight/export control
- Progress model:
  - FFmpeg writes `-progress pipe:2` and we convert it into **line-based stdout** for the web UI
  - The server drains child stdout on a background thread to avoid blocking HTTP requests

### `webui/`

Static HTML/CSS/JS (no bundler). The UI:

- uploads ZIP/folder to the server
- calls `/api/preflight` to validate + compute rough estimates
- starts export and polls `/api/export/status` for:
  - `progress_pct`
  - `rendered_s`, `elapsed_s`, `eta_s`, `wall_elapsed_s`
  - `new_log` lines to append to the live log

## Known limitations (by design)

- **PDF/whiteboard rendering**: we do not recreate the interactive “teacher marker” or exact slide visuals. We only show a text marker like “`<file>.pdf is being shown`”.
- **Perfect speaker separation**: Connect exports don’t always provide clean per-speaker streams. We do best-effort mixing.
- **Auth for direct downloads**: private servers usually require browser cookies; we prefer a browser-driven “download ZIP, then upload” workflow.

# Architecture

This exporter reconstructs an Adobe Connect recording export into a single MP4.

The key constraint is that Connect does **not** give you “one video” and “one audio”. It gives you:

- **XML event streams** describing what should happen at time \(t\)
- many **chunked media files** (`*.flv`) that cover only portions of the meeting

So we build a deterministic **timeline**, then render it via FFmpeg.

## High-level flow

1. **Input**: Connect export folder (or ZIP) containing at least `mainstream.xml` and media chunks
2. `replay_core/` parses XML into **timeline events**
3. `exporter/` converts events + chunk metadata into an **EDL-like plan**
4. `exporter/ffmpeg_render.py` runs FFmpeg with:
   - black stage base
   - screenshare overlays (when present)
   - audio mix (VoIP chunks aligned to timeline)
   - optional chat burn-in via ASS subtitles
   - optional “break skipped” slates
   - optional chapter metadata
5. `replay_web/` (FastAPI) provides:
   - upload endpoints for ZIP/folder
   - preflight/estimates endpoint
   - export start/status/stop endpoints
6. `webui/` polls status and shows live log/progress

## Components

### `replay_core/`

- `connect_xml.py`: generic parser for Connect “Message / Method / Object / Array” XML
- `events.py`: extracts the subset of events we care about (streams, screenshare, document share markers)
- `session_model.py`: loads a folder into a `SessionModel` (events + stream instances)

### `exporter/`

- `edl.py`: builds audio/video clips aligned to global session time
- `ffmpeg_render.py`: the render backend
- `export.py`: CLI entrypoint, ties everything together

### `replay_web/`

- `server.py`: FastAPI app, static mount, upload/preflight/export control
- Progress model:
  - FFmpeg writes `-progress pipe:2` and we convert it into **line-based stdout** for the web UI
  - The server drains child stdout on a background thread to avoid blocking HTTP requests

### `webui/`

Static HTML/CSS/JS (no bundler). The UI:

- uploads ZIP/folder to the server
- calls `/api/preflight` to validate + compute rough estimates
- starts export and polls `/api/export/status` for:
  - `progress_pct`
  - `rendered_s`, `elapsed_s`, `eta_s`, `wall_elapsed_s`
  - `new_log` lines to append to the live log

## Known limitations (by design)

- **PDF/whiteboard rendering**: we do not recreate the interactive “teacher marker” or exact slide visuals. We only show a text marker like “`<file>.pdf is being shown`”.
- **Perfect speaker separation**: Connect exports don’t always provide clean per-speaker streams. We do best-effort mixing.
- **Auth for direct downloads**: private servers usually require browser cookies; we prefer a browser-driven “download ZIP, then upload” workflow.

