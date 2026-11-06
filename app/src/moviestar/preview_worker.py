"""Isolated snapshot renderer owned by the foreground local preview session."""

import json
from pathlib import Path
import subprocess
import sys

import click


def render(request_file):
    from moviestar.project import set_explicit_workspace_root
    from moviestar.spatial_session import BrowserSession
    from moviestar import spatial_session, spatial_video

    request_file = Path(request_file)
    request = json.loads(request_file.read_text())
    folder = request_file.parent
    set_explicit_workspace_root(request["root"])
    # The supervisor owns the entire render group, including browser children.
    # No PID registry or process discovery is needed to clean up cancellation.
    class WorkerBrowserSession(BrowserSession):
        def __init__(self):
            super().__init__(own_process_group=False)

    spatial_video.BrowserSession = spatial_session.BrowserSession = WorkerBrowserSession
    original = folder / "render.mp4"
    from moviestar.cli import cli
    with click.Context(cli, info_name="preview"):
        spatial_video.spatial_video_command(
            project=request["project"], spec=request["spec"], command="watch", output=str(original),
            from_s=request["range"]["from"], to_s=request["range"]["to"],
            video_encoder="x264", quiet=True)
    profile = request["profile"]
    staged = folder / "review.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(original),
                    "-map", "0:v:0", "-map", "0:a:0?", "-vf",
                    f"scale={profile['width']}:{profile['height']}",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-ar", "48000", "-movflags", "+faststart", str(staged)], check=True)
    staged.replace(folder / "preview.mp4")


if __name__ == "__main__":
    render(sys.argv[1])
