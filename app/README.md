# moviestar

**Video editing CLI for AI agents.** Give your coding agent the ability to edit video.

Your agent loads one or many videos, fuzzy-searches the transcript, composes and shapes a timeline across multiple cameras, animates per-scene camera and layout motion, retimes footage, mixes source audio with voiceover and music, burns in captions with spoken-word highlighting, and renders with FFmpeg — or extracts any number of standalone clips in one pass. All output is structured JSON. Source files are never modified — edits are described in a declarative spec and applied at render time.

## Quick start

Make sure FFmpeg is on PATH (`brew install ffmpeg` on Mac), create a Python 3.10+ venv, then paste this into Claude Code, Cursor, or any coding agent:

```
Create a Python 3.10+ virtual environment, then:

pip install moviestar
moviestar --help

Read the --help output — it's your operational briefing.

Then load the video at ~/Downloads/podcast.mp4. Find the moment where
the host says "the bottom line, here's what I think." Trim to just
that sentence with --snap-to-words so the cuts don't land mid-word.
Export the result to ~/Desktop/clip.mp4 and tell me how long it is.
```

The agent will work through `load → find → trim → export`, returning structured JSON at every step. No GUI, no timeline, no manual scrubbing.

`python -m moviestar` is an equivalent entry point when an agent wants to
guarantee it is invoking MovieStar from the active Python environment.

For a multi-camera recording, the agent can `load cam1.mp4 cam2.mp4 screenshare.mp4`, `concat` ranges from each into one timeline, pick whose mic plays with `--audio-from`, choose a global layout with `--layout`, or use `scenes set` for layout changes over time. Then `captions generate` burns word-highlighted captions from the transcript into the final render.

For a product demo, `scenes geometry` resizes, places, shapes, borders, or bounces a picture-in-picture slot. `scenes motion` adds source-relative zooms and pans plus speed, target-duration, and hold pacing to individual scene slots. `audio` places voiceover and music on the finished timeline, adjusts gain and fades, loops beds, and ducks one layer under another.

## What's in MovieStar

**Browse:**
- `moviestar load` — index one or more videos, transcribe with local Whisper. `--as <name>` names sources; `--add` appends to an existing project; `--no-download` guarantees a hermetic run.
- `moviestar skim` — fast browse: thumbnails + transcript over a range (`--text-only` for transcript alone).
- `moviestar inspect` — dense thumbnails on demand at a configurable interval.
- `moviestar activity` — read-only visual-change evidence for silent recordings: per-sample scores plus candidate active, idle, and held-frame ranges (`--dry-run` previews decode cost).
- `moviestar storyboard` — one labeled contact sheet that maps a whole video or result-time range at a glance.
- `moviestar watch` — extract an MP4 segment for multimodal model analysis.
- `moviestar status` — current project state at a glance.
- `moviestar history` — step-by-step edit lineage for a source.

**Edit (multi-source):**
- `moviestar trim` — append a trim to a source's edit spec (result-time semantics; stacks compose).
- `moviestar cut` — remove a range from the middle of a source's result.
- `moviestar concat` — stitch ranges from one or more sources into a single composition. `--audio-from <source>` routes which source's audio plays across the cut; `--canvas`, `--layout`, `--slot`, and `--framing` build canvas-aware visual compositions.
- `moviestar layouts` — inspect preset layout regions and sample images before choosing a layout.
- `moviestar scenes set` / `moviestar scenes list` — author and read ordered scene-layout compositions where each scene can use its own preset layout, slots, source ranges, framing, and audio route. For large compositions, pass editable JSON with `moviestar scenes set scenes.json --dry-run`, review the resolved plan, then re-run without `--dry-run`.
- `moviestar scenes geometry SCENE:SLOT` — resize and place a slot with named, normalized, or custom `WxH` sizes; choose a circle, rounded rectangle, or rectangle; add a solid border; or animate a bounded bounce at a named speed. Matching bounce motion stays continuous across adjacent scene boundaries, while a new motion run starts at its authored placement. New picture-in-picture scenes default to a medium circular inset with a white border. `--dry-run` previews resolved pixels without writing, and `--reset` restores that preset default.
- `moviestar scenes inset` — move or resize the rectangular picture-in-picture inset suggested by camera coverage warnings; exact crop targets re-resolve against the new region.
- `moviestar scenes motion target` / `camera preview` / `camera apply` — point at important content on a real frame, inspect the exact from/to crop plus coverage and softness warnings, then persist exactly what was previewed. `camera list`, `update`, and `remove` handle later adjustments.
- `moviestar scenes motion dump` / `moviestar scenes motion set` — round-trip per-slot pacing and camera state as editable JSON. Speed a source-local range up or down, fit it to an exact result duration, hold a frame, or define source-relative zoom/pan targets. `--dry-run` reports calculated peer pacing, time maps, normalized/pixel targets, derived crops/zoom, and pacing-driven camera rebases. Screenshot, inspect, watch, and export all render the same resolved camera and pacing plan. Once motion exists, scene/slot IDs preserve it across explicit JSON renames; deletions report exactly which attached motion records were removed.
- `moviestar undo` — pop the last operation (or the last concat).
- `moviestar spec` — show the current spec (or `--edit` to replace, `--reset` to clear).
- `moviestar find` — fuzzy-search the transcript across every source; `--context` returns the surrounding timestamped segments.

