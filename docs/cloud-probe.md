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
codec/sample rate/channels. The plain probe describes audio streams. Add `--loudness` for audio-level
measurements; transcript generation is a separate operation.

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

## Measure audio loudness

```bash
moviestar cloud probe ASSET_ID --loudness
moviestar cloud probe ASSET_ID --loudness --from 00:10 --to 00:20
```

The range uses the original asset's clock. `--from` defaults to zero and `--to`
to the media duration; ranges require `--loudness`. The result adds `loudness`
with integrated LUFS, true peak dBTP, loudness range LU, threshold LUFS, target
offset LU, and the measured range. The normalization-target fields describe the
local measurement algorithm's reference settings; this operation measures the
existing audio and does not normalize or replace it.

A `null` metric means it could not be measured as a finite value, as can happen
with silence or very short windows. It does not mean zero loudness. Assets
without audio fail with `no_audio`; ranges beyond the actual duration fail with
`invalid_range`. Cloud execution is bounded; a job that exceeds its execution
budget fails rather than returning incomplete measurements as success.

Retry the same asset, loudness flag and range to recover an interrupted request.
Plain probes and different loudness ranges have separate automatic recovery
records. An explicit key belongs to one asset and one set of measurement options;
changing them returns `idempotency_conflict`. Use `--job-id JOB_ID` without
analysis flags to reopen a stored result without scheduling another execution.

REST uses the existing probe endpoint with, for example,
`{"loudness":true,"from_seconds":10,"to_seconds":20}`. Hosted MCP passes those
same optional fields to `moviestar_probe_asset` and reopens through
`moviestar_get_asset_probe`. Requests stay unavailable until the service has a
verified loudness-capable worker. Jobs and attempts retain normal account
Activity tracking; actual provider cost mapping remains a separate integration.
