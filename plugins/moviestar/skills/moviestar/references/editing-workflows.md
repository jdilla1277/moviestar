# Editing workflows

Choose the smallest workflow that satisfies the request. Run the relevant
`--help` command before authoring details; the CLI help is the command contract.

## Find and cut a spoken moment

1. `load` the source with transcription enabled. Read its warnings:
   `audio_channels_out_of_phase` means one channel was transcribed instead
   of a cancelling mix; `no_speech_in_audio` means Whisper was skipped; and
   `words_outside_detected_speech` means the transcript may be invented.
2. Use `find QUERY --context 1` to locate candidate speech and inspect its
   score, contiguous match, surrounding segments, and result range.
3. Use `skim --text-only` or `watch` around the candidate when wording or
   delivery needs confirmation.
4. Apply `trim --from ... --to ... --snap-to-words` using result-time
   boundaries. For independent source-time clips that must not alter the edit,
   use `clip` or an atomic `batch` recipe instead.

## Work with long recordings

Whisper on hours of footage takes hours. When the source has captions, such
as YouTube captions or a broadcaster's SRT, load them instead with
`load VIDEO --captions FILE.vtt`. This takes seconds, and the transcript
records `backend: "imported-captions"`. To get Whisper quality only where it
matters, use `load VIDEO --transcribe-range START END` or
`retranscribe --range START END`. Whisper then runs on that window alone,
and a ranged run replaces only the words inside the window. Do not parse
caption files or write transcript JSON yourself.

## Browse visual or silent footage

Use `storyboard` for a whole-source map, `skim` for indexed frames plus
transcript, `inspect` for dense frames over a range, and `activity` for
machine-readable visual-change candidates. Use `watch` when motion, timing, or
audio cannot be judged from still frames.

## Compose multiple sources

- Use `concat` for an ordered list of source segments or one global layout.
- Set `--audio-from` on the composition when one source should provide
  continuous audio across camera cuts.
- Use `layouts` before choosing a preset and slot names.
- Use `scenes set` when layouts, slots, framing, source ranges, or audio routes
  change over time. Prefer the editable JSON form for large compositions;
  validate it with `--dry-run` before replacing state.
- Use `scenes transition`, `scenes geometry`, `scenes inset`, and
  `scenes motion` only after inspecting their nested help and the current scene
  IDs. Preview camera changes before applying them.

## Add captions or text

- Use `captions generate` for transcript-derived captions and
  `captions import` for SRT/VTT sources.
- Use caption rules for repeatable token corrections; use dump/edit/set or the
  caption edit commands for cue-level changes. Rules ignore leading/trailing
  punctuation and keep it, so one rule covers `Moviestar` and `Moviestar.`.
  Check `caption_rule_unmatched` warnings after `captions generate` or
  `captions rules add` for rules that matched nothing.
- Use `captions placement` for caption size and position: `--at`, `--size`,
  `--margin`, `--width`, or `--x/--y`, scoped with `--scene` or `--layout`.
  Run it with no flags to read the current placement and exact pixels, and
  add `--dry-run` before changing it. Defaults already scale with the canvas
  and clear vertical-platform controls.
- Use `overlays add` for titles, lower thirds, labels, or emphasis text. Use
  dump/edit/set for bulk changes and validate the replacement first.
- Verify text at multiple timecodes with project-aware `screenshot` or a short
  `watch` render. Check legibility, timing, safe placement, and overlap.

## Mix audio

- Inspect routed source audio with `audio source`.
- Place voiceover or music with `audio add`; use explicit gain, fades, looping,
  and ducking only when the request calls for them.
- For complex mixes, use `audio dump`, edit the JSON, then run `audio set
  audio.json --dry-run` before applying.
- Use `watch --loudness-report` on a representative result-time window or
  `export --loudness-report` for the complete deliverable. Loudness metrics do
  not replace listening when subjective balance matters.

## Recover or revise

Use `history` and `status` to understand existing state. Use `undo` for the
last supported revision. Dump structured state before bulk replacement so the
previous version can be restored without reconstructing it from prose.
