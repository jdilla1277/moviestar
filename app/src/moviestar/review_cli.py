"""CLI for artifact-bound review reports; all judgments are caller-reported."""

from functools import wraps
import json
from pathlib import Path

import click

from moviestar.review_evidence import DIMENSIONS, STATUSES, load_report, record_entry, view_report
from moviestar.timecodes import parse_timecode


def _guard(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            click.echo(json.dumps({
                "command": f"review {click.get_current_context().command.name}",
                "error": str(exc),
                "hint": "Use 'moviestar review show --report FILE' to inspect evidence. "
                "Use 'moviestar export --review-report' or 'watch ... --review-report' "
                "for a new artifact/revision. Run 'review record --help' for coverage options.",
            }, indent=2))
            raise click.exceptions.Exit(1)
    return wrapped


def _report_option(function):
    return click.option("--report", required=True,
                        type=click.Path(exists=True, dir_okay=False, resolve_path=True, path_type=Path),
                        help="Review JSON produced by export/watch --review-report.")(function)


@click.group()
def review():
    """Record and inspect separate review evidence for an exact rendered file.

    First render with export/watch --review-report. Technical checks, source
    alignment, visual inspection, motion, and listening start unreviewed.
    These commands record caller-reported judgments; they do not watch, listen,
    detect editorial defects, authenticate a reviewer, or approve delivery.
    """


@review.command("show")
@_report_option
@_guard
def show(report: Path):
    """Show historical evidence, current validity, coverage, and next steps.

    Read-only. Checks the exact artifact bytes and recorded project state, even
    outside the original cwd. Changed/missing content is stale; old judgments
    remain in history. Sampled instants never establish continuous coverage.
    """
    click.echo(json.dumps(view_report(load_report(report)), indent=2, allow_nan=False))


@review.command("record")
@_report_option
@click.option("--dimension", type=click.Choice(DIMENSIONS), required=True)
@click.option("--status", type=click.Choice(STATUSES), required=True)
@click.option("--actor", required=True, help="Who actually reviewed: e.g. human:editor or agent:session.")
@click.option("--method", required=True, help="Actual review/check method, not a proposed action.")
@click.option("--full", is_flag=True, help="Caller reports reviewing the entire artifact duration.")
@click.option("--from", "from_tc", default=None, help="Start on this output file's clock, starting at zero.")
@click.option("--to", "to_tc", default=None, help="Exclusive end on this output file's clock.")
@click.option("--at", multiple=True, help="Sampled instant on the output file's clock. Repeatable; excludes motion/listening.")
@click.option("--note", default="", help="Judgment/context; required for unknown or unavailable, including next step.")
@click.option("--limitations", default="", help="Limits of the method/perception, beyond the recorded coverage.")
@click.option("--evidence", multiple=True, type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="Optional local evidence file; its exact bytes are pinned to this entry.")
@click.option("--supersedes", multiple=True, help="Prior entry ID in this dimension explicitly replaced by this judgment.")
@click.option("--dry-run", is_flag=True, help="Validate without changing the report or creating locks/temp files.")
@_guard
def record(report, dimension, status, actor, method, full, from_tc, to_tc, at,
           note, limitations, evidence, supersedes, dry_run):
    """Append evidence without changing media or the edit spec.

    For passed/rejected judgments choose --full, a --from/--to range, or repeated
    --at samples. All times refer to the rendered artifact, not source/project
    time: a watch clip beginning at project 12s starts at artifact 0s. Ranges are
    half-open [from,to); samples cannot equal the artifact end. Still samples
    cannot prove motion or listening. Unknown/unavailable may omit coverage.

    Explicit --supersedes keeps earlier judgments in history. A pass never
    silently overrides an active rejection. Changed media/project state refuses
    new entries; render a new report. Full review of one dimension never approves
    another. Judgments and actor names are caller assertions, not verification.
    """
    entry, view = record_entry(
        report, dry_run=dry_run, dimension=dimension, status=status, actor=actor, method=method,
        full=full, start=parse_timecode(from_tc) if from_tc is not None else None,
        end=parse_timecode(to_tc) if to_tc is not None else None,
        at=tuple(parse_timecode(value) for value in at), note=note, limitations=limitations,
        evidence=evidence, supersedes=supersedes,
    )
    if dry_run:
        planned = {key: value for key, value in entry.items() if key not in {"id", "created_at"}}
        result = {"status": "would_record", "dry_run": True, "would_write_to": str(report),
                  "would_record": planned}
    else:
        result = {"status": "recorded", "out": str(report), "entry": entry,
                  "dimensions": view["dimensions"], "next_steps": view["next_steps"],
                  "hint": f"Inspect all dimensions with 'moviestar review show --report {report}'."}
    click.echo(json.dumps(result, indent=2, allow_nan=False))
