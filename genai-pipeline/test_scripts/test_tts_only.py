"""
Standalone TTS test — calls MiniMax Speech-2.8-HD directly without running the full pipeline.

Usage:
    cd genai-pipeline
    python test_scripts/test_tts_only.py

Pre-requisites:
    - MINIMAX_API_KEY environment variable set (or .env file in genai-pipeline/)
    - Dependencies installed (requests, python-dotenv)
"""

import os
import sys

# Make genai-pipeline/ importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.tts import generate_tts_audio_tool_fn
from tools.utils import set_output_dir, get_media_duration


def test_tts():
    # ---- Config -------------------------------------------------------------
    # Change these to test different text / languages
    TEST_TEXT = (
        "The Pythagorean theorem is a fundamental relation in Euclidean geometry "
        "among the three sides of a right triangle. It states that the square of "
        "the hypotenuse is equal to the sum of the squares of the other two sides."
    )
    LANGUAGE = "Chinese"

    # ---- Setup output dir ---------------------------------------------------
    output_dir = os.path.join(os.path.dirname(__file__), "..", "test_output")
    output_dir = os.path.abspath(output_dir)
    set_output_dir(output_dir)
    print(f"Output directory: {output_dir}")

    # ---- Generate TTS -------------------------------------------------------
    print(f"\nGenerating TTS for text ({len(TEST_TEXT)} chars, language={LANGUAGE})...\n")

    audio_path, subtitles_path = generate_tts_audio_tool_fn(
        text=TEST_TEXT,
        language=LANGUAGE,
    )

    # ---- Report results -----------------------------------------------------
    if audio_path and os.path.exists(audio_path):
        duration = get_media_duration(audio_path)
        size_kb = os.path.getsize(audio_path) / 1024
        print(f"\n✓ Audio:  {audio_path}")
        print(f"  Duration: {duration:.1f}s  |  Size: {size_kb:.1f} KB")

        if subtitles_path and os.path.exists(subtitles_path):
            size_b = os.path.getsize(subtitles_path)
            print(f"✓ Subtitles: {subtitles_path}  ({size_b} bytes)")
        else:
            print("✗ No subtitles returned.")

        print("\nDone. Play the mp3 to verify quality and timing.")
    else:
        print(f"\n✗ TTS failed: {audio_path}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(test_tts())
