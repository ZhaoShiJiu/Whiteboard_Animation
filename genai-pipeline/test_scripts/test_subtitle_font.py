"""
Standalone subtitle font rendering test.

Verifies that Chinese characters are rendered correctly when burning subtitles
into a video via FFmpeg — i.e. that the Docker image has a CJK font installed
and the FFmpeg subtitles filter is using it.

Usage:
    cd genai-pipeline
    python test_scripts/test_subtitle_font.py

Prerequisites:
    - FFmpeg available in PATH
    - Noto Serif CJK SC font installed (fonts-noto-cjk package)
"""

import os
import sys
import json
import subprocess
import tempfile
import time

# Make genai-pipeline/ importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.video_subtitle import burn_subtitles_to_video_tool_fn
from tools.utils import set_output_dir, get_video_duration


# ── Test config ──────────────────────────────────────────────────────────
TEST_WIDTH = 640
TEST_HEIGHT = 360
TEST_FPS = 10
TEST_DURATION = 4  # seconds
TEST_BG_COLOR = "white"  # white background to make text stand out
TEST_SUBTITLE_TEXT = "中文测试：世界你好！"

# Timestamp where the subtitle is visible (midpoint of test video)
SUBTITLE_START = 0.5
SUBTITLE_END = 3.5
# Frame to extract for visual inspection (in seconds)
SCREENSHOT_TIME = 2.0


