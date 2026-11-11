"""Canvas edits validate drafts and undo history at the right boundaries (#170)."""

import copy
import json
import subprocess

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.spec import SpecValidationError, load_spec, save_spec, validate_spec


def invoke(args):
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, f"{args}: {result.output}"
    return json.loads(result.stdout)


def read_spec(tmp_path):
    return json.loads((tmp_path / "moviestar" / "spec.json").read_text())


def scene_args(canvas, *, extra=False):
    args = [
        "scenes", "set", "--canvas", canvas, "--scene", "intro=single",
        "--slot", "intro:main=src_0", "--from", "0", "--to", "1",
    ]
    if extra:
        args += [
            "--scene", "outro=single", "--slot", "outro:main=src_0",
            "--from", "1", "--to", "2",
        ]
    return args


def setup_project(test_video, tmp_path, monkeypatch, canvas, *, motion=False):
    monkeypatch.chdir(tmp_path)
    invoke(["load", test_video, "--as", "src_0", "--no-transcribe", "--no-frames"])
    invoke(scene_args(canvas, extra=motion))
    snapshots = [read_spec(tmp_path)]
    if motion:
        path = tmp_path / "motion.json"
        path.write_text(json.dumps({
            "version": 1,
            "scenes": [{"scene": "outro", "slots": [{"slot": "main", "pacing": [{
                "id": "faster", "mode": "speed", "speed": 2,
                "range": {"from": "0.2", "to": "0.8", "space": "source-local"},
            }]}]}],
        }))
        invoke(["scenes", "motion", "set", str(path)])
        snapshots.append(read_spec(tmp_path))
    for text in ("First", "Second", "Third"):
        invoke(["overlays", "add", "--text", text, "--from", "0", "--to", "0.5"])
        snapshots.append(read_spec(tmp_path))
    return snapshots


@pytest.mark.parametrize("initial,target", [
    ("854x480", "1080x1920"), ("1080x1920", "854x480"),
    ("854x480", "1920x1080"),
])
@pytest.mark.parametrize("file_input", [False, True])
@pytest.mark.parametrize("motion", [False, True])
def test_canvas_edits_dry_run_reload_and_undo_preserve_every_prior_state(
    test_video, tmp_path, monkeypatch, initial, target, file_input, motion
):
    snapshots = setup_project(test_video, tmp_path, monkeypatch, initial, motion=motion)
    spec_path = tmp_path / "moviestar" / "spec.json"
    for canvas in (target, initial, target):
        before = read_spec(tmp_path)
        before_bytes = spec_path.read_bytes()
        if file_input:
            path = tmp_path / "scenes.json"
            path.write_text(json.dumps({"canvas": canvas, "scenes": [{
                "name": "intro", "layout": "single", "slots": [{
                    "slot": "main", "source": "src_0", "from": "0", "to": "1",
                }],
            }]}))
            args = ["scenes", "set", str(path)]
        else:
            args = scene_args(canvas)
        dry = invoke([*args, "--dry-run"])
        assert dry["writes_spec"] is False
        assert spec_path.read_bytes() == before_bytes
        invoke(args)
        after = read_spec(tmp_path)
        validate_spec(after)
        assert load_spec(tmp_path) == after
        assert len(after["revisions"]) == len(before["revisions"]) + 1
        assert after["revisions"][:-1] == before["revisions"]
        assert after["overlays"] == before["overlays"]
        width, height = map(int, canvas.split("x"))
        assert after["composition_canvas"]["width"] == width
        assert after["composition_canvas"]["height"] == height
        assert after["composition"][0]["layout"]["orientation"] == (
            "vertical" if height > width else "horizontal"
        )
        assert after["motion"]["scenes"] == []
        # Read/render planning exercises the normal reload path without writes.
        after_bytes = spec_path.read_bytes()
        invoke(["status"])
        invoke(["export", "--dry-run"])
        assert spec_path.read_bytes() == after_bytes
        # Reapplying the exact same authoring input must not add a revision.
        invoke(args)
        assert read_spec(tmp_path) == after
        snapshots.append(after)
    for expected in reversed(snapshots[:-1]):
        invoke(["undo"])
        assert read_spec(tmp_path) == expected
        validate_spec(read_spec(tmp_path))


@pytest.mark.parametrize("layout", [False, True])
@pytest.mark.parametrize("initial,target", [
    ("854x480", "1080x1920"), ("1080x1920", "854x480"),
])
def test_concat_canvas_changes_preserve_history_and_exact_undo(
    test_video, tmp_path, monkeypatch, layout, initial, target
):
    setup_project(test_video, tmp_path, monkeypatch, initial)
    spec_path = tmp_path / "moviestar" / "spec.json"
    before = read_spec(tmp_path)
    before_bytes = spec_path.read_bytes()
    args = ["concat", "--canvas", target]
    args += (["--layout", "single", "--slot", "main=src_0"] if layout else ["--segment", "src_0"])
    args += ["--from", "0", "--to", "1"]
    invoke([*args, "--dry-run"])
    assert spec_path.read_bytes() == before_bytes
    invoke(args)
    after = read_spec(tmp_path)
    validate_spec(after)
    assert after["revisions"][:-1] == before["revisions"]
    assert after["revisions"][-1]["command"] == "concat"
    invoke(["undo"])
    assert read_spec(tmp_path) == before


