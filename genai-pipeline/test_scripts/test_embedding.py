"""
Test case: Doubao-Embedding-Vision provider integration.

Covers:
  - Provider factory instantiation
  - Registry correctness
  - Text-only embedding via generate() and embed()
  - Image embedding via embed()
  - Multimodal embedding via embed()
  - Empty-input guard
  - Output validation (type, non-empty, finite)
  - Cosine similarity sanity check

Usage:
  cd genai-pipeline
  python test_scripts/test_embedding.py
"""

import math
import os
import sys
from io import BytesIO

# -- Path setup: ensure we can import ai_gateway and tools --------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)  # genai-pipeline/
sys.path.insert(0, PROJECT_DIR)
os.environ.setdefault("LOG_LEVEL", "WARNING")  # suppress noisy gateway logs

from dotenv import load_dotenv
load_dotenv()

import yaml
from PIL import Image

from ai_gateway import generate
from ai_gateway.providers.doubao_embedding import DoubaoEmbeddingProvider
from ai_gateway.providers.registry import (
    PROVIDER_CLASSES,
    PROVIDER_TYPE_MAP,
    create_provider,
)
from tools.embedding_utils import embed

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_passed = 0
_failed = 0


def check(name: str, cond: bool, detail: str = ""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  [PASS] {name}")
    else:
        _failed += 1
        print(f"  [FAIL] {name}  -- {detail}")


def make_test_image() -> bytes:
    """Generate a tiny 100x100 white PNG in memory (no filesystem deps)."""
    img = Image.new("RGB", (100, 100), color=(255, 255, 255))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# load gateway config
# ---------------------------------------------------------------------------

with open(os.path.join(PROJECT_DIR, "ai_gateway", "gateway.yaml"), "r", encoding="utf-8") as fh:
    gateway_config = yaml.safe_load(fh)

embedding_cfg = gateway_config["providers"]["doubao_embedding"]

# ===========================================================================
# 1. Registry & factory
# ===========================================================================
print("--- 1. Registry & factory ---")

check("doubao_embedding in PROVIDER_CLASSES",
      "doubao_embedding" in PROVIDER_CLASSES)

check("PROVIDER_CLASSES maps to DoubaoEmbeddingProvider",
      PROVIDER_CLASSES["doubao_embedding"] is DoubaoEmbeddingProvider)

check("PROVIDER_TYPE_MAP maps to 'embedding'",
      PROVIDER_TYPE_MAP.get("doubao_embedding") == "embedding")

provider = create_provider("doubao_embedding", embedding_cfg)
check("create_provider returns DoubaoEmbeddingProvider",
      isinstance(provider, DoubaoEmbeddingProvider))

check("provider name is doubao_embedding",
      provider.name == "doubao_embedding")

check("provider model is doubao-embedding-vision-251215",
      provider.model == "doubao-embedding-vision-251215")

# ===========================================================================
# 2. Text-only embedding via generate()
# ===========================================================================
print("\n--- 2. Text-only embedding via generate() ---")

try:
    resp = generate(
        task="embedding",
        prompt="白板风格的故宫太和殿，黑色线条，红色选择性上色",
    )
    check("generate() returned GatewayResponse",
          resp is not None)

    text_vector = resp.content
    check("content is list[float]",
          isinstance(text_vector, list) and all(isinstance(v, (int, float)) for v in text_vector))

    dim_text = len(text_vector)
    check(f"vector dimensionality = {dim_text}",
          dim_text > 0)
    print(f"      (dimensionality: {dim_text})")

    check("all values are finite",
          all(math.isfinite(v) for v in text_vector))

    check("usage.input_tokens > 0",
          resp.usage.input_tokens > 0)

    check("provider reported correctly",
          resp.provider == "doubao_embedding")

    print(f"      latency: {resp.latency_ms:.0f} ms, "
          f"tokens: {resp.usage.input_tokens}, "
          f"cost: ¥{resp.usage.cost:.6f}")
except Exception as e:
    check(f"generate(text) raised no exception", False, str(e))

# ===========================================================================
# 3. Text-only embedding via embed() wrapper
# ===========================================================================
print("\n--- 3. Text-only embedding via embed() ---")

try:
    vec = embed(text="A whiteboard sketch of a rocket launching into space")
    check("embed(text=...) returns list[float]",
          isinstance(vec, list) and all(isinstance(v, (int, float)) for v in vec))

    dim_wrapper = len(vec)
    check(f"vector dimensionality = {dim_wrapper} (matches direct call)",
          dim_wrapper == dim_text)

    check("all values finite",
          all(math.isfinite(v) for v in vec))
except Exception as e:
    check("embed(text) raised no exception", False, str(e))

# ===========================================================================
# 4. Image-only embedding via embed()
# ===========================================================================
print("\n--- 4. Image-only embedding via embed() ---")

try:
    test_img = make_test_image()
    vec_img = embed(image_bytes=test_img)

    check("embed(image_bytes=...) returns list[float]",
          isinstance(vec_img, list) and all(isinstance(v, (int, float)) for v in vec_img))

    dim_image = len(vec_img)
    check(f"image vector dimensionality = {dim_image}",
          dim_image > 0)

    check("all values finite",
          all(math.isfinite(v) for v in vec_img))
except Exception as e:
    check("embed(image_bytes) raised no exception", False, str(e))

# ===========================================================================
# 5. Multimodal embedding via embed()
# ===========================================================================
print("\n--- 5. Multimodal embedding via embed() ---")

try:
    test_img2 = make_test_image()
    vec_multi = embed(
        text="A whiteboard sketch of a cat sitting on a mat",
        image_bytes=test_img2,
    )

    check("multimodal embed() returns list[float]",
          isinstance(vec_multi, list) and all(isinstance(v, (int, float)) for v in vec_multi))

    dim_multi = len(vec_multi)
    check(f"multimodal vector dimensionality = {dim_multi}",
          dim_multi > 0)

    check("all values finite",
          all(math.isfinite(v) for v in vec_multi))
except Exception as e:
    check("multimodal embed() raised no exception", False, str(e))

# ===========================================================================
# 6. Error handling
# ===========================================================================
print("\n--- 6. Error handling ---")

# 6a. Empty input
try:
    embed()
    check("embed() with no args raises ValueError", False, "should have raised")
except ValueError as e:
    check("embed() with no args raises ValueError", "require" in str(e).lower())
except Exception as e:
    check("embed() with no args raises ValueError",
          False, f"wrong exception: {type(e).__name__}: {e}")

# 6b. Empty string is falsy but valid — gateway handles it
try:
    # empty prompt + no image = both falsy → should raise
    embed(text="")
    check("embed(text='') raises ValueError",
          False, "should have raised (empty string is falsy)")
except ValueError:
    check("embed(text='') raises ValueError", True)
except Exception as e:
    # might succeed if gateway sends empty prompt to API (unlikely but ok)
    check("embed(text='') raises ValueError",
          False, f"unexpected: {type(e).__name__}")

# ===========================================================================
# 7. Cosine similarity sanity check
# ===========================================================================
print("\n--- 7. Cosine similarity sanity check ---")


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    return dot / (norm_a * norm_b)


try:
    # Two prompts about architecture (should be relatively similar)
    vec_a = embed(text="白板风格的故宫太和殿，黑色线条画，红色柱子和金色屋顶")
    vec_b = embed(text="白板手绘北京天坛祈年殿，极简线条，蓝色琉璃瓦")

    # A prompt about something completely different
    vec_c = embed(text="A realistic photograph of a hamburger with fries on a wooden table")

    sim_arch = cosine(vec_a, vec_b)   # both Chinese architecture
    sim_cross = cosine(vec_a, vec_c)  # architecture vs hamburger

    check(f"arch→arch sim ({sim_arch:.4f}) > arch→burger sim ({sim_cross:.4f})",
          sim_arch > sim_cross,
          f"similarity inversion: {sim_arch:.4f} <= {sim_cross:.4f}")

    # Self-similarity should be ~1.0
    sim_self = cosine(vec_a, vec_a)
    check(f"self-similarity ≈ 1.0 ({sim_self:.6f})",
          abs(sim_self - 1.0) < 0.001,
          f"self-sim={sim_self:.6f}")

    print(f"      sim(故宫, 天坛) = {sim_arch:.4f}")
    print(f"      sim(故宫, 汉堡) = {sim_cross:.4f}")
except Exception as e:
    check("cosine sanity check raised no exception", False, str(e))

# ===========================================================================
# Summary
# ===========================================================================
print(f"\n{'='*50}")
print(f"Results: {_passed} passed, {_failed} failed")
if _failed:
    print("❌ Some tests FAILED — check output above.")
    sys.exit(1)
else:
    print("✅ All tests PASSED.")
