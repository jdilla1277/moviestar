# Agent guide

MovieStar separates editorial judgment from deterministic execution. The agent
decides what the edit should communicate; MovieStar stores non-destructive
state, resolves time mappings, renders through FFmpeg, and returns structured
evidence.

## Project model

Run commands from a dedicated project directory. `moviestar load` creates a
`moviestar/` directory containing project metadata, transcripts, indexed
frames, and the edit specification. Source media is referenced by absolute
path and is never modified.

Use `--workspace PATH` when a command should target a project other than the
current directory or one of its parents. Use `status`, `history`, and the
various `dump`/`list` commands to inspect persisted state before changing it.

## Structured command loop

1. Discover the relevant surface with `moviestar --help` and nested help.
2. Inspect source or current project state.
3. Preview expensive or replacing commands with `--dry-run`.
4. Apply the smallest edit that satisfies the request.
5. Read the JSON response, including paths, ranges, durations, and warnings.
6. Verify the result visually and, when relevant, audibly.

MovieStar writes JSON to stdout and progress narration to stderr. If a harness
merges both streams, use `--quiet` on commands that support it. Prefer returned
file paths over `--inline`; Base64 only helps integrations that convert it into
an actual image content block.

## Time spaces

Do not assume every timecode uses the same clock:

- `trim` uses result-time, so later trims narrow the already-edited result.
- `find` reports source, segment, and result ranges.
- `clip` and source-targeted `watch` operate on source-time without changing
  the edit.
- External audio is placed on result-time, the finished video's clock.
- Composition and scene commands document whether their ranges are source,
  per-source result, composition result, or scene-local time.

Read the command's help and use the named range from the response instead of
copying a numerically similar value from another time space.

## Composition choices

Use `trim` and `cut` for one source. Use `concat` for ordered segments or one
global multicamera layout. Use `scenes set` when layouts, slots, framing, audio
routes, or source ranges change over the finished timeline. Scene motion,
geometry, transitions, captions, overlays, and audio mixing build on that
persisted composition.

Large scene, overlay, caption, and audio changes are easier to audit through
their dump/edit/set JSON workflows. Always validate replacement files with
`--dry-run` before committing them to project state.

## Verification

Use stills for framing and text, `watch` for motion and audio, and `probe` for
file structure. A final export should have a non-zero duration, expected video
dimensions, the intended audio/video streams, and representative frames that
match the request. Loudness reports help measure a mix but do not replace
listening when subjective balance matters.

The distributable [MovieStar skill](install-skill.md) gives compatible agents
this workflow automatically. The [complete command guide](../app/README.md)
covers every command family.
