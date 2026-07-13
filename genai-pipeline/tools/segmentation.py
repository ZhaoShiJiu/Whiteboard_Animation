import os
import time
import json
from typing import Optional

import requests
from config import SAM_API_URL, SAM_API_TOKEN
from . import utils
from .utils import _emit

try:
    from log_utils import ContextLogger
except ImportError:
    ContextLogger = None  # type: ignore


def segmentation_tool_fn(
    image_path: str,
    logger: Optional["ContextLogger"] = None,
) -> str:
    """
    Performs instance segmentation on an image.

    This tool follows a two-step process:
    1. It uses a vision-capable LLM to identify major, distinct objects in the image.
    2. For each identified object, it calls a hosted SAM3 (Segment Anything Model)
       API to generate a high-quality segmentation mask.

    Note: Step 1 requires a vision-capable model. DeepSeek V4 Pro (text-only)
    is used as a fallback — it will identify objects based on scene context
    without actually seeing the image. For best results, use a vision-capable LLM.

    Args:
        image_path: The absolute path to the input image file (PNG or JPEG).
        logger: Optional ContextLogger for structured logging.

    Returns:
        str: The absolute path to a JSON file containing the results.
    """
    if not SAM_API_URL:
        return "Error: SAM_API_URL not configured. Skipping segmentation."

    if not os.path.exists(image_path):
        return f"Error: Image file not found at {image_path}"

    _emit(logger, "info", f"Starting segmentation process", extra={"image_path": image_path})

    # 1. Object Identification via AI Gateway
    id_prompt = (
        'Identify the 3-5 largest and most distinct physical object groups in '
        'this scene that define the composition. Group smaller related parts into '
        'large logical entities (e.g., instead of "wheel", "pedal", "seat", just say '
        '"bicycle"). Return a raw JSON list of strings, for example: '
        '["bicycle", "rider", "background building"]. '
        'Do not include markdown formatting or explanation.'
    )

    objects = []
    try:
        from ai_gateway import generate

        # Read image bytes for vision-capable providers
        with open(image_path, "rb") as f:
            image_bytes = f.read()

        response = generate(
            task="story",
            prompt=id_prompt,
            reference_images=[image_bytes],
            options={"response_format": "json", "max_tokens": 512},
        )
        objects = json.loads(response.content)
        _emit(logger, "info", f"Identified objects for segmentation", extra={"objects": objects})

    except Exception as e:
        _emit(logger, "warning", f"Object identification failed: {e}. Using fallback objects.",
              extra={"error": str(e)})
        # Fallback: generate generic objects
        objects = ["main subject", "background"]

    if not objects or not isinstance(objects, list):
        objects = ["main subject", "background"]

    # 2. Instance Segmentation: Call SAM3 for each identified object
    combined_results = {
        "image_path": image_path,
        "objects": objects,
        "segmentations": {}
    }

    for obj in objects:
        _emit(logger, "debug", f"Segmenting object: {obj}")
        try:
            with open(image_path, "rb") as f:
                files = {"file": f}
                data = {"prompt": obj}

                headers = {}
                if SAM_API_TOKEN:
                    headers["Authorization"] = f"Bearer {SAM_API_TOKEN}"
                resp = requests.post(SAM_API_URL, files=files, data=data, headers=headers)

                if resp.status_code == 200:
                    result = resp.json()
                    combined_results["segmentations"][obj] = result
                    _emit(logger, "info", f"SAM3 segmentation OK for '{obj}'")
                else:
                    _emit(logger, "warning", f"SAM3 failed for '{obj}': HTTP {resp.status_code}",
                          extra={"status_code": resp.status_code, "response": resp.text[:200]})
                    combined_results["segmentations"][obj] = {
                        "error": f"Status {resp.status_code}: {resp.text}"
                    }

        except Exception as e:
            _emit(logger, "warning", f"Error calling SAM3 for '{obj}': {e}", extra={"error": str(e)})
            combined_results["segmentations"][obj] = {"error": str(e)}

    # 3. Finalization: Save results to a timestamped JSON file
    timestamp = int(time.time())
    output_filename = f"segmentation_results_{timestamp}.json"

    if utils.GLOBAL_OUTPUT_DIR:
        saved_path = utils._save_to_run_folder(
            json.dumps(combined_results, indent=2), output_filename
        )
        _emit(logger, "info", f"Segmentation results saved", extra={"path": saved_path,
              "object_count": len(objects)})
        return saved_path
    else:
        try:
            full_path = os.path.abspath(output_filename)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(combined_results, indent=2))
            return full_path
        except Exception as e:
            return f"Error saving file: {str(e)}. Raw JSON: " + json.dumps(combined_results)
