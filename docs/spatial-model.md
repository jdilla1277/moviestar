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
supported properties below. Elements and component instances can also carry
scene-relative visual motion, described below.

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
Transitions, watch, and export remain later renderer work.

## Responsive canvas rules

One saved arrangement can adapt to portrait, landscape, square, or numeric canvas
constraints. Add `format_rules` to a scene's `layout`, an element, or a component
instance. The rules below change a two-column scene into a stack in portrait:

```json
{
  "display": "grid",
  "columns": "1fr 2fr",
  "gap": 24,
  "format_rules": [
    {
      "id": "portrait-stack",
      "when": {"orientation": "portrait"},
      "layout": {"columns": "1fr", "rows": "auto 1fr auto"}
    },
    {
      "id": "compact-spacing",
      "when": {"max_width": 600},
      "layout": {"gap": 12}
    }
  ],
  "children": []
}
```

Rules on nodes inside a component definition are shared with every linked use.
For example, put a portrait rule on a component's grid child to stack its internal
contents. A rule on the instance controls its outer box. A component definition
itself has no rendered box; rules there return an error identifying the child or
instance target to use instead. Rule IDs are stable, non-empty, and unique within
their owning node or scene layout.

`when` combines all supplied conditions with **and**. It accepts:

- `orientation`: `portrait` means height > width, `landscape` means width > height,
  and `square` means equal dimensions.
- `min_width`, `max_width`, `min_height`, `max_height`: inclusive canvas pixel bounds.
- `min_aspect_ratio`, `max_aspect_ratio`: inclusive positive width/height ratios.

Rules always match the requested **output canvas**, including rules deep inside
components. They are not container queries. Bounds are numbers, not CSS strings;
unknown predicates and inverted bounds are rejected. Rules can change only
`layout` and `style`, using the same supported CSS properties as base rules.
They cannot change content bindings, source ranges, scene timing, or audio.
To switch arrangements cleanly, override the affected grid rows/columns or sizing
properties explicitly; properties not mentioned in a rule retain their base value.

Precedence is resolved per CSS property:

1. Base layout and style.
2. Matching format rules. The highest integer `priority` wins; its default is 0.
3. An occurrence customization of an inherited node, authored with
   `instance.customize`. That local value wins in every format until reset.

Different matching rules can set different properties. Equal-priority rules
setting the same normalized value are compatible. Conflicting values at the
highest matching priority produce `ambiguous_format_rule`, listing the occurrence,
property, and rule IDs. Rule order does not break ties. Increase a rule's priority,
make its predicate disjoint, or supply a local customization. A higher-priority
rule or local customization resolves lower-priority conflicts for that property.
CSS aliases such as `columns` and `grid_template_columns` identify the same
property for precedence and conflict checks. Overlapping shorthands are expanded
before selecting winners: `gap` assigns `row-gap` and `column-gap`; `grid_area`,
`grid_row`, and `grid_column` assign the corresponding grid start/end longhands.
For example, equally ranked `gap: 24` and `row_gap: 8` conflict on `row-gap` in
either rule order. Raising the row rule's priority selects an 8px row gap while
retaining the shorthand's 24px column gap. Identical values remain compatible.

Inspection reports these effective longhand names in the resolved style and
`rule_sources`. Grid start/end properties can also be authored directly as
`grid_row_start`, `grid_row_end`, `grid_column_start`, and `grid_column_end`.
Fixed values and math functions such as `calc()` retain their CSS meaning.
Substitution functions (`var()`, `env()`, or `attr()`) inside these shorthands
require explicit longhand declarations, so unresolved multi-value substitutions
cannot be guessed during expansion.

Editing a scene-owned base property or using `component.update` edits the base
layer; matching rules still apply. Resetting an inherited occurrence customization
restores the base/format cascade. Saving a customized occurrence as a separate
component bakes its unconditional choices into the new definition, removing
conflicting assignments for those properties from its copied format rules.
When only part of a shorthand overlaps, its unaffected assignments are retained
as longhands. A local `row_gap: 4` therefore remains 4px after saving a component
whose portrait rule sets `gap: 24`, while the column gap still responds to that rule.
Other properties keep responding to the canvas, and the original definition is
unchanged.

