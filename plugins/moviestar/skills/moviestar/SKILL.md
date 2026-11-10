---
name: moviestar
description: Edit and verify videos with the Moviestar CLI. Use for trimming, transcript search, multicamera composition, scene layout and motion, captions, overlays, audio mixing, rendering, or inspecting Moviestar projects and exports. Do not use for image-only editing or unrelated FFmpeg administration.
metadata:
  openclaw:
    requires:
      bins:
        - moviestar
        - ffmpeg
        - ffprobe
    homepage: https://trymoviestar.com
---

# Moviestar

Use Moviestar as the deterministic editing layer. Make editorial decisions from
the user's request and inspected media; let the CLI own project state,
time-mapping, rendering, and structured verification data.

## Start safely

1. Work in a dedicated task directory. Moviestar creates a `moviestar/`
   project there and stores absolute pointers to source media; it never copies
   or modifies the source files.
2. Run `moviestar doctor`. If the command is missing or the environment is not
   ready, follow the bundled [installation guidance](references/installation.md).
3. Run `moviestar probe VIDEO` when source dimensions, codecs, duration, or
   audio presence matter.
4. Use `moviestar --help` and `moviestar COMMAND --help` before unfamiliar
   operations. Do not invent flags from memory.

## Edit in an inspect-plan-apply-verify loop

- Load media once with `moviestar load`. Name sources with `--as` when stable
  IDs will help a multicamera or multi-file edit.
- Inspect before cutting: use `status`, `find`, `skim`, `storyboard`,
  `inspect`, `screenshot`, or `watch` according to the evidence needed.
- Treat stdout as structured JSON. Read paths and ranges from response fields;
  progress narration is on stderr. Use `--quiet` only when a harness combines
  both streams and requires pure JSON.
- Preview expensive or replacing operations with `--dry-run`. Review resolved
  ranges, durations, warnings, output paths, and FFmpeg plans before applying.
- Remember the clocks: `trim` is result-time, `find` reports both source and
  result ranges, and composition ranges are documented per command. Never
  substitute one time space for another.
- For local human review of a supported single-video spatial scene, keep
  `moviestar preview --mode native --from 0 --to 5 --no-open` running in a
  persistent terminal and return its URL. It follows saved edits and undo;
  the human presses Play for sound. Ranges use full composition result time and
  can select a section inside a multi-scene project, up to ten seconds wholly
  within one eligible spatial span. Crossing cuts/pacing boundaries or overlapping
  transitions is unsupported. Unsupported native projects fail explicitly:
  use `--mode rendered` for the completed-MP4 alternative. The link is local to
  the machine and needs explicit host port forwarding for remote access. See
  [local preview](https://github.com/jdilla1277/moviestar/blob/main/docs/local-preview.md).
- After each material edit, inspect `status` or the relevant read command.
  Verify visuals with returned image/video files rather than Base64 text.

Read [editing workflows](references/editing-workflows.md) when choosing between
single-source, transcript, multicamera, scene, caption, overlay, or audio
commands. Before delivery, follow [render verification](references/verification.md).

## Preserve user control

- Keep source media untouched and write generated media to the task directory
  or a user-approved output path.
- Do not use `--force`, replace a composition/state file, overwrite an explicit
  output path, or delete generated artifacts without previewing the effect and
  confirming it matches the request.
- Report important warnings instead of hiding them: weak transcript matches,
  framing coverage, softness, clipping, loudness limits, duration drift, and
  missing dependencies can change the deliverable.
- Do not submit feedback, create accounts, subscribe an email address, download
  large models, or install optional dependencies unless the user requested or
  approved that action.

## Finish with evidence

Return the final output path, probed duration and streams, the editing choices
made, and any remaining warning. A successful command is not by itself proof
that the edit looks or sounds right; inspect representative frames and motion,
and check audio when the deliverable contains it.
