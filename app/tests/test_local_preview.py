"""Local preview selection, bounded work, and private media delivery."""

import json
import os
from pathlib import Path
import threading
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.local_preview import PreviewQueue, PreviewServer, PreviewSession, read_snapshot
from tests.test_spatial_render import project
from tests.test_spatial_video import video_project
from moviestar.spatial_layout import browser_available


def snapshot(name):
    return {"state_id": name, "range": {"from": 0, "to": 5},
            "profile": {"name": "720p", "fps": 30}}


def test_late_completion_cannot_replace_newer_preview():
    queue = PreviewQueue()
    a = queue.submit(snapshot("A"))
    assert queue.take()["request_id"] == a["request_id"]
    queue.finish(a["request_id"], media="a.mp4")
    b = queue.submit(snapshot("B"))
    assert queue.take()["request_id"] == b["request_id"]
    queue.finish(b["request_id"], media="b.mp4")
    # A delayed duplicate completion/publication notification arrives last.
    queue.finish(a["request_id"], media="a.mp4")
    assert queue.state()["displayed"]["request_id"] == b["request_id"]
    assert queue.state()["is_current"] is True


def test_one_active_one_pending_and_newest_pending_wins():
    queue = PreviewQueue()
    a = queue.submit(snapshot("A"))
    queue.take()
    b = queue.submit(snapshot("B"))
    c = queue.submit(snapshot("C"))
    assert queue.take() is None
    state = queue.state()
    assert state["active"]["request_id"] == a["request_id"]
    assert state["pending"]["request_id"] == c["request_id"]
    assert state["latest"]["request_id"] == c["request_id"]
    assert queue.get(b["request_id"])["status"] == "superseded"
    queue.finish(a["request_id"], media="a.mp4")
    assert queue.state()["displayed"] is None
    assert queue.take()["request_id"] == c["request_id"]


def test_failed_latest_keeps_old_preview_explicitly_old():
    queue = PreviewQueue()
    a = queue.submit(snapshot("A"))
    queue.take()
    queue.finish(a["request_id"], media="a.mp4")
    b = queue.submit(snapshot("B"))
    queue.take()
    queue.finish(b["request_id"], error="Cannot render")
    state = queue.state()
    assert state["displayed"]["request_id"] == a["request_id"]
    assert state["latest"]["error"] == "Cannot render"
    assert state["is_current"] is False


def test_undo_gets_new_request_even_when_content_matches_old_version():
    queue = PreviewQueue()
    a = queue.submit(snapshot("A"))
    queue.take()
    queue.finish(a["request_id"], media="a.mp4")
    b = queue.submit(snapshot("B"))
    queue.take()
    restored = queue.submit(snapshot("A"))
    assert restored["request_id"] != a["request_id"]
    queue.finish(b["request_id"], media="b.mp4")
    assert queue.state()["is_current"] is False
    assert queue.state()["displayed"]["request_id"] == a["request_id"]
    queue.take()
    queue.finish(restored["request_id"], media="restored.mp4")
    assert queue.state()["displayed"]["request_id"] == restored["request_id"]


def test_invalid_project_supersedes_pending_and_active_results():
    queue = PreviewQueue()
    a = queue.submit(snapshot("A"))
    queue.take()
    queue.reject("Invalid project")
    queue.finish(a["request_id"], media="a.mp4")
    assert queue.state()["displayed"] is None
    assert queue.state()["latest"]["status"] == "failed"


def test_rapid_edit_history_and_snapshots_stay_bounded_before_worker_finishes():
    queue = PreviewQueue()
    queue.submit({**snapshot("A"), "spec": {"large": "document"}})
    queue.take()
    previous = None
    for index in range(30):
        request = queue.submit({**snapshot(str(index)), "spec": {"large": "document"}})
        if previous:
            assert "spec" not in queue.get(previous["request_id"])
        assert len(queue.requests) <= 8
        previous = request
    assert queue.state()["active"]["state_id"] == "A"
    assert queue.state()["pending"]["state_id"] == "29"


@pytest.fixture
def server(tmp_path):
    queue = PreviewQueue()
    a = queue.submit(snapshot("A"))
    queue.take()
    media = tmp_path / "a.mp4"
    media.write_bytes(b"0123456789")
    queue.finish(a["request_id"], media=str(media))
    service = PreviewServer(queue, token="a" * 48, port=0)
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    yield service, queue, a
    service.shutdown()
    service.server_close()
    thread.join(timeout=2)


