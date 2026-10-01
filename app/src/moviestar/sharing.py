"""Truthful command previews for the planned hosted-sharing workflow."""

from __future__ import annotations

import json
from pathlib import Path

import click

from moviestar.account import _EMAIL_RE, _read_state


FREE_LIMITS = {
    "stored_videos": 3,
    "total_bytes": 1_000_000_000,
    "per_video_bytes": 500_000_000,
}


def _emit(payload: dict) -> None:
    click.echo(json.dumps(payload, indent=2))


def _owner_from_local_state() -> dict:
    connection = _read_state().get("connection")
    if isinstance(connection, dict):
        handle = connection.get("handle")
        email = connection.get("email")
        if isinstance(handle, str) and isinstance(email, str):
            return {
                "connection": "local_record",
                "handle": handle,
                "email": email,
            }
    return {"connection": "not_connected", "handle": None, "email": None}


def _unavailable(command: str, **fields: object) -> None:
    _emit(
        {
            "command": command,
            "status": "not_available",
            **fields,
            "mocked_surface_note": "This command has no hosting backend yet.",
            "hint": (
                "Hosted sharing is not enabled yet. The --help text shows "
                "the planned workflow."
            ),
        }
    )
    raise click.exceptions.Exit(1)


@click.command("share")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--to", "recipient", metavar="EMAIL", help="Email the link to this exact address.")
@click.option(
    "--dry-run", is_flag=True,
    help="Preview the handoff without uploading or sending email.",
)
def share(file: Path, recipient: str | None, dry_run: bool) -> None:
    """Host a finished MP4 and optionally email its watch-and-download link.

    An unlisted link can be forwarded by anyone who receives it. Downloading
    gives the recipient a finished MP4, not source files or edit history.
    --to requires an exact email address; ask the human when you only know a
    person's name. Without --to, create a link without sending email.

    Example: moviestar share final.mp4 --to alex@example.com --dry-run

    This first surface preview does not upload, create a link, or send email.
    The planned live result includes video_id, link_id, url, delivery, and
    post-upload quota usage. Live quota usage is unavailable in this preview.
    """
    file_details = {"path": str(file.resolve()), "size_bytes": file.stat().st_size}
    delivery = {"to": recipient, "would_send_email": recipient is not None}
    no_mutation = {"upload_performed": False, "email_sent": False}

    if file.suffix.lower() != ".mp4":
        _emit(
            {
                "command": "share",
                "status": "error",
                "file": file_details,
                "delivery": delivery,
                **no_mutation,
                "error": "Hosted sharing accepts a finished MP4 file.",
                "hint": "Export a finished .mp4, then retry 'moviestar share FILE --dry-run'.",
            }
        )
        raise click.exceptions.Exit(1)

    if recipient is not None and not _EMAIL_RE.fullmatch(recipient):
        _emit(
            {
                "command": "share",
                "status": "error",
                "file": file_details,
                "delivery": delivery,
                **no_mutation,
                "error": "Recipient must be an email address.",
                "hint": "Ask the human for the recipient's exact email address, then retry.",
            }
        )
        raise click.exceptions.Exit(1)

    if not dry_run:
        _unavailable(
            "share",
            file=file_details,
            owner=_owner_from_local_state(),
            delivery=delivery,
            **no_mutation,
        )

    owner = _owner_from_local_state()
    _emit(
        {
            "command": "share",
            "status": "would_share",
            "file": file_details,
            "owner": owner,
            "access": {
                "mode": "unlisted",
                "forwardable": True,
                "downloadable": True,
            },
            "delivery": delivery,
            "quota": {
                "plan": "unknown",
                "current_usage": "unavailable",
                "projected_usage": "unavailable",
                "free_limits": FREE_LIMITS,
                "would_use_bytes": file_details["size_bytes"],
            },
            "file_validation": "extension_only; media verification follows in the live service",
            **no_mutation,
            "mocked_surface_note": "No file was uploaded, link created, or email sent.",
            "hint": (
                "Run 'moviestar account connect EMAIL' first; hosted sharing is not enabled yet."
                if owner["connection"] == "not_connected"
                else "Hosted sharing is not enabled yet. This is the planned handoff preview."
            ),
        }
    )


@click.group("shares", invoke_without_command=True)
@click.pass_context
def shares(ctx: click.Context) -> None:
    """List hosted videos, or delete a video and all its links.

    The planned list shows each video's ID, links, delivery recipients, size,
    and state. In this surface preview there is no hosted-video list yet.
    """
    if ctx.invoked_subcommand is None:
        _unavailable("shares")


@shares.command("delete")
@click.argument("video_id")
@click.option("--dry-run", is_flag=True, help="Preview deletion without removing the video.")
def delete_video(video_id: str, dry_run: bool) -> None:
    """Delete a hosted video, revoke all its links, and free its quota.

    This differs from ``unshare LINK_ID``, which only revokes one link. Copies
    already downloaded by recipients cannot be recalled.
    """
    if not dry_run:
        _unavailable("shares delete", video_id=video_id, video_deleted=False)
    _emit(
        {
            "command": "shares delete",
            "status": "would_delete_video",
            "video_id": video_id,
            "video_deleted": False,
            "all_links_revoked": False,
            "quota_released": False,
            "mocked_surface_note": "No hosted video or link was changed.",
            "hint": (
                "The live command will delete the asset and all its links; "
                "use 'unshare LINK_ID' to revoke one link."
            ),
        }
    )


@click.command("unshare")
@click.argument("link_id")
@click.option("--dry-run", is_flag=True, help="Preview revocation without changing the link.")
def unshare(link_id: str, dry_run: bool) -> None:
    """Revoke one hosted link without deleting its video.

    Other links to the same video keep working; revoking a link does not free
    storage. Use ``moviestar shares delete VIDEO_ID`` to remove the video and
    all its links. Already downloaded copies cannot be recalled.
    """
    if not dry_run:
        _unavailable("unshare", link_id=link_id, link_revoked=False)
    _emit(
        {
            "command": "unshare",
            "status": "would_revoke_link",
            "link_id": link_id,
            "link_revoked": False,
            "video_deleted": False,
            "quota_released": False,
            "mocked_surface_note": "No hosted link or video was changed.",
            "hint": (
                "The live command will revoke this link only; use "
                "'shares delete VIDEO_ID' to free storage."
            ),
        }
    )
