"""Portable origin metadata never changes editing/undo meaning."""
import copy
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
