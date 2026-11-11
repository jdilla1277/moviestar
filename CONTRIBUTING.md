# Contributing to Moviestar

Thank you for helping make video editing easier for agents.

## Before you start

For bugs and small improvements, open an issue or a focused pull request. For a
large feature or a change to Moviestar's command or JSON contracts, open an
issue first so maintainers and contributors can agree on the surface before a
large implementation lands.

Do not put security-sensitive details in a public issue. Follow
[`SECURITY.md`](SECURITY.md) instead.

## Make a change

1. Create a feature branch from `main`.
2. Add a failing test that demonstrates the change.
3. Implement the smallest complete fix.
4. Run `bin/test` while iterating and `bin/preflight --slow` before requesting
   review.
5. Open a focused pull request with the two-sentence plain-language opener
   described in [`CLAUDE.md`](CLAUDE.md).

Moviestar requires Python 3.10 or newer and FFmpeg plus ffprobe on `PATH`.
Create a development environment with:

```bash
python3 -m venv app/.venv
source app/.venv/bin/activate
python -m pip install -e './app[transcribe]' pytest
```

### Conductor

The shared Conductor settings create `app/.venv` when a workspace is created.
The Run menu provides the fast test suite and the full preflight check; FFmpeg
and ffprobe must still be installed on the host.

### Media-test CI setup

The fast and full media suites use `ubuntu-26.04`, rather than the moving
`ubuntu-latest` label. `python bin/setup-ci-ffmpeg` first checks the tools on
PATH. A usable pair must have matching FFmpeg/ffprobe major versions, version
6 or newer, the required filters (including drawtext and libass), and the
encoders used by our generated-media tests. Patch versions follow Ubuntu's
maintained archive; exact versions and executable paths appear in each run.
The [runner's software inventory](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2604-Readme.md)
does not currently list FFmpeg, so the helper also supports a missing install.

When tools are missing or unsuitable, setup makes one attempt with Ubuntu's
official `ffmpeg` package and `--no-install-recommends`. Apt update has a
45-second limit and installation a 120-second limit, with no download retries,
15-second network/lock waits, and root-owned GNU timeout cleanup. The helper's
overall budget is 210 seconds inside a four-minute workflow step. A failed
mirror or unsuitable build fails setup with diagnostics, preserving the
remaining job budget instead of retrying tests or reducing coverage.

Setup verifies filter/encoder listings and renders a short video with the
bundled Inter font, drawtext, ASS subtitles, H.264, and AAC, then probes the
result. `python bin/setup-ci-ffmpeg --check-only` runs these checks without
installing anything. The test suite covers failed downloads, timeouts, and
unsuitable tools; on GitHub's Linux runner it also exercises a real privileged
command timeout. This helper is CI tooling; local installation still follows
the [installation guide](docs/installation.md).

## Contribution terms

Moviestar is licensed under the Apache License 2.0. By intentionally submitting
a contribution for inclusion in this project, you agree that it may be
distributed under that license and represent that you have the right to submit
it. Clearly identify third-party code, data, media, fonts, or other assets and
include their provenance and license terms.

All merges are performed by a human repository owner after review.
