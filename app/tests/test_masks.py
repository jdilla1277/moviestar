"""M26 step 0 — slot shape mask module contracts.

Argv tests assert the generated FFmpeg command; pixel tests decode the
generated mask straight from a rawvideo pipe with stdlib bytes — no
imaging library on either side of the contract.
"""

import subprocess

import pytest

from moviestar.masks import (
    SUPERSAMPLE,
    MaskRenderError,
    build_mask_command,
    ensure_mask,
    mask_filename,
    mask_frame_size,
)


def _pixels(path, width, height):
    raw = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-i", str(path),
            "-f", "rawvideo", "-pix_fmt", "gray", "-",
        ],
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    assert len(raw) == width * height
    return raw


def _px(raw, width, x, y):
    return raw[y * width + x]


class TestMaskCommand:
    def test_circle_core_command_supersamples_and_downscales(self):
        command = build_mask_command(
            "/tmp/mask.png", width=120, height=120, kind="circle"
        )
        assert command[0] == "ffmpeg"
        assert command[-1] == "/tmp/mask.png"
        assert "-frames:v" in command
        assert command[command.index("-frames:v") + 1] == "1"
        source = command[command.index("-i") + 1]
        assert f"s={120 * SUPERSAMPLE}x{120 * SUPERSAMPLE}" in source
        graph = command[command.index("-vf") + 1]
        assert "geq=" in graph
        assert f"scale={120}:{120}:flags=lanczos" in graph
        # Full-range pins plus the tail snap: distro swscale builds
        # leave 1-4 valued noise in flat black, an alpha ghost outside
        # every mask. Neither may be silently dropped.
        assert "in_range=full:out_range=full" in graph
        assert "lutyuv=y='if(lt(val,8),0,if(gt(val,247),255,val))'" in graph
        assert "format=gray" in graph

    def test_ring_frame_is_padded_by_border_width(self):
        assert mask_frame_size(
            width=120, height=120, kind="circle",
            border_width=8, variant="ring",
        ) == (136, 136)
        command = build_mask_command(
            "/tmp/ring.png", width=120, height=120, kind="circle",
            border_width=8, variant="ring",
        )
        source = command[command.index("-i") + 1]
        assert f"s={136 * SUPERSAMPLE}x{136 * SUPERSAMPLE}" in source
        graph = command[command.index("-vf") + 1]
        assert "scale=136:136:flags=lanczos" in graph

    def test_core_frame_matches_content_dimensions(self):
        assert mask_frame_size(width=320, height=240, kind="rounded", radius=24) == (
            320,
            240,
        )

    def test_circle_requires_square_dimensions(self):
        with pytest.raises(ValueError, match="square"):
            build_mask_command("/tmp/m.png", width=120, height=100, kind="circle")

    def test_circle_rejects_explicit_radius(self):
        with pytest.raises(ValueError, match="radius"):
            build_mask_command(
                "/tmp/m.png", width=120, height=120, kind="circle", radius=10
            )

    def test_ring_requires_border_width(self):
        with pytest.raises(ValueError, match="border_width"):
            build_mask_command(
                "/tmp/m.png", width=120, height=120, kind="circle", variant="ring"
            )

    def test_core_rejects_border_width(self):
        with pytest.raises(ValueError, match="border_width"):
            build_mask_command(
                "/tmp/m.png", width=120, height=120, kind="circle", border_width=4
            )

    def test_rounded_radius_cannot_exceed_half_the_short_side(self):
        with pytest.raises(ValueError, match="radius"):
            build_mask_command(
                "/tmp/m.png", width=80, height=60, kind="rounded", radius=31
            )

    def test_unknown_kind_is_rejected(self):
        with pytest.raises(ValueError, match="kind"):
            build_mask_command("/tmp/m.png", width=120, height=120, kind="hexagon")

    def test_unknown_variant_is_rejected(self):
        with pytest.raises(ValueError, match="variant"):
            build_mask_command(
                "/tmp/m.png", width=120, height=120, kind="circle", variant="glow"
            )

    def test_filename_is_deterministic_and_parameter_sensitive(self):
        base = dict(width=120, height=120, kind="circle")
        name = mask_filename(**base)
        assert name == mask_filename(**base)
        assert name.endswith(".png")
        variants = [
            mask_filename(width=140, height=140, kind="circle"),
            mask_filename(width=120, height=100, kind="rounded", radius=12),
            mask_filename(width=120, height=100, kind="rounded", radius=16),
            mask_filename(
                width=120, height=120, kind="circle",
                border_width=8, variant="ring",
            ),
            mask_filename(
                width=120, height=120, kind="circle",
                border_width=6, variant="ring",
            ),
        ]
        assert len({name, *variants}) == len(variants) + 1


