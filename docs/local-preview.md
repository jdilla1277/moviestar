# Local preview viewer

Keep one browser page open while a coding agent edits a spatial scene project.
The viewer runs on the agent's machine, plays a short MP4 with sound, and follows
saved edits, including `moviestar undo`. No account, upload, or cloud render is
required. This first transport uses the existing renderer: generation is still
slow and does **not** yet meet the three-second preview target.

The viewer uses moviestar's gold star, serif wordmark and blue palette in a
dark interface by default, including when the operating system prefers light
mode. Docs, guided feedback and account links open the main site in another
tab without sending your local project or media. Local review requires no
account. Fonts are bundled, so branding does not require a network connection.

## Start the viewer

From a project containing saved spatial scenes, in a persistent terminal:

```bash
moviestar preview --from 0 --to 5
```

The command prints a JSON response with a `url`, opens that page in your local
browser, and keeps running until Ctrl-C. Edit with your agent in another terminal.
Use `--no-open` to return the link without opening a browser. The default port is
8765; `--port 0` selects an available temporary port. `--workspace PROJECT_ROOT`
selects an existing workspace explicitly. Ranges use composition result time and
are limited to ten seconds; restart the viewer to change the range.

macOS and Linux are supported. Chrome/headless Chrome, Node.js 22+, FFmpeg and
ffprobe are required, as for spatial rendering. Playback requires a browser with
H.264/AAC support. Press Play to hear sound: updates remain paused rather than
unexpectedly restarting audio. The service runs in the foreground so agents
should use their environment's persistent terminal/background-process facility.

The URL uses `127.0.0.1` and works on that machine only. On a remote coding-agent
VM, it requires the host's explicit port-forwarding capability; merely returning
the URL does not make it accessible on a phone. Hosted sharing is a separate
workflow. The default project link survives session restarts while its token and
port remain unchanged. Two sessions for the same project are rejected; an
occupied port is an error, never a silent switch to another URL.

## What follows an edit

Each requested preview pins a parsed spec and project metadata, receives a fresh
request ID, and reports the spec's `state_id`, time range, authored canvas, and
review profile. The renderer reads sources in place. Basic source/asset size,
modification time and inode changes cause a new request, but inputs are not copied
or frozen: leave media unchanged while reviewing. This is not a content-addressed
asset snapshot or a rendered-scene cache.

Only one generation runs per project, with at most one pending request. Rapid
edits replace the pending request with the newest one. Obsolete work receives an
interrupt; after six seconds its owned process group is killed. New generation
waits for cleanup. The same cancellation applies to preview work after undo;
independent exports are not affected. A late completion cannot select an obsolete
clip in the service or player.

Until the new clip is ready, any previous clip stays explicitly labeled older.
A failed latest request shows an error and never presents the previous clip as
current. The player loads the candidate before replacing the displayed video,
preserves a compatible playhead position, and leaves playback paused.

Use the Review menu to play an older **completed preview** without changing the
project. This is separate from `moviestar undo`, which changes the saved project
and requests a new preview. There is no redo command or Undo/Redo viewer button
in this slice. At most eight requests are retained, including the active/current
ones; historical media may expire. Completed media is session-local and removed
on shutdown. The viewer is not a permanent archive of every project revision.

## Review quality and measurements

The authored logical canvas, layout rules, timing and frame rate are preserved.
The existing spatial renderer first generates at the authored raster, then the
review encode fits within a 1280-pixel long edge and 720-pixel short edge without
upscaling. Output is H.264, yuv420p, x264 `fast`/CRF 23, optional AAC audio at
48 kHz, with MP4 fast-start metadata. This preserves geometry but does not yet
reduce per-frame composition work. There is no segmented/progressive delivery:
the completed short MP4 must be ready before this transport can play it.

The status page identifies the displayed request, version, range and quality.
For diagnostics, open the browser console and read:

```javascript
window.movieStarPreview.state
window.movieStarPreview.measurements
```

Measurements include generation time, request-to-playable readiness, player
loading, time waiting for Play, Play-to-first-presented-frame, and playback stall
count. First-frame timestamps use `requestVideoFrameCallback` when available,
with a `playing` event fallback. Absolute request-to-first-frame time includes
the user's Play delay and is explicitly labeled that way. These events cannot
prove perceptual audio/video synchronization: use matched flash/beep cues and
actual listening for that acceptance check. Diagnostics stay in the local page;
there is no telemetry upload.

Final delivery-quality export remains a separate `moviestar export` operation.
Use this viewer to establish delivery costs before optimizing generation; a
three-second readiness target and sustained playback still need measurement.

## Bundled viewer typeface

DM Serif Display Regular is bundled unmodified from
[Google Fonts, revision 7efaaeb](https://github.com/google/fonts/tree/7efaaeb60f72588557a6605b48311611937304ba/ofl/dmserifdisplay).
Its [SIL Open Font License 1.1](../app/src/moviestar/fonts/DMSerifDisplay-OFL.txt)
and copyright notice ship alongside the font and in the wheel's license files.
The font SHA-256 is `8cc3643535edf039aa5d95440a8542735e9197e4f4b8d9303e980fefbf5ab616`.
The viewer also uses the existing bundled Inter family under its existing OFL.
