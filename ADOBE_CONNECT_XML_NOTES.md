## Goal
This document summarizes the **useful data we can reliably extract** from an Adobe Connect “offline export” folder (the one containing `mainstream.xml`, `ftchat*.xml`, `ftcontent*.flv`, `screenshare_*.flv`, `cameraVoip_*.flv`, etc.).

It’s written to match what we implemented in this project: a **timeline-driven exporter** (not a traditional player) that reconstructs the session with FFmpeg.

## The core idea (most important)
Adobe Connect does **not** give you “one audio file” and “one video file”.

Instead, it gives you:
- **XML event logs** that describe *what should be happening at time \(t\)*.
- Many **FLV chunks** (files) that each cover some portion of the session.

So the correct mental model is:
- **Global session time** (ms)
- **Events** that switch state (talking, screen share, document share)
- **Chunked media** placed onto the global timeline

## Files and what they contain

### `mainstream.xml`
This is the most important timeline/event stream.

We parse messages that look like:
- `playEvent(meta, action, args)`

Where:
- **`meta`** is an object (dict) that may include:
  - `time` (ms in session timeline)
  - `name` (event name in some cases)
- **`action`** is a string like:
  - `streamAdded`
  - `playStream`
  - `stopStream`
  - `setContentSo`
- **`args`** is usually a list of dicts/strings depending on the action

#### `streamAdded` (critical)
This is where Connect announces that a **new FLV chunk exists** and when it starts on the global timeline.

Typical fields we use:
- **`streamId`**: logical id, e.g. `cameraVoip_0`, `screenshare_12`
- **`streamName`**: file-ish name, e.g. `/cameraVoip_0_3` (→ `cameraVoip_0_3.flv`)
- **`startTime`**: global start time in ms for that chunk (this is the key)
- **`streamType`**: helps classify the stream (`cameraVoip`, `screenshare`, …)
- **`streamPublisherID`**: publisher/person id (often the closest thing to “user id” we have)

Important: **`streamId` is logical**, and can map to *many* physical chunk files over time.

In this repo we model that explicitly as:
- `SessionModel.stream_instances`: a list of `(logical_id, start_time_ms, path, kind, publisher_id)`

#### `playStream` / `stopStream`
These indicate what Connect UI considers “active”, but for exporting audio we **do not rely on them** for correct chunk selection.

Reason: the chunking behavior means audio continuity is correctly expressed by:
- `streamAdded.startTime` + `ffprobe(file).duration`

We still keep these events as “useful hints” for analysis and future features.

#### `setContentSo` (screen share and document share)
This is the event family that signals:
- **Screen share starts/stops** (via `screenDescriptor.streamID`)
- **Document/whiteboard share** (via `shareType=document|wb`, plus `documentDescriptor.theName`)

For exporting we do:
- If screenshare video exists: overlay those chunks onto the stage
- For document share: we do not render PDF; we show a **text marker overlay** like:
  - `Introduction.pdf is being shown`

### `ftchat*.xml`
These files contain chat history messages.

We parse messages that include:
- `text`
- `fromName` (sometimes empty / not reliable)
- **`fromPID`** (useful stable sender id)
- `when` (often an epoch-like ms; we normalize to session-relative by subtracting first `when`)

In the exporter, we burn chat into the MP4 using an **ASS subtitle file**:
- Format: `user{PID}: message`
- Long messages are wrapped into multiple lines (width constrained)

### `document-metadata.xml`
We previously extracted some “chat-like” info from here, but it generally **does not contain sender PID** like `ftchat*.xml` does.

For “userPID: message” we prefer `ftchat*.xml`.

### `ftcontent*.flv`
These are often **data-only** (not decodable audio/video), and FFmpeg can throw:
`Output file does not contain any stream`.

So:
- Do not treat them as normal video/audio.
- Use XML events (like `setContentSo`) to show text overlays instead.

## What we can do well (today)
- **Accurate audio placement** by placing each `cameraVoip_*.flv` chunk at its `startTime` and mixing.
- **Screenshare overlay** for `screenshare_*.flv` chunks.
- **Chat overlay** with `fromPID` (`user123: ...`) using ASS subtitles.
- **Break detection** using “no active audio windows + no chat for a long time”, then compressing that gap into a short slate.
- **Chapters** (timeline headings) for chunk starts and breaks (MP4 chapter metadata).

## What is still hard / future work
- Perfect “who is talking” timeline: we have `userVoipStatusChanged`, but it is noisy and can toggle rapidly.
- Document/whiteboard true rendering: Connect’s internal whiteboard operations are non-trivial to reconstruct visually.
- Multi-party audio separation: we currently mix everything; separating speakers requires deeper mapping and sometimes isn’t possible from exports alone.

## Key vocabulary used in our code
- **Logical stream id**: e.g. `cameraVoip_0`
- **Physical chunk file**: e.g. `cameraVoip_0_3.flv`
- **Stream instance**: the mapping of a logical stream to a specific chunk + start time
- **Global timeline**: absolute time in session-relative ms

