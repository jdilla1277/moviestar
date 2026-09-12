"""Font resolution for the overlay renderer (M19)."""

from pathlib import Path

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
