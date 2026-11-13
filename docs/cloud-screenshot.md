# Inspect a frame of a Cloud asset

The screenshot service is an opt-in Preview capability. Use an existing ready
video asset owned by the connected account. Finished video outputs can be
inspected when they are available as ready account assets. Unrendered edits are
not supported. Ordinary `moviestar screenshot --file FILE --at TIMESTAMP`
continues to work locally without an account.

Connect your installation to the service using the existing account flow, then:

```sh
moviestar cloud screenshot ASSET_ID --at 0:20:00.000 --idempotency-key retained-frame-1200
moviestar cloud screenshot-show ASSET_ID JOB_ID
moviestar cloud screenshot-show ASSET_ID JOB_ID --out frame-1200.jpg
```

The first command returns the durable job ID. Poll `screenshot-show` until
`job.state` is `succeeded`; then `--out` retrieves its private JPEG. Existing
files are never overwritten. A failed or cancelled job includes `failure_code`.
Timestamp inputs have millisecond precision, from zero to 24 hours, and must
identify a frame within the video. Frames are bounded to 4096 pixels on each
axis and 1 MiB. Oversized or unsupported inputs can fail with a resource limit.

Keep the same asset, timestamp and key after an uncertain reply. If the key is
omitted, the CLI saves one before sending the request and retains it across
invocations, including successful ones. Pass a new key only to deliberately
request a fresh execution. Reopen a completed job after reconnecting with its
asset and job IDs. Image reads recheck account access; job IDs are not sharing
links. Job and attempt execution appear in Activity. Missing provider cost
receipts remain unknown.

Integrations use `POST /api/v1/assets/ASSET_ID/screenshot` with JSON
`{"at_ms":1200000}` and an `Idempotency-Key` header. Read status at
`GET /api/v1/assets/ASSET_ID/screenshots/JOB_ID`, and the JPEG bytes at the same
path plus `/image`. Personal API keys use a Bearer header; approved CLI
installation credentials additionally require `x-moviestar-client: moviestar-cli-v1`.
Keep credentials out of chat. OAuth clients use the hosted MCP tools directly:
`moviestar_screenshot_asset` and `moviestar_get_asset_screenshot`. The latter
returns a native MCP image content block once the job succeeds.
