# Spatial component model and measured layout

MovieStar can now store reusable component definitions and scene-owned visual
arrangements in `moviestar/spec.json`. The existing `composition` still owns
scene order, duration, and audio. Each key in `spatial.scenes` must match a
named composition scene ID. Older specs without `spatial` continue to work.

The frame command also resolves the current scene and each video element's
source position on the existing result clock. `model layout` measures a
supported CSS layout subset in local Chrome and can draw a labeled layout PNG.
`model preview` fills those positions with real video, images, text, and shapes.
To avoid producing a video that silently ignores authored visuals, composition
export stops when a spatial
scene tree is present. The existing `--source` export remains available.

## Authoring

Create a JSON file containing a `spatial` document, then apply it:

```sh
moviestar composition model apply spatial.json
moviestar composition model inspect scene_a
moviestar composition model frame --at 0:00:03.000
moviestar composition model layout --at 0:00:03.000 --canvas 1280x720 --out layout.png
moviestar composition model preview --at 0:00:03.000 --canvas 1280x720 --out frame.png --layout-out boxes.png
```

`apply` validates the document, writes it atomically to `spec.json`, and records
one undo revision. A malformed update leaves the previous spec untouched. Use
`moviestar undo` to restore the previous document. An incomplete input binding
can be saved and appears as a path-addressed diagnostic in `inspect`.

```json
{
  "definitions": {
    "listener_pair": {
      "name": "ListenerPair",
      "inputs": [
        {"name": "left", "type": "video", "required": true},
        {"name": "right", "type": "video", "required": true},
        {"name": "title", "type": "text", "default": "Listeners"}
      ],
      "children": [
        {"id": "left_video", "type": "video", "content": {"input": "left"}, "layout": {"grid_column": "1", "grid_row": "1"}},
        {"id": "right_video", "type": "video", "content": {"input": "right"}, "layout": {"grid_column": "2", "grid_row": "1"}},
        {"id": "title_text", "type": "text", "content": {"input": "title"}, "layout": {"grid_column": "1 / -1", "grid_row": "2"}}
      ]
    }
  },
  "scenes": {
    "scene_a": {
      "layout": {
        "display": "grid",
        "columns": "1fr",
        "children": [
          {
            "instance_id": "listeners_a",
            "definition_id": "listener_pair",
            "layout": {"display": "grid", "columns": "1fr 1fr", "rows": "240px auto", "gap": "16px"},
            "bindings": {
              "left": {"asset_id": "alice", "source": {"start": 2, "end": 10}},
              "right": {"asset_id": "bob", "source": {"start": 4, "end": 12}},
              "title": {"text": "Alice and Bob"}
            }
          }
        ]
      }
    }
  }
}
```

Video asset IDs refer to spec `sources`. Images may be registered under
`spatial.assets` as `{ "logo": { "type": "image", "path": "assets/logo.png" } }`;
an image element or input binding then uses `{ "asset_id": "logo" }`. An
instance inside a component can pass an enclosing input with
`{ "forward": "input_name" }`. Scene layouts and child nodes can carry
CSS-like `layout` properties. Measurement and rendered preview share the
supported properties below; authored movement belongs to a later slice.

The same definition can be instantiated in `scene_b` with different bindings.
Editing the one definition updates both expansions; each occurrence keeps its
own ID and content. `inspect` reports nested `occurrence_path` values for
addressing individual uses. `frame` selects the active scene using the canonical
result timeline and reports scene-relative result and source time, source media
time, and visibility for each video element. A video range is half-open and
starts at scene source-local time zero. Authored scene speed and holds use the
same mapping as the canonical timeline. Shorter media becomes transparent after
its range ends and produces a warning.

## Measured layout view

`model layout` uses the same expanded scene and result time as `model frame`.
It returns browser-measured `x`, `y`, `width`, and `height` for every stable
occurrence path, text line rectangles, the resolved CSS values, and DOM order.
`--canvas` changes only the measurement size; the existing timeline still
determines which scene and source times are active. `--out` writes a PNG with
labeled boxes and placeholders for video and images. Text is measured before
being hidden in that labeled view, so labels cannot change its line breaks.
Without `--out`, the command returns JSON only.

The current subset covers CSS Grid, flex, positioned children, gaps, padding,
explicit and constrained sizes, alignment, overflow, aspect ratio, background
color, borders, opacity, and basic text styling. Properties use snake_case or
CSS hyphen spelling; `columns`/`rows` are aliases for
`grid-template-columns`/`grid-template-rows`. Numeric lengths are pixels;
aspect ratios, grid line numbers, and other unitless CSS values retain their
numbers (for example, `"width": 100, "aspect_ratio": 2` gives a 100×50 box).
The requested canvas fixes the outer scene size; root width and height rules
are rejected.
Unsupported or unsafe values return an addressable error rather than a guessed
box. Nodes default to relative positioning as containing blocks for their
positioned children; explicit `position: static` uses normal CSS ancestry. Zero-size
boxes appear as `unresolved_geometry` diagnostics. Image and video intrinsic
sizes are not used in this placeholder view; give them a size, aspect ratio,
or a parent layout rule that determines their box.

Local measurement needs Node.js 22 or newer and Chrome or
`chrome-headless-shell`. MovieStar searches common executable names and the
macOS Chrome application; set `MOVIESTAR_CHROME_PATH` to a specific executable
when needed. The command does not download a browser or write a project
revision. In a container where Chrome cannot use its sandbox, set
`MOVIESTAR_CHROME_NO_SANDBOX=1`; the CI job uses that explicit setting.
Format-specific rules, scene motion, transitions, watch, and export remain
later renderer work.

