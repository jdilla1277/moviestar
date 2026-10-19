# Spatial component model (first implementation slice)

MovieStar can now store reusable component definitions and scene-owned visual
arrangements in `moviestar/spec.json`. The existing `composition` still owns
scene order, duration, and audio. Each key in `spatial.scenes` must match a
named composition scene ID. Older specs without `spatial` continue to work.

The frame command also resolves the current scene and each video element's
source position on the existing result clock. It does not yet measure CSS
layout or render the visual tree. To avoid producing a video
that silently ignores authored visuals, composition export stops when a spatial
scene tree is present. The existing `--source` export remains available.

## Authoring

Create a JSON file containing a `spatial` document, then apply it:

```sh
moviestar composition model apply spatial.json
moviestar composition model inspect scene_a
moviestar composition model frame --at 0:00:03.000
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
        {"id": "left_video", "type": "video", "content": {"input": "left"}, "layout": {"grid_area": "left"}},
        {"id": "right_video", "type": "video", "content": {"input": "right"}, "layout": {"grid_area": "right"}},
        {"id": "title_text", "type": "text", "content": {"input": "title"}}
      ]
    }
  },
  "scenes": {
    "scene_a": {
      "layout": {
        "display": "grid",
        "columns": "1fr 1fr",
        "children": [
          {
            "instance_id": "listeners_a",
            "definition_id": "listener_pair",
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
CSS-like `layout` properties; their measurement and animation belong to the
renderer milestone.

The same definition can be instantiated in `scene_b` with different bindings.
Editing the one definition updates both expansions; each occurrence keeps its
own ID and content. `inspect` reports nested `occurrence_path` values for
addressing individual uses. `frame` selects the active scene using the canonical
result timeline and reports scene-relative result and source time, source media
time, and visibility for each video element. A video range is half-open and
starts at scene source-local time zero. Authored scene speed and holds use the
same mapping as the canonical timeline. Shorter media becomes transparent after
its range ends and produces a warning.

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
result is structural and temporal; it contains no measured boxes or pixels.
At the exact project end, `frame` samples the last output-grid frame and reports
that timestamp as `sample_time_s` alongside the requested `result_time_s`.
At a time inside an authored scene or project-edge transition, `frame` returns
an explicit JSON error because a complete transition frame needs both visual
trees and transition state.
Layout measurement, static-only scenes, and rendered inspection are subsequent
implementation steps. Local editing of an inherited nested instance's input
bindings still requires editing its enclosing definition or applying a complete
spatial document; `input.bind` currently targets scene-owned instances. Input
declaration changes and static-only scene duration remain separate contract
work before this model is ready for export.

Replacing the timeline with `moviestar scenes set` retains visual trees for
scene IDs that remain and removes trees owned by deleted scenes in the same
undoable revision.