def test_http_serves_only_registered_media_with_seeking_and_head(server):
    service, queue, a = server
    url = service.url + "media/" + a["request_id"]
    with urlopen(Request(url, headers={"Range": "bytes=2-5"})) as response:
        assert response.status == 206
        assert response.headers["Content-Range"] == "bytes 2-5/10"
        assert response.read() == b"2345"
    with urlopen(Request(url, method="HEAD")) as response:
        assert response.headers["Content-Length"] == "10"
        assert response.read() == b""
    for path in ("media/../../spec.json", "media/missing", "../state"):
        with pytest.raises(HTTPError) as error:
            urlopen(service.url + path)
        assert error.value.code == 404
    with pytest.raises(HTTPError) as error:
        urlopen(Request(url, headers={"Range": "bytes=20-"}))
    assert error.value.code == 416


def test_foreign_origin_and_host_rejected_and_state_never_leaks_paths(server):
    service, queue, a = server
    for headers in ({"Origin": "https://foreign.example"}, {"Host": "foreign.example"}):
        with pytest.raises(HTTPError) as error:
            urlopen(Request(service.url + "state", headers=headers))
        assert error.value.code == 403
    with urlopen(service.url + "state") as response:
        data = response.read().decode()
        assert "a.mp4" not in data
        assert json.loads(data)["displayed"]["request_id"] == a["request_id"]


def test_historical_preview_is_read_only_and_independent_of_latest(server):
    service, queue, a = server
    b = queue.submit(snapshot("B"))
    with urlopen(service.url + "?request=" + a["request_id"]) as response:
        assert response.status == 200
    assert queue.state()["latest"]["request_id"] == b["request_id"]
    with urlopen(service.url + "media/" + a["request_id"]) as response:
        assert response.read() == b"0123456789"


def test_preview_help_and_invalid_range_have_no_side_effects(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    help_result = runner.invoke(cli, ["preview", "--help"])
    assert help_result.exit_code == 0
    assert "--no-open" in help_result.output
    result = runner.invoke(cli, ["preview", "--from", "0", "--to", "90", "--no-open"])
    assert result.exit_code != 0
    assert json.loads(result.stdout)["command"] == "preview"
    assert "maximum ten-second" in json.loads(result.stdout)["error"]
    assert not list(tmp_path.iterdir())


def test_saved_edit_then_actual_cli_undo_supersedes_unfinished_preview(project, tmp_path, monkeypatch):
    from copy import deepcopy
    from moviestar.project import save_project
    from moviestar.spec import save_spec, ensure_scene_motion_identities

    spec, metadata = project
    spec, _ = ensure_scene_motion_identities(spec)
    save_spec(spec, cwd=tmp_path)
    save_project(metadata, cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    session = PreviewSession(tmp_path, 0, .4, tmp_path / "previews")
    session.refresh()
    a = session.queue.take()
    session.queue.finish(a["request_id"], media="a.mp4")
    edited = deepcopy(spec)
    edited["spatial"]["scenes"]["interview"]["layout"]["background_color"] = "#aa0000"
    save_spec(edited, cwd=tmp_path, command="composition model edit")
    session.refresh()
    b = session.queue.take()
    assert b["state_id"] != a["state_id"]
    undo = CliRunner().invoke(cli, ["undo"])
    assert undo.exit_code == 0, undo.output
    session.refresh()
    restored = session.queue.state()["latest"]
    assert restored["state_id"] == a["state_id"]
    assert restored["request_id"] != a["request_id"]
    session.queue.finish(b["request_id"], media="b.mp4")
    assert session.queue.state()["displayed"]["request_id"] == a["request_id"]
    assert session.queue.state()["is_current"] is False


def test_replaced_source_invalidates_snapshot_even_with_unchanged_spec(project, tmp_path):
    from moviestar.project import save_project
    from moviestar.spec import save_spec
    spec, metadata = project
    save_spec(spec, cwd=tmp_path)
    save_project(metadata, cwd=tmp_path)
    a = read_snapshot(tmp_path, 0, .4)
    Path(spec["sources"][0]["path"]).touch()
    b = read_snapshot(tmp_path, 0, .4)
    assert a["state_id"] == b["state_id"]
    assert a["input_identity"] != b["input_identity"]


def test_preview_supervisor_cancels_before_starting_new_worker(tmp_path, monkeypatch):
    import signal
    import moviestar.local_preview as module
    wanted = [snapshot("A")]
    processes = []
    signals = []
    clock = [0]

    class Process:
        def __init__(self, *args, **kwargs):
            self.pid = 20000 + len(processes)
            self.returncode = None
            assert Path(kwargs["env"]["TMPDIR"]).is_relative_to(tmp_path)
            processes.append(self)
        def poll(self):
            return self.returncode

    def send(pid, sig):
        signals.append((pid, sig))
        if sig == signal.SIGKILL:
            processes[0].returncode = -9

    monkeypatch.setattr(module, "read_snapshot", lambda *_: wanted[0])
    monkeypatch.setattr(module.subprocess, "Popen", Process)
    monkeypatch.setattr(module.os, "killpg", send)
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    session = PreviewSession(tmp_path, 0, 5, tmp_path)
    session.tick()
    wanted[0] = snapshot("B")
    session.tick()
    assert signals == [(20000, signal.SIGINT)]
    assert len(processes) == 1
    wanted[0] = snapshot("C")
    clock[0] = module.CANCEL_SECONDS + .1
    session.tick()
    assert len(processes) == 2
    assert session.queue.state()["active"]["state_id"] == "C"
    assert not session.queue.state()["pending"]
    assert (20000, signal.SIGKILL) in signals
    session.log.close()


@pytest.mark.skipif(not browser_available(), reason="Chrome and Node are required")
def test_real_snapshot_worker_preserves_video_audio_and_project(video_project, tmp_path):
    import hashlib
    before = hashlib.sha256((tmp_path / "moviestar/spec.json").read_bytes()).hexdigest()
    request = read_snapshot(tmp_path, 0, .2)
    request_file = tmp_path / "request.json"
    request_file.write_text(json.dumps(request))
    result = subprocess.run([sys.executable, "-m", "moviestar.preview_worker", str(request_file)],
                            cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-of", "json",
                                               str(tmp_path / "preview.mp4")]))
    video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
    audio = next(stream for stream in probe["streams"] if stream["codec_type"] == "audio")
    assert (video["width"], video["height"], video["pix_fmt"]) == (320, 180, "yuv420p")
    assert audio["codec_name"] == "aac"
    assert before == hashlib.sha256((tmp_path / "moviestar/spec.json").read_bytes()).hexdigest()


