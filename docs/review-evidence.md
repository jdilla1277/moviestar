# Review evidence

Add `--review-report` to `export` or `watch` to create a local JSON report tied
to that exact rendered artifact and the project state at render start. It is
independent of `--review`, which selects a 720p rendition:

```bash
moviestar export --review --max-bytes 20000000 --review-report --out cut-review.mp4
moviestar watch --from 12 --to 20 --review-report --out section.mp4
```

The response's `review_report.out` is the absolute report path under the
workspace's `moviestar/reviews/` directory. Substitute that path for `REPORT.json`
in the commands below. Every render gets a new report; earlier reports and
judgments are retained. Normal render calls create no review state. Dry runs
describe the intended report directory without creating a report, hashing
output media, or claiming actual review evidence.

## Five independent dimensions

| Dimension | Question to review |
| --- | --- |
| `technical` | Does the encoded artifact meet the technical checks actually performed? |
| `source_alignment` | Does it contain the expected source content at the expected times? |
| `visual` | Are framing, text, and other visible details acceptable in the inspected material? |
| `motion` | Does the moving picture work across the reviewed playback ranges? |
| `listening` | Does the audio sound acceptable across the ranges someone actually listened to? |

All five start `unreviewed`. Rendering successfully, probing duration, meeting
a byte limit, or measuring LUFS does not pass a review dimension. Measured
renderer facts are kept separately in `render_facts`, including an unmet byte
constraint when applicable. There is no combined "approved" verdict.

## Record what actually happened

```bash
moviestar review record --report REPORT.json --dimension visual --status passed \
  --actor agent:session --method 'Inspected rendered screenshots' \
  --at 0.5 --at 3.0 --evidence frame-0001.jpg --evidence frame-0002.jpg \
  --limitations 'Only these stills; no motion or listening review'

moviestar review record --report REPORT.json --dimension listening --status unavailable \
  --actor agent:session --method 'Current tool has no listening capability' \
  --note 'Ask the human to listen to this clip before sharing'

moviestar review record --report REPORT.json --dimension listening --status passed \
  --actor human:editor --method 'Listened to the complete rendered clip' --full \
  --supersedes entry_ID --note 'Human reported that the audio is acceptable'

moviestar review show --report REPORT.json
```

Replace `entry_ID` with the unavailable entry's actual ID. Only record that
human pass after receiving the human's judgment. Actor names, methods, notes,
and judgments are **caller-reported assertions**, not authenticated identities
or independently verified perception. These commands do not watch or listen,
detect source mismatches, call a model, upload media, or repair an edit.

Choose one coverage form for `passed` or `rejected` entries:

- `--full`: the reviewer reports covering the complete artifact duration.
- `--from X --to Y`: a half-open playback range `[X,Y)` on the artifact clock.
- Repeated `--at X`: sampled instants, supported for technical, source-alignment,
  and visual evidence. Still instants cannot establish motion or listening.

**Times always start at zero in the output file.** A watch clip covering project
seconds 12–20 uses review times 0–8, not 12–20. A sample cannot equal the artifact
end. The report retains the original watch range as a renderer fact when present.

`unknown` and `unavailable` require a note explaining the limitation and next
step, and may omit coverage. `--limitations` records method/perception limits;
the report always preserves concrete coverage separately. Optional `--evidence`
files are pinned by SHA-256. A changed or missing proof file cannot continue
supporting a pass. `record --dry-run` validates without changing the report,
creating a lock, or allocating a persisted entry ID.

## Coverage and decisions

`review show` reports each dimension independently. Adjacent/overlapping passed
ranges merge; gaps remain visible in `coverage.unreviewed_ranges`. Samples
remain point evidence and never fill a playback gap. A sampled pass is `partial`,
not a full-timeline pass. A dimension becomes `passed` only with continuous
passed coverage over the entire artifact and no active rejected, unknown,
unavailable, or stale-proof entries in that dimension.

An active rejection with intact evidence remains `rejected` even after a full
pass is added or another entry's proof becomes stale. Stale-proof flags remain
visible; resolving the rejection alone does not resolve that other stale proof.
A rejection whose own attached proof is stale contributes `unknown` instead.
To replace a judgment explicitly, use `--supersedes ID` in the same dimension.
Earlier entries stay in history, marked superseded in the view. Unknown or
unavailable entries likewise remain unresolved until explicitly superseded.
Changing an edit requires a new render/report; supersession is for re-review of
the same artifact and project state. It is not a way to transfer approval.

`next_steps` identifies remaining review work. If the agent cannot assess a
dimension, record that limitation and ask the human to inspect/listen to the
specific output ranges. A technical pass or a good opening frame must not be
presented as listening, motion, or editorial approval.

## Staleness and preservation

The artifact is identified by a streaming SHA-256 digest. Project invalidation
conservatively covers project/spec content (excluding undo history), transcript
and managed-font contents, and referenced local files' size, modification/change
times, and inode. Source media is stat-checked rather than fully rehashed;
this is local invalidation, not complete or adversarial asset provenance.
Unrelated changes to captured project/font state can therefore require re-review.

`show` recomputes currency without rewriting history. Changed or missing
artifact/project inputs yield `currency.current: false` and stale dimension
summaries. The original judgments retain their historical status. Recording new
entries against a stale report is refused. A project change detected during
rendering produces a report already marked stale; it cannot approve the changed
revision. There is no automatic carryover to a replacement export.

Reports bind to absolute local artifact/workspace paths. Moving or deleting
those paths makes them unavailable for a current-project assessment. Keep
artifacts and proof files if the evidence must remain usable. Separate output
paths preserve old renders; explicit `--out` retains normal overwrite semantics,
which makes an old report stale if the bytes change.

Writes are atomic and reject simultaneous writers. If an interrupted process
leaves a `.json.lock`, confirm no writer is running before removing that lock.
Reports are local editable JSON, not a signed audit log. They record evidence
honestly supplied by the caller and expose its limits; they do not enforce a
delivery gate or implement structural editing warnings.