Every source-specific command takes `--source <id>`, required only when a project has more than one source.

**Captions and text overlays:**
- `moviestar captions generate` — compile the transcript into caption overlays, following each scene's audio routing. `--highlight spoken-word` colors the word being spoken, timed from the transcript's word timing.
- `moviestar captions import` — import `.srt` / `.vtt` caption files as overlays (cue timing is result-time; line breaks preserved).
- `moviestar captions rules add --merge "ground -truthing=ground-truthing"` / `--replace "mispelled=misspelled"` — persist exact project-level token corrections that every future `captions generate` applies before cue grouping. Use `captions rules list` and `captions rules remove <rule-id>` to inspect or remove them.
- `moviestar overlays add` — one timed text overlay: title, lower third, label, or creative emphasis text. Position presets or normalized `--x/--y`, `--z-index` stacking, style presets plus a CSS-like subset (`--css "font-size: 72px; color: white; -moviestar-stroke: 4px black; transform: rotate(-8deg)"`).
- `moviestar overlays dump` / `moviestar overlays set` — round-trip the complete overlay state as editable JSON: fix caption text, shift timing, restyle, then atomically replace after validation.
- `moviestar fonts list` / `moviestar fonts add path/to/font.ttf --name "My Font"` — see bundled fonts and install custom `.ttf`, `.otf`, or `.ttc` files without touching package internals. Inside a loaded project, fonts install to `moviestar/fonts`; outside a project, they install to `~/.moviestar/fonts`. Use them from captions or overlays with `--css "font-family: My Font"`.
- Overlays render on every verification surface — `screenshot`, `inspect`, `watch`, and `export` all burn them in, with bundled or installed fonts resolved before rendering.

**Audio mix:**
- `moviestar audio source` — inspect, mute, or adjust the gain of routed source audio.
- `moviestar audio add` — place voiceover or music against result time with source trimming, gain, fades, optional looping, and signal-driven ducking.
- `moviestar audio dump` / `moviestar audio set` — round-trip the complete mix as editable JSON and validate it with `--dry-run` before rendering.
- `watch` and `export` render the same resolved mix. `watch --from ... --to ... --loudness-report` measures every routed layer and the final mix over one result-time window. Non-default mixes use a final clipping-protection limiter, with optional loudness normalization applied after the complete mix.

**Output:**
- `moviestar export` — render the composition, scene composition, layout composition, or a single source's edit to MP4, `moviestar-clips/export.mp4` by default. Use `--loudness-target -14` to normalize shorts-style audio, and `--audio-join-fade 0.08` to smooth sequential cut/scene joins without changing result timing.
- `moviestar clip` — extract one standalone clip to its own MP4 (source-time, leaves the edit spec untouched).
- `moviestar batch` — extract many standalone clips from a JSON recipe in one atomic, frame-exact pass.
- `moviestar clean` — preview or delete generated media artifacts.
- `moviestar screenshot` — single frame at a timecode (project-aware: `--at` is in result-time). Pass `--file export.mp4` to extract directly from a raw or exported file and bypass the loaded project.

