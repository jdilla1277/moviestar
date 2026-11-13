# Inspect a Cloud asset

Use `moviestar cloud probe ASSET_ID` to inspect an existing, ready asset with
Cloud compute. Connect your installation with `moviestar account connect` first.
The asset must belong to that account. Remote probing does not need local media,
FFmpeg, or a local editing workspace.

```bash
moviestar cloud probe ASSET_ID
moviestar cloud probe ASSET_ID --no-wait
moviestar cloud probe ASSET_ID --job-id JOB_ID
```

The default command waits up to 300 seconds. `--timeout SECONDS` changes the
polling limit; it does not cancel the Cloud job. The JSON result contains the
asset ID, job ID, state, and media metadata when successful: duration, file size,
format, bitrate, video codec/dimensions/frame rate/pixel format, and audio
codec/sample rate/channels. This describes audio streams; it does not measure
loudness or generate a transcript.

Each new request can incur Cloud execution costs. The command saves its retry
key before scheduling. After a lost reply or interruption, rerun the same asset
command to recover that request. Preserve an explicitly supplied
`--idempotency-key KEY` and reuse it for retries. After a completed request,
another invocation starts a new probe. Use `--job-id JOB_ID` to read an existing
result without scheduling work, including after reconnecting.

Queued/running results exit successfully and include a recovery command. Failed
or cancelled jobs exit unsuccessfully with the durable job and failure code.
Authentication, access, invalid response, and unavailable-service errors preserve
retry information. Retry records are private files under `cloud-probe-requests`
beside the account profile, scoped to the service and installation credential.
If you reconnect with a different credential, use the original key or job ID to
recover an earlier request.

REST clients can schedule with `POST /api/v1/assets/ASSET_ID/probe`, an empty JSON
body, a personal API key, and `Idempotency-Key`. Reopen with
`GET /api/v1/assets/ASSET_ID/probes/JOB_ID`. Hosted MCP provides
`moviestar_probe_asset` and `moviestar_get_asset_probe`; agents generate and retain
the request key, then poll the same job. OAuth and personal API keys use the same
account access checks. Account activity records the job and each execution
attempt. Unavailable actual cost data means unknown, not zero.

Local `moviestar probe FILE` continues to read the local file without an account.
Cloud metadata omits local paths and diagnostic ffprobe commands. Availability
depends on the connected service deployment; an unavailable service returns an
explicit error and retains your request information.
