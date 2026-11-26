# Transcriptions using Moviestar

Use this guide when speech recognition is slow, words are wrong, captions drift,
or the user already has a transcript. Run installed command help before changing
an existing project. Commands below are for a local loaded video source; they
do not attach transcripts to a Cloud edit.

## Choose the smallest repair

| Need | Next step | Cost |
| --- | --- | --- |
| Audio must stay local | Local Whisper | Compute, model disk space and waiting |
| Wrong names or one bad passage | Vocabulary plus a ranged retry | Local runtime; agent review may use tokens |
| Existing corrected subtitles | Import SRT/VTT | No speech recognition or model download |
| Prefer a hosted speech service | Obtain timed output and import it | Provider setup, upload and usage charges |
| Correct captions but preserve source words | Caption correction rule | Source transcript stays unchanged |
| Correct words but drifting cuts | Check alignment against audio | Better text alone does not repair timing |

Check available disk space, model cache, memory and recording duration before
proposing a large local run. `load --dry-run` describes planned work without
running speech recognition. `large-v3` downloads roughly 3 GB; smaller models
need less space but may need more corrections. Model downloads and optional
installs follow the user's existing authorization. No model is always best.

Whisper itself does not consume language-model tokens. Agent review, repeated
commands and repairs may use additional tokens on the user's agent plan.

**Back up a captioned project before replacing source words.** After import,
`captions dump` should refresh derived captions from the new words and timing,
keeping the stored recipe and cue edits. Inspect those edits when the new words
change their anchors. Materialized captions keep their words and report
`stale: true`; do not overwrite them automatically.

If no words remain inside the edit, a derived track should have zero cues, not
old text. Missing or unreadable transcript data stops refresh and export with
an error; restore the transcript instead of exporting a cached caption track.

Older installations can leave cached words unchanged and report frozen tracks
as fresh. If you see that behavior, restore your backup or test in a new project.
Do not run `captions generate` as an automatic repair: it can reset generation
settings and break/join/suppress edits. A backup alone does not make regeneration
safe. Check the installed version before relying on transcript-aware refresh.

## 1. Inspect words and audio

Ask for the correct spelling of names or use a supplied speaker list/reference.
Search for a known phrase, inspect context and listen before changing words:

```bash
moviestar find "the quote you remember" --context 1
moviestar skim --text-only
```

Use `watch` for the audio; frames and a confidence score do not establish what
was said. Check source/result times in the response. A failed search can mean
missing transcription rather than missing speech. Flag uncertain wording.

## 2. Retry locally with the right vocabulary

Speech recognition needs `moviestar[transcribe]`; supplied captions work with
the base installation. Use the installation reference if the extra is missing.
For a new recording, choose a model at load:

```bash
moviestar load recording.mp4 --model medium --vocabulary 'Holden,Kubernetes'
```

For an existing project, back up the project first. `retranscribe` preserves
frames and the edit spec but replaces transcript data; `undo` is not a promised
transcript backup. Do not use `load --force` to fix an edited project.

```bash
moviestar retranscribe --model medium --range 00:01:10 00:01:30 --vocabulary 'Holden,Kubernetes'
```

This replaces only that original-source window; words outside it remain.
Use enough surrounding speech for context. `small`, `medium` and `large-v3`
are local options. `models pull MODEL` prepares a cache; `--no-download` avoids
an unexpected fetch. Ranges here are source time, not the shortened edit clock.

## 3. Correct captions or replace source words

For visible caption spelling, first inspect tokens, then correct an exact match:

```bash
moviestar captions dump
moviestar captions rules add --replace "Haulden=Holden"
moviestar captions dump
```

The rule is case-sensitive and changes every exact match in derived captions.
Inspect all matches. A one-word substitution keeps that word's timing. Read
`captions rules add --help` before merging or splitting tokens.

**A caption rule does not rewrite the source transcript.** Search and source
word snapping still use the old words. To change those too, retry recognition
or import a corrected timed transcript. `captions import` adds a caption track;
it is not a source-transcript replacement.

## 4. Bring a timed transcript

Use the original-language transcript for the same recording. New source:

```bash
moviestar load recording.mp4 --captions corrected.vtt
```

Existing project, after backing it up and checking the refresh behavior above:

```bash
moviestar retranscribe --captions corrected.vtt
```

SRT also works. Edit a copy, preserve cue timestamps and use full-recording
source time. For extracted clips, restore their original offsets and reconcile
overlaps before import. Plain text has no timing; align it against the audio
with a suitable tool rather than distributing words over the full duration.

For third-party services, coach the user through setup, cost, audio upload and
export. Keep keys in environment variables and retain the original response.
Read current provider instructions; do not invent flags, silently spend, or
promise that a hosted provider must improve this recording.

- [ElevenLabs Scribe](https://elevenlabs.io/docs/api-reference/speech-to-text/convert):
  submit audio to its speech-to-text endpoint with word timestamps; obtain a
  timed export and retain native word JSON.
- [Groq Whisper](https://console.groq.com/docs/speech-to-text): request
  `verbose_json` with word/segment timestamp granularities. Its JSON needs a
  checked converter to SRT/VTT; renaming the file is not conversion. Do not
  write Moviestar's internal transcript files as an import workaround.
- [AssemblyAI](https://www.assemblyai.com/docs/pre-recorded-audio/getting-started/transcribe-an-audio-file):
  upload audio, submit transcription and wait for completion. Retrieve SRT/VTT
  using its [subtitles endpoint](https://www.assemblyai.com/docs/pre-recorded-audio/api-reference/transcripts/get-subtitles)
  at `/v2/transcript/{transcript_id}/{srt_or_vtt}`. Import that saved file with
  the commands above; keep native word output if timing matters.

Do not infer hosted transcription availability from account sign-in. Inspect
the connected service's capabilities and installed CLI help before offering
a hosted operation; if unavailable, use a supported local or supplied path.

## 5. Verify the replacement

Check a known phrase, difficult names, and samples near the beginning, middle
and end against the audio. Preview corrected captions and word-snapped cuts;
read `captions dump` after import to refresh derived tracks. Do not regenerate
over existing custom edits or assume materialized captions follow the transcript.

SRT/VTT normally times cues, not each spoken word. Word timing may be
interpolated; inline tags are not independent alignment proof. Keep original
word-level output, state any conversion loss, and check supported importer
formats instead of claiming native JSON import exists.

Report the chosen model/import, changed source ranges, what was checked and
remaining timing uncertainty. A few successful samples are not a whole-file
accuracy guarantee. Restore the saved backup if the replacement is worse.