**Always-available:**
- `moviestar doctor` — check FFmpeg/ffprobe availability and the filters and encoders MovieStar needs before authoring. Its structured report names blocked features and exact install fixes.
- `moviestar probe` — ffprobe metadata as JSON. Add `--loudness` (optionally with `--from` / `--to`) for structured integrated loudness and peak metrics.
- `moviestar models pull <model>` — pre-download a Whisper model before a load, CI job, or offline session.
- `moviestar subscribe EMAIL` — sign a human up for MovieStar updates. The human receives a double-opt-in confirmation email and is not activated until they open its link.
- `moviestar feedback` — print a guided feedback report with required context plus optional diagnostic, positive-feedback, idea, wild-idea, and follow-up email prompts. Save it with `moviestar feedback > feedback.md`, complete it, then submit it with `moviestar feedback --file feedback.md` (`--file -` reads stdin). If an email is provided, it is stored separately and used to send a note when that feedback is addressed; it does not create a general updates subscription. For broader update emails, an agent can run `moviestar subscribe EMAIL`, and the human must confirm by email. For a deliberately short anonymous note, use `moviestar feedback --quick "message"`. The payload never automatically includes project files, paths, session logs, account identifiers, or command history.
- `moviestar account connect EMAIL` — ask MovieStar to email the human a private account claim. The human signs up or signs in, chooses a public handle when needed, and approves the named installation; `moviestar account status` then stores its revocable device credential without exposing the email link or password to the agent. Accounts remain optional for local editing.
- `--dry-run` on every expensive or high-impact command (`load`, `inspect`, `watch`, `export`, `clip`, `batch`, `concat`, `scenes set`, `spec --edit`).

Image-producing commands return files by default: read `out` or each
`thumbnails[].path` with the agent's image/file tool. Pass `--inline` only
when a custom executor converts the returned Base64 bytes into image content
blocks; ordinary CLI harnesses otherwise ingest those bytes as text.

Run `moviestar --help` for the full command list.

## Why moviestar

Video editing tools are built for humans with GUIs. Agents don't have hands on a timeline or eyes on a canvas. moviestar is the hands; the agent is the brain.

- **Verbose by default.** Every command returns rich structured JSON on stdout — agents can discard what they don't need, but can't invent data the CLI didn't provide. Progress narration goes to stderr; if you capture both streams together (`2>&1`), pass `--quiet` (or set `MOVIESTAR_QUIET=1`) on `load` / `retranscribe` / `export` so the merged stream stays pure JSON.
- **Deterministic.** Same input + same parameters = same output. No randomness, no hidden model calls.
- **Result-time semantics.** A second trim narrows the current result, not the original source. `find` matches resolve to both source-time and result-time so screenshots and exports compose cleanly.
- **Multi-source native.** Load many cameras into one workspace, compose a timeline across them, and route audio per-composition — the same structured CLI the single-source flow uses.
- **Non-destructive.** Source files are never modified.

Run `moviestar --help` for the operational contract exposed to agents.

## Requirements

- **Python 3.10+**
- **FFmpeg** on `PATH` (`brew install ffmpeg` / `apt install ffmpeg`)

Run `moviestar doctor` after installation to verify that the FFmpeg build has
the filters and encoders required by the features you plan to use.

The package weighs ~210MB on install — `faster-whisper` ships local transcription out of the box (no API keys, no cloud round-trip). Diarization is opt-in: `pip install moviestar[diarize]`.

## License and status

MovieStar supports multi-source scene composition, authorable slot geometry and
shapes, per-scene camera and layout motion, derived captions, timed overlays,
and result-time audio mixing. Every project shape resolves through one compiled
timeline; caption recipes follow later edits, manual overlays can bind to
scenes, and undo operates on command revisions.

MovieStar v0.7.0 and later is licensed under the Apache License 2.0. Versions
through v0.6.0 were released under the Business Source License 1.1; changing
the license for newer versions does not relicense those historical releases.
