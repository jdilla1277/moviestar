"""Small data-driven expansion proof; all component definitions use one path."""

from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent

DEFINITIONS = {
    "Stack": {
        "origin": "built_in",
        "tree": {"tag": "div", "class": {"input": "class"},
                 "id": {"input": "id"}, "children": {"input": "children"}},
    },
    "ListenerPair": {
        "origin": "custom",
        "tree": {
            "component": "Stack",
            "props": {
                "class": "pair", "id": "listener-pair",
                "children": [
                    {"tag": "div", "class": "listener", "id": "alice",
                     "children": [{"tag": "video", "id": "alice-video",
                                   "asset": {"input": "left"}}]},
                    {"tag": "div", "class": "listener", "id": "bob",
                     "children": [{"tag": "video", "id": "bob-video",
                                   "asset": {"input": "right"}}]},
                ],
            },
        },
    },
    "VideoCard": {
        "origin": "custom",
        "tree": {
            "tag": "div", "class": "card", "id": "card",
            "children": [
                {"tag": "video", "id": "video", "asset": {"input": "source"}},
                {"tag": "div", "class": "badge", "id": "badge",
                 "text": {"input": "badge"}},
            ],
        },
    },
}


def resolve(value, inputs):
    if isinstance(value, dict) and set(value) == {"input"}:
        return inputs[value["input"]]
    return value


def expand(node, inputs=None, path=()):
    inputs = inputs or {}
    if "component" in node:
        definition = DEFINITIONS[node["component"]]
        props = {key: resolve(value, inputs) for key, value in node["props"].items()}
        return expand(definition["tree"], {**inputs, **props},
                      (*path, node["component"]))

    tag = node["tag"]
    classes = resolve(node.get("class", ""), inputs)
    node_id = resolve(node.get("id", ""), inputs)
    attrs = f' class="{escape(classes)}"' if classes else ""
    if node_id:
        attrs += f' data-node="{escape(node_id)}"'
        attrs += f' data-path="{escape("/".join((*path, node_id)))}"'
    if tag == "video":
        name = resolve(node["asset"], inputs)
        uri = (ROOT / f"{name}.mp4").as_uri()
        occurrence = "/".join((*path, node_id))
        return (f'<div class="media" data-node="{escape(node_id)}" '
                f'data-path="{escape(occurrence)}" '
                f'data-label="VIDEO: {escape(name.upper())}"><video muted '
                f'playsinline preload="auto" src="{uri}"></video></div>')
    children = resolve(node.get("children", []), inputs)
    body = "".join(expand(child, inputs, (*path, node_id)) for child in children)
    text = resolve(node.get("text", ""), inputs)
    return f"<{tag}{attrs}>{escape(text)}{body}</{tag}>"


def podcast(left, right, title="Three voices, one conversation"):
    scene = {
        "tag": "div", "class": "root podcast", "id": "scene",
        "children": [
            {"tag": "div", "class": "pod-title", "id": "title", "text": title},
            {"tag": "div", "class": "pod-main", "id": "host",
             "children": [{"tag": "video", "id": "host-video", "asset": "host"}]},
            {"component": "ListenerPair", "props": {"left": left, "right": right}},
            {"tag": "div", "class": "pod-caption", "id": "caption",
             "text": "We have a lot to unpack about today's story."},
        ],
    }
    return expand(scene)
