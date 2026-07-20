"""
Test case: Doubao-Seedream-4.5 image generation provider integration.

Covers:
  - Provider factory instantiation
  - Registry correctness
  - Text-to-image via generate()
  - Output validation (bytes, non-empty, valid image format)
  - Usage / cost reporting
  - Error handling (missing API key)

Usage:
  cd genai-pipeline
  python test_scripts/test_doubao_image.py
"""

import os
import sys
from io import BytesIO

# -- Path setup ---------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)  # genai-pipeline/
sys.path.insert(0, PROJECT_DIR)
os.environ.setdefault("LOG_LEVEL", "WARNING")

from dotenv import load_dotenv
load_dotenv()

import yaml
from PIL import Image

from ai_gateway import generate
from ai_gateway.providers.doubao_image import DoubaoImageProvider
from ai_gateway.providers.registry import (
    PROVIDER_CLASSES,
    PROVIDER_TYPE_MAP,
    create_provider,
)

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


def is_valid_image(img_bytes: bytes) -> bool:
    """Return True if img_bytes can be opened by Pillow as a valid image."""
    try:
        im = Image.open(BytesIO(img_bytes))
        im.verify()
        return True
    except Exception:
        return False


def get_image_size(img_bytes: bytes) -> tuple[int, int]:
    """Return (width, height) of the image."""
    im = Image.open(BytesIO(img_bytes))
    return im.size


# ---------------------------------------------------------------------------
# load gateway config
# ---------------------------------------------------------------------------

with open(os.path.join(PROJECT_DIR, "ai_gateway", "gateway.yaml"), "r", encoding="utf-8") as fh:
    gateway_config = yaml.safe_load(fh)

doubao_cfg = gateway_config["providers"]["doubao_image"]

# ===========================================================================
# 1. Registry & factory
# ===========================================================================
print("--- 1. Registry & factory ---")

check("doubao_image in PROVIDER_CLASSES",
      "doubao_image" in PROVIDER_CLASSES)

check("PROVIDER_CLASSES maps to DoubaoImageProvider",
      PROVIDER_CLASSES["doubao_image"] is DoubaoImageProvider)

check("PROVIDER_TYPE_MAP maps to 'image'",
      PROVIDER_TYPE_MAP.get("doubao_image") == "image")

provider = create_provider("doubao_image", doubao_cfg)
check("create_provider returns DoubaoImageProvider",
      isinstance(provider, DoubaoImageProvider))

check("provider name is doubao_image",
      provider.name == "doubao_image")

check("provider model is doubao-seedream-4-5-251128",
      provider.model == "doubao-seedream-4-5-251128")

# ===========================================================================
# 2. Text-to-image via generate(task="image_doubao")
# ===========================================================================
print("\n--- 2. Text-to-image via generate(task='image_doubao') ---")

try:
    resp = generate(
        task="image_doubao",
        prompt=(
            "A minimal whiteboard line-drawing of a cute cat sitting on a bookshelf. "
            "Black outlines on a clean white background. No hands, no markers, no frames."
        ),
        options={"size": "2K"},
    )

    check("generate() returned GatewayResponse",
          resp is not None)

    check("content is bytes",
          isinstance(resp.content, bytes))

    check("content is non-empty",
          len(resp.content) > 0)

    check("content is a valid image",
          is_valid_image(resp.content),
          f"bytes len={len(resp.content)}, head={resp.content[:20].hex()}")

    w, h = get_image_size(resp.content)
    check(f"image has reasonable dimensions ({w}x{h})",
          w > 100 and h > 100)

    # Save generated image for visual inspection
    out_dir = os.path.join(PROJECT_DIR, "test_output")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "doubao_image_test_1_text2img.png")
    with open(out_path, "wb") as f:
        f.write(resp.content)
    print(f"      saved: {out_path}")

    check("provider reported correctly",
          resp.provider == "doubao_image")

    check("model reported correctly",
          resp.model == "doubao-seedream-4-5-251128")

    check("usage.images > 0",
          resp.usage.images > 0)

    check("usage.output_tokens > 0",
          resp.usage.output_tokens > 0)

    check("usage.resolution is populated",
          bool(resp.usage.resolution))

    check("cost was calculated",
          resp.usage.cost > 0)

    print(f"      resolution: {resp.usage.resolution}")
    print(f"      actual size: {w}x{h}")
    print(f"      latency: {resp.latency_ms:.0f} ms")
    print(f"      images: {resp.usage.images}, "
          f"output_tokens: {resp.usage.output_tokens}, "
          f"cost: CNY {resp.usage.cost:.6f}")

