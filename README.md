<div align="center">

# Moviestar

**Edit video by telling your agent what you want.**

Open source · Runs locally · Works with all agents

[Website](https://trymoviestar.com) · [Documentation](docs/README.md) · [PyPI](https://pypi.org/project/moviestar/) · [Contributing](CONTRIBUTING.md)

[![PyPI](https://img.shields.io/pypi/v/moviestar?label=PyPI&color=183a24)](https://pypi.org/project/moviestar/)
[![Python](https://img.shields.io/pypi/pyversions/moviestar?color=4f7c56)](https://pypi.org/project/moviestar/)
[![License](https://img.shields.io/pypi/l/moviestar?color=bf9b30)](LICENSE)

</div>

Moviestar is an open-source video editing CLI built for AI agents. Describe the
edit you want inside Claude Code, Codex, or the agent you already use, and
Moviestar gives it the tools to understand your footage, make precise edits,
burn in captions, mix audio, and render a finished video with FFmpeg.

## See Moviestar in action

<div align="center">

[![Watch Moviestar edit a video](https://img.youtube.com/vi/8CbBgtiSlNw/maxresdefault.jpg)](https://youtu.be/8CbBgtiSlNw)

*A video about how Moviestar works, edited by Moviestar. [Watch the demo →](https://youtu.be/8CbBgtiSlNw)*

</div>

## You direct. Your agent edits.

You say what you want. Your agent makes the edit by running `moviestar`
commands, and Moviestar answers each command with structured JSON the agent can
read, verify, and act on.

| | |
| --- | --- |
| **Sees and hears your footage**<br>Transcripts, scene changes, loud and silent moments, and frames on demand help your agent understand a video before changing it. | **Cuts by words or by time**<br>Keep a precise time range or find and cut the sentence where someone restarts—without eyeballing a scrubber. |
| **Builds reversible edits**<br>Every edit is a step in a plan, not a change to the source file. Inspect it, undo it, and render it again at any time. | **Captions, layouts, and audio**<br>Create word-by-word captions, compose scenes and picture-in-picture layouts, animate camera crops, and mix a soundtrack. |
| **Checks before it commits**<br>Validate the plan, inspect a single frame in seconds, or dry-run the export before spending time on a full render. | **Renders deterministically**<br>The same inputs and edit plan produce the same output—no hidden model calls and no randomness in the rendering path. |

## Quick start

Moviestar requires Python 3.10 or newer plus FFmpeg and ffprobe on `PATH`.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade moviestar
moviestar doctor
```

Then attach a video to your agent and try:

> Install the moviestar package from PyPI and use it to edit the attached
> video. Find the strongest 30–60 second moment, cut it into a vertical clip
> with word-by-word captions, and show me the proposed edit before you render
> it.

Your agent drives the full loop:

1. **Understand** — load the video, transcribe speech, and inspect frames.
2. **Edit** — find moments by transcript or timecode and build a non-destructive
   edit plan.
3. **Verify** — check the spec, screenshots, and preview clips.
4. **Render** — export the finished video from the verified plan.

The [getting-started guide](docs/getting-started.md) walks through installation
and a first verified edit. For the complete command surface, see the
[CLI reference](app/README.md).

## Documentation

- [Getting started](docs/getting-started.md)
- [Agent guide](docs/agent-guide.md)
- [Install the Moviestar skill](docs/install-skill.md)
- [Documentation index](docs/README.md)

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

Moviestar v0.7.0 and later is licensed under the
[Apache License 2.0](LICENSE). Versions through v0.6.0 were released under the
Business Source License 1.1; this repository's license does not retroactively
relicense those historical versions.

The bundled Inter font files retain their own SIL Open Font License in
[`app/src/moviestar/fonts/OFL-LICENSE.txt`](app/src/moviestar/fonts/OFL-LICENSE.txt).
See [`TRADEMARKS.md`](TRADEMARKS.md) for the separate branding policy.
