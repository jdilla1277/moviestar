# Installation guidance

Do not change the user's Python environment or install a large dependency
without permission. Prefer a dedicated virtual environment for the editing
task.

Moviestar needs Python 3.10 or newer. Local media operations also need
`ffmpeg` and `ffprobe`; account and Cloud metadata commands do not. If FFmpeg is
missing, identify the platform package-manager command and ask the user before
installing it.

Install the current Moviestar release from PyPI:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install moviestar
# Add local speech recognition when the task needs it:
python -m pip install 'moviestar[transcribe]'
```

Confirm that `moviestar --version` reports v0.7.0 or newer before using v0.7
commands.

The base installation can edit with supplied `--captions FILE.vtt` or
`--no-transcribe`. The `transcribe` extra is required for speech recognition,
including `retranscribe --range` and `models pull`; `diarize` includes it.
Upgrading an existing environment keeps already installed transcription
dependencies; a new environment needs the extra explicitly.

Run `moviestar doctor` before local media work. Treat blocked features in its JSON
response as setup failures to resolve or report before editing. Whisper models
download on first transcription; use `moviestar models pull MODEL` only after
the user approves the download, or use an already cached model. `--no-download`
guarantees a hermetic load.