Author rules through the existing `model dump` → edit → `model apply` file loop.
They survive save/reload and participate in the normal revision and undo path.
`model layout --canvas WIDTHxHEIGHT` and `model preview --canvas WIDTHxHEIGHT`
select rules without modifying the saved document. Both views expose
`scene_layout` for the scene, and each measured box includes `matched_format_rules`
and a per-property `rule_sources` map identifying `base`, `format_rule`, or
`local_customization`. `model frame` remains the temporal tree before canvas
selection; it retains the rules for the layout stage.

On an ambiguous property, layout inspection reports the conflict and shows
provisional base geometry marked `ambiguous` in its provenance. Rendered preview
rejects the ambiguity before touching an existing output. Caption wrapping and
overflow are measured after applying the selected format. Scene duration, media
source times, active caption cues, and audio routing are unchanged by the canvas.

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
path-addressed error. Optional empty media remain transparent. Alpha video
returns an explicit unsupported error in this slice. Unplaced draft
definitions do not block a complete scene. The command needs the same Chrome and
Node setup as layout inspection, plus FFmpeg/ffprobe. Output paths must be distinct
PNG files in existing directories and cannot overwrite a project asset. Asset or
paint failures preserve an existing preview, and no project revision is written.

This is single-frame rendering. It does not implement transition frames,
static-only scene timing, watch, or export. The browser
graphics and video visibility masks are temporary artifacts; no unchanged-clip
cache is introduced. See the [rendering contract](design/spatial-frame-rendering.md)
for the compositing boundary and parity evidence.

## Element and component motion

Add a `motion` object to an element or a component **instance**, including one
inside a reusable definition. Definitions themselves and the scene layout have
no motion target; place a group instance if you want to move a whole scene tree.
The stable occurrence path identifies the target. A component's entire subtree
moves together, and a child's own motion composes with its ancestors:

```json
{
  "instance_id": "speaker",
  "definition_id": "SpeakerCard",
  "motion": {
    "translate_x": [{ "at": 0, "value": "-100%" }, { "at": 1, "value": "0%" }],
    "translate_y": [{ "at": 0, "value": 0 }, { "at": 1, "value": 24 }],
    "scale": [{ "at": 0, "value": 0.8 }, { "at": 1, "value": 1 }],
    "opacity": [{ "at": 0, "value": 0 }, { "at": 0.5, "value": 1 }]
  }
}
```

Each property has an independent, non-empty track of `{ "at", "value" }`
keyframes. `at` is a non-negative numeric **scene-relative result time in
seconds**. Times must strictly increase. Values interpolate linearly between
keys, hold the first value before the first key, and hold the last value after
the last key. A one-key track is constant. Omitted properties use translation
zero, scale one, and opacity one. An empty `motion` object has no effect.
Keyframes past the scene end are allowed; a shortened scene samples the portion
that still exists. There is no automatic retiming of these visual tracks.

Translation values are finite CSS pixel numbers, `"24px"`, or `"-100%"`.
Every value in a given translation track must use the same unit (numbers and
`px` strings can mix). Percentages refer to the target's own **base border
box**, after responsive layout. Positive x moves right; positive y moves down.
Scale is a finite positive uniform factor around the box's top-left corner.
Opacity is a finite factor from zero to one, multiplied by the resolved
`style.opacity`; nested opacities multiply. Use opacity zero to hide a target.
These transforms use normal CSS stacking contexts and ancestor overflow clips.
They do not move siblings, change text wrapping, or change the base layout.
Translated or scaled targets use the normal spatial `position: relative` default or an
explicit non-static position; `position: static` is rejected for animated
targets because transforming it would change its descendants' containing block.
Fixed-position descendants of translated/scaled targets are also rejected; use
absolute positioning inside the component. Opacity-only tracks can fade a static
target without introducing a transform.

Visual motion uses `scene_time_s`, so it keeps advancing while source footage
is held and is unaffected by source playback speed. It restarts at each scene's
zero, even when the scene is later reordered. Source decoding, caption cue
selection, and audio continue using their existing clocks. The exact project
end still samples the last output frame, including for motion.

