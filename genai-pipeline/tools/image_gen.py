import os
import uuid
from typing import Optional

from . import utils
from .utils import _emit

try:
    from log_utils import ContextLogger
except ImportError:
    ContextLogger = None  # type: ignore


def image_gen_tool_fn(
    prompt: str,
    reference_image_path: str = None,
    subject_reference_image_path: str = None,
    logger: Optional["ContextLogger"] = None,
) -> str:
    """
    Generates a whiteboard animation image using Qwen-Image-2.0-Pro via the AI Gateway.

    Args:
        prompt: The specific text prompt to generate an image for.
        reference_image_path: Optional path to a previously generated image for aesthetic consistency.
        subject_reference_image_path: Optional path to a real-world photo of the subject.
        logger: Optional ContextLogger for structured logging.
    Returns:
        The path to the generated image or an error message.
    """

    try:
        # Build the enhanced prompt
        enhanced_prompt = (
            prompt
            + " Ensure the generated image is in 16:9 aspect ratio (1920x1080). "
            "CRITICAL: DO NOT draw any hands, human arms, markers, pens, or people drawing. "
            "CRITICAL: DO NOT draw any picture frames, borders, decorative edges, "
            "wooden frames, metal frames, or any enclosing boundary around the artwork. "
            "The artwork must extend edge-to-edge with NO frame of any kind. "
            "Draw ONLY the pure artwork on the whiteboard."
        )

        # Collect reference images as bytes
        reference_images = []
        ref_count = 0

        # Style consistency reference
        if reference_image_path and os.path.exists(reference_image_path):
            try:
                with open(reference_image_path, "rb") as f:
                    reference_images.append(f.read())
                    ref_count += 1
            except Exception as e:
                _emit(logger, "warning", f"Could not read reference image: {reference_image_path}",
                      extra={"error": str(e)})

        # Real-world subject reference (Internet image)
        if subject_reference_image_path and os.path.exists(subject_reference_image_path):
            try:
                with open(subject_reference_image_path, "rb") as f:
                    reference_images.append(f.read())
                    ref_count += 1
            except Exception as e:
                _emit(logger, "warning", f"Could not read subject reference image: {subject_reference_image_path}",
                      extra={"error": str(e)})

        from ai_gateway import generate

        _emit(logger, "info", "Calling image generation API...",
              extra={"prompt_length": len(enhanced_prompt), "reference_images": ref_count})

        response = generate(
            task="image",
            prompt=enhanced_prompt,
            reference_images=reference_images if reference_images else None,
            options={"aspect_ratio": "16:9"},
        )

        # Save generated image to file
        image_bytes = response.content
        filename = f"generated_image_{uuid.uuid4().hex[:8]}.png"
        output_path = os.path.join(utils.GLOBAL_OUTPUT_DIR, filename) if utils.GLOBAL_OUTPUT_DIR else filename

        with open(output_path, "wb") as f:
            f.write(image_bytes)

        _emit(logger, "info", f"Image generated",
              extra={"path": output_path, "provider": response.provider, "model": response.model})
        return output_path

    except Exception as e:
        _emit(logger, "error", f"Image generation failed: {e}", extra={"error": str(e)})
        return f"An error occurred during image generation: {str(e)}"
