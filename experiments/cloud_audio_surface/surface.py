#!/usr/bin/env python3
"""Canned Cloud audio interface probe; no engine, persistence or network."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

import click

CONTRACT = "cloud-editing-v1"
PROJECT = "00000000-0000-4000-8000-000000000010"
REVISIONS = [f"00000000-0000-4000-8000-{n:012}" for n in range(101, 105)]
HERE = Path(__file__).parent
LIMIT = "Fixed independent fixtures only. No Cloud save, engine validation, authorization, render, file delivery or listening is exercised."
RETRY = "Retain the exact request, expected_base_revision and idempotency_key before sending. Retry those unchanged after an uncertain response."
ROUTING = [
    dict(scene_id="scene_a", result_range_ms=[0, 5000], source_id="speaker", source_range_ms=[10000, 15000]),
    dict(scene_id="scene_b", result_range_ms=[5000, 10000], source_id="camera", source_range_ms=[20000, 25000]),
]
OPERATIONS = [dict(type="audio_source", gain_db=-6.0), dict(type="audio_source", muted=True), dict(type="undo")]
REQUESTS = [dict(contract_version=CONTRACT, expected_base_revision=REVISIONS[i], operation=op) for i, op in enumerate(OPERATIONS)]
NOOP_INDEX = 3
REQUESTS.append(dict(contract_version=CONTRACT, expected_base_revision=REVISIONS[1],
                     operation=dict(type="audio_source", gain_db=-6.0)))


def mix(index):
    return dict(source_audio=dict(gain_db=0.0 if index == 0 else -6.0,
                                 muted=index == 2, fade_in=0.0, fade_out=0.0), tracks=[])


def resolved(index):
    return dict(audio_mix=mix(index), routing=copy.deepcopy(ROUTING), result_duration_ms=10000,
                undo_remaining=[0, 1, 2, 1][index], loudness=None,
                loudness_status="not_measured", render_availability="not_established")


def revision(index):
    return dict(id=REVISIONS[index], contract_version=CONTRACT, engine_version="fixture-engine-not-qualified",
                base_revision=None if index == 0 else REVISIONS[index - 1],
                spec=dict(audio_mix=mix(index)), resolved=resolved(index),
                dependency_manifest=[dict(source_id=source, reference_kind="account_asset",
                                          asset_id=f"00000000-0000-4000-8000-{asset:012}",
                                          reference_id=f"00000000-0000-4000-8000-{ref:012}",
                                          transcript_version_id=None)
                                     for source, asset, ref in (("speaker", 21, 31), ("camera", 22, 32))])


@click.group(help="MOCK ONLY: proposed Cloud audio through shared 'moviestar cloud edits show/validate/apply'. Each invocation is an independent canned example. Read the mix, validate gain/mute, apply with a retained key, reopen exact revisions, undo. No working Cloud client or server is installed.")
@click.option("--surface", type=click.Choice(["cli", "api", "mcp"]), default="cli", show_default=True,
              help="Print proposed transport with the same response; does not call that transport.")
@click.option("--scenario", type=click.Choice(["normal", "retry", "lost-response", "stale", "changed-key", "unauthorized", "disabled"]), default="normal", show_default=True,
              help="Select a fixed outcome, not an actual injected service failure.")
@click.pass_context
def cli(ctx, surface, scenario):
    ctx.obj = dict(surface=surface, scenario=scenario)


def emit(ctx, command, response, status=200, body=None, key=None, selected=None, saved=0, uncertain=False):
    transport = dict(surface=ctx.obj["surface"])
    if ctx.obj["surface"] == "api":
        route = f"/revisions/{selected}" if selected else "/edit"
        route = {"validate": "/edit/validate", "apply": "/edit/mutations"}.get(command, route)
        transport.update(method="GET" if command == "show" else "POST",
                         path=f"/api/v1/account/cloud/projects/{PROJECT}{route}")
        if body is not None:
            transport["body"] = body
        if key:
            transport["headers"] = {"Idempotency-Key": key}
    elif ctx.obj["surface"] == "mcp":
        transport.update(tool={"show": "moviestar_get_edit", "validate": "moviestar_validate_edit", "apply": "moviestar_apply_edit"}[command],
                         arguments=dict(project_id=PROJECT))
        if body is not None:
            transport["arguments"]["request"] = body
        if key:
            transport["arguments"]["idempotency_key"] = key
        if selected:
            transport["arguments"]["revision_id"] = selected
    effects = dict(saved_revisions=saved, activity_events=saved, processing_jobs=0, customer_usage=0)
    envelope = dict(mock=True, fixture_limit=LIMIT,
                    fixture_context="Independent read fixture current is 103, or 104 when reading undo revision 104. Recovered undo also has current 104; gain recovery has current 103. No state carries between invocations.",
                    transport=transport, http_status=status, response=response, effects=None if uncertain else effects)
    if uncertain:
        envelope["fixture_expected_effects"] = effects
        envelope["fixture_effects_note"] = "Omniscient canned scenario only; the client does not know whether a save occurred until recovery."
    click.echo(json.dumps(envelope, indent=2))
    if status >= 400:
        raise click.exceptions.Exit(1)


def failure(ctx, command, code, hint, status=409, error_fields=None, **kwargs):
    emit(ctx, command, dict(request_id="mock-audio-request", error=dict(code=code, message=hint, details=error_fields or {},
                                      retryable=code == "edits_unavailable")), status=status, **kwargs)


@cli.command()
@click.option("--revision", "selected", type=click.Choice(REVISIONS), help="Read exact immutable fixture snapshot; does not move current pointer.")
@click.pass_context
def show(ctx, selected):
    """Read saved mix intent and resolved scene routing; no writes or analysis.

    Fixture current is revision 103 (muted, -6 dB). Revision 101 is the baseline;
    102 lowers routed source gain; 104 is undo of mute. Source controls adjust
    the finished routed dialogue layer, not a camera ID. spec is an audio-only
    excerpt here; production reads must return the complete portable edit.
    Loudness is unmeasured; metadata is not listening acceptance.
    """
    if ctx.obj["scenario"] == "unauthorized":
        failure(ctx, "show", "not_found", "Check project access.", status=404, selected=selected)
    index = REVISIONS.index(selected) if selected else 2
    emit(ctx, "show", dict(project_id=PROJECT, current_revision=REVISIONS[max(2, index)], revision=revision(index)), selected=selected)


def read_request(ctx, command, path):
    try:
        body = json.loads(path.read_text())
    except (ValueError, OSError):
        failure(ctx, command, "mock_fixture_required", "Use a JSON file from requests/.", status=422)
    for index, expected in enumerate(REQUESTS):
        if body == expected:
            return index, body
    failure(ctx, command, "mock_fixture_required", "Only requests/gain.json, mute.json, undo.json and noop.json are canned. This is not production validation.", status=422)


def changed_paths(index):
    return [["audio_mix.source_audio.gain_db"], ["audio_mix.source_audio.muted"], ["audio_mix"]][index]


@cli.command()
@click.option("--request", "path", type=click.Path(path_type=Path), required=True)
@click.pass_context
def validate(ctx, path):
    """Dry-run a canned request: no save, undo entry, Activity or media job.

    gain.json sets routed-source gain to -6 dB; mute.json mutes the same layer.
    Neither selects a different speaker or changes result-time placement.
    Actual engine validation and permissions remain implementation work.
    """
    index, body = read_request(ctx, "validate", path)
    if ctx.obj["scenario"] == "unauthorized":
        failure(ctx, "validate", "not_found", "Check project access.", status=404, body=body)
    if ctx.obj["scenario"] == "disabled":
        failure(ctx, "validate", "edits_unavailable", "Read saved revisions; new validation/admission is unavailable.", status=503, body=body)
    noop = index == NOOP_INDEX
    current_index, candidate_index = (1, 1) if noop else (index, index + 1)
    emit(ctx, "validate", dict(status="would_apply", normalized_request=body,
                               changes=not noop, changed_fields=[] if noop else ["audio_mix"],
                               changed_paths=[] if noop else changed_paths(index),
                               current_undo_remaining=resolved(current_index)["undo_remaining"],
                               projected_resolved=resolved(candidate_index),
                               hint="Apply this exact request with a retained idempotency key. No loudness measurement or render was requested."), body=body)


@cli.command()
@click.option("--request", "path", type=click.Path(path_type=Path), required=True)
@click.option("--idempotency-key", required=True, help="1–128 ASCII letters, digits, underscore or hyphen. " + RETRY)
@click.pass_context
def apply(ctx, path, idempotency_key):
    """Save a canned source-gain/mute change or undo one complete command.

    gain.json: baseline 101 -> 102 at -6 dB; mute.json: 102 -> 103 muted;
    undo.json: 103 -> new revision 104 restores all prior mix fields, retaining
    -6 dB and unmuting. Undo creates a revision; it does not rewind Cloud history.
    noop.json reasserts -6 dB at 102: unchanged, no new revision/journal/event.
    Retry recovers that original unchanged revision 102 even when current is 103.
    Saves are unmetered and start no render. Use the shared delivery path after
    saving, then listen to the actual output. This mock cannot render or play.
    """
    if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", idempotency_key) is None:
        failure(ctx, "apply", "invalid_request", "Use an idempotency key of 1–128 ASCII letters, digits, underscore or hyphen.",
                status=422, error_fields=dict(field="idempotency_key"))
    index, body = read_request(ctx, "apply", path)
    noop = index == NOOP_INDEX
    scenario = ctx.obj["scenario"]
    kwargs = dict(body=body, key=idempotency_key)
    errors = dict(stale=("revision_conflict", "Reopen current revision and deliberately rebase; do not silently retry against latest."),
                  **{"changed-key": ("idempotency_conflict", "Use the original body/base/key to recover its result; a changed edit needs a new key.")},
                  unauthorized=("not_found", "Check project and input asset access."),
                  disabled=("edits_unavailable", "Authorized saved reads remain available; new mutation admission is disabled."))
    if scenario in errors:
        code, hint = errors[scenario]
        fields = dict(expected_base_revision=body["expected_base_revision"], current_revision=REVISIONS[3] if index == 2 else REVISIONS[2]) if scenario == "stale" else None
        failure(ctx, "apply", code, hint, status=404 if scenario == "unauthorized" else 503 if scenario == "disabled" else 409, error_fields=fields, **kwargs)
    if scenario == "lost-response":
        emit(ctx, "apply", dict(outcome="unknown", idempotency_key=idempotency_key, request=body, hint=RETRY), status=504, saved=0 if noop else 1, uncertain=True, **kwargs)
    replayed = scenario == "retry"
    result_index = 1 if noop else index + 1
    emit(ctx, "apply", dict(status="unchanged" if noop else "committed", changes=not noop,
                            project_id=PROJECT, idempotency_key=idempotency_key,
                            request_sha256=hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                            expected_base_revision=body["expected_base_revision"], replayed=replayed,
                            revision=revision(result_index), current_revision=REVISIONS[max(2, result_index)] if replayed else REVISIONS[result_index],
                            changed_fields=[] if noop else ["audio_mix"], changed_paths=[] if noop else changed_paths(index),
                            hint="Reopen this exact revision on another surface. Request preview/final through shared delivery, then verify the saved file and listen."),
         status=200 if replayed or noop else 201, saved=0 if replayed or noop else 1, **kwargs)


if __name__ == "__main__":
    cli()
