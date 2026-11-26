# Inspect one frame of an exact rendered output — design mock

**Not a shipped command or live inspection service.** This isolated Click app
proposes the CLI/API/MCP surface before wiring it into the distributed package.
Every UUID/hash/job/runtime and image is synthetic. The small JPEG is generated
locally once under ignored `.context/cloud-result-frame-mock/`; no generated media
is committed, uploaded or extracted from a video. A locally generated JPEG says
“MOCK ONLY”; `--out` copies that fixture, never a video frame.

The proposed journey is: choose an existing verified rendered artifact, request
one frame in its result timeline, then reconnect/reopen the same job and see or
save its image. It never selects the latest edit, rerenders, imports an asset or
changes the spec. A preview and final remain distinct artifacts.

## Run the design walkthrough

From this public repository, using an environment with Click and FFmpeg (including drawtext):

```sh
PY=app/.venv/bin/python
MOCK=experiments/cloud_result_frame_surface/surface.py
P=00000000-0000-4000-8000-000000000001
A=00000000-0000-4000-8000-000000000002
J=00000000-0000-4000-8000-000000000005

"$PY" "$MOCK" --help
"$PY" "$MOCK" --surface cli screenshot "$P" "$A" --at-ms 10000 --idempotency-key result-frame-10s
"$PY" "$MOCK" --surface api screenshot-show "$P" "$A" "$J"
"$PY" "$MOCK" --surface mcp screenshot-show "$P" "$A" "$J"
"$PY" "$MOCK" --scenario replay screenshot "$P" "$A" --at-ms 10000 --idempotency-key result-frame-10s
"$PY" "$MOCK" screenshot-show "$P" "$A" "$J" --out /tmp/moviestar-MOCK-frame.jpg
```

Expected: the same canned IDs/resource across transports; queued submission,
succeeded read, retained replay of the original job, and a labeled synthetic JPEG.
The outer `mock`, `fixture_limit`, `transport`, `effects` and `file_saved` fields
explain the experiment; `response` contains the proposed canonical service body.
These scripts have **no durable state machine**. Scenarios represent server/client
outcomes; they do not test real idempotency, authority, persistence or usage.

Try `--scenario lost-response`, `conflict`, `missing`, `foreign`, `revoked`,
`disabled`, `pending` or `failed`. Lost response reports unknown effects and keeps
the exact recovery instructions. Missing/foreign/revoked share the same 404.
Disabled admission leaves saved-job reads available. Existing files are protected.

The proposed production range is integer milliseconds `0 <= at_ms < duration`.
The small fixture models only 0, 10000, 10017 and 29999ms. Its explicitly canned
presentation times demonstrate requested versus decoded time; **no frame rate
is used to infer actual PTS**. Other in-range times are a mock coverage gap,
not a proposed production restriction. Each modeled sample has a distinct job UUID; reopening that UUID retains its
original requested and decoded time. The primary reconnect example is 10000ms.

## Proposed transports

| Surface | Submit | Reopen |
| --- | --- | --- |
| CLI | `cloud artifacts screenshot P A --at-ms 10000 --idempotency-key K` | `cloud artifacts screenshot-show P A J [--out NEW.jpg]` |
| API | `POST /projects/P/artifacts/A/screenshots`, `{at_ms:10000}`, retained `Idempotency-Key` | `GET /projects/P/artifacts/A/screenshots/J`; `GET .../J/image` for JPEG |
| MCP | `moviestar_screenshot_render_artifact` | `moviestar_get_render_artifact_screenshot`, proposed native JPEG plus canonical metadata |

API paths are relative to the existing account Cloud prefix. The image route,
native MCP image and SHA-verified CLI download are **design only**; the mock's
MCP delivery descriptor does not establish agent perception or human display.
Original-source screenshot tools retain their existing meanings.

```sh
app/.venv/bin/python -m unittest discover -s experiments/cloud_result_frame_surface
```