class TestMaskPixels:
    def test_circle_core_is_antialiased(self, tmp_path):
        path = ensure_mask(tmp_path, width=120, height=120, kind="circle")
        raw = _pixels(path, 120, 120)
        for corner in ((0, 0), (119, 0), (0, 119), (119, 119)):
            assert _px(raw, 120, *corner) == 0
        assert _px(raw, 120, 59, 59) == 255
        assert _px(raw, 120, 60, 60) == 255
        # Machine-checkable proof of antialiasing: the boundary must
        # contain values strictly between black and white — including
        # the center row, where the circle is tangent to the frame. The
        # edge insets half a pixel so the ramp fits even there; without
        # the inset the tangent arc renders as a hard flat spot.
        row = raw[60 * 120:61 * 120]
        assert any(8 < value < 247 for value in row)
        assert any(8 < value < 247 for value in raw)

    def test_rounded_at_radius_zero_is_pixel_identical_to_rect(self, tmp_path):
        path = ensure_mask(tmp_path, width=80, height=60, kind="rounded", radius=0)
        raw = _pixels(path, 80, 60)
        assert set(raw) == {255}

    def test_rounded_cuts_corners_and_keeps_center_and_edges(self, tmp_path):
        path = ensure_mask(tmp_path, width=120, height=80, kind="rounded", radius=16)
        raw = _pixels(path, 120, 80)
        assert _px(raw, 120, 0, 0) == 0
        assert _px(raw, 120, 60, 40) == 255
        # Straight edges away from the corners stay fully opaque.
        assert _px(raw, 120, 60, 2) == 255
        assert _px(raw, 120, 2, 40) == 255

    def test_circle_ring_sits_outside_the_content_edge(self, tmp_path):
        path = ensure_mask(
            tmp_path, width=120, height=120, kind="circle",
            border_width=8, variant="ring",
        )
        raw = _pixels(path, 136, 136)
        # Inside the content circle the ring is transparent.
        assert _px(raw, 136, 67, 67) == 0
        assert _px(raw, 136, 97, 67) == 0
        # The middle of the ring band, just outside the content radius,
        # is fully opaque.
        assert _px(raw, 136, 131, 67) == 255
        # Outside the outer edge it is transparent again.
        assert _px(raw, 136, 0, 0) == 0

    def test_ensure_mask_generates_once_and_reuses_the_cache(
        self, tmp_path, monkeypatch
    ):
        first = ensure_mask(tmp_path, width=120, height=120, kind="circle")
        assert first.exists()

        def _explode(*args, **kwargs):  # pragma: no cover - guard
            raise AssertionError("cached mask must not be regenerated")

        monkeypatch.setattr("moviestar.masks.subprocess.run", _explode)
        second = ensure_mask(tmp_path, width=120, height=120, kind="circle")
        assert second == first

    def test_render_failure_raises_with_stderr(self, tmp_path, monkeypatch):
        def _fail(command, **kwargs):
            return subprocess.CompletedProcess(
                command, returncode=1, stdout="", stderr="boom"
            )

        monkeypatch.setattr("moviestar.masks.subprocess.run", _fail)
        with pytest.raises(MaskRenderError, match="boom"):
            ensure_mask(tmp_path, width=64, height=64, kind="circle")
        assert list(tmp_path.iterdir()) == []
