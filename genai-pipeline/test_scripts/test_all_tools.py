import os
import sys
import time
import datetime
import subprocess
import json

# Add parent directory to sys.path to allow running from within test_scripts/ folder
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import (
    research_tool_fn, 
    director_tool_fn, 
    prompt_tool_fn, 
    image_gen_tool_fn, 
    generate_tts_audio_tool_fn,
    segmentation_tool_fn,
    merge_audio_video_tool_fn,
    concatenate_videos_tool_fn,

    burn_subtitles_to_video_tool_fn,

    set_output_dir,
    reference_search_tool_fn,
    generate_video_seedance_tool_fn
)

GLOBAL_TEST_DIR = ""

def test_research():
    context = "The origin of the humble pencil"
    print(f"\n--- Testing Research Tool with context: '{context}' ---")
    print("This may take several minutes. Please wait...")
    
    start_time = time.time()
    result = research_tool_fn(context)
    end_time = time.time()
    
    print(f"\n--- Research Completed in {round(end_time - start_time, 2)} seconds ---")
    print("\nResult Summary (first 500 chars):")
    print("-" * 20)
    print(result[:500] + "...")
    print("-" * 20)
    return result

def test_image_gen(prompt=None):
    print("\n--- Testing Image Generation Tool ---")
    if not prompt:
        prompt = "A professional whiteboard animation of a coder working on a complex AI project."
    print(f"Generating image for: {prompt}")
    
    image_path = image_gen_tool_fn(prompt)
    
    if image_path and "Error" not in image_path:
        print(f"SUCCESS: Image generated and saved to {image_path}")
        return image_path
    else:
        print(f"FAILED: {image_path}")
        return None

def test_director_and_prompt(user_instructions=None, run_image=False, language="english"):
    if user_instructions is None:
        user_instructions = "Story of shepherd boy and wolf"
    
    print(f"\n--- Testing Director Tool with language: {language} ---")
    video_plan = director_tool_fn(user_instructions, language=language)
    global_plan = video_plan.get("global_plan", {})
    scenes = video_plan.get("scenes", [])
    
    if scenes and isinstance(scenes, list) and len(scenes) > 0:
        print(f"Director planned {len(scenes)} scenes. Tone: {global_plan.get('tone')}")
        print(f"Narrative Arc: {global_plan.get('narrative_arc', 'N/A')}")
        print("\n--- Testing Prompt Tool with the first scene ---")
        first_scene = scenes[0]
        description = first_scene.get('description', '')
        narration = first_scene.get('narration', '')
        visual_setup = first_scene.get('visual_setup', '')
        print(f"Scene Summary: {first_scene.get('summary', '')}")
        print(f"Narration: {narration[:200]}...")
        if description:
            prompt = prompt_tool_fn(description, visual_setup=visual_setup, global_plan=global_plan)
            print(f"Scene Description: {description}")
            print(f"Generated Prompt: {prompt}")
            
            if run_image:
                test_image_gen(prompt)
        else:
            print("First scene logic error: no description found.")
    else:
        print("Director tool failed to generate scenes.")

def test_tts(language="english"):
    print(f"\n--- Testing TTS Generation Tool with language: {language} ---")
    
    # 1. Single Speaker Test
    if language.lower() == "spanish":
        text_single = "¡Hola! Que tengas un maravilloso día."
    elif language.lower() == "hindi":
        text_single = "नमस्ते! आपका दिन बहुत अच्छा रहे।"
    else:
        text_single = "Say cheerfully: Have a wonderful day!"
        
    print(f"Testing Single Speaker TTS: '{text_single}'")
    audio_path, _ = generate_tts_audio_tool_fn(text_single, language=language)
    if audio_path and "Error" not in audio_path:
        print(f"SUCCESS: Single speaker audio saved to {audio_path}")
        return audio_path
    else:
        print(f"FAILED: {audio_path}")
        return None

def test_segmentation(image_path=None):
    print("\n--- Testing Instance Segmentation Tool ---")
    
    # If no image provided, generate one or use a fallback
    if not image_path:
        print("No image path provided. Generating a test image first...")
        prompt = "A wooden table with a red apple, a yellow banana, and a blue coffee mug. High isolation."
        image_path = test_image_gen(prompt)
        
    if not image_path or "Error" in image_path:
        print("Skipping segmentation test because image generation failed.")
        return

    print(f"Running segmentation on: {image_path}")
    result_path = segmentation_tool_fn(image_path)
    
    if result_path and "Error" not in result_path:
        print(f"SUCCESS: Segmentation results saved to {result_path}")
    else:
        print(f"FAILED: {result_path}")

