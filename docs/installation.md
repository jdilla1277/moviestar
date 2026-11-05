# Installation profiles

Moviestar requires Python 3.10 or newer. Choose dependencies for the work you
intend to run; installing the package does not connect an account or run a model.

| Task | Install | Other requirements |
| --- | --- | --- |
| Account and Cloud project metadata | `python -m pip install moviestar` | Approved account connection and an enabled service for remote operations |
| Local editing with supplied captions or no transcription | `python -m pip install moviestar` | FFmpeg and ffprobe on PATH; use `load --captions FILE.vtt` or `load --no-transcribe` |
| Local speech recognition | `python -m pip install 'moviestar[transcribe]'` | FFmpeg/ffprobe; model download on first use or a pre-warmed cache |
| Speaker diarization | `python -m pip install 'moviestar[diarize]'` | Includes transcription dependencies; additional model/provider access may be needed |
| Fetch captions from a video page | `python -m pip install 'moviestar[url-captions]'` | FFmpeg/ffprobe for local media work; no Whisper needed for a captions-only import |

Extras combine, for example `python -m pip install 'moviestar[transcribe,url-captions]'`.

The base package excludes faster-whisper, PyAV, CTranslate2, ONNX Runtime, and
Whisper model downloads. Account commands and the Cloud metadata journey run
without FFmpeg or ffprobe. A lightweight client installation does not enable
unsupported operations on the remote service; inspect `moviestar cloud capabilities`.

For local speech recognition, `load` still transcribes by default. Without the
extra, `load`, `retranscribe`, and `models pull` return structured installation
guidance when speech recognition is needed, before replacing a workspace or
downloading a model. Videos with no audio stream load without the extra,
including with `--no-download`; dry-run reports the same `no_audio` skip. If any
input in a multi-source load needs speech recognition, the dependency check
still runs before the workspace changes. Use supplied captions or skip
transcription deliberately; the CLI does not silently change your requested
editing workflow. Transcription ranges on inputs with audio still require the
extra.

Run `moviestar doctor` to check local media requirements. Its FFmpeg diagnostics
do not determine whether account and Cloud metadata commands can run.

## Existing installations

Starting with v0.8.1, local transcription is an optional extra; see the
[v0.8.1 migration notes](releases/v0.8.1.md). Previously the base package
included local transcription. For new environments that need the same behavior,
install `moviestar[transcribe]` explicitly. The existing `av<19` compatibility
constraint is preserved in the transcription and diarization extras.

An upgrade does not uninstall dependencies already present in an environment.
To get a minimal installation, create a new virtual environment and install the
base package normally. Avoid removing shared libraries from an environment used
by other tools. No model runs or downloads merely because you add an extra;
model use remains explicit through local transcription or `models pull`.

For a source checkout used to develop or run the complete test suite:

```sh
python -m pip install -e './app[transcribe]' pytest
bin/preflight
```

Continue with [account connection](accounts.md), [Cloud projects](cloud-projects.md),
or the [first local edit](getting-started.md).