@pytest.mark.parametrize("initial,target", [
    ("854x480", "1080x1920"), ("1080x1920", "854x480"),
])
def test_resized_canvas_and_undo_render_the_correct_dimensions(
    test_video, tmp_path, monkeypatch, initial, target
):
    setup_project(test_video, tmp_path, monkeypatch, initial)
    before = read_spec(tmp_path)
    invoke(scene_args(target))
    spec_path = tmp_path / "moviestar" / "spec.json"
    for canvas in (target, initial):
        path = tmp_path / f"frame-{canvas}.png"
        before_bytes = spec_path.read_bytes()
        data = invoke(["screenshot", "--at", "0.25", "--out", str(path)])
        assert data["overlays"]["burned_in"] is True
        dimensions = json.loads(subprocess.check_output([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "json", str(path),
        ]))["streams"][0]
        width, height = map(int, canvas.split("x"))
        assert dimensions == {"width": width, "height": height}
        assert spec_path.read_bytes() == before_bytes
        if canvas == target:
            invoke(["undo"])
            assert read_spec(tmp_path) == before


@pytest.mark.parametrize("bad_entry", [0, 1, 3])
@pytest.mark.parametrize("dry_run", [False, True])
def test_invalid_stored_history_cannot_be_hidden_by_canvas_edit(
    test_video, tmp_path, monkeypatch, bad_entry, dry_run
):
    setup_project(test_video, tmp_path, monkeypatch, "854x480")
    spec_path = tmp_path / "moviestar" / "spec.json"
    spec = read_spec(tmp_path)
    # Unknown source in a prior composition: the current document remains valid.
    broken = copy.deepcopy(spec["composition"])
    broken[0]["slots"][0]["source"] = "missing_source"
    spec["revisions"][bad_entry]["changed"]["composition"] = broken
    spec_path.write_text(json.dumps(spec))
    before = spec_path.read_bytes()
    result = CliRunner().invoke(cli, [*scene_args("1080x1920"), *(["--dry-run"] if dry_run else [])])
    assert result.exit_code == 1
    assert "prior state" in json.loads(result.stdout)["error"]
    assert spec_path.read_bytes() == before


def test_invalid_current_scene_is_rejected_without_changing_history(
    test_video, tmp_path, monkeypatch
):
    setup_project(test_video, tmp_path, monkeypatch, "854x480")
    spec_path = tmp_path / "moviestar" / "spec.json"
    before = spec_path.read_bytes()
    for dry_run in (True, False):
        args = scene_args("1080x1920")
        args[args.index("intro:main=src_0")] = "intro:main=missing_source"
        result = CliRunner().invoke(cli, [*args, *(["--dry-run"] if dry_run else [])])
        assert result.exit_code == 1
        assert "unknown source" in json.loads(result.stdout)["error"]
        assert spec_path.read_bytes() == before


def test_save_still_rejects_invalid_incoming_history_atomically(
    test_video, tmp_path, monkeypatch
):
    setup_project(test_video, tmp_path, monkeypatch, "854x480")
    spec_path = tmp_path / "moviestar" / "spec.json"
    before = spec_path.read_bytes()
    spec = read_spec(tmp_path)
    spec["revisions"][0]["changed"]["composition_canvas"] = {"width": -1}
    with pytest.raises(SpecValidationError, match="prior state"):
        save_spec(spec, tmp_path, record=False)
    assert spec_path.read_bytes() == before


def test_save_rereads_and_rejects_externally_corrupted_history(
    test_video, tmp_path, monkeypatch
):
    setup_project(test_video, tmp_path, monkeypatch, "854x480")
    spec_path = tmp_path / "moviestar" / "spec.json"
    draft = read_spec(tmp_path)
    external = copy.deepcopy(draft)
    external["revisions"][0]["changed"]["composition_canvas"] = {"width": -1}
    spec_path.write_text(json.dumps(external))
    before = spec_path.read_bytes()
    with pytest.raises(SpecValidationError, match="prior state"):
        save_spec(draft, tmp_path, command="scenes set")
    assert spec_path.read_bytes() == before


def test_canvas_edit_at_history_limit_preserves_fifo_and_restores_prior_state(
    test_video, tmp_path, monkeypatch
):
    from moviestar.spec import REVISION_LIMIT

    setup_project(test_video, tmp_path, monkeypatch, "854x480")
    for index in range(REVISION_LIMIT - 4):
        invoke(["overlays", "add", "--text", str(index), "--from", "0", "--to", "0.5"])
    before = read_spec(tmp_path)
    assert len(before["revisions"]) == REVISION_LIMIT
    spec_path = tmp_path / "moviestar" / "spec.json"
    before_bytes = spec_path.read_bytes()
    args = scene_args("1080x1920")
    invoke([*args, "--dry-run"])
    assert spec_path.read_bytes() == before_bytes
    invoke(args)
    after = read_spec(tmp_path)
    assert len(after["revisions"]) == REVISION_LIMIT
    assert after["revisions"][:-1] == before["revisions"][1:]
    validate_spec(after)
    invoke(["undo"])
    expected = {**before, "revisions": before["revisions"][1:]}
    assert read_spec(tmp_path) == expected
