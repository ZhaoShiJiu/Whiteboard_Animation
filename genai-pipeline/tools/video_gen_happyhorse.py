"""
HappyHorse Video Generation Tool — happyhorse-1.0/1.1-i2v via Alibaba DashScope.

Generates a short AI video clip from a static whiteboard image + text prompt.

Key differences from Seedance:
  - NO aspect_ratio parameter (aspect ratio follows the input image automatically)
  - Resolution uses uppercase: "720P" / "1080P"
  - Duration range: [3, 15] seconds
  - Async-only API (X-DashScope-Async: enable header, handled by provider)
"""

import os
import time
from typing import Optional

from .utils import GLOBAL_OUTPUT_DIR, _emit, postprocess_ai_video

try:
    from log_utils import ContextLogger
except ImportError:
    ContextLogger = None  # type: ignore

# HappyHorse API options (Alibaba DashScope)
# - duration: [3, 15] seconds, default 5
# - resolution: "720P" or "1080P" (uppercase per DashScope docs)
# - NO aspect_ratio — follows first-frame image dimensions automatically
HAPPYHORSE_DURATION = 8
HAPPYHORSE_RESOLUTION = "1080P"


def generate_video_happyhorse_tool_fn(
    image_path: str,
    prompt: str,
    logger: Optional["ContextLogger"] = None,
) -> str:
    """
    Generates a whiteboard animation video using HappyHorse-1.0-I2V.

    Args:
        image_path: Absolute path to the input image (first frame).
        prompt: Scene description to animate.
        logger: Optional ContextLogger.

    Returns:
        Path to the generated MP4 video, or an error message string.
    """
    if not os.path.exists(image_path):
        return f"Error: Input image file not found at {image_path}"

    try:
        from ai_gateway import generate

        _emit(logger, "info", "Generating AI video via HappyHorse...",
              extra={"image_path": image_path})

        with open(image_path, "rb") as f:
            img_bytes = f.read()

        response = generate(
            task="video_happyhorse",
            prompt=prompt,
            reference_images=[img_bytes],
            options={
                "duration": HAPPYHORSE_DURATION,
                "resolution": HAPPYHORSE_RESOLUTION,
                # NOTE: No aspect_ratio — HappyHorse auto-follows first-frame dimensions
            },
        )

        # Save video bytes to file
        video_bytes = response.content
        timestamp = int(time.time())
        filename = f"happyhorse_video_{timestamp}.mp4"
        output_path = os.path.join(GLOBAL_OUTPUT_DIR, filename) if GLOBAL_OUTPUT_DIR else filename

        with open(output_path, "wb") as f:
            f.write(video_bytes)

        _emit(logger, "info", "HappyHorse video saved",
              extra={"path": output_path, "provider": response.provider,
                     "size_bytes": len(video_bytes)})

        # Shared post-processing (audio extraction, audio strip, first-frame replace)
        return postprocess_ai_video(output_path, image_path, logger=logger, provider_name="HappyHorse")

    except Exception as e:
        _emit(logger, "error", f"HappyHorse video generation failed: {e}", extra={"error": str(e)})
        return f"An error occurred during HappyHorse video generation: {str(e)}"
