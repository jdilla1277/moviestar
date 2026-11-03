# Audit caption timing across the whole edit

Use `moviestar captions audit` to find short text flashes and inspect how
caption components appear together over the result timeline. It reports
authored timing and estimated geometry without changing the project.

```bash
moviestar captions audit --language en
```

For example, a treatment might contain these states:

| Result interval | Active text | Finding |
| --- | --- | --- |
| 0.00–0.70 | Make good edits | No duration finding |
| 0.70–0.80 | and | Short component, short state, connector-only candidate |
| 0.80–2.00 | Keep them readable | No duration finding |

The report includes every selected cue/overlay start and end, simultaneous text
components, empty gaps, active IDs, durations, and estimated bounds. A component
is one timed overlay; a state is one interval with a fixed set of active
components. One short component can create several short states when it
overlaps other text, so warning counts are not counts of distinct mistakes.
Numeric `seconds` retain boundary precision; `text` is a millisecond-rounded
display value. Use numeric seconds for very short intervals and proof times.

## Select and group text

By default, each caption track is audited independently. Manual text is opt-in:

```bash
moviestar captions audit --track titles --language en
```

If a heading and its connector are on separate tracks but form one treatment,
combine those tracks explicitly. A persistent heading then supplies context
when the connector appears:

```bash
moviestar captions audit \
  --track headlines --track connectors --combine-tracks --language en
```

Scene-relative manual overlays follow their scene's current result-time
position. Detached overlays and clipped ranges remain visible as warnings.
Generated captions refresh in memory; if refresh fails, the command refuses to
present the old cache as current. Frozen/imported captions keep their authored
times and report known staleness.

## Choose advisory rules

The initial duration threshold is 0.25 seconds. It is a review threshold, not a
universal reading-speed rule:

```bash
moviestar captions audit --min-duration 0.4
moviestar captions audit --min-duration 0
```

Zero disables duration warnings. Connector checks are disabled unless requested.
`--language en` uses a small, reported English function-word list. Use repeated
`--connector-word` options for another language or a custom list:

```bash
moviestar captions audit --track titles --connector-word y --connector-word de
```

The heuristic matches whole words, normalizes canonically equivalent Unicode
spellings, and retains combining marks (for example, `कि` and decomposed accented
words). It does not infer grammar or meaning.
Intentional emphasis may legitimately contain short or connector-only text.
Geometry changes are reported as facts, without automatically calling them
mistakes: a longer caption naturally changes its estimated width.

## Render proof frames

The default command decodes no media, uses no network or model, and writes no
files. It resolves fonts locally for its geometry estimates. Request
representative JPEGs explicitly:

```bash
moviestar captions audit --language en \
  --proof-dir caption-proof --max-proof-frames 12
```

`caption-proof` must be a new directory whose parent already exists. It receives
project screenshots and `report.json`; the response's `out` names that report.
Each frame links to its sampled states and result time. Text is selected at
that exact instant using half-open intervals (`from <= at < to`), so a caption
starting just after the sample is excluded. Sampling keeps timeline
endpoints and distributes findings across the edit, with duplicate frame times
shared between tracks. The report states how many frames were planned and
rendered. An interval falling entirely between expected output-frame times is
marked as having no proof frame.

Proof generation requires FFmpeg and a composition that supports burned text.
If the current project shape cannot render its overlays, the report warns and
proof generation refuses to produce misleading images. Screenshots may create
render caches; the edit spec, active transcript, and source media stay unchanged.
Existing proof directories are never replaced. A render failure returns an
error; any already-created frames remain available for diagnosis.

## What this establishes

The interval report covers selected authored text on the complete result clock.
Its rectangles are the current overlay planner's character-width estimates,
not measured glyph ink; rotation and italic overhang can extend beyond them.
Authored presence and opacity do not establish actual pixel visibility through
other elements or against a changing background.

Rendered samples are evidence for a person or agent to inspect. They do not
approve every frame, prove final-export parity, or check contrast, baked-in text,
motion, listening quality, or transcript correctness. Inspect a finished export
and use `watch` for motion and audio when those matter.
