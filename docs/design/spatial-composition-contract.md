# Spatial composition contract, milestone 0.1

**Status:** exploratory. The commands below are executable, read-only examples.
The document format and layout syntax are provisional until the renderer spike.
No example here is accepted as a production project edit.

Run `moviestar composition` to discover the mock. `composition edit --example CASE`
returns a proposed request and result; `composition edit REQUEST_FILE` accepts a
JSON object and returns a canned example for its `op` without applying it.
`composition check --example CASE` returns sample
readiness or conflict diagnostics; `composition inspect SCENE --at TIME --canvas
WIDTHxHEIGHT --view layout|rendered` returns a sample frame description.
Every response says `status: "mock_only"` and `writes_spec: false`.

## The worked project

An interview starts from a built-in scene layout. The project defines one
`ListenerPair` component with two required video inputs and two video elements.
Scene A places an instance beside the host. Scene B places another instance of
the *same definition* and binds different footage and source ranges. Scene A
customizes its left frame, then the project updates the shared definition. The
local customization remains local; the compatible shared property reaches both
scenes. Scene A can save its customized instance as a new definition without
relinking any use implicitly.

The mock sequence is:

```text
composition edit --example start
composition edit --example define
composition edit --example place
composition edit --example reuse
composition edit --example bind
composition edit --example customize
composition edit --example insert
composition edit --example reorder
composition edit --example remove
composition edit --example update
composition edit --example vary
composition edit --example reset
composition edit --example duplicate
composition edit --example clear
composition edit --example draft
composition check --example draft
composition check --example conflict
composition check --example referenced
composition inspect scene_interview_a --at 00:03.000 --canvas 1080x1920 --view layout
```

`scene_explainer` has an explicit six-second duration, a headline, and a logo
image; no video is required. It is saved with an unfilled required image input.
Reloading the draft must preserve its IDs, content, and diagnostics. Binding the
image later makes the selected scene exportable. A project may retain unrelated
incomplete drafts while exporting ready scenes.

## Identity and ownership

The project owns component definitions, assets, ordered scenes, and revisions.
Every scene owns its layout. A layout contains elements and component instances;
an instance refers to a project component definition. A definition may contain
elements and nested instances. Grouping components accept children through an
input; no additional container registry is needed.

IDs are stable opaque values, separate from names and array indices. A
definition node has an ID stable within its definition. Each instance use has
an occurrence ID. To address a nested use, start with the scene ID, follow
instance IDs, and end with the node ID, for example:

```json
{"occurrence_path":["scene_interview_a","inst_listeners_a","el_left_frame"]}
```

Rename, reorder, and move preserve an existing ID. Duplicating allocates new
occurrence IDs for the copied subtree and retains definition references. A
built-in layout copies its arrangement into a scene with fresh occurrence IDs;
it does not duplicate shared component definitions. The project captures
built-in definitions it uses so package updates cannot silently change them.
Invalid duplicate IDs make a submitted document unreadable; they never select
an arbitrary target.

## Content and time

Inputs declare a type, required flag, and optional default. The starting types
are video, image, text, number, color, and child content. Binding an instance
input supplies a literal, an asset reference with a source range, or a forward
from an enclosing input. A forwarded value is evaluated at the enclosing
instance; cycles are diagnostics. Child content follows the same binding rule.

An explicit binding wins over the declared default. An explicit empty value
blocks a default and is valid only for an optional input. Missing required
values remain valid *draft* content but block rendering. An element either
holds direct content or refers to an input for that property; both at once are
an error, avoiding a hidden precedence rule. Asset references must resolve to
the declared media type. Text and image elements need no video asset.

Source ranges are half-open `[start, end)` seconds on the asset's source clock.
The canonical result timeline remains the authority for scene order,
transitions, and audio. A scene may set an explicit duration. Without one, a
video scene infers duration from its bound media ranges; a scene containing only
static elements needs an explicit duration. Longer media stops at the scene
boundary. Shorter media shows no frames beyond its range and reports a
diagnostic; it never silently loops or stretches. Video placement does not
implicitly add that video's audio. The scene's audio selection is explicit.
Format selection and layout rules do not change scene duration.

## Edits, history, and deletion

An edit request names a scope: `scene`, `occurrence`, `definition`, or `project`.
Scene edits change one scene-owned layout. Occurrence edits change one use,
including a nested use. Definition edits affect every linked use that has no
local override for the changed property. Local customization can replace a
property or add, remove, and reorder children. A local child insertion gets a
new occurrence-owned ID; reordering retains it. `reset` removes the specific
local choice and restores inheritance. Saving a variation creates a new
definition; relinking the source instance must be explicit.

A shared edit preview reports all affected scene and occurrence paths. Deleting
or changing a definition child that a use customizes, or changing an input to
an incompatible type, is a conflict. It blocks the *whole* proposed revision;
no local work is silently discarded. A stale `expected_revision` also blocks
the edit. Referenced assets and definitions cannot be deleted, including
references in drafts. Required-input changes report every affected binding.
Successful multi-use changes are one project revision; undo restores the
previous definitions, bindings, and customizations together.

Draft proposals that conflict may be kept separately for repair, but they do
not mutate the current project revision. A readable project with missing
bindings or assets can be saved and reloaded with stable diagnostics. Cycles
and unresolved references stop expansion safely. Export checks only selected
scenes and their transitive dependencies.

## Spatial rules and inspection

The scene layout positions an instance's outer box. The component definition
arranges its children inside that box. Percentages refer to the *parent content
box*, after padding. Flow, rows, columns, grids, positioned layers, minimum and
maximum sizes, aspect ratio, gap, alignment, draw order, and explicit overflow
behavior are required capabilities. A child can size to content where the
chosen engine can measure it. Clipping applies at the box that requests it.
The exact property names and CSS subset remain provisional.

Base rules apply first, then the matching format rule, then an occurrence's
local customization for that same property. A rule must identify whether it
matches portrait/landscape or a concrete canvas constraint; ambiguous ties
are diagnostics. Motion applies after base layout at scene/result time to an
element or whole instance subtree. A moving component carries its children.

Inspection asks for one result time, canvas size, and `layout` or `rendered`
view. Both views consume the same measured frame: occurrence paths, rectangles,
transforms, clipping, draw order, media mapping, and the rule or binding that
produced each box. The layout view uses labeled placeholders instead of media
pixels. During a transition, the frame describes both scenes and transition
state. In this mock, the returned rectangle is illustrative and `artifact` is
always `null`; no screenshot is rendered.

## Questions for the renderer spike

This contract deliberately leaves the authoring file grammar, layout engine,
font measurement path, and preview/export frame strategy open. The renderer
spike must test the same frame description with real text, footage, clipping,
alpha, movement, portrait/landscape, and structured/legacy transitions before
those choices become a public persistence format.
