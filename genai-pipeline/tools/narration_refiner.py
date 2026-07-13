import os
from typing import Optional

from .utils import _save_to_run_folder, _emit

try:
    from log_utils import ContextLogger
except ImportError:
    ContextLogger = None  # type: ignore


def refine_narration_tool_fn(
    original_narration: str,
    image_path: str,
    video_duration: float = None,
    global_plan: dict = None,
    language: str = "english",
    logger: Optional["ContextLogger"] = None,
) -> str:
    """
    Enhances the Director's narration script — preserving ALL information.

    The Director's narration is the PRIMARY source of truth. This tool only
    refines it for pacing and flow. It does NOT replace content with image
    descriptions.

    Args:
        original_narration: The Director's narration script (PRIMARY CONTENT).
        image_path: Path to the generated image (for visual context ONLY).
        video_duration: Duration of the animation in seconds (for pacing).
        global_plan: The Director's global plan (for tone/persona consistency).
        language: The target language for the narration script (default: English).
        logger: Optional ContextLogger for structured logging.
    Returns:
        An enhanced narration string with ALL original information preserved.
    """

    if not os.path.exists(image_path):
        _emit(logger, "warning", f"Image not found for narration refinement: {image_path}. Using original.",
              extra={"image_path": image_path})
        return original_narration

    # Calculate target word count based on video duration
    # Average speaking pace: ~140 words per minute (natural, unhurried)
    duration_guidance = ""
    if video_duration:
        target_words = int((video_duration / 60) * 140)
        max_words = int(target_words * 1.5)
        bypass_threshold = int(target_words * 2.0)
        original_words = len(original_narration.split())

        if original_words > bypass_threshold:
            duration_guidance = f"""
    PACING CONSTRAINT:
    - The animation is {video_duration:.1f} seconds long.
    - The Director's narration is intentionally rich and detailed ({original_words} words).
    - Do NOT shorten, compress, or tighten the narration. Preserve it in full.
    - The video will hold on the last frame to accommodate the longer audio. That is expected and fine.
    """
        else:
            duration_guidance = f"""
    PACING CONSTRAINT:
    - The animation is {video_duration:.1f} seconds long.
    - At natural speaking pace (~140 words/min), aim for approximately {target_words}-{max_words} words.
    - If the Director's narration is already close to this length, make minimal changes.
    - If it's too short, EXPAND with more vivid storytelling detail (not new information).
    - If it's too long, TIGHTEN the language (but keep ALL facts/information).
    """

    persona = global_plan.get("narrative_persona", "Professional Storyteller") if global_plan else "Professional Storyteller"
    tone = global_plan.get("tone", "dramatic") if global_plan else "dramatic"
    narrative_arc = global_plan.get("narrative_arc", "") if global_plan else ""

    arc_context = ""
    if narrative_arc:
        arc_context = f"\n    Overall Story Arc: {narrative_arc}"

    prompt = f"""
    You are a Narration Enhancer working under the direction of a {persona}.

    DIRECTOR'S ORIGINAL NARRATION (THIS IS YOUR PRIMARY INPUT — PRESERVE IT):
    "{original_narration}"

    Tone: {tone}
    {arc_context}
    {duration_guidance}

    An image of the whiteboard animation frame has been generated for visual context.

    YOUR TASK: Enhance the Director's narration for spoken delivery.

    ABSOLUTE RULES — VIOLATION IS FAILURE:
    1. PRESERVE ALL INFORMATION: Every fact, name, number, and detail from the Director's
       narration MUST appear in your output. You are ENHANCING, not replacing.
    2. DO NOT DESCRIBE THE IMAGE: You must NEVER say things like "we see a drawing of..."
       or "the whiteboard shows..." or "lines and shapes depict...". The narration is for
       the AUDIENCE watching the video, not someone looking at a whiteboard.
    3. TELL THE STORY: The narration should sound like a compelling story being told to
       an audience. Use the {tone} tone throughout.
    4. KEEP THE VOICE: Maintain the {persona} voice consistently.
    5. FLOW NATURALLY: The narration should sound natural when spoken aloud. Use MiniMax's
       native pause format <#x#> for timing control (e.g., <#0.5#> for a half-second pause,
       <#1.0#> for a full second). Do NOT use [pause], [softly], or any other bracketed
       English cue — the TTS engine will read those words aloud, ruining the narration.
    6. OUTPUT LANGUAGE: The refined narration MUST be written in {language}.

    WHAT YOU MAY DO:
    - Improve word choice for more vivid, engaging storytelling
    - Adjust sentence rhythm for better spoken delivery
    - Add transitional phrases for smoother flow
    - Expand briefly if the narration needs to be longer for the video duration
    - Add emotional coloring that matches the scene's mood

    WHAT YOU MUST NEVER DO:
    - Remove or replace factual content from the Director's narration
    - Add information that wasn't in the original narration
    - Describe what's drawn in the whiteboard image
    - Add meta-commentary about the video or animation
    - Change the core message or meaning

    Output: Return ONLY the enhanced narration text. No explanations, no labels, no quotes.
    """

    try:
        from ai_gateway import generate

        _emit(logger, "debug", "Calling narration refiner LLM...",
              extra={"original_length": len(original_narration), "video_duration": video_duration})

        response = generate(
            task="story",
            prompt=prompt,
            options={"max_tokens": 4096, "temperature": 0.7},
        )
        result = response.content.strip()

        # Clean any wrapping quotes the model might add
        if result.startswith('"') and result.endswith('"'):
            result = result[1:-1]

        _save_to_run_folder(f"Original: {original_narration}\nRefined: {result}\n---\n", "narration_refinement_log.txt", mode="a")
        _emit(logger, "info", "Narration refined",
              extra={"original_length": len(original_narration), "refined_length": len(result)})
        return result

    except Exception as e:
        _emit(logger, "warning", f"Narration refinement failed: {e}. Using original.",
              extra={"error": str(e)})
        return original_narration
