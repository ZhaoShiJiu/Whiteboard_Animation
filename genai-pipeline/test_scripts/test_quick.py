import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import run_pipeline


if __name__ == "__main__":
    prompt = "Who is Madhvacharya ? what is their history ?  (strictly 5 scenes)"
    print(f"Testing pipeline with prompt: '{prompt}'")
    # The pipeline now auto-sets up logging with a run_id.
    # Set LOG_LEVEL=DEBUG for verbose output during tests.
    os.environ.setdefault("LOG_LEVEL", "DEBUG")
    final_video = run_pipeline(
        prompt,
        do_research=False,
        do_web_search=True,
        fast_mode=True,
        language="Marathi",
        video_provider=None,
        veo_direction_by_director=False
    )
    if final_video:
        print(f"Pipeline SUCCESS! Final Video: {final_video}")
    else:
        print("Pipeline FAILED.")
