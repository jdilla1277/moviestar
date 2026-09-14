from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins" / "moviestar"
SKILL = PLUGIN / "skills" / "moviestar"
PUBLIC_DOCS = (
    ROOT / "docs" / "README.md",
    ROOT / "docs" / "getting-started.md",
    ROOT / "docs" / "agent-guide.md",
    ROOT / "docs" / "install-skill.md",
    ROOT / "docs" / "skill-validation.md",
)


def _json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text())


def _relative_markdown_links(path: Path) -> list[Path]:
    links = re.findall(r"\[[^]]*\]\(([^)]+)\)", path.read_text())
    resolved: list[Path] = []
    for link in links:
        target = link.split("#", 1)[0]
        if not target or "://" in target or target.startswith(("mailto:", "#")):
            continue
        resolved.append((path.parent / target).resolve())
    return resolved


def test_wp3_public_documentation_set_exists_and_has_valid_local_links() -> None:
    for path in PUBLIC_DOCS:
        assert path.is_file(), path
        for target in _relative_markdown_links(path):
            assert target.exists(), f"broken link from {path}: {target}"


def test_public_readme_routes_users_to_docs_and_skill_installation() -> None:
    readme = (ROOT / "README.md").read_text()
    assert "docs/getting-started.md" in readme
    assert "docs/agent-guide.md" in readme
    assert "docs/install-skill.md" in readme


def test_dev_gitignore_excludes_package_test_workspace() -> None:
    ignored = (ROOT / ".gitignore").read_text().splitlines()
    assert "/app/moviestar/" in ignored


def test_plugin_manifest_matches_package_identity() -> None:
    manifest = _json(PLUGIN / ".codex-plugin" / "plugin.json")
    pyproject = tomllib.loads((ROOT / "app" / "pyproject.toml").read_text())

    assert manifest["name"] == "moviestar"
    assert manifest["version"] == pyproject["project"]["version"]
    assert manifest["license"] == "Apache-2.0"
    assert manifest["repository"] == "https://github.com/jdilla1277/moviestar"
    assert manifest["skills"] == "./skills/"


def test_repository_marketplace_exposes_moviestar_plugin() -> None:
    marketplace = _json(ROOT / ".agents" / "plugins" / "marketplace.json")
    assert marketplace["name"] == "moviestar"
    assert marketplace["interface"]["displayName"] == "MovieStar"

    assert marketplace["plugins"] == [
        {
            "name": "moviestar",
            "source": {"source": "local", "path": "./plugins/moviestar"},
            "policy": {
                "installation": "AVAILABLE",
                "authentication": "ON_INSTALL",
            },
            "category": "Productivity",
        }
    ]


def test_skill_has_portable_metadata_and_resolvable_references() -> None:
    skill_md = SKILL / "SKILL.md"
    text = skill_md.read_text()

    assert text.startswith("---\nname: moviestar\n")
    assert "description:" in text.split("---", 2)[1]
    assert "[TODO:" not in text
    for target in _relative_markdown_links(skill_md):
        assert target.exists(), f"broken skill reference: {target}"
        assert target.is_relative_to(SKILL), f"skill reference escapes plugin: {target}"

    openai_yaml = (SKILL / "agents" / "openai.yaml").read_text()
    assert 'display_name: "MovieStar"' in openai_yaml
    assert "$moviestar" in openai_yaml


def test_public_docs_and_skill_contain_no_private_repository_markers() -> None:
    paths = (
        *PUBLIC_DOCS,
        ROOT / ".agents" / "plugins" / "marketplace.json",
        *(path for path in PLUGIN.rglob("*") if path.is_file()),
    )
    forbidden = ("prd/", ".context/", "moviestar-internal", "/Users/jamesdillard")

    for path in paths:
        text = path.read_text()
        for marker in forbidden:
            assert marker not in text, f"{path} contains private marker {marker!r}"
