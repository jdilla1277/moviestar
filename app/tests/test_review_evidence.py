"""A render, a sampled frame, and a listening judgment prove different things."""

import hashlib
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from moviestar.cli import cli


def invoke(*args, expected=0):
    result = CliRunner().invoke(cli, list(args))
    assert result.exit_code == expected, (result.output, result.exception)
    return json.loads(result.stdout)


def export_report(tmp_path, *args):
    data = invoke("export", "--out", str(tmp_path / "cut.mp4"), "--review-report", *args)
    return data, Path(data["review_report"]["out"])


def show(report):
    return invoke("review", "show", "--report", str(report))


def record(report, dimension="visual", status="passed", *args, expected=0):
    return invoke("review", "record", "--report", str(report), "--dimension", dimension,
                  "--status", status, "--actor", "agent:test", "--method", "test review",
                  *args, expected=expected)


@pytest.mark.parametrize("command", ["export", "watch"])
def test_report_is_opt_in_and_dry_run_creates_no_state(test_video, loaded_project, tmp_path, command):
    loaded_project(test_video, frames=False)
    args = ["--from", "0", "--to", "1"] if command == "watch" else []
    before = {str(p): p.read_bytes() for p in (tmp_path / "moviestar").rglob("*") if p.is_file()}
    data = invoke(command, *args, "--review-report", "--dry-run")
    report = data["review_report"]
    assert report["dry_run"] is True
    assert "out" not in report and "id" not in report
    assert report["clock"] == "artifact_seconds"
    assert {str(p): p.read_bytes() for p in (tmp_path / "moviestar").rglob("*") if p.is_file()} == before
    normal = invoke(command, *args, "--out", str(tmp_path / "normal.mp4"))
    assert "review_report" not in normal
    assert not (tmp_path / "moviestar" / "reviews").exists()


