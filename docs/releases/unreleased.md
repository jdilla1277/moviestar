# Upcoming change: optional local transcription

This change is prepared in source and has not been assigned a release version.
It does not publish a package or change an existing installation automatically.

New base installations support account and Cloud metadata commands without
installing the local Whisper/PyAV stack. Local media editing remains available
with FFmpeg and supplied captions or `--no-transcribe`.
Videos with no audio stream also load normally without the transcription extra,
including with `--no-download`.

**Installation migration:** local speech recognition now requires an explicit
extra:

```sh
python -m pip install --upgrade 'moviestar[transcribe]'
```

Use that command for new environments that need the previous default
transcription behavior. The `diarize` extra continues to include transcription.
The existing PyAV compatibility constraint is retained. Already installed
dependencies remain installed when upgrading; use a fresh environment for a
minimal base installation.

`load` still requests local transcription by default. When the extra is absent,
the CLI returns a structured error and install hint before destructive workspace
work or model downloads. It does not silently skip speech recognition.

See [installation profiles](../installation.md) for base, captions, transcription,
and development setup. Remote capabilities and account approval remain governed
by the connected service.
