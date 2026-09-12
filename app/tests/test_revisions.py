"""Stage 7: revision-based undo (the resolved-project-compiler
proposal's last stage).

Every spec-mutating command appends one revision — the command name
plus the prior values of exactly the fields it touched. Bare `undo`
pops the last revision atomically, so coupled edits (composition +
reconciled motion, captions recipe + cue cache) restore together.
The four independent history stacks this replaces produced the
motion-undo footgun that opened this whole arc.
"""

import json

import pytest

from moviestar.spec import (
    REVISION_FIELDS,
    REVISION_LIMIT,
    SpecValidationError,
    append_revision,
    empty_spec,
    pop_revision,
    validate_spec,
    load_spec,
)


def _spec():
    return empty_spec([{"id": "holden", "path": "/h.mp4"}])


class TestAppendRevision:
    def test_records_only_changed_fields_with_prior_values(self):
        before = _spec()
        after = {**before, "overlays": [
            {"id": "manual_0001", "track": "titles", "kind": "manual",
             "timing": {"space": "result", "from": "0:00:00.000",
                        "to": "0:00:01.000"}, "text": "T",
             "position": {"preset": "top", "coordinate_space": "canvas"},
             "style": {}},
        ]}
        out = append_revision(before, after, "overlays add")
        validate_spec(out)
        assert len(out["revisions"]) == 1
        revision = out["revisions"][0]
        assert revision["command"] == "overlays add"
        assert set(revision["changed"]) == {"overlays"}
        assert revision["changed"]["overlays"] == []

    def test_coupled_fields_land_in_one_revision(self):
        before = _spec()
        after = {
            **before,
            "composition": None,
            "motion": {"version": 1, "scenes": []},
        }
        # Simulate a scenes set that also reconciles motion.
        after = dict(after)
        after["composition_canvas"] = {
            "preset": "short", "width": 1080, "height": 1920,
            "aspect_ratio": "9:16",
        }
        after["motion"] = {"version": 1, "scenes": [
            {"scene": "intro", "slots": []},
        ]}
        out = append_revision(before, after, "scenes set")
        revision = out["revisions"][0]
        assert set(revision["changed"]) == {"composition_canvas", "motion"}
        assert revision["changed"]["motion"] == {"version": 1, "scenes": []}

    def test_no_change_appends_nothing(self):
        before = _spec()
        out = append_revision(before, dict(before), "status")
        assert out["revisions"] == []

    def test_revisions_are_capped_fifo(self):
        spec = _spec()
        for i in range(REVISION_LIMIT + 5):
            after = {**spec, "composition_audio_from": f"src_{i}"}
            spec = append_revision(spec, after, f"cmd_{i}")
        assert len(spec["revisions"]) == REVISION_LIMIT
        assert spec["revisions"][-1]["command"] == (
            f"cmd_{REVISION_LIMIT + 4}"
        )
        assert spec["revisions"][0]["command"] == "cmd_5"

    def test_tracked_fields_cover_every_history_family(self):
        # The revision model replaces these stacks; every family they
        # covered must be tracked.
        for field in (
            "sources", "composition", "composition_audio_from", "composition_canvas",
            "composition_authored_as", "motion", "overlays", "audio_mix",
            "captions",
        ):
            assert field in REVISION_FIELDS

    def test_replacement_uses_existing_journal_not_incoming_journal(self):
        before = _spec()
        before = append_revision(
            before,
            {**before, "composition_audio_from": "holden"},
            "concat",
        )
        incoming = _spec()
        incoming["sources"][0]["operations"] = [
            {"type": "trim", "from": "0:00:00.000", "to": "0:00:01.000"}
        ]
        incoming["revisions"] = []

        out = append_revision(before, incoming, "spec")

        assert [r["command"] for r in out["revisions"]] == ["concat", "spec"]
        assert "sources" in out["revisions"][-1]["changed"]


class TestPopRevision:
    def test_pop_restores_exactly_the_touched_fields(self):
        before = _spec()
        after = {**before, "composition_audio_from": "holden"}
        spec = append_revision(before, after, "concat")
        restored, popped = pop_revision(spec)
        validate_spec(restored)
        assert popped["command"] == "concat"
        assert restored["composition_audio_from"] is None
        assert restored["revisions"] == []

    def test_two_commands_pop_in_reverse_order(self):
        s0 = _spec()
        s1 = append_revision(
            s0, {**s0, "composition_audio_from": "holden"}, "concat"
        )
        s2 = append_revision(
            s1,
            {**s1, "motion": {"version": 1, "scenes": [
                {"scene": "x", "slots": []},
            ]}},
            "scenes motion set",
        )
        r1, p1 = pop_revision(s2)
        assert p1["command"] == "scenes motion set"
        assert r1["motion"] == {"version": 1, "scenes": []}
        assert r1["composition_audio_from"] == "holden"
        r0, p0 = pop_revision(r1)
        assert p0["command"] == "concat"
        assert r0["composition_audio_from"] is None

    def test_pop_on_empty_revisions_raises(self):
        with pytest.raises(SpecValidationError):
            pop_revision(_spec())

    def test_validation_rejects_an_invalid_stored_prior_state(self):
        spec = _spec()
        spec["revisions"] = [{
            "command": "overlays add",
            "changed": {"overlays": [{"id": "broken"}]},
        }]

        with pytest.raises(SpecValidationError, match=r"revisions\[0\].*prior state"):
            validate_spec(spec)


class TestLegacyHistoryBoundary:
    def test_new_specs_do_not_write_legacy_global_histories(self):
        spec = _spec()
        for field in (
            "composition_history", "overlays_history", "audio_mix_history",
            "motion_history", "captions_history",
        ):
            assert field not in spec

    def test_old_spec_error_explains_exact_port_transform(self, tmp_path):
        project_dir = tmp_path / "moviestar"
        project_dir.mkdir()
        legacy = _spec()
        legacy["overlays_history"] = [[]]
        (project_dir / "spec.json").write_text(json.dumps(legacy))

        with pytest.raises(SpecValidationError) as exc_info:
            load_spec(tmp_path)

        message = str(exc_info.value)
        assert "current edit" in message
        assert "undo history" in message
        assert "del(.composition_history" in message
        assert '.revisions = []' in message
        assert "spec.pre-stage7.json" in message
        assert "mv moviestar/spec.stage7.json moviestar/spec.json" in message
