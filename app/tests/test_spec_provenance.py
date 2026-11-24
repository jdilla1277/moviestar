"""Portable origin metadata never changes editing/undo meaning."""
import copy
import json
import pytest
from moviestar.spec import empty_spec, validate_spec, append_trim, append_revision, pop_revision, SpecValidationError

ORIGIN = {"project_id":"00000000-0000-4000-8000-000000000001", "base_revision_id":"00000000-0000-4000-8000-000000000002", "base_document_sha256":"a"*64, "engine_version":"b"*40, "contract_version":"cloud-editing-v1"}
def spec():
    value=empty_spec([{"id":"speaker","path":"asset:00000000-0000-4000-8000-000000000003"}])
    value["provenance"]=copy.deepcopy(ORIGIN)
    return value

@pytest.mark.parametrize("patch", [{"project_id":"not-a-uuid"},{"base_revision_id":None},{"base_document_sha256":"bad"},{"engine_version":"latest"},{"contract_version":"other"},{"local_path":"/private/file"}])
def test_invalid_origin_rejected(patch):
    value=spec(); value["provenance"].update(patch)
    with pytest.raises(SpecValidationError,match="provenance"):
        validate_spec(value)

@pytest.mark.parametrize("value", [None,[],"origin",{}])
def test_origin_has_explicit_complete_shape(value):
    document=spec(); document["provenance"]=value
    with pytest.raises(SpecValidationError,match="provenance"):
        validate_spec(document)

def test_origin_survives_edits_and_undo_without_claiming_new_cloud_revision():
    before=spec(); validate_spec(before)
    changed=append_revision(before,append_trim(before,"speaker",10,40,60),"trim")
    restored,_=pop_revision(changed)
    assert changed["provenance"]==restored["provenance"]==ORIGIN
    assert restored==before
    assert changed["version"]=="0.2"

def test_older_local_projects_need_no_origin_and_unknown_hash_stays_unknown():
    document=spec(); document["provenance"]["base_document_sha256"]=None; validate_spec(document)
    del document["provenance"]; validate_spec(document)

def test_origin_survives_actual_spec_file_io(tmp_path):
    from moviestar.spec import save_spec, load_spec
    before=spec()
    save_spec(before,cwd=tmp_path,record=False)
    assert load_spec(cwd=tmp_path)==before

    changed=append_trim(before,'speaker',10,40,60)
    save_spec(changed,cwd=tmp_path,command='trim')
    saved=load_spec(cwd=tmp_path)
    assert saved['provenance']==ORIGIN
    restored,_=pop_revision(saved)
    save_spec(restored,cwd=tmp_path,record=False)
    assert load_spec(cwd=tmp_path)==before


@pytest.mark.parametrize("cloud_origin", [True, False])
def test_cli_reset_and_undo_preserve_origin(tmp_path, test_video, cloud_origin, monkeypatch):
    from click.testing import CliRunner
    from moviestar.cli import cli
    from moviestar.spec import load_spec, save_spec

    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    def invoke(*args):
        result = runner.invoke(cli, list(args))
        assert result.exit_code == 0, result.output
        return json.loads(result.stdout)

    invoke("load", test_video, "--as", "speaker", "--no-transcribe", "--no-frames")
    invoke("trim", "--from", "0.25", "--to", "1.5")
    before = load_spec()
    if cloud_origin:
        before["provenance"] = copy.deepcopy(ORIGIN)
        save_spec(before, record=False)

    reset = invoke("spec", "--reset")
    reset_spec = load_spec()
    assert reset["spec"] == reset_spec
    assert reset_spec["sources"][0]["operations"] == []
    if cloud_origin:
        assert reset_spec["provenance"] == ORIGIN
    else:
        assert "provenance" not in reset_spec

    invoke("undo")
    assert load_spec() == before


@pytest.mark.parametrize("origin_change", ["replace", "remove", "add"])
@pytest.mark.parametrize("instructions_change", [True, False])
@pytest.mark.parametrize("dry_run", [True, False])
def test_cli_spec_replacement_rejects_origin_changes(
    tmp_path, test_video, monkeypatch, origin_change, instructions_change, dry_run
):
    from click.testing import CliRunner
    from moviestar.cli import cli
    from moviestar.spec import load_spec, save_spec

    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    for args in [
        ["load", test_video, "--as", "speaker", "--no-transcribe", "--no-frames"],
        ["trim", "--from", "0.25", "--to", "1.5"],
    ]:
        result = runner.invoke(cli, args)
        assert result.exit_code == 0, result.output
    before = load_spec()
    if origin_change != "add":
        before["provenance"] = copy.deepcopy(ORIGIN)
        save_spec(before, record=False)
    replacement = copy.deepcopy(before)
    if instructions_change:
        replacement["sources"][0]["operations"] = []
    if origin_change == "remove":
        del replacement["provenance"]
    else:
        replacement["provenance"] = {**ORIGIN, "base_revision_id": "00000000-0000-4000-8000-000000000099"}
    path = tmp_path / "replacement.json"
    path.write_text(json.dumps(replacement))
    spec_file = tmp_path / "moviestar" / "spec.json"
    before_bytes = spec_file.read_bytes()
    result = runner.invoke(cli, ["spec", "--edit", str(path), *(["--dry-run"] if dry_run else [])])
    assert result.exit_code != 0
    error = json.loads(result.stdout)
    assert error["reason"] == "provenance_mismatch"
    assert "provenance" in error["error"]
    assert "moviestar spec" in error["hint"]
    assert spec_file.read_bytes() == before_bytes
    assert load_spec() == before


@pytest.mark.parametrize("cloud_origin", [True, False])
def test_cli_spec_replacement_with_same_origin_is_undoable(
    tmp_path, test_video, monkeypatch, cloud_origin
):
    from click.testing import CliRunner
    from moviestar.cli import cli
    from moviestar.spec import load_spec, save_spec

    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    def invoke(*args):
        result = runner.invoke(cli, list(args))
        assert result.exit_code == 0, result.output
        return json.loads(result.stdout)

    invoke("load", test_video, "--as", "speaker", "--no-transcribe", "--no-frames")
    invoke("trim", "--from", "0.25", "--to", "1.5")
    before = load_spec()
    if cloud_origin:
        before["provenance"] = copy.deepcopy(ORIGIN)
        save_spec(before, record=False)
    replacement = copy.deepcopy(before)
    replacement["sources"][0]["operations"] = []
    path = tmp_path / "replacement.json"
    path.write_text(json.dumps(replacement))
    assert invoke("spec", "--edit", str(path), "--dry-run")["status"] == "would_replace"
    assert load_spec() == before
    assert invoke("spec", "--edit", str(path))["status"] == "replaced"
    assert load_spec()["sources"][0]["operations"] == []
    invoke("undo")
    assert load_spec() == before
