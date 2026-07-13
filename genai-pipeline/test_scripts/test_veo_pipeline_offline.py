import os
import sys
# Add parent directory to sys.path to allow running from within test_scripts/ folder
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shutil
import subprocess
import pipeline
from tools import utils


# Define paths
EXISTING_RUN_DIR = r"D:\software-dev\storyboard-ai\genai-pipeline\output\20260613_010608"
EXISTING_IMAGE = os.path.join(EXISTING_RUN_DIR, "generated_image_1781292986.png")
EXISTING_VIDEO = os.path.join(EXISTING_RUN_DIR, "veo_video_1781293052.mp4")

# Verify inputs exist
if not os.path.exists(EXISTING_IMAGE) or not os.path.exists(EXISTING_VIDEO):
    print(f"Error: Required mock inputs not found in {EXISTING_RUN_DIR}")
    exit(1)

# Mock tools
def mock_research_tool_fn(context, **kwargs):
    print("[MOCK] Research Tool called")
    return "Mock research report"

def mock_director_tool_fn(user_context, **kwargs):
    print("[MOCK] Director Tool called")
    return {
        "global_plan": {"tone": "inspiring", "narrative_arc": "adventure"},
        "scenes": [
            {
                "description": "Sheep grazing in a green meadow under a bright blue sky.",
                "narration": "In a quiet valley, a flock of sheep grazed peacefully.",
                "veo_prompt": "whiteboard animation style sheep grazing on hills",
                "visual_setup": "sheep grazing",
                "summary": "sheep grazing",
                "emotional_beat": "peaceful",
                "search_query": "sheep grazing meadow"
            }
        ]
    }

def mock_prompt_tool_fn(description, **kwargs):
    print("[MOCK] Prompt Tool called")
    return "Mock image prompt"

def mock_image_gen_tool_fn(prompt, **kwargs):
    print("[MOCK] Image Gen Tool called")
    # Copy the existing image to output directory
    output_dir = utils.GLOBAL_OUTPUT_DIR
    target_path = os.path.join(output_dir, "generated_image_temp.png")
    shutil.copy2(EXISTING_IMAGE, target_path)
    return target_path

def mock_generate_video_seedance_tool_fn(image_path, prompt, **kwargs):
    print("[MOCK] Seedance Video Gen Tool called")
    output_dir = utils.GLOBAL_OUTPUT_DIR
    target_video_path = os.path.join(output_dir, "veo_video_mock.mp4")
    # Copy existing video as the "veo video" output
    shutil.copy2(EXISTING_VIDEO, target_video_path)
    
    # Simulate what video_gen.py does:
    # 1. Extract first frame of the generated video and replace original reference image (image_path)
    # Since our EXISTING_IMAGE is already matching, we can just extract from the video to test ffmpeg extraction
    ext = ".png"
    if image_path.lower().endswith((".jpg", ".jpeg")):
        ext = ".jpg"
    temp_first_frame = target_video_path + "_first_frame" + ext
    extract_frame_cmd = [
        "ffmpeg", "-y",
        "-i", target_video_path,
        "-vframes", "1",
        "-f", "image2",
        temp_first_frame
    ]
    print(f"[MOCK] Extracting first frame from video: {' '.join(extract_frame_cmd)}")
    subprocess.run(extract_frame_cmd, capture_output=True)
    
    if os.path.exists(temp_first_frame):
        if os.path.exists(image_path):
            os.remove(image_path)
        os.rename(temp_first_frame, image_path)
        print(f"[MOCK] Replaced {image_path} with first frame of Veo video.")
    
    # 2. Extract/Generate audio as a separate .wav (silenced for the video itself)
    # The real tool saves a silent audio file and strips audio from the video.
    # Let's just return the path to the silent video.
    return target_video_path

def mock_segmentation_tool_fn(image_path, **kwargs):
    print("[MOCK] Segmentation Tool called (returning None for fallback)")
    return None

def mock_refine_narration_tool_fn(narration, *args, **kwargs):
    print("[MOCK] Refine Narration Tool called")
    return narration

def mock_generate_tts_audio_tool_fn(text, **kwargs):
    print("[MOCK] TTS Audio Tool called")
    output_dir = utils.GLOBAL_OUTPUT_DIR
    audio_path = os.path.join(output_dir, "tts_audio_mock.wav")

    # Generate a simple 5-second silent audio track using ffmpeg
    tts_cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", "anullsrc=r=24000:cl=mono",
        "-t", "5",
        "-acodec", "pcm_s16le",
        audio_path
    ]
    subprocess.run(tts_cmd, capture_output=True)
    return audio_path, None  # (audio_path, subtitles_json_path) — no subtitles in mock

# Patch pipeline imports
pipeline.research_tool_fn = mock_research_tool_fn
pipeline.web_grounded_research_tool_fn = mock_research_tool_fn
pipeline.director_tool_fn = mock_director_tool_fn
pipeline.prompt_tool_fn = mock_prompt_tool_fn
pipeline.image_gen_tool_fn = mock_image_gen_tool_fn
pipeline.generate_video_seedance_tool_fn = mock_generate_video_seedance_tool_fn
pipeline.segmentation_tool_fn = mock_segmentation_tool_fn
pipeline.refine_narration_tool_fn = mock_refine_narration_tool_fn
pipeline.generate_tts_audio_tool_fn = mock_generate_tts_audio_tool_fn

if __name__ == "__main__":
    print("Starting OFFLINE pipeline integration test...")
    final_video = pipeline.run_pipeline(
        user_context="sheep grazing offline test",
        do_research=False,
        do_web_search=False,
        use_internet_image_search=False,
        fast_mode=False,
        language="english",
        video_provider="seedance",
        veo_direction_by_director=False
    )
    if final_video and os.path.exists(final_video):
        print(f"\nSUCCESS! Offline pipeline ran successfully. Output: {final_video}")
    else:
        print(f"\nFAILURE! Pipeline output path: {final_video}")
