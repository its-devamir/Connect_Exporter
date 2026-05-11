## Adobe Connect Exporter (web UI)

This folder contains everything needed to export an Adobe Connect recording folder into a **single MP4** with:

- Mixed VoIP audio (correct chunk placement)
- Screenshare overlaid on a black stage
- Optional chat popups
- Optional break skipping
- Optional MP4 chapters (timeline headings)

### Project status / limitations

- **Works well for**: offline exports where Connect provides `mainstream.xml` + the media chunk FLVs.
- **Does not**: perfectly recreate interactive slide pointers / whiteboard drawing (“teacher marker”). We only show a short text marker when a PDF is shared.
- **Auth note**: many Connect servers require login to download the export ZIP; this tool supports a browser-driven workflow.

### 1. Requirements

- Python 3.10+ installed
- `ffmpeg` and `ffprobe` on PATH
- Internet access to install Python dependencies (first run only)

Install Python deps:

```bash
python -m pip install -r requirements.txt
```

### 2. Start the web UI

From this folder:

```bash
python -m replay_web.run_server
```

Your browser should open `http://127.0.0.1:8765/`.

Static assets load from **`/static/`** (`styles.css`, `app.js`). If the UI looks wrong after pulling changes, do a hard refresh (**Ctrl+F5**).

### Student-friendly (Windows) usage

If you don’t want to install Python or use the terminal:

- Download the **latest GitHub Release** (a ZIP that contains `ConnectExporter.exe` + a `bin/` folder with FFmpeg).
- Unzip it.
- Double-click **`START_HERE.bat`** (or `ConnectExporter.exe`).

Maintainers can rebuild that release on Windows with:

```powershell
.\tools\build_windows_release.ps1 `
  -Python "C:\Python313\python.exe" `
  -FfmpegExe "C:\ffmpeg\bin\ffmpeg.exe" `
  -FfprobeExe "C:\ffmpeg\bin\ffprobe.exe"
```

This produces `release\` (exe + `bin\ffmpeg.exe` + `bin\ffprobe.exe` + `START_HERE.bat` + `README-WIN.txt`) and a `ConnectExporter-win64.zip` ready to attach to a GitHub Release. Verify the resulting exe with `.\tools\smoke_exe.ps1`.

### 3. Using the UI (wizard)

The UI has four steps: **Download** → **Upload** → **Settings** → **Export**.

1. **Download**
   - Paste the **session URL** (e.g. `https://…/?session=…`).
   - Click **Open** to download the ZIP in your browser (uses your login).
   - Click **I already downloaded →**.
2. **Upload**
   - Drag–drop the **ZIP** you downloaded (recommended), or a **folder** export.
   - Or paste a **disk path** (same machine as `run_server`).
   - Click **Next: Settings →** (the app verifies the session).
3. **Settings**
   - See **Your system** (OS, FFmpeg, NVENC, GPUs).
   - **Rough estimates** for encode time and output size update when you change options.
   - Use presets (**Fast preview**, **Balanced**, **High quality**) or customize resolution (720p, 1080p, custom), FPS, quality (CRF), encoder, and toggles. **`?`** icons explain each control.
4. **Export**
   - **Start export** opens step 4 with a **live log**, progress bar, percent, and ETA. Use **Stop** to cancel.

Outputs default next to the recording folder unless you use an absolute path.

Install **`python-multipart`** (`requirements.txt`) so ZIP/folder uploads work.

### 4. Command-line export (advanced)

You can still run the exporter directly:

```bash
python -m exporter.export --folder . --out replay.mp4 --fps 15 --crf 30 --preset ultrafast --encoder auto
```

See `exporter/README.md` for more CLI details.

### Docs

- `ARCHITECTURE.md`: how the exporter works internally
- `ADOBE_CONNECT_XML_NOTES.md`: what we can and can’t reliably extract from Connect exports
- `CONTRIBUTING.md`: contribution guidelines

