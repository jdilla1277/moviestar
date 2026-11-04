"""Delivery settings independent of the project's authored geometry."""

REVIEW_PROFILE_NAME = "review-720p"
REVIEW_LONG_EDGE = 1280
REVIEW_SHORT_EDGE = 720

# Pad odd authored dimensions consistently with full H.264 exports, then fit
# inside an orientation-aware box. min(input, limit) prevents upscaling.
REVIEW_VIDEO_FILTER = (
    "pad=ceil(iw/2)*2:ceil(ih/2)*2,"
    f"scale=w='if(gte(iw,ih),min(iw,{REVIEW_LONG_EDGE}),min(iw,{REVIEW_SHORT_EDGE}))':"
    f"h='if(gte(iw,ih),min(ih,{REVIEW_SHORT_EDGE}),min(ih,{REVIEW_LONG_EDGE}))':"
    "force_original_aspect_ratio=decrease:force_divisible_by=2"
)


def review_video_settings(encoder: str) -> dict:
    if encoder == "x264":
        return {"encoder": encoder, "codec": "libx264", "preset": "fast",
                "crf": 23, "pixel_format": "yuv420p"}
    if encoder == "videotoolbox":
        return {"encoder": encoder, "codec": "h264_videotoolbox",
                "target_bitrate": "4M", "profile": "high", "pixel_format": "yuv420p"}
    raise ValueError(f"Unknown review-profile video encoder: {encoder!r}")


def review_video_args(encoder: str) -> list[str]:
    settings = review_video_settings(encoder)
    args = ["-c:v", settings["codec"]]
    if encoder == "x264":
        args += ["-preset", settings["preset"], "-crf", str(settings["crf"])]
    else:
        args += ["-b:v", settings["target_bitrate"], "-profile:v", settings["profile"]]
    return [*args, "-pix_fmt", settings["pixel_format"]]


def review_profile_receipt(encoder: str) -> dict:
    return {
        "name": REVIEW_PROFILE_NAME,
        "max_dimensions": {"long_edge": REVIEW_LONG_EDGE, "short_edge": REVIEW_SHORT_EDGE},
        "upscale": False,
        "resize_stage": "after_composition_and_text",
        "video_encoding": review_video_settings(encoder),
        "actual_dimensions": None,
    }
