# Getting started

Moviestar requires Python 3.10 or newer plus FFmpeg and ffprobe. Install FFmpeg
with your system package manager, then create an isolated Python environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install the current Moviestar release from PyPI:

```bash
python -m pip install --upgrade moviestar
```

Confirm that `moviestar --version` reports v0.7.0 or newer before using v0.7
commands.

Check the environment before starting an edit:

```bash
moviestar doctor
moviestar --help
```

## Make a first edit

Create a dedicated working directory. The source video can live elsewhere;
Moviestar records a pointer and never modifies it.

```bash
mkdir first-moviestar-edit
cd first-moviestar-edit
moviestar probe /path/to/video.mp4
moviestar load /path/to/video.mp4 --as source
moviestar status
```

For spoken footage, locate a phrase and use the returned result-time range:

```bash
moviestar find "the phrase to keep" --context 1
moviestar trim --from 0:10 --to 0:24 --snap-to-words
```

Preview the render plan, export, and inspect the actual file:

```bash
moviestar export --dry-run --out final.mp4
moviestar export --out final.mp4
moviestar probe final.mp4
moviestar screenshot --file final.mp4 --at 0:01 --out check.jpg
```

Open `check.jpg` with an image viewer or the agent's image tool. For motion or
audio review, inspect `final.mp4` directly or extract a short precise segment
with `moviestar watch` from the loaded project.

Continue with the [agent guide](agent-guide.md) for composition, time semantics,
structured responses, and a stronger verification loop.
