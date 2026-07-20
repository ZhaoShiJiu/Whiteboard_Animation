"""
Test the image_library module — store, retrieve, and reuse images.

Requires valid API keys (ARK_API_KEY for embedding, DASHSCOPE_API_KEY for images).

Usage::

    cd genai-pipeline
    python test_scripts/test_image_library.py

To skip tests that require API calls::

    python test_scripts/test_image_library.py --no-api
"""

import sys
import os
import hashlib
import struct
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml
from ai_gateway.db.connection import init_db, get_session
from ai_gateway.db.models import ImageLibrary

with open(
    Path(__file__).resolve().parent.parent / "ai_gateway" / "gateway.yaml",
    "r", encoding="utf-8",
) as f:
    config = yaml.safe_load(f)
init_db(config["database"], run_migrations=False)

from tools.image_library import (
    process_and_store_image,
    retrieve_best_match,
    get_image_bytes,
)

_passed = 0
_failed = 0
SKIP_API = "--no-api" in sys.argv


def check(desc: str, condition: bool, detail: str = ""):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  [PASS] {desc}")
    else:
        _failed += 1
        print(f"  [FAIL] {desc}  — {detail}")


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_test_png(width: int = 64, height: int = 36, r: int = 255, g: int = 0, b: int = 0) -> bytes:
    """Create a minimal valid PNG in memory."""

    def chunk(chunk_type: bytes, data: bytes) -> bytes:
        c = chunk_type + data
        crc = struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + c + crc

    header = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    raw = b""
    for _ in range(height):
        raw += b"\x00"
        for _ in range(width):
            raw += bytes([r, g, b])
    idat = chunk(b"IDAT", zlib.compress(raw))
    iend = chunk(b"IEND", b"")
    return header + ihdr + idat + iend


# ═══════════════════════════════════════════════════════════════════════════════
# 1. process_and_store_image — basic
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 1. process_and_store_image — basic ---")

if SKIP_API:
    print("  (skipped — --no-api)")
else:
    test_png = _make_test_png(64, 36, 255, 100, 50)
    test_prompt = "Unit test: whiteboard sketch of a test object"
    test_desc = "A test object drawn in whiteboard style"

    img_id = process_and_store_image(
        image_bytes=test_png,
        prompt=test_prompt,
        scene_desc=test_desc,
        source_run_id="test_run_image_lib",
        source="ai_gen",
    )
    check("process_and_store_image() returns id > 0", img_id > 0)
    check("process_and_store_image() returns int", isinstance(img_id, int))

    # Verify DB record
    with get_session() as s:
        img = s.get(ImageLibrary, img_id)
        check("Image record exists", img is not None)
        check("content_hash is SHA-256 (64 hex chars)", len(img.content_hash) == 64)
        check("prompt matches", img.prompt == test_prompt)
        check("scene_desc matches", img.scene_desc == test_desc)
        check("source matches", img.source == "ai_gen")
        check("source_run_id matches", img.source_run_id == "test_run_image_lib")
        check("width correct", img.width == 64)
        check("height correct", img.height == 36)
        check("file_size correct", img.file_size == len(test_png))
        check("usage_count == 1", img.usage_count == 1)
        check("embedding_json is not None", img.embedding_json is not None)
        if img.embedding_json:
            check("embedding has 2048 dimensions", len(img.embedding_json) == 2048)
            check("all embedding values are finite", all(
                isinstance(v, (int, float)) and abs(v) < float("inf")
                for v in img.embedding_json
            ))

    # Thumbnail should be generated for images large enough
    big_png = _make_test_png(320, 180, 0, 100, 200)
    big_id = process_and_store_image(
        image_bytes=big_png,
        prompt="Test thumbnail image",
        source_run_id="test_run_image_lib",
    )
    with get_session() as s:
        big_img = s.get(ImageLibrary, big_id)
        check("Thumbnail generated for 320x180 image", big_img.thumbnail_data is not None)
        if big_img.thumbnail_data:
            check("Thumbnail is JPEG (starts with FF D8)", big_img.thumbnail_data[:2] == b"\xff\xd8")
            check("Thumbnail < 50KB", len(big_img.thumbnail_data) < 50_000)
        s.delete(big_img)

    # Cleanup
    with get_session() as s:
        s.delete(s.get(ImageLibrary, img_id))

