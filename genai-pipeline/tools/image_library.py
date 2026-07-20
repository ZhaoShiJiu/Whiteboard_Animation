"""
Image library — store, index, and retrieve generated images for reuse.

Core workflow:
1. Before generating a new image, call ``retrieve_best_match(prompt)``.
2. If a match is found (similarity >= threshold), reuse the existing image.
3. Otherwise, generate a new image and call ``process_and_store_image()``
   to hash, thumbnail, embed, and persist it.

Usage::

    from tools.image_library import retrieve_best_match, process_and_store_image

    match = retrieve_best_match("whiteboard sketch of the Eiffel Tower")
    if match:
        image_bytes = match["image_bytes"]
    else:
        image_bytes = call_ai_image_gen(...)
        process_and_store_image(image_bytes, prompt, scene_desc, run_id)
"""

import hashlib
import io
import json
import logging
import os
from datetime import datetime
from typing import Optional

import numpy as np

_logger = logging.getLogger("image_library")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def process_and_store_image(
    image_bytes: bytes,
    prompt: str,
    scene_desc: str = "",
    source_run_id: str = "",
    source: str = "ai_gen",
) -> int:
    """
    Process a newly generated image and store it in the image_library table.

    Steps:
        1. Compute SHA-256 content hash (exact dedup).
        2. If hash already exists → return existing image_id.
        3. Generate thumbnail (320×180 JPEG).
        4. Generate embedding via Doubao-Embedding-Vision.
        5. Extract dimensions via Pillow.
        6. Write to ``image_library`` table.

    Returns:
        The ``image_id`` (primary key) of the stored (or existing) row.
    """
    from ai_gateway.db.models import ImageLibrary
    from tools import db_utils as _db

    # 1. Content hash (exact dedup)
    content_hash = hashlib.sha256(image_bytes).hexdigest()

    # Check for existing image with same hash
    try:
        with _db._get_session() as session:
            existing = (
                session.query(ImageLibrary)
                .filter(ImageLibrary.content_hash == content_hash)
                .first()
            )
            if existing is not None:
                _logger.info(
                    "Image already in library (hash=%s), id=%s",
                    content_hash[:12], existing.id,
                )
                return existing.id
    except Exception:
        pass  # DB not available — proceed to store fresh

    # 2. Extract dimensions
    width, height = _get_dimensions(image_bytes)

    # 3. Generate thumbnail
    thumbnail_data = _make_thumbnail(image_bytes)

    # 4. Generate embedding
    embedding = _generate_embedding(image_bytes, prompt)

    file_size = len(image_bytes)

    # 5. Write to DB
    try:
        with _db._get_session() as session:
            img = ImageLibrary(
                content_hash=content_hash,
                file_data=image_bytes,
                thumbnail_data=thumbnail_data,
                embedding_json=embedding,
                prompt=prompt,
                scene_desc=scene_desc,
                width=width,
                height=height,
                file_size=file_size,
                source=source,
                source_run_id=source_run_id,
                usage_count=1,
            )
            session.add(img)
            session.flush()
            image_id = img.id
            _logger.info(
                "Image stored in library: id=%s, hash=%s, size=%d KB",
                image_id, content_hash[:12], file_size // 1024,
            )
            return image_id
    except Exception as exc:
        _logger.warning("Failed to store image in library: %s", exc)
        return -1


