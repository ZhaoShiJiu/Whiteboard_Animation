"""
Standalone image generation test — verifies generated images have no frames/borders.

Usage:
    cd genai-pipeline
    python test_scripts/test_image_gen_noframe.py

Pre-requisites:
    - DASHSCOPE_API_KEY environment variable set (or .env file in genai-pipeline/)
    - DEEPSEEK_API_KEY environment variable set (for prompt generation)
    - Dependencies installed
"""

import os
import sys

# Make genai-pipeline/ importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.image_prompt_tool import prompt_tool_fn
from tools.image_gen import image_gen_tool_fn
from tools.utils import set_output_dir


def test_image_gen():
    # ---- Config -------------------------------------------------------------
    SCENE_DESCRIPTION = (
        "A majestic Bengal tiger standing on a rocky cliff at sunset, "
        "looking out over a vast jungle valley below."
    )
    VISUAL_SETUP = "Wide shot, tiger in silhouette on the left third, golden sky filling the right side."
    TEXT_OVERLAY = "The King of the Jungle"
    GLOBAL_PLAN = {
        "tone": "dramatic",
        "visual_style": "Clean Whiteboard Animation",
    }

    # ---- Setup output dir ---------------------------------------------------
    output_dir = os.path.join(os.path.dirname(__file__), "..", "test_output")
    output_dir = os.path.abspath(output_dir)
    os.makedirs(output_dir, exist_ok=True)
    set_output_dir(output_dir)
    print(f"Output directory: {output_dir}")

    # ---- Step 1: Generate image prompt -------------------------------------
    print("\n[Step 1] Generating image prompt via LLM...\n")
    img_prompt = prompt_tool_fn(
        scene_description=SCENE_DESCRIPTION,
        visual_setup=VISUAL_SETUP,
        text_overlay=TEXT_OVERLAY,
        global_plan=GLOBAL_PLAN,
    )

    if not img_prompt or "Error" in img_prompt:
        print(f"\n✗ Prompt generation failed: {img_prompt}")
        return 1

    print("-" * 60)
    print("Generated Prompt:")
    print("-" * 60)
    print(img_prompt)
    print("-" * 60)

    # Quick sanity check: does the prompt mention whiteboard / no-frame concepts?
    prompt_lower = img_prompt.lower()
    if "white" not in prompt_lower and "board" not in prompt_lower:
        print("⚠ Warning: prompt doesn't seem to mention 'whiteboard' — may not be whiteboard style.")

    # ---- Step 2: Generate image --------------------------------------------
    print("\n[Step 2] Generating image (this may take 10-30 seconds)...\n")
    image_path = image_gen_tool_fn(
        prompt=img_prompt,
    )

    # ---- Report results ----------------------------------------------------
    if image_path and os.path.exists(image_path):
        size_kb = os.path.getsize(image_path) / 1024
        print(f"\n✓ Image generated: {image_path}")
        print(f"  Size: {size_kb:.1f} KB")
        print("\n→ Open the image and check: are there any frames/borders/decorative edges?")
        print("  Expected: pure artwork on white background, NO enclosing frame of any kind.")
    else:
        print(f"\n✗ Image generation failed: {image_path}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(test_image_gen())