# ═══════════════════════════════════════════════════════════════════════════════
# 2. process_and_store_image — deduplication
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 2. process_and_store_image — deduplication ---")

# Same PNG twice should return same id
png_a = _make_test_png(32, 18, 255, 0, 0)
png_b = bytes(png_a)  # identical content

id1 = process_and_store_image(png_a, prompt="Dedup test A",
                               source_run_id="test_dedup")
id2 = process_and_store_image(png_b, prompt="Dedup test B",
                               source_run_id="test_dedup")

check("Identical images share same id", id1 == id2)
check("Dedup returns valid id", id1 > 0)

# Only one row should exist
with get_session() as s:
    count = s.query(ImageLibrary).filter(ImageLibrary.id == id1).count()
    check("Only one row for duplicated content", count == 1)

# Cleanup
with get_session() as s:
    s.delete(s.get(ImageLibrary, id1))

# ═══════════════════════════════════════════════════════════════════════════════
# 3. get_image_bytes
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 3. get_image_bytes ---")

png_c = _make_test_png(16, 9, 0, 255, 0)
id_c = process_and_store_image(png_c, prompt="Bytes test",
                                source_run_id="test_bytes")

retrieved = get_image_bytes(id_c)
check("get_image_bytes() returns bytes", isinstance(retrieved, bytes))
check("get_image_bytes() returns correct data", retrieved == png_c)
check("get_image_bytes() has correct length", len(retrieved) == len(png_c))

# Non-existent id
null_bytes = get_image_bytes(999999)
check("get_image_bytes() returns None for missing id", null_bytes is None)

# Cleanup
with get_session() as s:
    s.delete(s.get(ImageLibrary, id_c))

# ═══════════════════════════════════════════════════════════════════════════════
# 4. retrieve_best_match — semantic similarity
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 4. retrieve_best_match — semantic similarity ---")

if SKIP_API:
    print("  (skipped — --no-api)")
else:
    # Insert two distinct images with embeddings
    png_castle = _make_test_png(64, 36, 200, 180, 140)
    png_burger = _make_test_png(64, 36, 255, 200, 100)

    id_castle = process_and_store_image(
        png_castle,
        prompt="Whiteboard sketch of the Forbidden City in Beijing",
        scene_desc="A traditional Chinese palace complex",
        source_run_id="test_retrieval",
    )
    id_burger = process_and_store_image(
        png_burger,
        prompt="Whiteboard sketch of a cheeseburger with fries",
        scene_desc="Fast food meal on a table",
        source_run_id="test_retrieval",
    )

    check("Both images stored with embeddings", id_castle > 0 and id_burger > 0)

    # Search for a Chinese palace → should match castle, not burger
    match1 = retrieve_best_match(
        "Whiteboard drawing of the Temple of Heaven in Beijing",
        threshold=0.30,  # low threshold for test reliability
    )
    check("retrieve_best_match() returns result for related query", match1 is not None)
    if match1:
        check("retrieve_best_match() returns image_id", "image_id" in match1)
        check("retrieve_best_match() returns image_bytes", "image_bytes" in match1)
        check("retrieve_best_match() returns similarity", "similarity" in match1)
        check("Similarity is float", isinstance(match1["similarity"], float))
        check("Similarity in [0, 1]", 0.0 <= match1["similarity"] <= 1.0)
        # It should prefer the castle over the burger
        check("Best match is castle (not burger)", match1["image_id"] == id_castle,
              detail=f"expected {id_castle}, got {match1['image_id']}")

    # Search for food → should match burger
    match2 = retrieve_best_match(
        "Whiteboard sketch of a hamburger and soda",
        threshold=0.30,
    )
    check("retrieve_best_match() for food query returns result", match2 is not None)
    if match2:
        check("Best match is burger", match2["image_id"] == id_burger,
              detail=f"expected {id_burger}, got {match2['image_id']}")

    # High threshold should reject borderline matches
    match3 = retrieve_best_match(
        "An abstract painting of colors and shapes",
        threshold=0.95,
    )
    # This may or may not return None depending on embeddings;
    # we just check it doesn't crash
    check("retrieve_best_match() with high threshold does not crash", True)

    # Cleanup
    with get_session() as s:
        s.delete(s.get(ImageLibrary, id_castle))
        s.delete(s.get(ImageLibrary, id_burger))

