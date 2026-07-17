"""
Seedance Video Generation Tool — Doubao-Seedance-2.0 via Volcengine Ark.

Generates a short AI video clip from a static whiteboard image + text prompt.
"""

import os
import time
from typing import Optional

from . import utils
from .utils import _emit, postprocess_ai_video

try:
    from log_utils import ContextLogger
except ImportError:
    ContextLogger = None  # type: ignore

# Seedance API options (Volcengine Ark)
SEEDANCE_DURATION = 8
SEEDANCE_RESOLUTION = "1080p"
SEEDANCE_ASPECT_RATIO = "16:9"


def generate_video_seedance_tool_fn(
    image_path: str,
    prompt: str,
    logger: Optional["ContextLogger"] = None,
) -> str:
    """
    Generates a whiteboard animation video using Doubao-Seedance-2.0.

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

        _emit(logger, "info", "Generating AI video via Seedance...",
              extra={"image_path": image_path})

        with open(image_path, "rb") as f:
            img_bytes = f.read()

        response = generate(
            task="video",
            prompt=prompt,
            reference_images=[img_bytes],
            options={
                "duration": SEEDANCE_DURATION,
                "resolution": SEEDANCE_RESOLUTION,
                "aspect_ratio": SEEDANCE_ASPECT_RATIO,
            },
        )

        # Save video bytes to file
        video_bytes = response.content
        timestamp = int(time.time())
        filename = f"seedance_video_{timestamp}.mp4"
        output_path = os.path.join(utils.GLOBAL_OUTPUT_DIR, filename) if utils.GLOBAL_OUTPUT_DIR else filename

        with open(output_path, "wb") as f:
            f.write(video_bytes)

        _emit(logger, "info", "Seedance video saved",
              extra={"path": output_path, "provider": response.provider,
                     "size_bytes": len(video_bytes)})

        # Shared post-processing (audio extraction, audio strip, first-frame replace)
        return postprocess_ai_video(output_path, image_path, logger=logger, provider_name="Seedance")

    except Exception as e:
        _emit(logger, "error", f"Seedance video generation failed: {e}", extra={"error": str(e)})
        return f"An error occurred during Seedance video generation: {str(e)}"