def test_merge_av():
    print("\n--- Testing Merge A/V Tool ---")
    
    # 1. Generate Audio
    print("Step 1: generating audio...")
    audio_path = test_tts()
    if not audio_path:
        print("Merge A/V test failed at audio generation step.")
        return

    # 2. Generate Dummy Video with ffmpeg
    print("Step 2: generating dummy video...")
    video_path = os.path.join(GLOBAL_TEST_DIR, "dummy_video.mp4")
    
    # Create a 2-second blue video
    # -t 2 : duration 2s
    # -f lavfi -i color=c=blue:s=640x480 : blue background
    # -pix_fmt yuv420p : standard pixel format
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "color=c=blue:s=640x480",
        "-t", "2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        video_path
    ]
    
    try:
        subprocess.run(cmd, check=True)
        print(f"Dummy video created at {video_path}")
    except Exception as e:
        print(f"FAILED to create dummy video. Ensure ffmpeg is installed. Error: {e}")
        return

    # 3. Merge
    print("Step 3: Merging audio and video...")
    output_path = os.path.join(GLOBAL_TEST_DIR, "merged_test_output.mp4")
    result = merge_audio_video_tool_fn(video_path, audio_path, output_path)
    
    if result and "Error" not in result:
        print(f"SUCCESS: Merged file saved to {result}")
    else:
        print(f"FAILED: {result}")

def test_concatenate_av():
    print("\n--- Testing Concatenate Videos Tool ---")
    
    # 1. Generate first dummy video (blue, 2s)
    print("Step 1: Generating dummy video 1...")
    video1_path = os.path.join(GLOBAL_TEST_DIR, "concat_video1.mp4")
    cmd1 = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "color=c=blue:s=640x480:d=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        video1_path
    ]
    try:
        subprocess.run(cmd1, check=True)
        print(f"Dummy video 1 created at {video1_path}")
    except Exception as e:
        print(f"FAILED to create dummy video 1. Ensure ffmpeg is installed. Error: {e}")
        return
    
    # 2. Generate second dummy video (red, 2s)
    print("Step 2: Generating dummy video 2...")
    video2_path = os.path.join(GLOBAL_TEST_DIR, "concat_video2.mp4")
    cmd2 = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "color=c=red:s=640x480:d=2",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        video2_path
    ]
    try:
        subprocess.run(cmd2, check=True)
        print(f"Dummy video 2 created at {video2_path}")
    except Exception as e:
        print(f"FAILED to create dummy video 2. Ensure ffmpeg is installed. Error: {e}")
        return
    
    # 3. Concatenate
    print("Step 3: Concatenating videos...")
    output_path = os.path.join(GLOBAL_TEST_DIR, "concatenated_test_output.mp4")
    result = concatenate_videos_tool_fn([video1_path, video2_path], output_path)
    
    if result and "Error" not in result:
        print(f"SUCCESS: Concatenated file saved to {result}")
    else:
        print(f"FAILED: {result}")

def test_video_subtitle():
    print("\n--- Testing Video Subtitle Burning Tool ---")
    
    # 1. Generate a dummy video with audio first
    print("Step 1: Generating dummy video with audio...")
    video_path = os.path.join(GLOBAL_TEST_DIR, "video_for_subs.mp4")
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "color=c=black:s=640x480:d=5",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        video_path
    ]
    try:
        subprocess.run(cmd, check=True)
        print(f"Dummy video created at {video_path}")
    except Exception as e:
        print(f"FAILED to create dummy video. Error: {e}")
        return

    # 2. Create a mock subtitle JSON
    print("Step 2: Creating mock subtitle JSON...")
    sub_data = {
        "subtitles": [
            {"start": 0.5, "end": 2.0, "text": "Hello, this is a test of burned-in subtitles."},
            {"start": 2.5, "end": 4.5, "text": "FFmpeg is now overlaying this text on the video."}
        ]
    }
    sub_path = os.path.join(GLOBAL_TEST_DIR, "mock_subs.json")
    with open(sub_path, 'w') as f:
        json.dump(sub_data, f)

    # 3. Burn subtitles
    print("Step 3: Burning subtitles...")
    output_path = os.path.join(GLOBAL_TEST_DIR, "video_with_burned_subs.mp4")
    result = burn_subtitles_to_video_tool_fn(video_path, sub_path, output_path)
    
    if result and "Error" not in result:
        print(f"SUCCESS: Subtitled video saved to {result}")
    else:
        print(f"FAILED: {result}")

