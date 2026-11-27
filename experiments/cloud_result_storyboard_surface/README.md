# Rendered-output storyboard — design mock

This proposes one contact sheet from an **existing verified rendered video**.
It is outside the distributed package. It makes no network calls, creates no
jobs and does not decode an input video. All identities, hashes, timestamps and
pixels are synthetic; the generated sheet says MOCK ONLY in every cell.

```sh
PY=app/.venv/bin/python
M=experiments/cloud_result_storyboard_surface/surface.py
P=00000000-0000-4000-8000-000000000001
A=00000000-0000-4000-8000-000000000002
J=00000000-0000-4000-8000-000000000005
"$PY" "$M" storyboard "$P" "$A" --idempotency-key retained-sheet
"$PY" "$M" --surface api storyboard-show "$P" "$A" "$J"
"$PY" "$M" --surface mcp storyboard-show "$P" "$A" "$J"
"$PY" "$M" storyboard-show "$P" "$A" "$J" --out /tmp/moviestar-MOCK-sheet.jpg
"$PY" "$M" --scenario lost-response storyboard "$P" "$A" --idempotency-key retained-sheet
"$PY" "$M" --scenario paused-replay storyboard "$P" "$A" --idempotency-key retained-sheet
```

Expected: queued request, succeeded canned read, identical canonical job across
CLI/API/MCP examples, native MCP JPEG payload and a new labeled mock file.
Retained replay recovers the same job/plan, even with new admission paused.
An uncertain reply has unknown effects. Neither canned replay nor file copying
establishes persistence, access checks, extraction, actual usage or native client
perception. Existing files are preserved. Failed jobs are terminal.

Proposed production names:

| Surface | Request | Reopen |
|---|---|---|
| Remote CLI | `cloud artifacts storyboard P A [--idempotency-key K]` | `cloud artifacts storyboard-show P A J [--out NEW.jpg]` |
| API under account Cloud prefix | `POST /projects/P/artifacts/A/storyboards`, body `{}`, retained header | `GET /projects/P/artifacts/A/storyboards/J`, image at `.../J/image` |
| MCP | `moviestar_storyboard_render_artifact(project_id,artifact_id,idempotency_key)` | `moviestar_get_render_artifact_storyboard(project_id,artifact_id,job_id)` |

The mock requires an explicit key; the production CLI would generate/save one
before submission when omitted. API/MCP retain an explicit key. The first slice
uses whole-video automatic sampling only; range/interval/column options are not
admitted. No latest-output selection, automatic render or input-asset import.
Original-asset storyboard commands keep their existing meanings.

The immutable job identifies project/revision/render-job/artifact/hash and
`clock:rendered_result`. Its saved sampling plan includes requested times, grid,
range and interval. Each successful cell reports requested and actual decoded
result milliseconds; labels show decoded time, with `frame_at_or_before`
selection. VFR holds can repeat the same frame/time in different sample cells;
those are not dropped samples. Preview evidence applies only to that artifact.
The 30-second fixture has 15 requested samples at 0,2,…28 seconds in a 5×3 grid.
Its second cell deliberately requests 2000ms but reports/labels 1967ms, exposing
requested-versus-decoded time differences. This is one example, not an implemented
general-purpose planner.

Proposed bounds reuse a 64 MiB personal-render input and storyboard's 60-cell,
384px tile, 2 MiB JPEG, 16-megapixel/16,384px-edge limits. Runtime/processing bounds,
actual decoded-time labels and installed-worker support require qualification
before activation. An incomplete or oversized sheet must fail, not silently
return fewer samples. Sampling an unrendered edit, windows and playback are later.

Tests: `app/.venv/bin/python -m pytest experiments/cloud_result_storyboard_surface/test_surface.py -q`.
FFmpeg with drawtext is used solely to generate ignored synthetic test media.
No generated JPEG, token, user media or dependency is committed. This experiment
is not a released CLI feature or a live service.
