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
    - The Director's narration is quite long ({original_words} words, target ~{target_words}).
    - TIGHTEN the language significantly: cut filler, merge redundant sentences,
      remove throat-clearing phrases. Keep all key facts but reduce the word count.
    - Prioritise impact — punchier is better.
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

    DIRECTOR'S ORIGINAL NARRATION (THIS IS YOUR PRIMARY INPUT):
    "{original_narration}"

    Tone: {tone}
    {arc_context}
    {duration_guidance}

    An image of the whiteboard animation frame has been generated for visual context.

    YOUR TASK: Polish the Director's narration for compelling spoken delivery.

    CORE RULES:
    1. KEEP THE ESSENCE: Preserve key facts, names, numbers, and the core message.
       But CUT filler phrases, redundant explanations, and throat-clearing.
    2. TIGHTEN FOR IMPACT: Shorter, punchier sentences are better than rambling ones.
       If a sentence doesn't earn its place, cut it. Surgery, not sanding.
    3. DO NOT DESCRIBE THE IMAGE: Never say "we see a drawing of..." or
       "the whiteboard shows..." or "lines and shapes depict...".
    4. TELL THE STORY: Sound like a compelling story, not a textbook reading.
       Use the {tone} tone throughout.
    5. KEEP THE VOICE: Maintain the {persona} voice consistently.
    6. FLOW NATURALLY: Use MiniMax's native pause format <#x#> for timing control
       (e.g., <#0.5#> for a half-second pause, <#1.0#> for a full second).
       Do NOT use [pause], [softly], or any other bracketed English cue.
    7. OUTPUT LANGUAGE: The narration MUST be in {language}.

    WHAT YOU MAY DO:
    - Improve word choice for vivid, engaging storytelling
    - Adjust sentence rhythm for better spoken delivery
    - Add transitional phrases for smoother flow
    - Add emotional coloring matching the scene's mood
    - CUT or MERGE sentences that are redundant or drag the pacing
    - Tighten verbose passages while keeping their meaning

    WHAT YOU MUST NEVER DO:
    - Add NEW facts or information not in the original narration
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
