"""
Test: Verify that AI-generated images are saved to the correct output directory.

This test validates the GLOBAL_OUTPUT_DIR fix — after set_output_dir() is called,
image_gen_tool_fn must save files to that directory, not to the current working
directory. Output goes to genai-pipeline/test_output/ (separate from normal runs).
"""

import os
import sys

# Add parent directory to sys.path to allow running from within test_scripts/ folder
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import image_gen_tool_fn, set_output_dir

# ── Test output goes to a dedicated test_output/ directory ──────────────────
# Normal pipeline runs still use output/ — this test is isolated.
TEST_OUTPUT_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test_output"
)


def main():
    # Create a timestamped subdirectory for this test run
    import datetime
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    test_run_dir = os.path.join(TEST_OUTPUT_ROOT, f"test_{timestamp}")
    os.makedirs(test_run_dir, exist_ok=True)

    print(f"Test output directory: {test_run_dir}")

    # ── Set the global output directory ──────────────────────────────────
    # This is the key operation being tested — set_output_dir must propagate
    # so that image_gen_tool_fn saves files here.
    set_output_dir(test_run_dir)

    # ── Generate an image ────────────────────────────────────────────────
    prompt = (
        "A simple whiteboard sketch of a smiling sun rising over green hills, "
        "clean outlines, white background, minimalist style."
    )
    print(f"\nGenerating image for prompt: '{prompt}'")
    result = image_gen_tool_fn(prompt)

    # ── Verify ───────────────────────────────────────────────────────────
    if not result or "Error" in str(result):
        print(f"\nFAILED: Image generation returned an error:\n  {result}")
        sys.exit(1)

    if not os.path.exists(result):
        print(f"\nFAILED: Returned path does not exist on disk:\n  {result}")
        sys.exit(1)

    # The critical check: the generated file MUST be inside the test output dir
    result_abs = os.path.abspath(result)
    test_dir_abs = os.path.abspath(test_run_dir)
    if not result_abs.startswith(test_dir_abs):
        print(
            f"\nFAILED: Generated image is NOT in the expected output directory!\n"
            f"  Expected parent: {test_dir_abs}\n"
            f"  Actual path:     {result_abs}"
        )
        sys.exit(1)

    # Extra sanity: check it's a real PNG file
    file_size = os.path.getsize(result)
    if file_size < 100:
        print(f"\nFAILED: Generated file is too small ({file_size} bytes).")
        sys.exit(1)

    # ── Report ───────────────────────────────────────────────────────────
    print(f"\n✓ PASSED")
    print(f"  Image saved to: {result}")
    print(f"  File size:      {file_size:,} bytes")
    print(f"  Parent dir:     {os.path.dirname(result)}")
    print(f"\nAll assertions passed. The GLOBAL_OUTPUT_DIR fix is working correctly.")


if __name__ == "__main__":
    main()
