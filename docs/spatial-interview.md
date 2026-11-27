# Edit a reusable three-person interview

Build two scenes with three independently editable pictures: Alice leads first,
then Bob. Both scenes reuse one `ListenerPair` definition containing two
`SpeakerCard` instances. The large speaker uses the same card definition.
Changing a shared card updates both scenes; a local override belongs to just
one occurrence. This example uses the real CLI and saved spec, without flattened
listener footage or mock edit commands.

Use this branch's installed package, FFmpeg, Node 22+ and Chrome. From the
repository checkout, create a new example directory:

```sh
python -m pip install -e app
python experiments/live_browser_preview/interview.py --out /tmp/spatial-interview
cd /tmp/spatial-interview
```

The helper loads three generated H.264/AAC clips, sets two three-second scenes,
applies `interview.json` and imports `captions.srt`. The colored clips carry source
frame numbers and beeps. Alice's beep is twice Bob's amplitude; Casey stays
inaudible. Caption text is a layout sample, not an automatic transcription.
Source offsets differ for all three pictures. `scenes-*.json` and `edit-*.json`
remain available to inspect and change.

To reference your own files in place instead, run the helper with
`--sources /absolute/alice.mp4 /absolute/bob.mp4 /absolute/casey.mp4` and optionally
`--offsets 1 2 3`. Files must cover the selected ranges: Alice through offset+5,
Bob through offset+3, Casey through offset+4. The helper indexes those files
without modifying them or running transcription. The native profile still
requires compatible MP4 media and matching frame rates.

## Make local and shared changes

Start fast native review of the first scene, then leave that terminal running:

```sh
moviestar preview --mode native --from 0 --to 3
```

In another terminal, enter `/tmp/spatial-interview` and run:

```sh
moviestar composition model edit edit-local.json --dry-run
moviestar composition model edit edit-local.json
moviestar composition model edit edit-shared.json
moviestar composition model edit edit-reset.json
```

The first edit makes only Bob's listener border cyan. The shared edit changes
all inherited card borders to purple, including the second scene, while Bob's
cyan override remains. Reset removes that override, so Bob inherits purple too.
The targets are real nested occurrence paths, such as
`intro/listeners/left/video`; the shared target is `speaker_card/video`.

Run `moviestar undo` three times to reverse reset, shared update and local edit.
After each undo, inspect the viewer: cyan returns, then the other cards return
to yellow, then all cards return to yellow. Undo restores the saved component
state; selecting a historical preview alone does not undo an edit.

## Reuse the arrangement in another format

```sh
moviestar scenes set scenes-640x360.json
moviestar composition model preview --at 0.5 --out landscape.png
moviestar undo
```

Portrait places the speaker above two listeners; landscape places the speaker
beside stacked listeners. Names, gaps and caption type scale with the canvas.
The component definitions and source bindings remain the same. The helper also
writes `scenes-1080x1920.json` and `scenes-1920x1080.json` for HD output.
Changing a canvas records one undo entry.

## Review the other scene and export

Stop the first preview with Ctrl-C, then review Bob's scene:

```sh
moviestar preview --mode native --from 3 --to 6
```

Bob is now large and audible, with Alice and Casey as listeners. Check the
second scene's inherited border, names, picture offsets and captions. Play,
Pause, Restart and seek apply to all three pictures.

For continuous review across the scene cut, stop the selected-scene preview:

```sh
moviestar preview --mode native --from 0 --to 6
```

All six picture players stay within the native resource bound. At three seconds,
Bob becomes large and audible without restarting the continuous mixed soundtrack.
Check the cut again after local/shared edits and undo, then seek across it and use
Restart. For a canonical rendered comparison, stop native preview and run:

```sh
moviestar preview --mode rendered --from 0 --to 6
```

Rendered preview encodes a review MP4 and can take substantially longer than
native edits. It uses the canonical composition renderer. To create the final
full-resolution deliverable:

```sh
moviestar export --out interview.mp4
```

Watch the full export: all three pictures must move, the selected speaker must
change at three seconds, and captions must change on the result clock. Compare
both portrait and landscape outputs. Generated bars/beeps verify clocks and
routing; real interview speech, face framing and lip sync still need a human
review with representative recordings.