@pytest.mark.skipif(os.name != "posix", reason="POSIX worker groups")
def test_real_cancellation_reaps_descendants_before_next_generation(tmp_path, monkeypatch):
    import moviestar.local_preview as module
    wanted = [snapshot("A")]
    processes = []
    original_popen = subprocess.Popen
    marker = tmp_path / "child.pid"
    # Both processes deliberately ignore SIGINT, forcing the cleanup deadline.
    script = ("import signal,subprocess,sys,time;from pathlib import Path;"
              "signal.signal(signal.SIGINT,signal.SIG_IGN);"
              "p=subprocess.Popen([sys.executable,'-c','import signal,time;signal.signal(signal.SIGINT,signal.SIG_IGN);time.sleep(60)']);"
              f"Path({str(marker)!r}).write_text(str(p.pid));time.sleep(60)")
    def launch(*args, **kwargs):
        process = original_popen([sys.executable, "-c", script], **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(module, "read_snapshot", lambda *_: wanted[0])
    monkeypatch.setattr(module.subprocess, "Popen", launch)
    monkeypatch.setattr(module, "CANCEL_SECONDS", .2)
    session = PreviewSession(tmp_path, 0, 5, tmp_path)
    try:
        session.tick()
        deadline = time.monotonic() + 3
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert marker.exists()
        child = int(marker.read_text())
        wanted[0] = snapshot("B")
        while len(processes) == 1 and time.monotonic() < deadline:
            session.tick()
            time.sleep(.02)
        assert len(processes) == 2
        assert processes[0].poll() is not None
        # Use the original Popen through subprocess.run, not the worker factory.
        monkeypatch.setattr(module.subprocess, "Popen", original_popen)
        state = subprocess.run(["ps", "-o", "stat=", "-p", str(child)], capture_output=True, text=True).stdout.strip()
        assert not state or state.startswith("Z"), state
    finally:
        session.stopped.set()
        session.run()  # Executes owned-worker cleanup without starting another job.


def test_duplicate_project_session_rejected_even_on_different_ports(project, tmp_path, monkeypatch):
    import moviestar.local_preview as module
    from moviestar.project import save_project
    from moviestar.spec import save_spec
    spec, metadata = project
    save_spec(spec, cwd=tmp_path)
    save_project(metadata, cwd=tmp_path)
    def inside(*args, **kwargs):
        with pytest.raises(ValueError, match="already running"):
            module.serve(tmp_path, 0, .4, port=0, open_browser=False, ready=lambda _: None)
    monkeypatch.setattr(module, "_serve_session", inside)
    module.serve(tmp_path, 0, .4, port=0, open_browser=False, ready=lambda _: None)
