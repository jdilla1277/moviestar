"""Anonymous, bounded, non-overwriting download of hosted finished MP4s."""
from __future__ import annotations

import json
import os
import re
import tempfile
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, build_opener

import click

from moviestar.account import _NoRedirectHandler
from moviestar.upload import _safe_signed_url, _upload_url

MAX_DOWNLOAD_BYTES = 1_000_000_000


def watch_link(url: object, *, allowed_origin: str | None = None) -> tuple[str, str] | None:
    if not isinstance(url, str):
        return None
    try:
        parsed = urlsplit(url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.hostname:
            return None
        origin = f"{parsed.scheme}://{parsed.netloc}"
        configured = allowed_origin
        if configured is None:
            endpoint = _upload_url()
            configured = endpoint.removesuffix("/api/v1/videos/uploads") if endpoint else None
        if origin not in {"https://trymoviestar.com", configured}:
            return None
        slug = re.fullmatch(r"/v/([A-Za-z0-9_-]{43})", parsed.path)
        return (origin, slug.group(1)) if slug else None
    except ValueError:
        return None


def _emit(status: str, **fields: object) -> None:
    click.echo(json.dumps({"command": "download", "status": status, **fields}, indent=2))


def _fail(status: str, hint: str, **fields: object) -> None:
    _emit(status, hint=hint, download_performed=False, **fields)
    raise click.exceptions.Exit(1)


def _open(url: str):
    # Neither this request nor an R2 request receives an installation bearer,
    # cookies, or a Referer. Redirects are checked explicitly one hop at a time.
    try:
        return build_opener(_NoRedirectHandler()).open(Request(url, headers={"Accept": "video/mp4"}), timeout=60)
    except HTTPError as response:
        return response


def _transfer(endpoint: str, origin: str, destination: Path) -> int:
    with _open(endpoint) as redirect:
        if redirect.status not in {302, 303, 307, 308}:
            if redirect.status in {404, 410}:
                raise ValueError("not_found")
            if redirect.status == 429:
                raise ValueError("rate_limited")
            raise ValueError("download_unavailable")
        location = redirect.headers.get("Location")
        if not location:
            raise ValueError("download_unavailable")
        signed = urljoin(endpoint, location)
        if not _safe_signed_url(signed):
            raise ValueError("download_unavailable")
        target = urlsplit(signed)
        if target.scheme == "http" and f"{target.scheme}://{target.netloc}" != origin:
            raise ValueError("download_unavailable")
    temporary = None
    try:
        with _open(signed) as response:
            if response.status != 200 or response.headers.get_content_type() != "video/mp4":
                raise ValueError("download_unavailable")
            length = response.headers.get("Content-Length", "")
            if not re.fullmatch(r"[0-9]{1,10}", length) or not 1 <= int(length) <= MAX_DOWNLOAD_BYTES:
                raise ValueError("download_unavailable")
            expected, total, prefix = int(length), 0, bytearray()
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".moviestar-download-", delete=False) as output:
                temporary = Path(output.name)
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > expected:
                        raise ValueError("download_unavailable")
                    if len(prefix) < 12:
                        prefix.extend(chunk[:12 - len(prefix)])
                    output.write(chunk)
                if total != expected or len(prefix) < 12 or prefix[4:8] != b"ftyp":
                    raise ValueError("download_unavailable")
                output.flush()
                os.fsync(output.fileno())
            # A same-directory hard link publishes the complete file atomically
            # and fails if any destination appeared during transfer, even a
            # dangling symlink. os.replace would silently overwrite that race.
            os.link(temporary, destination)
            return total
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@click.command("download")
@click.argument("url")
@click.option("--out", type=click.Path(dir_okay=False, path_type=Path), metavar="FILE",
              help="Save here; defaults to the unlisted link slug plus .mp4. Existing files are never overwritten.")
@click.option("--dry-run", is_flag=True, help="Preview without network requests, account credentials, or file writes.")
def download(url: str, out: Path | None, dry_run: bool) -> None:
    """Download the original finished MP4 from an unlisted Moviestar watch URL.

    Anyone holding the link can download; no recipient account is required.
    Only the published file is downloaded, not projects, sources, or history.
    Preview links are accepted only for the configured account service origin.

    Example: moviestar download https://trymoviestar.com/v/SLUG --out cut.mp4
    """
    parsed = watch_link(url)
    if parsed is None:
        _fail("error", "Use a complete Moviestar watch link, https://trymoviestar.com/v/SLUG, or a link on the configured service origin.")
    origin, slug = parsed
    destination = (out or Path(f"{slug}.mp4")).expanduser().absolute()
    fields = {"url": url, "out": str(destination), "account_required": False}
    if os.path.lexists(destination):
        _fail("error", "Choose another --out path; downloads never overwrite an existing file.", **fields)
    if dry_run:
        _emit("would_download", **fields, download_performed=False)
        return
    try:
        size = _transfer(f"{origin}/v/{slug}/download", origin, destination)
    except FileExistsError:
        _fail("error", "The destination appeared during download. Choose another --out; the existing file was preserved.", **fields)
    except (ValueError, HTTPException, URLError, TimeoutError, OSError) as error:
        status = str(error) if type(error) is ValueError and str(error) in {"not_found", "rate_limited"} else "download_unavailable"
        hint = "This link is no longer available. Ask the sender for a new link." if status == "not_found" else "Retry the original watch URL later. No partial destination was published."
        _fail(status, hint, **fields)
    _emit("downloaded", **fields, size_bytes=size, download_performed=True)
