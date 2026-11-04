"""Font resolution for the overlay renderer (M19)."""

import errno
import json
from pathlib import Path
import shutil
import stat
import sys

from click.testing import CliRunner

import pytest

from moviestar.fonts import (
    DEFAULT_FONT_FAMILY,
    FontResolutionError,
    add_font,
    bundled_font_dir,
    list_managed_fonts,
    normalize_weight,
    resolve_font,
)


class TestBundledFonts:
    def test_bundled_files_ship_with_the_package(self):
        font_dir = bundled_font_dir()
        assert (font_dir / "Inter-Regular.ttf").is_file()
        assert (font_dir / "Inter-Bold.ttf").is_file()
        assert (font_dir / "OFL-LICENSE.txt").is_file()

    def test_default_family_resolves_bundled(self):
        font = resolve_font("Inter", "bold")
        assert font["source"] == "bundled"
        assert font["family"] == DEFAULT_FONT_FAMILY
        assert font["weight"] == "bold"
        assert Path(font["path"]).name == "Inter-Bold.ttf"
        assert Path(font["path"]).is_file()

    def test_family_match_ignores_case_and_spacing(self):
        assert resolve_font("inter")["source"] == "bundled"
        assert resolve_font(" INTER ")["source"] == "bundled"

    def test_normal_weight_resolves_regular_file(self):
        font = resolve_font("Inter", "normal")
        assert Path(font["path"]).name == "Inter-Regular.ttf"


class TestWeightNormalization:
    def test_numeric_and_keyword_bold_weights(self):
        assert normalize_weight("bold") == "bold"
        assert normalize_weight(700) == "bold"
        assert normalize_weight("900") == "bold"

    def test_everything_else_is_normal(self):
        assert normalize_weight("normal") == "normal"
        assert normalize_weight(400) == "normal"
        assert normalize_weight("light") == "normal"


class TestUnavailableFont:
    def test_unknown_family_raises_with_bundled_default(self):
        with pytest.raises(FontResolutionError) as exc_info:
            resolve_font("NoSuchFontFamilyXYZ")
        message = str(exc_info.value)
        assert "NoSuchFontFamilyXYZ" in message
        assert DEFAULT_FONT_FAMILY in message


class TestManagedFonts:
    def test_project_font_alias_resolves_before_system(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "moviestar").mkdir()
        (tmp_path / "moviestar" / "project.json").write_text("{}")

        font = add_font(
            bundled_font_dir() / "Inter-Regular.ttf",
            name="Project Caption",
            scope="project",
        )
        assert font["family"] == "Project Caption"
        assert font["source"] == "project"
        assert Path(font["path"]).is_file()

        resolved = resolve_font("Project Caption")
        assert resolved["family"] == "Project Caption"
        assert resolved["source"] == "project"
        assert resolved["ass_family"] == "Inter"

        listed = list_managed_fonts()
        assert any(
            item["family"] == "Project Caption" and item["source"] == "project"
            for item in listed
        )


@pytest.fixture
def font_registration_dirs(tmp_path, monkeypatch):
    project = tmp_path / "moviestar"
    project.mkdir()
    (project / "project.json").write_text("{}")
    user = tmp_path / "user-fonts"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("moviestar.fonts._USER_FONT_DIR", user)
    return {"project": project / "fonts", "user": user}


@pytest.mark.parametrize("scope", ["project", "user"])
def test_readable_font_registers_when_metadata_copy_is_forbidden(
    tmp_path, monkeypatch, font_registration_dirs, scope
):
    source = tmp_path / "readable.ttf"
    content = (bundled_font_dir() / "Inter-Regular.ttf").read_bytes()
    source.write_bytes(content)
    source.chmod(0o444)

    def forbidden_metadata(*args, **kwargs):
        raise PermissionError(errno.EPERM, "system metadata cannot be copied")

    monkeypatch.setattr(shutil, "copystat", forbidden_metadata)
    font = add_font(source, name="Readable Font", scope=scope)
    target = Path(font["path"])
    assert target.parent == font_registration_dirs[scope]
    assert target.read_bytes() == content
    assert target.stat().st_mode & stat.S_IWUSR
    assert font["font_family"] == "Inter"
    assert font["source"] == scope
    assert resolve_font("Readable Font")["path"] == str(target)
    # Registering the managed file itself must not rewrite/truncate it.
    modified = target.stat().st_mtime_ns
    assert add_font(target, name="Readable Font", scope=scope)["path"] == str(target)
    assert target.stat().st_mtime_ns == modified
    manifest = json.loads((target.parent / "fonts.json").read_text())
    assert len(manifest["fonts"]) == 1
    assert manifest["fonts"][0]["path"] == target.name
    assert manifest["fonts"][0]["source"] == scope