except Exception as e:
    check(f"generate(task='image_doubao') raised no exception", False, str(e))
    import traceback
    traceback.print_exc()

# ===========================================================================
# 3. Text-to-image via generate(task="image", provider="doubao_image")
# ===========================================================================
print("\n--- 3. Text-to-image via runtime provider override ---")

try:
    resp = generate(
        task="image",
        prompt=(
            "A whiteboard sketch of a rocket launching into space. "
            "Black outlines, clean white background, no hands or markers."
        ),
        options={"size": "2K", "provider": "doubao_image"},
    )

    check("runtime override — generate() returned GatewayResponse",
          resp is not None)

    check("runtime override — content is bytes",
          isinstance(resp.content, bytes))

    check("runtime override — content is a valid image",
          is_valid_image(resp.content))

    check("runtime override — provider is doubao_image",
          resp.provider == "doubao_image")

    w, h = get_image_size(resp.content)
    out_path = os.path.join(out_dir, "doubao_image_test_2_override.png")
    with open(out_path, "wb") as f:
        f.write(resp.content)
    print(f"      saved: {out_path}")
    print(f"      resolution: {resp.usage.resolution}, actual: {w}x{h}")
    print(f"      latency: {resp.latency_ms:.0f} ms, cost: CNY {resp.usage.cost:.6f}")

except Exception as e:
    check("runtime override raised no exception", False, str(e))
    import traceback
    traceback.print_exc()

# ===========================================================================
# 4. Error handling
# ===========================================================================
print("\n--- 4. Error handling ---")

# 4a. Instantiation without API key
import importlib
import ai_gateway.providers.doubao_image as dm

try:
    orig_key = os.environ.pop("ARK_API_KEY", None)
    provider_no_key = dm.DoubaoImageProvider(
        "doubao_image",
        {"type": "image", "model": "test", "api_key_env": "ARK_API_KEY"},
    )
    check("missing API key raises ValueError", False, "should have raised")
except ValueError as e:
    check("missing API key raises ValueError",
          "API key not found" in str(e))
except Exception as e:
    check("missing API key raises ValueError",
          False, f"wrong exception: {type(e).__name__}: {e}")
finally:
    if orig_key is not None:
        os.environ["ARK_API_KEY"] = orig_key

# 4b. is_retryable
provider_rt = create_provider("doubao_image", doubao_cfg) if os.getenv("ARK_API_KEY") else None
if provider_rt is None:
    # Re-instantiate with key restored
    provider_rt = create_provider("doubao_image", doubao_cfg)

retry_cases = [
    (RuntimeError("429 Too Many Requests"), True, "429"),
    (RuntimeError("internal server error 500"), True, "500"),
    (RuntimeError("connection timeout"), True, "timeout"),
    (RuntimeError("Service Unavailable 503"), True, "503"),
    (RuntimeError("invalid API key"), False, "auth error (non-retryable)"),
]
for exc, expected, label in retry_cases:
    actual = provider_rt.is_retryable(exc)
    check(f"is_retryable → {label}", actual == expected,
          f"expected {expected}, got {actual}")

# ===========================================================================
# Summary
# ===========================================================================
print(f"\n{'=' * 50}")
print(f"Results: {_passed} passed, {_failed} failed")
if _failed:
    print("[FAIL] Some tests FAILED -- check output above.")
    sys.exit(1)
else:
    print("[OK] All tests PASSED.")
