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

## Change the cut after composing

Scene edits preserve the arrangement by **scene ID**. Start from the helper's
`scenes-640x360.json`, keep the IDs, and use `scenes set changed-scenes.json
--dry-run` before saving. The following are separate edits; undo each before
trying the next:

| Change | Edit the scene document | What follows the scene |
| --- | --- | --- |
| Shorten Alice's first scene from three seconds to two | Change its slot's `to` from `4` to `3` | The existing picture bindings play their first two seconds. Motion keeps its authored scene-relative keyframes; it is not compressed. |
| Put Bob's scene first | Reverse the two scene records, retaining `intro` and `reply` | Each scene keeps its component bindings and local customizations. Motion restarts at scene time zero in its new position. |
| Remove Alice's opening scene | Remove the record with ID `intro` | Bob's retained arrangement moves to the beginning. The removed scene's arrangement is pruned; undo restores it. |

Save a changed document and inspect the resulting scene before continuing:

```sh
moviestar scenes set changed-scenes.json --dry-run
moviestar scenes set changed-scenes.json
moviestar composition model preview --at 0.5 --out changed.png
moviestar undo
```

This is **not yet an automatic edit of all related tracks**. Imported SRT cues
keep their explicit finished-video timestamps. External microphone recordings,
voiceovers and music added with `audio add` do too. For example, shortening the
first scene by one second moves Bob's pictures from 3s to 2s, but an imported
caption or external voiceover placed at 3s still starts at 3s. A looping music
bed still runs to the new project end; that does not mean narration has followed
the pictures. Source audio routed by a scene's `audio_from` is a separate case.

`scenes set` warns about stored captions remaining at their old positions.
Do not interpret the absence of an audio warning as confirmation of sync.
There is also a known validity failure: removing enough footage can leave an
external track starting after the new ending. `scenes set` currently accepts
that state, but audio resolution then fails ([#330](https://github.com/jdilla1277/moviestar/issues/330)).
Undo the scene change to recover before trying a revised edit.

Rebuild or re-import captions against the new cut and explicitly retime external
tracks through `audio dump` → edit → `audio set`. Review a rendered export with
speech before declaring the edited project ready. Every saved correction is a
separate undo entry; undo them before undoing the scene change.

`cut --source alice --from … --to …` edits Alice's source timeline. It does not
remove an interval from an already composed interview: saved scene ranges and
spatial picture bindings refer to source media, and independent result-time
tracks retain their timestamps. Use the scene document for scene shortening,
removal or reordering. Removing a passage from the middle of a finished scene
requires splitting its scene and explicitly adjusting picture bindings and
related tracks; there is no single composition-wide ripple-cut command.

The repeatable acceptance cases in `app/tests/test_spatial_composed_editing.py`
cover scene shortening, removal, reordering, source-cut scope and exact undo on
generated three-picture footage. They also render the actual export audio mix
and measure separate voice/music tones. These checks establish current clock
semantics; they do not claim that automatic cross-track retiming is implemented.

The suite retains a strict expected failure for #330. Automatic scene-attached
dialogue/caption editing is tracked separately in [#331](https://github.com/jdilla1277/moviestar/issues/331).