@pytest.mark.parametrize("scope", ["project", "user"])
def test_font_content_copy_preserves_collision_handling_and_weight_resolution(
    font_registration_dirs, scope
):
    regular_source = bundled_font_dir() / "Inter-Regular.ttf"
    bold_source = bundled_font_dir() / "Inter-Bold.ttf"
    regular = add_font(regular_source, name="Shared Alias", scope=scope)
    bold = add_font(bold_source, name="Shared Alias", scope=scope)
    assert regular["path"] != bold["path"]
    assert Path(regular["path"]).read_bytes() == regular_source.read_bytes()
    assert Path(bold["path"]).read_bytes() == bold_source.read_bytes()
    assert resolve_font("Shared Alias", "normal")["path"] == regular["path"]
    assert resolve_font("Shared Alias", "bold")["path"] == bold["path"]
    assert add_font(bold_source, name="Shared Alias", scope=scope)["path"] == bold["path"]
    manifest = json.loads((font_registration_dirs[scope] / "fonts.json").read_text())
    assert len(manifest["fonts"]) == 2
    assert {entry["path"] for entry in manifest["fonts"]} == {
        Path(regular["path"]).name, Path(bold["path"]).name,
    }


@pytest.mark.parametrize("failure", ["invalid", "unreadable"])
def test_font_registration_errors_preserve_existing_manifest(
    tmp_path, monkeypatch, font_registration_dirs, failure
):
    from moviestar.cli import cli

    existing = add_font(bundled_font_dir() / "Inter-Regular.ttf",
                        name="Existing Font", scope="project")
    manifest = font_registration_dirs["project"] / "fonts.json"
    before = manifest.read_bytes()
    source = tmp_path / "bad.ttf"
    if failure == "invalid":
        source.write_bytes(b"not a font")
    else:
        source.write_bytes((bundled_font_dir() / "Inter-Regular.ttf").read_bytes())
        original_read = Path.read_bytes

        def deny_source_read(path):
            if path == source:
                raise PermissionError(errno.EACCES, "font content cannot be read", str(path))
            return original_read(path)

        monkeypatch.setattr(Path, "read_bytes", deny_source_read)
    result = CliRunner().invoke(cli, ["fonts", "add", str(source),
                                     "--name", "Bad Font", "--scope", "project"])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["command"] == "fonts add"
    assert data["error"]
    assert data["hint"]
    assert manifest.read_bytes() == before
    assert not (font_registration_dirs["project"] / "Bad_Font.ttf").exists()
    assert resolve_font("Existing Font")["path"] == existing["path"]


@pytest.mark.skipif(sys.platform != "darwin", reason="requires macOS system fonts")
@pytest.mark.parametrize("scope", ["project", "user"])
def test_real_macos_system_font_registers_without_protected_flags(
    font_registration_dirs, scope
):
    source = Path("/System/Library/Fonts/Geneva.ttf")
    if not source.is_file():
        pytest.skip("Geneva system font is unavailable")
    content = source.read_bytes()
    flags = source.stat().st_flags
    font = add_font(source, name="System Geneva", scope=scope)
    target = Path(font["path"])
    assert target.parent == font_registration_dirs[scope]
    assert target.read_bytes() == content
    assert not target.stat().st_flags & getattr(stat, "UF_RESTRICTED", 0x80000)
    assert source.stat().st_flags == flags
    assert resolve_font("System Geneva")["path"] == str(target)
