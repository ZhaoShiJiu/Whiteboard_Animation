import json
from typing import Any, Dict, Optional

from .utils import _save_to_run_folder, _emit

# Forward reference for type hinting
try:
    from log_utils import ContextLogger
except ImportError:
    ContextLogger = None  # type: ignore


def _sanitize_json_control_chars(text: str) -> str:
    """
    Escape literal control characters found inside JSON string values.

    LLMs occasionally emit unescaped newlines, tabs, or other control chars
    within string fields like 'narration'. Python's json.loads rejects these
    per RFC 7159. This state machine tracks quote boundaries so structural
    braces/commas outside strings are left untouched.
    """
    result = []
    in_string = False
    escape_next = False
    for ch in text:
        if escape_next:
            result.append(ch)
            escape_next = False
            continue
        if ch == '\\':
            result.append(ch)
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            result.append(ch)
            continue
        if in_string and ord(ch) < 0x20:
            if ch == '\n':
                result.append('\\n')
            elif ch == '\r':
                result.append('\\r')
            elif ch == '\t':
                result.append('\\t')
            else:
                result.append(' ')
            continue
        result.append(ch)
    return ''.join(result)


def director_tool_fn(
    user_instructions: str,
    research_material: str = None,
    language: str = "english",
    enable_veo: bool = False,
    veo_direction_by_director: bool = False,
    logger: Optional["ContextLogger"] = None,
    feedback: str = "",
) -> Dict[str, Any]:
    """
    Acts as the Video Director & Writer — plans the entire video journey.

    The Director decides how many scenes are needed, writes the narration script
    for each scene (as a storyteller, not just descriptions), plans the visual
    setup, and defines the overall narrative arc.

    Args:
        user_instructions: The user's original topic/instructions.
        research_material: Optional detailed research report to incorporate.
        language: The target language for the narration script (default: English).
        enable_veo: Whether AI video generation (any provider) is enabled.
        veo_direction_by_director: Whether the director should explicitly draft video prompts.
        logger: Optional ContextLogger for structured logging.
    Returns:
        A dictionary with 'global_plan' and 'scenes'.
    """

    # If research material is provided, include it; otherwise just use instructions
    research_block = ""
    if research_material and research_material != user_instructions:
        research_block = f"""

    Deep Research Material (USE THIS — it contains rich, detailed information that MUST be woven into your narration):
    ---
    {research_material}
    ---
    """

    veo_instruction = ""
    veo_schema_field = ""
    if enable_veo and veo_direction_by_director:
        veo_instruction = (
            "- 'veo_prompt': Write a descriptive prompt for AI video generation. This prompt should describe "
            "how the elements in the whiteboard drawing should come to life, animate, move, or transition. "
            "The video starts from the final whiteboard drawing, so describe the motion continuation. "
            "Keep it focused on movement, actions, and style continuity from the sketch. Avoid adding marker pens/hands."
        )
        veo_schema_field = '\n          "veo_prompt": "...",'

    feedback_block = ""
    if feedback:
        feedback_block = f"""

    USER FEEDBACK FOR REVISION (CRITICAL — address every point):
    ---
    {feedback}
    ---
    """

    prompt = f"""
    You are an award-winning Video Director, Writer, and Storyteller.
    You are planning a whiteboard animation video. Your job is to craft the ENTIRE video —
    the narrative arc, the script, and the visual direction for every single scene.

    User's Topic / Instructions:
    "{user_instructions}"
    {research_block}{feedback_block}

    YOUR TASK — Plan the complete video:

    STEP 1: Analyze the topic and decide:
    - What TONE fits? (informative, dramatic, playful, sad, etc.)
    - What is the NARRATIVE ARC? (beginning hook → build-up → climax → resolution)
    - Who is narrating? (a professional explainer, a storyteller, a historian, etc.)
    - How many scenes are needed? (CRITICAL: Follow these rules strictly)
    - The goal is that no single scene should have more than ~30-40 seconds of narration.

    STEP 2: For EACH scene, you must provide:
    - 'scene_number': Sequential number
    - 'summary': A 1-line summary of what this scene accomplishes in the narrative arc
    - 'narration': The FULL spoken script for this scene. THIS IS THE MOST IMPORTANT PART.
    - 'description': Visual description for the image generator (what should be DRAWN in this frame)
    - 'visual_setup': Specific visual direction for this frame (composition, key elements, focal points)
    {veo_instruction}
    - 'search_query': (OPTIONAL) If this scene features a specific real-world person, historical figure, or landmark, provide a search query.
    - 'text_overlay': (OPTIONAL) If you want specific impactful text visually rendered.
    - 'key_information': Any critical facts/data from the research that this scene must convey
    - 'emotional_beat': The emotional tone of this specific scene

    CRITICAL RULES:
    - LANGUAGE: The entire script's narration and summary values MUST be written in {language}.
    - ATTRACTIVE PACING & TONE: You MUST detect pacing instructions from the user.

    Output Format (Strict JSON) where all values (specifically 'narration' and 'summary') are in the language '{language}', but the JSON keys remain exactly as defined below in English:
    {{{{
      "global_plan": {{{{
        "title": "Video title",
        "tone": "informative" | "dramatic" | "educational" | "cautionary",
        "narrative_persona": "e.g., Wise Storyteller",
        "visual_style": "e.g., Clean Whiteboard Animation",
        "pacing": "e.g., steady/educational",
        "narrative_arc": "...",
        "target_audience": "...",
        "total_scenes": <number>
      }}}},
      "scenes": [
        {{{{
          "scene_number": 1,
          "summary": "...",
          "narration": "...",
          "description": "...",
          "visual_setup": "...",{veo_schema_field}
          "search_query": "...",
          "text_overlay": "...",
          "key_information": "...",
          "emotional_beat": "..."
        }}}},
        ...
      ]
    }}}}
    """

    try:
        from ai_gateway import generate

        _emit(logger, "info", "Calling Director LLM for scene planning...",
              extra={"language": language, "research_provided": bool(research_material and research_material != user_instructions)})

        response = generate(
            task="story",
            prompt=prompt,
            options={"response_format": "json", "max_tokens": 8192, "temperature": 0.7},
        )
        try:
            result = json.loads(response.content)
        except json.JSONDecodeError:
            _emit(logger, "warning", "Director JSON had control characters — sanitizing and retrying...")
            sanitized = _sanitize_json_control_chars(response.content)
            result = json.loads(sanitized)
        scene_count = len(result.get("scenes", []))
        _save_to_run_folder(json.dumps(result, indent=2), "video_plan.json")

        _emit(logger, "info", f"Director planned {scene_count} scenes",
              extra={"scene_count": scene_count, "tone": result.get("global_plan", {}).get("tone")})
        return result
    except Exception as e:
        _emit(logger, "error", f"Director tool failed: {e}", extra={"error": str(e)})
        # Fallback to a basic structure if parsing fails
        return {
            "global_plan": {
                "title": "Untitled Video",
                "tone": "informative",
                "narrative_persona": "Professional Storyteller",
                "visual_style": "Clean Whiteboard Animation",
                "pacing": "steady",
                "narrative_arc": "Linear exploration of the topic",
                "target_audience": "general public",
                "total_scenes": 1
            },
            "scenes": [{
                "scene_number": 1,
                "summary": "Error parsing",
                "description": "Error parsing",
                "narration": "Error parsing",
                "visual_setup": "Simple sketch",
                "search_query": "",
                "text_overlay": "",
                "key_information": "",
                "emotional_beat": "neutral"
            }]
        }
