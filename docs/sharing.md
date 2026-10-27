# Share and download finished videos

Local editing needs no account. Hosted upload and publication require a connected
installation and current account-wide terms/age consent; see [account setup](accounts.md).
These commands work when their service has enabled the corresponding feature.
A paused service returns a structured error; CLI availability alone does not
mean public publication is enabled.

## Upload privately, then share

```sh
moviestar upload final.mp4 --dry-run
moviestar upload final.mp4 --idempotency-key upload-cut-1
moviestar share VIDEO_ID --dry-run
moviestar share VIDEO_ID --idempotency-key share-cut-1
```

`VIDEO_ID` is the UUID returned by upload. Upload stays private; sharing creates
an unlisted watch-and-download link for an existing, ready video without using
another storage slot. Free upload limits are three stored videos, 1 GB total,
and 500 MB per video. Sharing does not change storage usage.

You can combine the two operations:

```sh
moviestar share final.mp4 --dry-run
moviestar share final.mp4 --idempotency-key handoff-cut-1
```

Live stdout is one JSON result with `video_id`, `link_id`, `url`, quota,
`upload_performed`, and `email_sent: false`. `quota_scope: "link_publication"`
means its before/after snapshot covers link creation, after any upload has
already consumed storage; `quota.after` is the account's final usage. Uploading
a local file can use a slot even though link publication itself changes no
quota. Anyone with the unlisted link can
watch, download, and forward it without signing in. Ask before uploading or
publishing a user's private video when that action has not been authorized.

Email delivery is a later feature. Live `--to EMAIL` returns `not_available`
before uploading or publishing; omit it to create a link and hand it to the
user. `--dry-run --to EMAIL` can preview the requested address, but never sends.
Do not describe the link as restricted to that address.

## Recover an interrupted handoff

The CLI prints a retry key to stderr before any network request. Preserve it
and retry the **same file or video** with `--idempotency-key KEY`. A file/key
retry recovers the existing upload and link rather than consuming another
video slot. Never reuse the key for different content.

If upload succeeds but publication is rejected or cannot be confirmed, the
error includes `video_id`. An explicit service rejection is reported as
`publication_outcome: "rejected"` and `link_created: false` for this request;
previously published links are unaffected.

A lost response, generic server failure, or invalid success response has
`publication_outcome: "unknown"` and `link_created: null`. The service may
already have committed a public link. Do not assume the video remains private,
and do not switch to a new key. Reconcile by retrying just publication with
the **same video and original key**:

```sh
moviestar share VIDEO_ID --idempotency-key ORIGINAL_KEY
```

Success returns `publication_outcome: "shared"` or `"already_shared"` with the
active link. `link_created: false` on `already_shared` means the original link
was recovered, not that the video is private. A failure before attempting
publication reports `publication_outcome: "not_attempted"`.

Validation failure does not attempt new publication. A temporarily unavailable
validation step can be resumed with `moviestar upload FILE --resume RESERVATION_ID`
using the returned reservation ID. Once ready, share that video ID. No terms
are accepted or installation grants approved implicitly by these commands.

## List and revoke links

```sh
moviestar shares
moviestar shares --video VIDEO_ID
moviestar shares --video VIDEO_ID --cursor LINK_ID
moviestar unshare LINK_ID --dry-run
moviestar unshare LINK_ID
```

Listings return up to 50 links, including revoked history with `url: null`.
Continue with the returned `next_cursor`, retaining the same `--video` scope
(or omitting it for account-wide listing). Use `link_id` from share or `id`
from a listed link for revocation, not the video ID or URL slug.

Revocation is retryable and keeps the video, other links, and storage usage.
A revoked request key cannot reopen its link; deliberately choose a new key
if the owner wants a new link. A previously issued media URL can remain usable
for up to 60 seconds; transfers already started can finish, and downloaded
copies cannot be recalled. Live video deletion (`shares delete`) is not
implemented; its dry-run does not remove anything or release quota.

## Download for the next edit

```sh
moviestar download https://trymoviestar.com/v/SLUG --dry-run
moviestar download https://trymoviestar.com/v/SLUG --out incoming/cut.mp4
moviestar probe incoming/cut.mp4
```

Use the complete watch URL returned by sharing, including its opaque token.
Downloads need no recipient account and do not read a device credential.
Without `--out`, the default is `SLUG.mp4` in the current directory; the remote
filename never chooses a local path. Existing files and symlinks are never
overwritten, including if a destination appears during transfer. Failed or
interrupted transfers remove the temporary file and do not publish a partial
MP4. Transfers stream with a 1 GB safety limit and require the declared byte
length and MP4 signature. The download is a finished video, not source files,
project state, or edit history.

The download first checks the link with Moviestar, then follows only a validated
private-storage redirect. It sends no account bearer, cookies, or referrer.
If a link is revoked or a short-lived transfer URL expires, retry the original
watch URL rather than reusing a signed storage URL. Never put signed URLs,
account credentials, or full capability links in unrelated logs or analytics.

## Preview and local service targets

Owner commands use the same service origin as the connected installation.
`MOVIESTAR_ACCOUNT_INSTALLATION_URL` can explicitly select a Preview or local
service's `/api/v1/account/installation` endpoint. Outside loopback it must use
HTTPS. Connect/approve that installation on the intended service first.
The canonical watch site can differ from the API hostname, for example when a
deployment-specific API URL returns watch links on a stable branch alias.
Configure the exact watch origin **before** sharing in that setup:

```sh
export MOVIESTAR_ACCOUNT_INSTALLATION_URL=https://deployment.example/api/v1/account/installation
export MOVIESTAR_WATCH_ORIGIN=https://branch.example
```

`MOVIESTAR_WATCH_ORIGIN` must contain only an origin: no credentials, path
(except a trailing `/`), query, or fragment. It must use HTTPS outside loopback.
Invalid configuration stops live sharing/listing and downloads before network
work; sharing/listing check it before credential lookup or upload as well.
Owner revocation remains available even if the watch origin is misconfigured. The explicit watch origin is trusted consistently for
share responses, link listings, and anonymous downloads. Owner credentials
still go only to the configured API origin, never to the watch site.

Without that setting, owner responses must use their API origin. Downloads
accept production watch links, the configured API origin, and any explicit
watch origin. They do not broadly trust `vercel.app` or other preview domains.
Local HTTP storage redirects remain restricted to the watch link's same
loopback origin for development rehearsal.