## Rendered frame preview

`model preview` renders a single PNG at a global result time. FFmpeg decodes the
video positions selected by `model frame`; Chrome paints text, shapes, and local
images and measures each video's visibility through foreground graphics and
clipping. FFmpeg combines those graphics with the source frames. Nested component
backgrounds, stacking order, rounded clips, and group opacity follow the browser's
paint. This does not use DOM order as a substitute for paint order.

`--layout-out` saves a labeled view from that exact measured scene. Both views
keep the same element rectangles and text wrapping. The JSON response reports
the frame's bindings and clocks, measured boxes, source positions, and the media
rectangles sampled by FFmpeg. Video sampling rectangles are snapped to output
pixels; browser masks retain the clipping at fractional parent edges.
FFmpeg uses bilinear video sampling. Sharp resampled video edges can differ from
Chrome's image sampling; the browser reference verifies paint composition, not
pixel-identical video resampling.

Video and image elements use the content box inside their border and padding.
`style.object_fit` accepts CSS `fill` (the default), `contain`, `cover`, `none`,
and `scale-down`, centered in that box. Letterboxes remain transparent to the
element's background. Images retain their alpha; this slice requires opaque video.
A shorter video is transparent after its bound range ends and never loops.
At a position between source timestamps, preview selects the frame already on
screen, including the final frame's display interval. Variable frame rates use
presentation timestamps. Static-positioned video and images paint in their own
measured content boxes, keeping the authored background visible through padding
and image transparency. Alpha video is detected using both pixel format and
stream metadata, including WebM's `alpha_mode` tag.
Local image paths are relative to the project root, including when the command
runs from a subdirectory. Media must have layout rules determining its box;
preview does not infer layout size from intrinsic media dimensions.

Missing required inputs or assets in the selected scene block preview with a
path-addressed error. Optional empty media remain transparent. Caption elements
and alpha video return explicit unsupported errors in this slice. Unplaced draft
definitions do not block a complete scene. The command needs the same Chrome and
Node setup as layout inspection, plus FFmpeg/ffprobe. Output paths must be distinct
PNG files in existing directories and cannot overwrite a project asset. Asset or
paint failures preserve an existing preview, and no project revision is written.

This is single-frame rendering. It does not implement caption cues, format rules,
motion, transition frames, static-only scene timing, watch, or export. The browser
graphics and video visibility masks are temporary artifacts; no unchanged-clip
cache is introduced. See the [rendering contract](design/spatial-frame-rendering.md)
for the compositing boundary and parity evidence.

## Editing a linked use

Put one edit request in a JSON file and preview its impact before saving:

```sh
moviestar composition model edit change.json --dry-run
moviestar composition model edit change.json
moviestar composition model inspect scene_a
moviestar undo
```

For example, this changes one nested element in one scene while its component
definition remains shared:

```json
{"op":"instance.customize",
 "target":{"occurrence_path":["scene_a","listeners_a","left_video"]},
 "property":"style.border_color","value":"#1E90FF"}
```

Occurrence paths come from `model inspect`; IDs remain stable when siblings are
reordered or the component is renamed. Local changes are stored on the nearest
scene-owned instance as ID-addressed `customizations`, then replayed over the
current shared definition. A shared style or layout change reaches uses without
a local choice for that property. Use `instance.reset` with the same target and
property to inherit again.

Structural requests use `instance.child_insert` with a parent occurrence path,
`child` node, and optional `after_id`; `instance.child_remove` and
`instance.child_move` target a child path. Omitting `after_id` places a child
first. `instance.reset` without a property resets the targeted child subtree,
including its nested local edits. Shared `component.update`,
`component.child_insert`, `component.child_remove`, and `component.child_move`
target a definition ID plus an optional child ID or ID path. Removing a shared
child that has local edits, or an anchor used by a local move, returns every
affected occurrence as `customization_conflict` and writes nothing. Reset that
local choice or save a separate component variation first.

`component.save_variation` copies the effective component structure to a new
definition ID; `relink_source: true` moves only the source scene instance to
that new definition. `instance.duplicate` creates a new scene occurrence ID;
`input.bind` changes one scene-owned instance's input; `component.rename`
changes the display name without changing its ID. `component.delete` and
`asset.delete` reject referenced definitions or image assets, including uses in
unplaced drafts. Every successful edit saves one project revision, and a request
may include `expected_state_id` from `model inspect` to reject a stale edit.
The state ID fingerprints the current editable spec and changes even when the
undo journal is full. The dry run reports its before and after state IDs,
affected scenes, changed occurrence paths, and created definition IDs without
writing the spec.

An omitted range uses the source's full recorded duration. Missing or invalid
media appears in diagnostics without making the document unreadable. The frame
result is structural and temporal; `model layout` adds measured boxes and an
optional labeled PNG without changing the frame's media mapping.
At the exact project end, `frame` samples the last output-grid frame and reports
that timestamp as `sample_time_s` alongside the requested `result_time_s`.
At a time inside an authored scene or project-edge transition, `frame` returns
an explicit JSON error because a complete transition frame needs both visual
trees and transition state.
Static-only scenes and spatial export are subsequent implementation steps.
Local editing of an inherited nested instance's input
bindings still requires editing its enclosing definition or applying a complete
spatial document; `input.bind` currently targets scene-owned instances. Input
declaration changes and static-only scene duration remain separate contract
work before this model is ready for export.

Replacing the timeline with `moviestar scenes set` retains visual trees for
scene IDs that remain and removes trees owned by deleted scenes in the same
undoable revision.
