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
    enable_retrieval: bool = True,
    provider: str = "qwen",
) -> str:
    """
    Generates a whiteboard animation image using Qwen-Image-2.0-Pro via the AI Gateway.

    Before generating, checks the image library for a semantically similar existing
    image (via Doubao-Embedding-Vision).  If a match is found above the threshold,
    the existing image is reused, saving time and API cost.

    Args:
        prompt: The specific text prompt to generate an image for.
        reference_image_path: Optional path to a previously generated image for
            aesthetic consistency.
        subject_reference_image_path: Optional path to a real-world photo of the
            subject.
        logger: Optional ContextLogger for structured logging.
        enable_retrieval: If False, skip the image-library lookup (emergency override).

    Returns:
        The path to the generated (or reused) image, or an error message.
    """

    # ── Step 0: Check image library for a reusable match ─────────────────
    if enable_retrieval:
        try:
            from tools.image_library import retrieve_best_match

            _emit(logger, "info", "Checking image library for reusable match...",
                  extra={"prompt_len": len(prompt)})

            match = retrieve_best_match(prompt, threshold=0.85)

            if match:
                image_id = match["image_id"]
                similarity = match["similarity"]
                image_bytes = match["image_bytes"]

                # Write the reusable image to the current output directory
                filename = f"generated_image_{uuid.uuid4().hex[:8]}.png"
                output_path = (
                    os.path.join(utils.GLOBAL_OUTPUT_DIR, filename)
                    if utils.GLOBAL_OUTPUT_DIR
                    else filename
                )

                with open(output_path, "wb") as f:
                    f.write(image_bytes)

                _emit(logger, "info",
                      f"Image REUSED from library (id={image_id}, sim={similarity:.4f})",
                      extra={"image_id": image_id, "similarity": round(similarity, 4),
                             "path": output_path})
                return output_path
        except Exception as e:
            _emit(logger, "warning",
                  f"Image retrieval check failed (non-critical): {e}. Proceeding to generation.",
                  extra={"error": str(e)})

    # ── Step 1: Build the enhanced prompt ────────────────────────────────
    try:
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
            options={"aspect_ratio": "16:9", "provider": provider},
        )

        # Save generated image to file
        image_bytes = response.content
        filename = f"generated_image_{uuid.uuid4().hex[:8]}.png"
        output_path = os.path.join(utils.GLOBAL_OUTPUT_DIR, filename) if utils.GLOBAL_OUTPUT_DIR else filename

        with open(output_path, "wb") as f:
            f.write(image_bytes)

        _emit(logger, "info", f"Image generated",
              extra={"path": output_path, "provider": response.provider, "model": response.model})

        # ── Step 3: Store in image library for future reuse ──────────────
        try:
            from tools.image_library import process_and_store_image

            # Determine run_id from output path
            run_id = ""
            if utils.GLOBAL_OUTPUT_DIR:
                run_dir = os.path.basename(utils.GLOBAL_OUTPUT_DIR)
                if run_dir.startswith("run_"):
                    run_id = run_dir

            image_id = process_and_store_image(
                image_bytes=image_bytes,
                prompt=prompt,
                scene_desc=prompt[:500],
                source_run_id=run_id,
                source="ai_gen",
            )
            if image_id > 0:
                _emit(logger, "info", f"Image stored in library",
                      extra={"image_id": image_id})
        except Exception as e:
            _emit(logger, "warning",
                  f"Failed to store image in library (non-critical): {e}",
                  extra={"error": str(e)})

        return output_path

    except Exception as e:
        _emit(logger, "error", f"Image generation failed: {e}", extra={"error": str(e)})
        return f"An error occurred during image generation: {str(e)}"