`model layout --at ...` and `model preview --at ...` sample these tracks at the
same time. Every measured box reports `base_rect` before any motion, and `rect`
after its own and its ancestors' motion. These are geometric border boxes;
`rect` can extend outside an ancestor clip or the canvas and does not claim to
be a segmentation of visible pixels. Text line rectangles use transformed
coordinates. Animated boxes also report their sampled `motion`; all boxes
report `effective_opacity`, including ancestors. Labeled PNGs show the same
transformed boxes, while rendered PNGs include the assets. Compare snapshots
before, during, and after a move to inspect intermediate positions. Automated
interval-wide collision checking is not part of this slice.

Use the existing `instance.customize`, `component.update`, and `instance.reset`
operations with `property: "motion.translate_x"` (or the other three track
names) and the keyframe array as `value`. This replaces that whole track. Local
occurrence tracks take precedence over shared tracks and survive saving a
component variation. To remove a track from a direct scene node, reset that
property; an empty motion object is a no-op. Persistence, dry-run conflict
checks, revisions, and undo use the existing model editing path.

For moving video subtrees, FFmpeg decodes the exact selected source frame and
Chrome paints that still with the subtree's transforms. This keeps resampling,
text, images, opacity, and clips in the same coordinate system. Stationary video
continues through the existing browser-mask/FFmpeg composition path, including
when moving graphics overlap it. Both paths report the sampled media rectangle.
This adds no rendered-clip cache, video export, or scene transition renderer.
Rotation, nonuniform scale, custom easing, and transcript-triggered motion are
not supported by these tracks.

## Timed caption elements

Generate a caption track with `moviestar captions generate`, or import cues with
`moviestar captions import`. Place it in a structured scene with an element:

```json
{
  "id": "subtitles",
  "type": "caption",
  "content": {"track": "captions"},
  "layout": {
    "position": "absolute", "left": "8%", "right": "8%", "bottom": 24,
    "height": 96, "padding": 12
  },
  "style": {
    "font_size": 28, "line_height": 1.3, "color": "#ffffff",
    "text_align": "center", "background_color": "#13263a"
  }
}
```

The element uses cues from that track on the existing **result clock**. Cue
intervals include their start and exclude their end; at the exact project end,
the usual last-output-frame sampling applies. Gaps have no text. Overlapping
cues appear on separate lines, ordered by start time then cue ID. Authored line
breaks remain; Chrome also wraps to the element's available width.

Omitting `content` selects the default `captions` track. An explicit
`{"empty": true}` binding leaves the element empty. A reusable component can
declare an input such as `{"name": "subtitles", "type": "caption"}` and use
`{"input": "subtitles"}` on its caption element. Bind that input with
`{"track": "captions"}` or forward it through another component as usual.
Different instances can select different tracks.

Placement and typography come from the **element's layout and style**. The
legacy cue's position, style preset, and spoken-word highlighting are not applied
by this caption path. This slice displays whole cues; animated word highlighting
remains later work. Existing preset scenes keep their existing caption renderer.
Transcript data, project settings, and caption tracks do not inject a caption
layer into a structured scene without an element. Each authored occurrence
renders once. An empty caption keeps its authored box and background, allowing
a reserved region to stay in place as speech changes.

`model frame`, `model layout`, and `model preview` refresh live caption recipes
in memory using the existing derivation pipeline after timeline edits. They do
not write cue caches or revisions. Frozen/imported tracks keep their stored
result-time cues. Direct Python callers of `compile_spatial_frame` supply a spec
with current cue records, as with other compiler inputs.

Inspection reports `caption.track`, `caption.text`, `caption.visible`, and active
`caption.cues` with IDs and result-time bounds. Measured caption boxes also expose
`text_lines` and `overflow`. The rendered and labeled views measure the same
active text before labels hide it. Give the region sufficient height for its
font, line height, and padding; one- and two-line cues can then share a stable box.

Unknown tracks produce `unknown_caption_track`; a known recipe with no cues is
an empty region. A live recipe that cannot refresh produces `stale_caption_track`
only where that track is placed. Text outside its content box produces an
addressed `caption_overflow`, even with `overflow: hidden`. Layout inspection
returns that diagnostic and its measured lines; rendered preview fails and
preserves any prior artifact. Enlarge the region or reduce the font size before
rendering again. Unplaced tracks do not add layers or block a scene.

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