def retrieve_best_match(
    prompt: str,
    threshold: float = 0.85,
) -> Optional[dict]:
    """
    Search the image library for the best match to *prompt*.

    Args:
        prompt: The image-generation prompt text.
        threshold: Minimum cosine similarity to consider a match (0.0–1.0).

    Returns:
        ``{"image_id": int, "image_bytes": bytes, "similarity": float}``
        if a match is found, or ``None`` if no image exceeds the threshold.
    """
    from ai_gateway.db.models import ImageLibrary
    from tools import db_utils as _db

    # 1. Get text embedding for the prompt
    text_vec = _generate_embedding(text=prompt)
    if text_vec is None:
        _logger.warning("Could not generate text embedding — skipping retrieval.")
        return None

    # 2. Load candidate images with embeddings from DB
    try:
        with _db._get_session() as session:
            candidates = (
                session.query(
                    ImageLibrary.id,
                    ImageLibrary.embedding_json,
                    ImageLibrary.file_data,
                )
                .filter(ImageLibrary.embedding_json.isnot(None))
                .all()
            )
    except Exception as exc:
        _logger.warning("Failed to query image library: %s", exc)
        return None

    if not candidates:
        _logger.debug("No images in library with embeddings — nothing to match.")
        return None

    # 3. Compute cosine similarity
    text_arr = np.array(text_vec, dtype=np.float32)
    best_sim = -1.0
    best_match = None

    for c in candidates:
        try:
            img_vec = np.array(
                json.loads(c.embedding_json)
                if isinstance(c.embedding_json, str)
                else c.embedding_json,
                dtype=np.float32,
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            continue

        sim = _cosine_similarity(text_arr, img_vec)
        if sim > best_sim:
            best_sim = sim
            best_match = c

    # 4. Threshold check
    if best_match is not None and best_sim >= threshold:
        _logger.info(
            "Image match found: id=%s, similarity=%.4f (threshold=%.2f)",
            best_match.id, best_sim, threshold,
        )

        # Update usage stats
        try:
            with _db._get_session() as session:
                img = session.get(ImageLibrary, best_match.id)
                if img:
                    img.usage_count = (img.usage_count or 0) + 1
                    img.last_used_at = datetime.utcnow()
        except Exception:
            pass

        return {
            "image_id": best_match.id,
            "image_bytes": best_match.file_data,
            "similarity": float(best_sim),
        }

    _logger.debug(
        "No match above threshold: best_sim=%.4f < %.2f",
        best_sim if best_match else 0, threshold,
    )
    return None


def get_image_bytes(image_id: int) -> Optional[bytes]:
    """Read the full image BLOB from the library by ID."""
    from ai_gateway.db.models import ImageLibrary
    from tools import db_utils as _db

    try:
        with _db._get_session() as session:
            img = session.get(ImageLibrary, image_id)
            return img.file_data if img else None
    except Exception as exc:
        _logger.warning("get_image_bytes(%s) failed: %s", image_id, exc)
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_dimensions(image_bytes: bytes) -> tuple[int, int]:
    """Return (width, height) of the image."""
    try:
        from PIL import Image as PILImage
        with PILImage.open(io.BytesIO(image_bytes)) as img:
            return img.width, img.height
    except Exception:
        return 1920, 1080


def _make_thumbnail(image_bytes: bytes, size: tuple[int, int] = (320, 180)) -> Optional[bytes]:
    """Create a JPEG thumbnail. Returns bytes or None."""
    try:
        from PIL import Image as PILImage
        with PILImage.open(io.BytesIO(image_bytes)) as img:
            img = img.convert("RGB")
            img.thumbnail(size, PILImage.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=70)
            return buf.getvalue()
    except Exception as exc:
        _logger.warning("Thumbnail generation failed: %s", exc)
        return None


def _generate_embedding(
    image_bytes: Optional[bytes] = None,
    text: Optional[str] = None,
) -> Optional[list]:
    """
    Generate an embedding via Doubao-Embedding-Vision.

    Returns a list of floats (2048 dims), or None on failure.
    """
    try:
        from tools.embedding_utils import embed

        if image_bytes and text:
            vec = embed(text=text, image_bytes=image_bytes)
        elif image_bytes:
            vec = embed(image_bytes=image_bytes)
        elif text:
            vec = embed(text=text)
        else:
            raise ValueError("Need at least one of image_bytes or text.")

        return vec
    except Exception as exc:
        _logger.warning("Embedding generation failed: %s", exc)
        return None


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two 1-D arrays."""
    dot = np.dot(a, b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(dot / (norm_a * norm_b))
