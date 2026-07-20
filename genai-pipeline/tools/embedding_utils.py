"""
Multimodal embedding utility — thin convenience wrapper over ai_gateway.

Usage::

    from tools.embedding_utils import embed

    # Text embedding (most common for retrieval)
    text_vec = embed(text="白板风格的故宫太和殿线条画")

    # Image embedding
    image_vec = embed(image_bytes=open("image.png", "rb").read())

    # Multimodal embedding (text + image)
    joint_vec = embed(
        text="白板素描风格的埃菲尔铁塔",
        image_bytes=generated_image_bytes,
    )
"""

from typing import Optional


def embed(
    text: Optional[str] = None,
    image_bytes: Optional[bytes] = None,
) -> list[float]:
    """
    Generate a multimodal embedding vector via Doubao-Embedding-Vision.

    At least one of *text* or *image_bytes* must be provided.  When both are
    supplied the model produces a joint text+image embedding in a single
    vector — useful for cross-modal similarity search where you want to
    compare prompts against previously generated images with their prompts.

    Args:
        text: The text to embed (e.g. an image-generation prompt).
        image_bytes: Raw PNG/JPEG bytes of the image to embed.

    Returns:
        The embedding vector as a ``list[float]`` (dimensionality depends on
        the model; typically 1024, 1536, or 2048).

    Raises:
        ValueError: If neither *text* nor *image_bytes* is provided.
    """
    if not text and not image_bytes:
        raise ValueError(
            "embed() requires at least one of 'text' or 'image_bytes'."
        )

    from ai_gateway import generate

    resp = generate(
        task="embedding",
        prompt=text or "",
        reference_images=[image_bytes] if image_bytes else None,
    )
    return resp.content
