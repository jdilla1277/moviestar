from __future__ import annotations

import hashlib
import json
import re
import shutil
import struct
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins" / "moviestar"
SKILL = PLUGIN / "skills" / "moviestar"
BUILD_ARCHIVE = ROOT / "bin" / "build-plugin-zip"
AGENT_PLUGINS_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
TEXT_SUFFIXES = {".json", ".md", ".svg", ".yaml", ".yml"}
PUBLIC_DOCS = (
    ROOT / "docs" / "README.md",
    ROOT / "docs" / "getting-started.md",
    ROOT / "docs" / "agent-guide.md",
    ROOT / "docs" / "install-skill.md",
    ROOT / "docs" / "skill-validation.md",
    ROOT / "docs" / "plugin-submission.md",
    ROOT / "docs" / "releases" / "v0.7.0.md",
    ROOT / "docs" / "releases" / "v0.7.1.md",
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


def test_v070_release_notes_lead_with_the_product_and_demo() -> None:
    notes = (ROOT / "docs" / "releases" / "v0.7.0.md").read_text()

    assert notes.startswith("# A video editor for AI agents")
    assert "https://trymoviestar.com" in notes
    assert "https://youtu.be/8CbBgtiSlNw" in notes
    assert "https://i.ytimg.com/vi/8CbBgtiSlNw/hqdefault.jpg" in notes


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


def test_skill_declares_runtime_binaries_and_uses_the_pypi_release() -> None:
    skill = (SKILL / "SKILL.md").read_text()
    frontmatter = skill.split("---", 2)[1]
    installation = (SKILL / "references" / "installation.md").read_text()

    assert "metadata:\n  openclaw:\n    requires:\n      bins:" in frontmatter
    for binary in ("moviestar", "ffmpeg", "ffprobe"):
        assert f"        - {binary}\n" in frontmatter

    assert "python -m pip install moviestar" in installation
    assert "git+https://" not in installation


def test_public_docs_and_skill_contain_no_private_repository_markers() -> None:
    paths = (
        *PUBLIC_DOCS,
        ROOT / ".agents" / "plugins" / "marketplace.json",
        *(
            path
            for path in PLUGIN.rglob("*")
            if path.is_file() and path.suffix in TEXT_SUFFIXES
        ),
    )
    forbidden = ("prd/", ".context/", "moviestar-internal", "/Users/jamesdillard")

    for path in paths:
        text = path.read_text()
        for marker in forbidden:
            assert marker not in text, f"{path} contains private marker {marker!r}"


def _openai_interface(manifest: dict[str, object]) -> dict[str, object]:
    return manifest["extensions"]["com.openai"]["interface"]  # type: ignore[index]


def _png_size(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"
    return struct.unpack(">II", header[16:24])


def _svg_viewbox(path: Path) -> tuple[float, float]:
    match = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', path.read_text())
    assert match, f"{path} has no origin-based viewBox"
    return float(match.group(1)), float(match.group(2))


def _build_archive(output: Path) -> dict[str, object]:
    result = subprocess.run(
        [sys.executable, str(BUILD_ARCHIVE), "--output", str(output)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_root_manifest_is_a_portable_agent_plugins_manifest() -> None:
    manifest = _json(PLUGIN / "plugin.json")
    codex = _json(PLUGIN / ".codex-plugin" / "plugin.json")

    # Agent Plugins 1.0.0 rejects unknown top-level keys; client data lives
    # under reverse-domain extension namespaces.
    allowed = {
        "$schema", "name", "version", "description", "author", "homepage",
        "repository", "license", "keywords", "extensions",
    }
    assert set(manifest) <= allowed
    assert manifest["$schema"] == AGENT_PLUGINS_SCHEMA
    assert re.fullmatch(r"(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", manifest["name"])
    for key in ("name", "version", "description", "author", "homepage",
                "repository", "license", "keywords"):
        assert manifest[key] == codex[key], key


def test_openai_listing_metadata_fits_directory_limits() -> None:
    interface = _openai_interface(_json(PLUGIN / "plugin.json"))

    assert interface["displayName"] == "MovieStar"
    assert 0 < len(interface["shortDescription"]) <= 30
    assert 0 < len(interface["longDescription"]) <= 4000
    assert 0 < len(interface["developerName"]) <= 80
    assert interface["category"] == "Productivity"
    assert interface["capabilities"]
    assert interface["supportURL"] == "https://github.com/jdilla1277/moviestar/issues"

    prompts = interface["defaultPrompt"]
    assert 1 <= len(prompts) <= 3
    assert len(set(prompts)) == len(prompts)
    for prompt in prompts:
        assert len(prompt) <= 128, prompt
        # Directory prompts must work on every surface: no Codex `$skill`
        # syntax and no app @mentions.
        assert "$" not in prompt and "@" not in prompt, prompt


def test_listing_claims_only_validated_environments() -> None:
    interface = _openai_interface(_json(PLUGIN / "plugin.json"))
    listing = f"{interface['shortDescription']} {interface['longDescription']}"

    # Hosted ChatGPT execution is unproven until the Work Cloud probe passes.
    # Update this guard together with the listing once that evidence exists.
    for claim in ("ChatGPT", "Work Cloud", "cloud", "no install"):
        assert claim not in listing, claim
    assert "Python" in interface["longDescription"]
    assert "FFmpeg" in interface["longDescription"]


def test_codex_manifest_shares_the_listing_metadata() -> None:
    portable = _openai_interface(_json(PLUGIN / "plugin.json"))
    codex = _json(PLUGIN / ".codex-plugin" / "plugin.json")["interface"]

    assert codex == portable


def test_listing_icons_are_square_supported_assets() -> None:
    interface = _openai_interface(_json(PLUGIN / "plugin.json"))

    for field in ("logo", "composerIcon"):
        relative = interface[field]
        assert relative.startswith("./assets/"), relative
        path = (PLUGIN / relative).resolve()
        assert path.is_file() and path.is_relative_to(PLUGIN.resolve()), relative
        assert path.stat().st_size <= 5 * 1024 * 1024
        if path.suffix == ".png":
            width, height = _png_size(path)
            assert width == height and 48 <= width <= 4096, (width, height)
        else:
            assert path.suffix == ".svg", relative
            width, height = _svg_viewbox(path)
            assert width == height and width >= 48, (width, height)
    assert re.fullmatch(r"#[0-9a-f]{6}", interface["brandColor"])


def test_plugin_is_intentionally_skills_only() -> None:
    for name in ("mcp.json", ".mcp.json", ".app.json", "hooks"):
        assert not (PLUGIN / name).exists(), name
    for manifest in (PLUGIN / "plugin.json", PLUGIN / ".codex-plugin" / "plugin.json"):
        text = manifest.read_text()
        for key in ('"mcpServers"', '"apps"', '"hooks"'):
            assert key not in text, f"{manifest} declares {key}"


def test_plugin_archive_is_reproducible_and_complete(tmp_path: Path) -> None:
    first = _build_archive(tmp_path / "first")
    second = _build_archive(tmp_path / "second")
    version = _json(PLUGIN / "plugin.json")["version"]

    archive = Path(first["path"])
    assert archive.name == f"moviestar-plugin-{version}.zip"
    assert first["sha256"] == second["sha256"]
    assert first["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert Path(second["path"]).read_bytes() == archive.read_bytes()

    expected = {
        path.relative_to(PLUGIN).as_posix()
        for path in PLUGIN.rglob("*")
        if path.is_file() and path.name != ".DS_Store" and "__pycache__" not in path.parts
    } | {"LICENSE", "TRADEMARKS.md"}
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        assert names == sorted(names)
        assert set(names) == expected
        assert first["files"] == names
        assert bundle.read("plugin.json") == (PLUGIN / "plugin.json").read_bytes()
        assert bundle.read("LICENSE") == (ROOT / "LICENSE").read_bytes()
        interface = _openai_interface(json.loads(bundle.read("plugin.json")))
        for field in ("logo", "composerIcon"):
            assert interface[field].removeprefix("./") in names


def test_plugin_archive_build_refuses_mcp_configuration(tmp_path: Path) -> None:
    plugin = tmp_path / "plugin"
    shutil.copytree(PLUGIN, plugin)
    (plugin / "mcp.json").write_text("{}")

    result = subprocess.run(
        [
            sys.executable, str(BUILD_ARCHIVE),
            "--plugin", str(plugin), "--output", str(tmp_path / "dist"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "skills-only" in result.stderr
    assert not (tmp_path / "dist").exists()


def test_submission_guide_documents_build_and_claim_boundary() -> None:
    guide = (ROOT / "docs" / "plugin-submission.md").read_text()
    install = (ROOT / "docs" / "install-skill.md").read_text()

    assert "bin/build-plugin-zip" in guide
    assert "skill scan" in guide
    assert "codex plugin marketplace add ." in install
    assert "plugin-submission.md" in install