def test_reference_search():
    print("\n--- Testing Reference Search Tool ---")
    query = "5th president of France"
    print(f"Searching for: {query}")
    
    result = reference_search_tool_fn(query)
    if result and "Error" not in result and os.path.exists(result):
        print(f"SUCCESS: Reference image downloaded to {result}")
        return result
    else:
        print(f"FAILED: {result}")
        return None

def test_veo_video_gen(image_path=None, prompt=None):
    print("\n--- Testing Veo Video Generation Tool ---")
    if not image_path:
        print("No image path provided. Generating a test image first...")
        test_prompt = "A simple hand-drawn whiteboard sketch of a cute robot waving hello. White background, simple lines."
        image_path = test_image_gen(test_prompt)
        
    if not image_path or "Error" in image_path:
        print("Skipping Veo video generation because image generation failed.")
        return
        
    if not prompt:
        prompt = "The robot waves its arm happily back and forth while smiling."
        
    print(f"Running Veo video generation using first frame: {image_path}")
    print(f"Animation prompt: {prompt}")
    
    result_path = generate_video_seedance_tool_fn(image_path, prompt)
    
    if result_path and "Error" not in result_path:
        print(f"SUCCESS: Veo video saved to {result_path}")
    else:
        print(f"FAILED: {result_path}")


def main():
    global GLOBAL_TEST_DIR
    
    # Setup global run directory
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    base_dir = os.path.dirname(os.path.abspath(__file__))
    output_root = os.path.join(base_dir, "output")
    if not os.path.exists(output_root):
        os.makedirs(output_root)
    GLOBAL_TEST_DIR = os.path.join(output_root, f"{timestamp}")
    set_output_dir(GLOBAL_TEST_DIR)
    print(f"--- Started Test Run : {timestamp} ---")
    print(f"Output Directory: {GLOBAL_TEST_DIR}")
    
    user_lang = input("Enter language for Director / TTS tests (default 'english'): ").strip()
    if not user_lang:
        user_lang = "english"
    
    options = {
        "1": ("Research Tool", test_research),
        "2": ("Director Tool", lambda: test_director_and_prompt(language=user_lang)),
        "3": ("Prompt Tool", lambda: test_director_and_prompt(None, run_image=False, language=user_lang)), # Director+prompt
        "4": ("Image Gen Tool", test_image_gen),
        "5": ("TTS Tool", lambda: test_tts(language=user_lang)),
        "6": ("Segmentation Tool", test_segmentation),
        "7": ("Merge A/V Tool", test_merge_av),
        "8": ("Concatenate Videos Tool", test_concatenate_av),
        "9": ("Video Subtitle Tool (Burn-in)", test_video_subtitle),
        "10": ("Reference Search Tool", test_reference_search),
        "11": ("Veo Video Gen Tool", test_veo_video_gen)
    }
    
    print("\nAvailable Tests:")
    for key, (name, _) in options.items():
        print(f"{key}. {name}")
        
    user_input = input("\nEnter choice(s) comma-separated (e.g. 1,3,5) or press Enter for ALL: ").strip()
    
    selected_keys = []
    if not user_input:
        selected_keys = list(options.keys())
        print("Selected: ALL")
    else:
        parts = user_input.split(',')
        for p in parts:
            p = p.strip()
            if p in options:
                selected_keys.append(p)
    
    if not selected_keys:
        print("No valid tests selected.")
        return
 
    # Special handling for Research -> Director flow if both selected?
    # For now, keep them independent or allow the user to chain manually.
    
    for key in selected_keys:
        name, func = options[key]
        print(f"\n>>> RUNNING: {name} <<<")
        try:
            func()
        except Exception as e:
            print(f"CRITICAL ERROR running {name}: {e}")

if __name__ == "__main__":
    main()
