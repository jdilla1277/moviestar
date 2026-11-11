# Create a private account asset

Upload a video, audio file, image, caption file or transcript to your account
without creating a project or share link. The original bytes are preserved.

## Remote CLI

Use a CLI build containing `cloud assets create` and the existing human-approved
account connection. This command needs the base package only: no FFmpeg,
Whisper, transcription model or local editing workspace.

```sh
moviestar account connect you@example.com
# Open the approval email, then confirm the connection:
moviestar account status
moviestar cloud assets create ./photo.png
```

If already connected, skip the connection steps. The upload uses the same
approved installation and service origin; you do not need to create an API key.
See [account connection](accounts.md) for service configuration and revocation.

The CLI reserves an upload, transfers the original privately, and asks the
service to validate it. Success returns one JSON object on stdout with
`status: "ready"`, `asset.id`, `asset.kind`, `asset.format`, and `upload_id`.
The asset is account-owned. Creating it does not attach it to a project, expose
it publicly, or modify a local workspace. The older `moviestar upload` command
remains the separate finished-MP4 hosting workflow.

### Recover an interrupted upload

A generated idempotency key appears on stderr before the first request. Save
it, or supply your own from the start:

```sh
moviestar cloud assets create ./photo.png --idempotency-key photo-original-1
```

Retry the **same unchanged original and key** after a lost reply or transfer
failure. The service reuses the same asset and upload, without charging another
slot. A reused key is scoped to the account, not just one CLI installation.
Changing the declaration with that key returns `idempotency_conflict`; the key
does not compare file hashes or authorize replacing an original.

If the transfer succeeded but completion/validation failed, use the returned
`upload_id` (not `asset.id`) to retry without transferring again:

```sh
moviestar cloud assets create ./photo.png --resume UPLOAD_ID
```

Commands do not automatically retry writes. Errors return structured JSON with
a stable `status`, recovery `hint`, and the retry key/upload ID when known.
Unsupported formats, unavailable service operations, quota limits, invalid
content and revoked credentials fail without claiming the asset is ready.
Keep retry identifiers when an outcome is uncertain. A genuinely invalid file
must be corrected and submitted with a new key.

## Starter formats and limits

| Kind | Formats |
| --- | --- |
| Video | MP4/MOV with H.264 or HEVC; WebM with VP8 or VP9 |
| Audio | MP3, WAV, M4A, FLAC |
| Image | JPEG (`.jpg`/`.jpeg`), PNG, WebP, GIF including animated GIF |
| Caption/transcript | UTF-8 SRT, WebVTT (`.vtt`), plain text (`.txt`) |

Extensions declare the format; the server checks the actual content. Renaming
an unsupported file does not convert it. Files must be nonempty. Format ceilings
are 1 GB for video/audio, 32 MiB for images and 256 KiB for captions/transcripts;
account limits may be lower. Media admission is bounded; acceptance is not a
full inspection or an editing compatibility guarantee.

All kinds consume one slot in the same account allowance. Free allows 100
assets, 1 GB total and 500 MB per file; Plus allows 1,000 assets, 25 GB total and
1 GB per file (GB/MB decimal). Existing hosted media also consumes that
allowance. Pending uploads reserve capacity. The service checks live capacity
on reservation/completion; the CLI does not bypass it.

## HTTP API

Developers can create a personal API key at `/account/api-keys` on the website
for the target service. Use it as `Authorization: Bearer ...` on API requests:

1. `POST /api/v1/assets/uploads` with JSON `filename`, `size_bytes` and an
   `Idempotency-Key` header. Save `asset.id`, `upload_id`, and the key.
2. If `upload` is non-null, PUT the unchanged original to `upload.url`, using
   exactly `upload.headers`, including `If-None-Match: *`. Never send your API
   credential to storage. A 412 on a replay means an original is already there;
   completion still checks it against the reservation.
3. `POST /api/v1/assets/uploads/UPLOAD_ID/complete`. Only `asset.state: "ready"`
   means validation succeeded. Preserve retry identifiers after a 503.
4. `GET /api/v1/assets/ASSET_ID` reads metadata;
   `GET /api/v1/assets/ASSET_ID/download` returns the ready original.

The CLI uses these same endpoints with its approved installation bearer and
`x-moviestar-client: moviestar-cli-v1`. Every operation rechecks live credentials,
ownership and account consent. Browser cookies are not authentication for these
endpoints. Create keys and connect the CLI on the same service you are testing;
Preview and Production credentials are separate.