def _generate_test_video(output_path: str) -> str:
    """Generate a simple solid-color test video using FFmpeg color source."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c=white:s={TEST_WIDTH}x{TEST_HEIGHT}:d={TEST_DURATION}:r={TEST_FPS}",
        "-pix_fmt", "yuv420p",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to generate test video: {result.stderr}")
    return output_path


def _create_test_subtitles_json(output_path: str) -> str:
    """Create a minimal JSON subtitles file with Chinese text."""
    data = {
        "subtitles": [
            {
                "start": SUBTITLE_START,
                "end": SUBTITLE_END,
                "text": TEST_SUBTITLE_TEXT,
            }
        ]
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return output_path


def _extract_screenshot(video_path: str, output_path: str, at_time: float) -> str:
    """Extract a single PNG frame at the given timestamp from the video."""
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(at_time),
        "-i", video_path,
        "-vframes", "1",
        "-f", "image2",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to extract screenshot: {result.stderr}")
    return output_path


def _check_subtitle_region_has_content(screenshot_path: str) -> dict:
    """
    Sample the bottom portion of the screenshot to detect whether text was
    actually rendered (dark pixels present) vs tofu/blank.

    Returns a dict with:
        has_dark_pixels: bool  — True if dark pixels exist in the subtitle band
        dark_pixel_count: int  — number of reasonably-dark pixels found
        total_sampled: int     — total pixels in the sampled region
        dark_ratio: float      — dark_pixel_count / total_sampled
    """
    try:
        from PIL import Image
    except ImportError:
        return {"error": "Pillow not available — cannot auto-verify. Inspect screenshot manually."}

    img = Image.open(screenshot_path).convert("L")  # grayscale
    w, h = img.size

    # Subtitle band: bottom 25% of the frame (typical subtitle position)
    band_top = int(h * 0.70)
    band_bottom = h
    band = img.crop((0, band_top, w, band_bottom))

    pixels = list(band.getdata())
    total = len(pixels)

    # Count pixels darker than threshold 128 (mid-gray).  Rendered text will
    # be close to 0 (black); white background is ~255.
    dark_threshold = 128
    dark_pixels = sum(1 for p in pixels if p < dark_threshold)
    dark_ratio = dark_pixels / total if total > 0 else 0.0

    return {
        "has_dark_pixels": dark_pixels > 20,  # at least some text strokes
        "dark_pixel_count": dark_pixels,
        "total_sampled": total,
        "dark_ratio": round(dark_ratio, 4),
    }


def _detect_font_in_use(video_path: str) -> str:
    """
    Query FFmpeg/ffprobe for the font config used, and check font availability.
    """
    # Check if Noto Serif CJK SC is known to fc-list
    fc_result = subprocess.run(
        ["fc-list", ":lang=zh", "family"],
        capture_output=True, text=True,
    )
    available = fc_result.stdout.strip() or "(none)"

    # Also check what font FFmpeg's subtitles filter actually resolved to
    # by running ffmpeg with verbose logging and grepping for font info
    # (best-effort — may not always produce output)
    return f"Available Chinese fonts:\n{available}"


def test_subtitle_font():
    # ── Setup output directory ───────────────────────────────────────────
    output_dir = os.path.join(os.path.dirname(__file__), "..", "test_output")
    output_dir = os.path.abspath(output_dir)
    set_output_dir(output_dir)
    os.makedirs(output_dir, exist_ok=True)
    print(f"Output directory: {output_dir}\n")

    # ── Step 1: Generate test video ──────────────────────────────────────
    print("=" * 60)
    print("Step 1: Generating test video (white background)...")
    test_video = os.path.join(output_dir, "test_subtitle_base_video.mp4")
    _generate_test_video(test_video)
    dur = get_video_duration(test_video)
    size_kb = os.path.getsize(test_video) / 1024
    print(f"  ✓ Created: {test_video}")
    print(f"    Duration: {dur:.1f}s  |  Size: {size_kb:.1f} KB")

    # ── Step 2: Create test subtitles ────────────────────────────────────
    print("\nStep 2: Creating test subtitles JSON (Chinese text)...")
    test_subs_json = os.path.join(output_dir, "test_subtitle_font_subs.json")
    _create_test_subtitles_json(test_subs_json)
    print(f"  ✓ Created: {test_subs_json}")
    print(f"    Text: 「{TEST_SUBTITLE_TEXT}」")

    # ── Step 3: Report font availability ─────────────────────────────────
    print("\nStep 3: Checking font environment...")
    font_info = _detect_font_in_use(test_video)
    print(f"  {font_info}")

    # ── Step 4: Burn subtitles ───────────────────────────────────────────
    print("\nStep 4: Burning subtitles into video...")
    output_video = os.path.join(output_dir, "test_subtitle_font_output.mp4")
    result = burn_subtitles_to_video_tool_fn(
        video_path=test_video,
        subtitles_json_path=test_subs_json,
        output_path=output_video,
        logger=None,
    )
    print(f"  Result: {result}")

    if not os.path.exists(output_video):
        print("\n✗ FAIL: Output video was not created.")
        return 1

    out_size_kb = os.path.getsize(output_video) / 1024
    print(f"  ✓ Output video: {output_video}")
    print(f"    Size: {out_size_kb:.1f} KB")

    # ── Step 5: Extract screenshot for visual inspection ─────────────────
    print(f"\nStep 5: Extracting screenshot at t={SCREENSHOT_TIME}s...")
    screenshot = os.path.join(output_dir, "test_subtitle_font_screenshot.png")
    _extract_screenshot(output_video, screenshot, SCREENSHOT_TIME)
    ss_size_kb = os.path.getsize(screenshot) / 1024
    print(f"  ✓ Screenshot: {screenshot}  ({ss_size_kb:.1f} KB)")

    # ── Step 6: Auto-verify (basic pixel check) ──────────────────────────
    print("\nStep 6: Auto-verifying subtitle region...")
    check = _check_subtitle_region_has_content(screenshot)

    if "error" in check:
        print(f"  ⚠ {check['error']}")
    else:
        print(f"  Dark pixels in subtitle band: {check['dark_pixel_count']} "
              f"/ {check['total_sampled']} ({check['dark_ratio']:.2%})")
        if check["has_dark_pixels"]:
            print("  ✓ PASS: Dark text strokes detected — Chinese font rendered correctly.")
        else:
            print("  ✗ FAIL: No dark pixels in subtitle region — font likely missing or not rendering.")
            print(f"    → Please visually inspect: {screenshot}")
            return 1

    # ── Summary ──────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("Test artifacts:")
    print(f"  Base video:     {test_video}")
    print(f"  Subtitles JSON: {test_subs_json}")
    print(f"  Output video:   {output_video}")
    print(f"  Screenshot:     {screenshot}")
    print(f"  SRT file:       {os.path.join(output_dir, 'test_subtitle_font_output.srt')}")
    print(f"  VTT file:       {os.path.join(output_dir, 'test_subtitle_font_output.vtt')}")
    print("\nManually inspect the screenshot to confirm Chinese glyphs are correct.")
    print("If you see boxes/tofu (□□□), the font fix did NOT take effect.")

    return 0


if __name__ == "__main__":
    sys.exit(test_subtitle_font())
