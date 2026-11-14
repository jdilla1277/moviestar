# Private Cloud watch excerpts

Use an existing ready Cloud video asset on that file's own timeline. This also
works for an already rendered MP4 stored as a ready asset. It does not render an
unrendered composition. Local `moviestar watch` remains account-free.

```sh
moviestar cloud watch ASSET_ID --from 1.250 --to 6.250 --idempotency-key review-motion-1
moviestar cloud watch-show ASSET_ID JOB_ID
moviestar cloud watch-show ASSET_ID JOB_ID --out excerpt.mp4
```

Connect the CLI to the same service/account first using `moviestar account
connect`. The first command returns a durable `job.id` immediately. Poll with
`watch-show` until `state` is `succeeded`, then save the MP4. An existing output
file is never overwritten. Disconnecting and reconnecting does not lose the job.
Reopening with the same IDs does not run another extraction.

Seconds and timecodes accept millisecond precision. Both endpoints are required;
use an increasing range of at most 30 seconds within the asset. Cloud uses the
local precise review extraction engine: H.264 video inside an orientation-aware
720p box with available audio encoded to AAC. For a local comparison on the
raw file clock, use `watch --source SOURCE_ID --review`; omitting `--source`
can apply an authored project audio mix instead. It intentionally differs from
local watch's default keyframe-aligned stream copy. Source frame granularity
and AAC padding can affect measured duration; the result reports requested and
effective ranges, actual media duration, and first video/audio offsets relative
to the excerpt. These are timing measurements, not semantic analysis.

Inputs are limited to 1 GiB, decoded frames to 16,777,216 pixels, extraction to
90 seconds, and output to 2 MiB. A source can therefore pass admission but fail
execution if its output exceeds the resource budget. Silent video stays silent;
audio-only assets are unsupported. Worker capability and service enablement
are required; unavailable workers produce a structured availability error.

The CLI saves generated retry keys locally before submitting. Retry the identical
command after an interrupted response. Explicitly reusing a key with a different
asset or range returns `idempotency_conflict`. Saved results and keys persist;
a new key explicitly requests another extraction.

Media access requires current account authorization on every download. There is
no public hosting URL. Excerpt media is available for seven days from completion,
independently of the durable result. After that, `artifact_unavailable` means the
metadata/result remains but the media cannot be retrieved. Expired credentials
can be replaced and the same result reopened while its media remains; doing so
does not reprocess it. Account deletion removes retained media and results.

## API

Use a personal API key from the service's account settings in your own secret
storage. Never paste it into a chat. Installation credentials also work with the
CLI client marker.

```sh
curl --fail-with-body "$SERVICE/api/v1/assets/$ASSET_ID/watch" \
  -H "Authorization: Bearer $MOVIESTAR_API_KEY" \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: review-motion-1' \
  --data '{"from_ms":1250,"to_ms":6250}'
curl --fail-with-body "$SERVICE/api/v1/assets/$ASSET_ID/watches/$JOB_ID" \
  -H "Authorization: Bearer $MOVIESTAR_API_KEY"
curl --fail-with-body "$SERVICE/api/v1/assets/$ASSET_ID/watches/$JOB_ID/media" \
  -H "Authorization: Bearer $MOVIESTAR_API_KEY" --output excerpt.mp4
```

Submission returns 202 while pending and 200 for a retained terminal result.
Status/download authorization is independent of the submitting connection.
Jobs and execution attempts appear on the service's `/activity` page; unknown
provider cost remains unknown.

## Hosted MCP

Ask: “Request a watch excerpt of asset ASSET_ID from 1250 to 6250 milliseconds.
Generate and retain a retry key, poll the job, and give me its saved job ID and
private retrieval handoff. Do not infer video or speech content from metadata.”

The authenticated tools are `moviestar_watch_asset` and
`moviestar_get_asset_watch`. A job created through the API or CLI can be reopened
through MCP with the same asset and job IDs. A completed MCP result supplies the
CLI download command. This MCP surface does not return native video/audio blocks;
actual target-client video/audio consumption is not verified. Use the CLI handoff
for a capable agent or a human player, and report those forms of inspection
separately. A successful tool call or MP4 download does not prove that the
requesting agent watched or listened to the excerpt.
