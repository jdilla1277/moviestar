# Sharing with Moviestar

You have a finished video called `final.mp4` on your computer. Ask your agent
to upload it to Moviestar and return a link. Send that link to your editor,
teammate, or client: they can open it in a browser, watch the video, and download
the MP4 without creating an account.

For example:

> Share `final.mp4` with Moviestar and give me the watch link.

Or, to send it directly:

> Share `final.mp4` with Moviestar and email the link to editor@example.com.

Use the recipient's actual address. Your agent should use the address you
provide rather than guess from a name.

## Connect your account once

Local editing needs no account. To upload and share videos, install Moviestar
v0.8.0 or newer and connect the installation to your account. You can ask:

> Help me connect this Moviestar installation to my account so I can share videos.

Your agent checks the connection and, if needed, starts setup with your email:

```sh
moviestar account status
moviestar account connect you@example.com
```

Open the Moviestar email yourself. Sign in or create your account, complete
the age and terms steps if prompted, and approve the installation in your
browser. Then tell your agent you're ready; it checks the connection again.
Keep sign-in links and verification codes with you.

You don't need to sign in again for each video. The connection works until
its approval expires or you revoke it.
See [account setup](accounts.md) if you need to reconnect or change accounts.
If the service has paused uploads or sharing, your agent should tell you which
step is blocked and what the error says to do next.

## Share a finished video

Choose the finished MP4 you want someone to see. When you ask your agent to
share it, Moviestar uploads the video and creates an unlisted watch-and-download
link. This does not modify your local video or editing project.

If you're running the command yourself:

```sh
moviestar share final.mp4 --idempotency-key final-handoff-1
```

Your agent gives you a URL such as `https://trymoviestar.com/v/SLUG`; the actual
link contains a unique token in place of `SLUG`. Open it to watch the video or
copy it into a message. The command also returns a `video_id` for the uploaded
MP4 and a `link_id` for this particular link. Keep both so you can delete the
upload or revoke the link later.

`final-handoff-1` is a name you choose for this request. If the connection drops,
retry with the same name and file so Moviestar can find the existing upload
and link.

**Anyone with the link can watch, download, and forward it.** Unlisted sharing
means someone can open it without signing in. Emailing the link to one person
does not stop them from forwarding it to someone else.

If you want the video stored privately first, say:

> Upload `final.mp4` to Moviestar, but keep it private. Don't create a share link yet.

```sh
moviestar upload final.mp4 --idempotency-key private-upload-1
```

You can share it later using the returned video ID:

```sh
moviestar share VIDEO_ID --idempotency-key publish-upload-1
```

Uploading stores one video in your account and counts its file size toward
your storage limit. Creating another link to that uploaded video stores no
second copy. To check whether another video will fit, ask:

> Check my Moviestar account's remaining video slots and storage.

Your agent reads them with `moviestar account status`.

## Email the link

To upload, share, and request an email in one step:

```sh
moviestar share final.mp4 --to editor@example.com --idempotency-key email-handoff-1
```

The email shows your Moviestar handle and a link to watch or download the video.
It does not subscribe the recipient to updates.

Already have a link? Ask your agent to email that existing link, or use its ID:

```sh
moviestar shares email LINK_ID --to editor@example.com --idempotency-key email-link-1
```

Your agent can check email history with `moviestar shares deliveries LINK_ID`.
“Accepted” means the email provider accepted the request; it does not confirm
arrival in the recipient's inbox.

## What the recipient gets

The recipient opens the link in their browser to watch or download the finished
video without signing in. The download contains the published MP4, rather than
your source footage, project, or edit history.

If they use Moviestar, they can ask their own agent:

> Download the video at this Moviestar link as `received.mp4` so we can edit it.

```sh
moviestar download https://trymoviestar.com/v/SLUG --out received.mp4
```

Replace the example URL with the complete watch link. Downloads preserve
existing files; choose a different output name if that file already exists.

## Stop sharing or remove the upload

To stop access through a particular link, ask:

> Revoke the link we created for `final.mp4`. Keep the uploaded video.

Your agent can find and revoke it:

```sh
moviestar shares
moviestar unshare LINK_ID
```

Revoking one link stops new access through that link. The video stays in your
account, still uses storage, and remains available through any other active
links. A playback or download URL issued just before revocation can work for
up to 60 seconds, and a download already started can finish. Anyone who already
downloaded the MP4 keeps their copy.

To remove the hosted video and disable all its links, ask your agent to delete
the upload, or run:

```sh
moviestar delete VIDEO_ID
```

Deletion permanently removes the copy stored on Moviestar. Your local files and
project stay intact. Its video slot and storage become available after deletion
finishes. If the command returns `deletion_pending`, retry the same video ID
to finish deleting it.

## If the upload share or email command fails

Tell your agent which command failed and give it the result. Keep the original
retry key and any video or link IDs returned. If the command timed out, the
upload, link, or email may already exist even though you didn't get a reply.

- **Upload or link creation interrupted:** follow the returned recovery hint
  with the same file or video and original key. Starting over with a new key
  can create another upload or link.
- **Link created, email failed:** keep the link and retry only the email step.
  There is no need to upload again.
- **Email outcome pending or unknown:** check delivery history and follow the
  retry hint. The email may already have been sent; a new key requests another
  message.

For a preview before ordinary upload, share, or email commands, add `--dry-run`.
These previews do not upload, publish, send mail, or verify live account capacity.

See the [sharing command guide](sharing.md) for detailed recovery, replacing a
cut behind existing links, and downloading videos accepted into your account.
