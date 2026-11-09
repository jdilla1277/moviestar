"""Recover from treating find's transcript-scope flag as a source selector."""

import json
import subprocess
import sys

import pytest
from click.testing import CliRunner

from moviestar.cli import cli


@pytest.fixture
def search_project(test_video, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    captions = tmp_path / "captions.vtt"
    captions.write_text(
        "WEBVTT\n\n00:00:00.200 --> 00:00:00.400\nspeaker\n\n"
        "00:00:01.200 --> 00:00:01.400\nlater\n"
    )
    result = CliRunner().invoke(cli, [
        "load", test_video, test_video, "--as", "speaker", "--as", "screen",
        "--captions", str(captions), "--captions", str(captions), "--no-frames",
    ])
    assert result.exit_code == 0, result.output
    return tmp_path


@pytest.mark.parametrize("arguments", [
    ["find", "--source", "speaker", "later"],
    ["find", "later", "--source", "speaker"],
    ["find", "speaker", "later", "--source"],
])
@pytest.mark.parametrize("workspace_mode", ["cwd", "subdirectory", "explicit"])
def test_source_id_argument_returns_scope_recovery_hint_without_mutation(
    search_project, arguments, workspace_mode, monkeypatch
):
    root = search_project
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    if workspace_mode != "cwd":
        other = root / "nested"
        other.mkdir()
        monkeypatch.chdir(other)
        if workspace_mode == "explicit":
            arguments = [*arguments, "--workspace", str(root)]
    result = CliRunner().invoke(cli, arguments)
    assert result.exit_code == 2
    body = json.loads(result.stdout)
    assert body["command"] == "find"
    assert "unexpected extra argument" in body["error"].lower()
    assert "--source is a boolean" in body["hint"]
    assert "all sources" in body["hint"]
    assert "matches[].source" in body["hint"]
    assert "--limit" in body["hint"]
    assert 'moviestar find --source "QUERY"' in body["hint"]
    assert "Usage:" in result.stderr
    assert {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_boolean_scope_and_queries_matching_a_source_id_keep_working(search_project):
    runner = CliRunner()
    trimmed = runner.invoke(cli, ["trim", "--source", "speaker", "--from", "1", "--to", "2"])
    assert trimmed.exit_code == 0, trimmed.output
    for flags, expected in [([], {"screen"}), (["--source"], {"speaker", "screen"})]:
        result = runner.invoke(cli, ["find", "speaker", "--exact", "--limit", "50", *flags])
        assert result.exit_code == 0, result.output
        body = json.loads(result.stdout)
        assert body["source_only"] == bool(flags)
        assert set(body["sources_searched"]) == {"speaker", "screen"}
        assert {match["source"] for match in body["matches"]} == expected
        if flags:
            [match] = [m for m in body["matches"] if m["source"] == "speaker"]
            assert match["excluded_by_trim"] is True
            assert match["result_range"] is None


@pytest.mark.parametrize("args", [
    ["find", "unquoted", "phrase"],
    ["find", "--source", "--unknown", "phrase"],
    ["probe", "video.mp4", "extra"],
])
def test_unrelated_usage_errors_keep_generic_hint(args, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 2
    body = json.loads(result.stdout)
    assert "--source is a boolean" not in body["hint"]
    assert "--help" in body["hint"]


def test_real_process_scope_error_is_one_json_document(search_project):
    result = subprocess.run(
        [sys.executable, "-m", "moviestar", "find", "--source", "speaker", "later"],
        cwd=search_project, capture_output=True, text=True,
    )
    assert result.returncode == 2
    body = json.loads(result.stdout)
    assert body["command"] == "find"
    assert "--source is a boolean" in body["hint"]
    assert "Usage:" in result.stderr