@pytest.mark.parametrize("command", ["export", "watch"])
def test_report_identifies_exact_render_and_all_dimensions_start_unreviewed(
    test_video, loaded_project, tmp_path, command
):
    loaded_project(test_video, frames=False)
    args = ["--from", "0.2", "--to", "1.8"] if command == "watch" else []
    out = tmp_path / "exact.mp4"
    data = invoke(command, *args, "--review-report", "--out", str(out))
    report = Path(data["review_report"]["out"])
    view = show(report)
    assert view["artifact"]["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
    assert view["artifact"]["size_bytes"] == out.stat().st_size
    assert view["currency"]["current"] is True
    assert set(view["dimensions"]) == {"technical", "source_alignment", "visual", "motion", "listening"}
    assert all(item["status"] == "unreviewed" for item in view["dimensions"].values())
    assert view["clock"] == "artifact_seconds"
    assert "approved" not in view
    assert "listening" in " ".join(view["next_steps"])


def test_sampled_visual_pass_never_becomes_motion_or_listening_approval(
    test_video, loaded_project, tmp_path
):
    loaded_project(test_video, frames=False)
    _, report = export_report(tmp_path)
    evidence = tmp_path / "frame.png"
    evidence.write_bytes(b"reviewed example frame")
    result = record(report, "visual", "passed", "--at", "0.5", "--evidence", str(evidence),
                    "--limitations", "Only one still; no motion or audio reviewed")
    assert result["status"] == "recorded"
    view = show(report)
    visual = view["dimensions"]["visual"]
    assert visual["status"] == "partial"
    assert visual["coverage"]["sample_times"] == [.5]
    assert visual["coverage"]["full"] is False
    assert view["dimensions"]["motion"]["status"] == "unreviewed"
    assert view["dimensions"]["listening"]["status"] == "unreviewed"
    entry = view["entries"][0]
    assert entry["actor"] == "agent:test" and entry["method"] == "test review"
    assert entry["provenance"] == "reviewer_reported"


def test_listening_unavailable_and_range_coverage_are_explicit(test_video, loaded_project, tmp_path):
    loaded_project(test_video, frames=False)
    _, report = export_report(tmp_path)
    blocked = record(report, "listening", "unavailable", "--note", "No audio-capable tool; human must listen")
    view = show(report)
    assert view["dimensions"]["listening"]["status"] == "unavailable"
    assert "human" in " ".join(view["next_steps"]).lower()
    record(report, "listening", "passed", "--from", "0", "--to", "1",
           "--supersedes", blocked["entry"]["id"])
    view = show(report)
    assert view["dimensions"]["listening"]["status"] == "partial"
    assert view["dimensions"]["listening"]["coverage"]["ranges"] == [[0.0, 1.0]]


def test_rejection_requires_explicit_supersession_and_history_is_retained(test_video, loaded_project, tmp_path):
    loaded_project(test_video, frames=False)
    _, report = export_report(tmp_path)
    bad = record(report, "visual", "rejected", "--from", "0", "--to", "1", "--note", "Text not readable")
    record(report, "visual", "passed", "--full")
    assert show(report)["dimensions"]["visual"]["status"] == "rejected"
    record(report, "visual", "passed", "--full", "--supersedes", bad["entry"]["id"],
           "--note", "Human rechecked and accepted the intentional treatment")
    view = show(report)
    assert view["dimensions"]["visual"]["status"] == "passed"
    assert len(view["entries"]) == 3
    assert view["entries"][0]["status"] == "rejected"
    assert view["entries"][0]["superseded"] is True
    assert view["dimensions"]["listening"]["status"] == "unreviewed"


@pytest.mark.parametrize("change", ["spec", "artifact", "source", "missing_artifact", "transcript", "font"])
def test_changed_content_is_stale_and_cannot_receive_fresh_approval(
    test_video, loaded_project, tmp_path, change
):
    local_source = tmp_path / "source.mp4"
    local_source.write_bytes(Path(test_video).read_bytes())
    loaded_project(local_source, frames=False)
    _, report = export_report(tmp_path)
    record(report, "visual", "passed", "--full")
    before = report.read_bytes()
    if change == "spec":
        invoke("trim", "--from", "0.1", "--to", "1.5")
    elif change == "artifact":
        out = tmp_path / "cut.mp4"
        content = bytearray(out.read_bytes())
        content[-1] ^= 1
        out.write_bytes(content)  # same size, exact hash must detect the change
    elif change == "source":
        local_source.write_bytes(local_source.read_bytes() + b"changed")
    elif change == "missing_artifact":
        (tmp_path / "cut.mp4").unlink()
    else:
        directory = tmp_path / "moviestar" / ("transcripts" if change == "transcript" else "fonts")
        directory.mkdir(exist_ok=True)
        (directory / "changed.json").write_text('{"new": true}')
    view = show(report)
    assert view["currency"]["current"] is False
    assert view["dimensions"]["visual"]["status"] == "stale"
    assert view["entries"][0]["status"] == "passed"  # historical judgment survives
    result = record(report, "visual", "passed", "--full", expected=1)
    assert "stale" in result["error"].lower()
    assert report.read_bytes() == before


def test_changed_proof_file_does_not_remain_a_pass(test_video, loaded_project, tmp_path):
    loaded_project(test_video, frames=False)
    _, report = export_report(tmp_path)
    proof = tmp_path / "frame.jpg"
    proof.write_bytes(b"old image")
    record(report, "visual", "passed", "--full", "--evidence", str(proof))
    proof.write_bytes(b"new image")
    view = show(report)
    assert view["currency"]["current"] is True
    assert view["entries"][0]["evidence_current"] is False
    assert view["dimensions"]["visual"]["status"] != "passed"


@pytest.mark.parametrize("proof_change", ["replaced", "deleted"])
def test_stale_proof_on_another_entry_does_not_hide_an_intact_rejection(
    test_video, loaded_project, tmp_path, proof_change
):
    loaded_project(test_video, frames=False)
    _, report = export_report(tmp_path)
    rejected = record(report, "visual", "rejected", "--full", "--note", "Caption is unreadable")
    proof = tmp_path / "frame.jpg"
    proof.write_bytes(b"reviewed image")
    passed = record(report, "visual", "passed", "--full", "--evidence", str(proof))
    assert show(report)["dimensions"]["visual"]["status"] == "rejected"
    if proof_change == "replaced":
        proof.write_bytes(b"different image")
    else:
        proof.unlink()
    before = report.read_bytes()
    view = show(report)
    assert view["currency"]["current"] is True
    assert view["entries"][0]["superseded"] is False
    assert view["entries"][0]["evidence_current"] is True
    assert view["entries"][1]["evidence_current"] is False
    assert view["dimensions"]["visual"]["status"] == "rejected"
    assert any("rejected visual" in step for step in view["next_steps"])
    assert report.read_bytes() == before
    # Resolving the rejection must not also clear the other entry's stale proof.
    record(report, "visual", "passed", "--full", "--supersedes", rejected["entry"]["id"])
    assert show(report)["dimensions"]["visual"]["status"] == "unknown"
    record(report, "visual", "passed", "--full", "--supersedes", passed["entry"]["id"])
    assert show(report)["dimensions"]["visual"]["status"] == "passed"


def test_rejection_with_its_own_stale_proof_remains_unknown(test_video, loaded_project, tmp_path):
    loaded_project(test_video, frames=False)
    _, report = export_report(tmp_path)
    proof = tmp_path / "frame.jpg"
    proof.write_bytes(b"rejected image")
    record(report, "visual", "rejected", "--full", "--evidence", str(proof), "--note", "Unreadable text")
    proof.unlink()
    view = show(report)
    assert view["entries"][0]["evidence_current"] is False
    assert view["dimensions"]["visual"]["status"] == "unknown"


@pytest.mark.parametrize("dimension,args", [
    ("visual", []), ("visual", ["--at", "2"]), ("visual", ["--from", "1", "--to", "0"]),
    ("visual", ["--from", "0"]), ("visual", ["--full", "--at", ".5"]),
    ("visual", ["--at", "nan"]), ("motion", ["--at", ".5"]),
    ("listening", ["--at", ".5"]), ("visual", ["--full", "--supersedes", "missing"]),
])
def test_invalid_coverage_is_rejected_without_mutation(test_video, loaded_project, tmp_path, dimension, args):
    loaded_project(test_video, frames=False)
    _, report = export_report(tmp_path)
    before = report.read_bytes()
    data = record(report, dimension, "passed", *args, expected=1)
    assert data["error"] and data["hint"]
    assert report.read_bytes() == before


def test_coverage_union_retains_gaps_and_points_do_not_fill_them(test_video, loaded_project, tmp_path):
    loaded_project(test_video, frames=False)
    _, report = export_report(tmp_path)
    duration = show(report)["artifact"]["duration_seconds"]
    record(report, "visual", "passed", "--from", "0", "--to", ".75")
    record(report, "visual", "passed", "--from", "1.25", "--to", str(duration))
    record(report, "visual", "passed", "--at", "1")
    partial = show(report)["dimensions"]["visual"]
    assert partial["status"] == "partial"
    assert partial["coverage"]["unreviewed_ranges"] == [[.75, 1.25]]
    record(report, "visual", "passed", "--from", ".75", "--to", "1.25")
    complete = show(report)["dimensions"]["visual"]
    assert complete["status"] == "passed"
    assert complete["coverage"]["ranges"] == [[0.0, duration]]


def test_record_dry_run_and_show_do_not_change_report_or_project(test_video, loaded_project, tmp_path):
    loaded_project(test_video, frames=False)
    _, report = export_report(tmp_path)
    before = {str(p): p.read_bytes() for p in (tmp_path / "moviestar").rglob("*") if p.is_file()}
    data = record(report, "technical", "passed", "--full", "--dry-run")
    assert data["status"] == "would_record" and data["dry_run"] is True
    assert "out" not in data and "id" not in data["would_record"]
    assert show(report)["dimensions"]["technical"]["status"] == "unreviewed"
    assert {str(p): p.read_bytes() for p in (tmp_path / "moviestar").rglob("*") if p.is_file()} == before


def test_failed_atomic_write_preserves_history_and_cleans_lock(test_video, loaded_project, tmp_path, monkeypatch):
    from moviestar import review_evidence as module
    loaded_project(test_video, frames=False)
    _, report = export_report(tmp_path)
    before = report.read_bytes()

    def fail(*args):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(module.os, "replace", fail)
    data = record(report, "visual", "passed", "--full", expected=1)
    assert "disk failure" in data["error"]
    assert report.read_bytes() == before
    assert list(report.parent.iterdir()) == [report]


def test_another_writer_lock_is_preserved_and_does_not_lose_entries(test_video, loaded_project, tmp_path):
    loaded_project(test_video, frames=False)
    _, report = export_report(tmp_path)
    lock = report.with_suffix(".json.lock")
    lock.write_text("another writer")
    before = report.read_bytes()
    data = record(report, "visual", "passed", "--full", expected=1)
    assert "lock" in data["error"].lower()
    assert lock.read_text() == "another writer"
    assert report.read_bytes() == before


@pytest.mark.parametrize("contents", ["{", "[]", '{"version":2}', '{"version":1,"clock":"source","entries":[]}'])
def test_malformed_report_returns_structured_error(tmp_path, contents):
    report = tmp_path / "bad.json"
    report.write_text(contents)
    data = invoke("review", "show", "--report", str(report), expected=1)
    assert data["error"] and data["hint"]
    assert report.read_text() == contents


def test_overwriting_an_export_does_not_inherit_its_reviews(test_video, loaded_project, tmp_path):
    loaded_project(test_video, frames=False)
    _, old_report = export_report(tmp_path)
    record(old_report, "visual", "rejected", "--full", "--note", "First version rejected")
    before = old_report.read_bytes()
    invoke("trim", "--from", "0", "--to", "1")
    _, new_report = export_report(tmp_path)
    assert old_report != new_report
    assert old_report.read_bytes() == before
    assert show(old_report)["currency"]["current"] is False
    fresh = show(new_report)
    assert fresh["currency"]["current"] is True
    assert all(d["status"] == "unreviewed" for d in fresh["dimensions"].values())


def test_report_keeps_a_failed_byte_constraint_as_a_fact(test_video, loaded_project, tmp_path):
    loaded_project(test_video, frames=False)
    data = invoke("export", "--review", "--max-bytes", "1", "--review-report",
                  "--out", str(tmp_path / "large.mp4"), expected=1)
    view = show(data["review_report"]["out"])
    assert view["render_facts"]["size_budget"]["met"] is False
    assert view["render_facts"]["status"] == "size_limit_unmet"
    assert "byte limit" in " ".join(view["next_steps"]).lower()
    assert view["artifact"]["sha256"] == hashlib.sha256((tmp_path / "large.mp4").read_bytes()).hexdigest()
    assert all(d["status"] == "unreviewed" for d in view["dimensions"].values())


def test_scene_watch_coverage_uses_the_output_clock(test_video, loaded_project, tmp_path):
    loaded_project(test_video, frames=False)
    invoke("scenes", "set", "--canvas", "320x240", "--scene", "one=single",
           "--slot", "one:main=src_0", "--from", "0", "--to", "2")
    data = invoke("watch", "--from", "1", "--to", "1.8", "--review-report",
                  "--out", str(tmp_path / "window.mp4"))
    report = data["review_report"]["out"]
    view = show(report)
    assert view["artifact"]["duration_seconds"] == pytest.approx(.8, abs=.05)
    record(report, "visual", "passed", "--at", ".5")
    record(report, "visual", "passed", "--at", "1.5", expected=1)


def test_project_changed_during_render_is_not_labeled_current(test_video, loaded_project, tmp_path, monkeypatch):
    from moviestar import cli as module
    loaded_project(test_video, frames=False)
    original = module.create_report

    def changed_project(*args, **kwargs):
        path = tmp_path / "moviestar" / "project.json"
        project = json.loads(path.read_text())
        project["changed_during_render"] = True
        path.write_text(json.dumps(project))
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "create_report", changed_project)
    data, report = export_report(tmp_path)
    assert data["review_report"]["currency"]["current"] is False
    assert show(report)["dimensions"]["visual"]["status"] == "stale"


@pytest.mark.parametrize("defect", ["range", "sample", "duration", "duplicate_id", "foreign_supersession"])
def test_corrupt_persisted_entries_cannot_appear_approved(test_video, loaded_project, tmp_path, defect):
    loaded_project(test_video, frames=False)
    _, report = export_report(tmp_path)
    record(report, "visual", "passed", "--full")
    data = json.loads(report.read_text())
    if defect == "range":
        data["entries"][0]["coverage"]["ranges"] = [[-10, 20]]
    elif defect == "sample":
        data["entries"][0]["coverage"] = {"ranges": [], "sample_times": [20]}
    elif defect == "duration":
        data["artifact"]["duration_seconds"] = True
    elif defect == "duplicate_id":
        data["entries"].append(data["entries"][0])
    else:
        data["entries"][0]["supersedes"] = ["nonexistent"]
    report.write_text(json.dumps(data))
    response = invoke("review", "show", "--report", str(report), expected=1)
    assert response["error"] and response["hint"]