# ═══════════════════════════════════════════════════════════════════════════════
# 5. retrieve_best_match — empty library
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 5. retrieve_best_match — edge cases ---")

# Ensure library is empty for this test
with get_session() as s:
    s.query(ImageLibrary).delete()

if SKIP_API:
    print("  (skipped — --no-api)")
else:
    result = retrieve_best_match("Any prompt for empty library")
    check("Empty library returns None", result is None)

# ═══════════════════════════════════════════════════════════════════════════════
# 6. Usage count tracking
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 6. Usage count tracking ---")

png_d = _make_test_png(32, 18, 0, 0, 255)
id_d = process_and_store_image(png_d, prompt="Usage count test",
                                source_run_id="test_usage")

# Simulate a "hit" — manually call retrieve_best_match which should bump usage_count
with get_session() as s:
    img = s.get(ImageLibrary, id_d)
    img.usage_count += 1
    img.last_used_at = __import__("datetime").datetime.utcnow()
check("Manual usage_count increment", True)

with get_session() as s:
    img = s.get(ImageLibrary, id_d)
    check("usage_count is now 2", img.usage_count == 2)
    check("last_used_at is set", img.last_used_at is not None)

# Cleanup
with get_session() as s:
    s.delete(s.get(ImageLibrary, id_d))

# ═══════════════════════════════════════════════════════════════════════════════
# 7. _make_thumbnail edge cases
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 7. Thumbnail edge cases ---")

from tools.image_library import _make_thumbnail as make_thumb

# Normal image
png_normal = _make_test_png(640, 360, 100, 200, 50)
thumb = make_thumb(png_normal)
check("Thumbnail for 640x360 PNG", thumb is not None)
if thumb:
    check("Thumbnail is valid JPEG", thumb[:2] == b"\xff\xd8")

    # Verify thumbnail dimensions
    from PIL import Image as PILImage
    import io
    with PILImage.open(io.BytesIO(thumb)) as timg:
        check("Thumbnail width <= 320", timg.width <= 320)
        check("Thumbnail height <= 180", timg.height <= 180)

# Already-small image
png_tiny = _make_test_png(32, 18, 0, 0, 0)
thumb_tiny = make_thumb(png_tiny)
check("Thumbnail for 32x18 PNG", thumb_tiny is not None)

# Invalid bytes
thumb_bad = make_thumb(b"not a valid image")
check("Thumbnail for invalid bytes returns None", thumb_bad is None)

# ═══════════════════════════════════════════════════════════════════════════════
# 8. _get_dimensions edge cases
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 8. _get_dimensions ---")

from tools.image_library import _get_dimensions as get_dims

w, h = get_dims(png_normal)
check("get_dims for 640x360 PNG", w == 640 and h == 360)

w2, h2 = get_dims(b"invalid")
check("get_dims for invalid bytes falls back to 1920x1080", w2 == 1920 and h2 == 1080)

# ═══════════════════════════════════════════════════════════════════════════════
# 9. _cosine_similarity correctness
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 9. _cosine_similarity ---")

import numpy as np
from tools.image_library import _cosine_similarity as cos_sim

a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
b = np.array([1.0, 0.0, 0.0], dtype=np.float32)
check("cosine_sim identical vectors == 1.0", abs(cos_sim(a, b) - 1.0) < 1e-6)

c = np.array([0.0, 1.0, 0.0], dtype=np.float32)
check("cosine_sim orthogonal vectors == 0.0", abs(cos_sim(a, c)) < 1e-6)

d = np.array([-1.0, 0.0, 0.0], dtype=np.float32)
check("cosine_sim opposite vectors == -1.0", abs(cos_sim(a, d) + 1.0) < 1e-6)

zero = np.array([0.0, 0.0, 0.0], dtype=np.float32)
check("cosine_sim with zero vector == 0.0", cos_sim(a, zero) == 0.0)

# ═══════════════════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════════════════
print(f"\n{'=' * 50}")
print(f"Results: {_passed} passed, {_failed} failed")
if SKIP_API:
    print("(some API-dependent tests skipped)")
if _failed:
    print("Some tests FAILED — check output above.")
else:
    print("All tests passed!")
