"""History-copy optimization preserves full validation and caller isolation."""
import copy
import json

import pytest

import moviestar.spec as module
from moviestar.spec import append_revision, empty_spec, pop_revision, validate_spec, SpecValidationError, REVISION_LIMIT


class JournalMustNotBeCopied(list):
    def __deepcopy__(self, memo):
        raise AssertionError('A discarded journal was traversed by deepcopy')


def document_with_history(length):
    spec = empty_spec([{'id': 'camera', 'path': '/camera.mp4'}])
    for i in range(length):
        spec = append_revision(spec, {**spec, 'composition_audio_from': 'camera' if i % 2 == 0 else None}, f'edit {i}')
    return spec


def test_append_ignores_incoming_history_without_copying_it():
    before = document_with_history(2)
    incoming = {**before, 'composition_audio_from': 'camera',
                'revisions': JournalMustNotBeCopied([{'command': 'foreign history', 'changed': {'sources': []}}])}
    original = json.dumps(incoming)
    result = append_revision(before, incoming, 'new edit')
    validate_spec(result)
    assert json.dumps(incoming) == original
    assert [r['command'] for r in result['revisions']] == ['edit 0', 'edit 1', 'new edit']
    restored, _ = pop_revision(result)
    assert restored == before


def test_validation_checks_all_prior_states_without_copying_journal_container(monkeypatch):
    spec = document_with_history(REVISION_LIMIT)
    spec['revisions'] = JournalMustNotBeCopied(spec['revisions'])
    original = json.dumps(spec)
    checked = []
    real_validate = module.validate_spec

    def checking(candidate):
        checked.append(candidate['composition_audio_from'])
        real_validate(candidate)

    monkeypatch.setattr(module, 'validate_spec', checking)
    real_validate(spec)
    assert checked == ['camera' if i % 2 else None for i in range(REVISION_LIMIT - 1, -1, -1)]
    assert json.dumps(spec) == original


@pytest.mark.parametrize('index', [0, REVISION_LIMIT // 2, REVISION_LIMIT - 1])
def test_invalid_history_is_still_rejected_at_any_depth_without_mutation(index):
    spec = document_with_history(REVISION_LIMIT)
    spec['revisions'][index]['changed']['sources'] = [{'id': 'broken'}]
    original = copy.deepcopy(spec)
    with pytest.raises(SpecValidationError, match=rf'revisions\[{index}\].*invalid prior state'):
        validate_spec(spec)
    assert spec == original


def overlay(text):
    return {'id': 'title', 'track': 'titles', 'kind': 'manual',
            'timing': {'space': 'result', 'from': '0:00:00.000', 'to': '0:00:01.000'},
            'text': text, 'position': {'preset': 'top', 'coordinate_space': 'canvas'}, 'style': {}}


def test_append_current_state_and_old_and_new_history_are_mutually_isolated():
    first = empty_spec([{'id': 'camera', 'path': '/camera.mp4'}])
    first['overlays'] = [overlay('first')]
    before = append_revision(first, {**first, 'overlays': [overlay('second')]}, 'second')
    after = copy.deepcopy(before)
    after['overlays'][0]['text'] = 'third'
    before_copy, after_copy = copy.deepcopy(before), copy.deepcopy(after)
    result = append_revision(before, after, 'third')
    validate_spec(result)
    result['overlays'][0]['text'] = 'changed current'
    result['sources'][0]['path'] = '/changed.mp4'
    result['revisions'][0]['changed']['overlays'][0]['text'] = 'changed oldest'
    result['revisions'][-1]['changed']['overlays'][0]['text'] = 'changed newest'
    assert before == before_copy
    assert after == after_copy
    result_copy = copy.deepcopy(result)
    before['overlays'][0]['text'] = 'caller changed prior'
    after['overlays'][0]['text'] = 'caller changed incoming'
    before['revisions'][0]['changed']['overlays'][0]['text'] = 'caller changed history'
    assert result == result_copy


@pytest.mark.parametrize('changed', [False, True])
def test_full_history_noop_and_rollover_keep_isolation_and_exact_undo(changed):
    before = document_with_history(REVISION_LIMIT)
    after = {**before, 'composition_audio_from': 'camera' if changed else before['composition_audio_from']}
    result = append_revision(before, after, 'new edit')
    validate_spec(result)
    assert len(result['revisions']) == REVISION_LIMIT
    if changed:
        assert result['revisions'][0]['command'] == 'edit 1'
        restored, _ = pop_revision(result)
        assert restored == {**before, 'revisions': before['revisions'][1:]}
    else:
        assert result == before
    result['revisions'][0]['command'] = 'caller mutation'
    assert before['revisions'][0]['command'] == 'edit 0'


@pytest.mark.parametrize('history_only', [False, True])
def test_save_rereads_external_changes_including_history_only_changes(tmp_path, history_only):
    initial = empty_spec([{'id': 'camera', 'path': '/camera.mp4'}])
    module.save_spec(initial, cwd=tmp_path, record=False)
    stale = module.load_spec(tmp_path)
    external = module.save_spec({**initial, 'composition_audio_from': 'camera'}, cwd=tmp_path, command='external on')
    if history_only:
        external = module.save_spec({**external, 'composition_audio_from': None}, cwd=tmp_path, command='external off')
        assert {k:v for k,v in external.items() if k != 'revisions'} == {k:v for k,v in initial.items() if k != 'revisions'}
    stale['overlays'] = [overlay('local edit')]
    saved = module.save_spec(stale, cwd=tmp_path, command='local edit')
    assert saved['revisions'][:-1] == external['revisions']
    restored, _ = pop_revision(saved)
    assert restored == external
    assert module.load_spec(tmp_path) == saved


def test_save_rejects_invalid_external_history_without_overwriting_it(tmp_path):
    spec = empty_spec([{'id': 'camera', 'path': '/camera.mp4'}])
    module.save_spec(spec, cwd=tmp_path, record=False)
    stale = module.load_spec(tmp_path)
    broken = document_with_history(2)
    broken['revisions'][0]['changed']['sources'] = [{'id': 'broken'}]
    path = tmp_path/'moviestar/spec.json'
    path.write_text(json.dumps(broken))
    before = path.read_bytes()
    stale['overlays'] = [overlay('local edit')]
    with pytest.raises(SpecValidationError, match='invalid prior state'):
        module.save_spec(stale, cwd=tmp_path, command='local edit')
    assert path.read_bytes() == before
