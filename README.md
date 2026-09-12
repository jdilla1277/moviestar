# MovieStar

MovieStar is an open-source video editing CLI built for AI agents. It turns
structured commands into deterministic, non-destructive edits and renders them
with FFmpeg.

An agent can load and transcribe one or more videos, search spoken content,
compose scenes, animate layouts and camera crops, mix audio, generate captions,
and export finished clips. Commands return structured JSON so an agent can
inspect each result and decide what to do next.

## Quick start

Install FFmpeg and Python 3.10 or newer, then:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install moviestar
moviestar doctor
moviestar --help
```

Give your coding agent a video-editing task and tell it to use the `moviestar`
CLI. MovieStar's help output is designed as the operational briefing; every
editing step remains visible as structured data and source media is never
modified.

The detailed command guide is in [`app/README.md`](app/README.md).

## Develop

```bash
python3 -m venv app/.venv
source app/.venv/bin/activate
python -m pip install -e app pytest
bin/preflight --slow
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the contribution workflow and
[`SECURITY.md`](SECURITY.md) for private vulnerability reporting.

## License

MovieStar v0.7.0 and later is licensed under the
[Apache License 2.0](LICENSE). Versions through v0.6.0 were released under the
Business Source License 1.1; this repository's license does not retroactively
relicense those historical versions.

The bundled Inter font files retain their own SIL Open Font License in
[`app/src/moviestar/fonts/OFL-LICENSE.txt`](app/src/moviestar/fonts/OFL-LICENSE.txt).
See [`TRADEMARKS.md`](TRADEMARKS.md) for the separate branding policy.
