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


def _repair_truncated_json(text: str) -> str:
    """
    Attempt to repair a truncated or structurally broken JSON string.

    Handles common LLM output failures:
      - Unterminated strings (missing closing quote)
      - Missing closing braces/brackets
      - Trailing content after the last valid token

    Returns the repaired string, or the original if repair fails.
    """
    if not text or not text.strip():
        return text

    text = text.strip()

    # 1. If the last non-whitespace char is inside an unclosed string, close it
    in_string = False
    escape_next = False
    for i, ch in enumerate(text):
        if escape_next:
            escape_next = False
            continue
        if ch == '\\':
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
    if in_string:
        text = text + '"'

    # 2. Count and balance braces/brackets
    open_braces = text.count('{') - text.count('}')
    open_brackets = text.count('[') - text.count(']')

    if open_braces > 0 or open_brackets > 0:
        # Try to find the last valid structural character and trim to it,
        # then append missing closers
        repair = text.rstrip(',\n\r\t ')
        repair = repair + ']' * max(0, open_brackets)
        repair = repair + '}' * max(0, open_braces)
        text = repair

    # 3. Try incremental parse — use what we can decode
    try:
        import json as _json
        decoder = _json.JSONDecoder()
        decoder.raw_decode(text)
        return text
    except _json.JSONDecodeError:
        pass

    # 4. Last resort: find last valid "}," or "]," and close from there
    last_good = -1
    for candidate_char in ['}', ']', '"']:
        pos = text.rfind(candidate_char)
        if pos > last_good:
            last_good = pos

    if last_good > 0:
        truncated = text[:last_good + 1]
        open_b = truncated.count('{') - truncated.count('}')
        open_br = truncated.count('[') - truncated.count(']')
        truncated = truncated + ']' * max(0, open_br)
        truncated = truncated + '}' * max(0, open_b)
        return truncated

    return text


def director_tool_fn(
    user_instructions: str,
    research_material: str = None,
    language: str = "english",
    enable_veo: bool = False,
    veo_direction_by_director: bool = False,
    logger: Optional["ContextLogger"] = None,
    feedback: str = "",
    target_duration_sec: int = 240,
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
        target_duration_sec: Target total video duration in seconds (default 240 = 4 min).
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

    # ── Calculate word/character budget based on language and target duration ──
    # CJK languages: ~200 characters/min. Others: ~140 words/min.
    cjk_languages = {"chinese", "japanese", "korean", "中文", "日语", "韩语",
                     "mandarin", "cantonese", "zh", "ja", "ko", "zh-cn", "zh-tw"}
    is_cjk = language.lower() in cjk_languages
    target_minutes = target_duration_sec / 60.0
    if is_cjk:
        total_budget = int(target_minutes * 200)   # characters
        per_scene_budget = int(total_budget / 6)    # ~6 scenes average
        budget_unit = "characters"
        budget_note = f"~{total_budget} characters total for all scenes"
    else:
        total_budget = int(target_minutes * 140)    # words
        per_scene_budget = int(total_budget / 6)
        budget_unit = "words"
        budget_note = f"~{total_budget} words total for all scenes"

    min_scenes = 5
    max_scenes = 7

    feedback_block = ""
    if feedback:
        feedback_block = f"""

    USER FEEDBACK FOR REVISION (CRITICAL — address every point):
    ---
    {feedback}
    ---
    """

    prompt = f"""
You are an award-winning Video Director, Writer, and Storyteller who specialises in
creating irresistible, emotionally compelling whiteboard animation videos.

Your job is to craft the ENTIRE video — the narrative arc, the script, and the visual
direction for every single scene. You are a STORYTELLER first, information organiser second.

User's Topic / Instructions:
"{user_instructions}"
{research_block}{feedback_block}

=== DURATION CONSTRAINT ===
Target: {target_duration_sec}s ({target_minutes:.0f} min). Exactly {min_scenes}-{max_scenes} scenes.
Budget: {budget_note}. Per scene: ~{per_scene_budget} {budget_unit} (35-50s spoken).
Count your {budget_unit} before outputting.

=== NARRATIVE ARC — MANDATORY ELEMENTS ===

HOOK (Scene 1, first 10s):
MUST open with a provocative question, shocking statistic, or vivid relatable scenario.
FORBIDDEN: "Today we're going to talk about..." / "今天我们来聊聊..." / "In this video..."
The hook must trigger curiosity OR emotional engagement within the first 2 sentences.

CURIOSITY GAPS (end of Scenes 1 through N-1):
Every scene except the last MUST end with an unanswered question, teaser, or mini-cliffhanger.
Examples: "But the real surprise was yet to come..." / "然而，没有人预料到..."

PAYOFF + CTA (Last scene):
Deliver the hook's promise. One clear takeaway. Natural call-to-action from the story.

EMOTIONAL ARC:
Distinct emotional_beat per scene, NEVER repeat consecutively. Progression:
curiosity → surprise/concern → tension → revelation → understanding → satisfaction/empowerment
Valid values: curiosity, surprise, concern, tension, revelation, excitement, relief,
satisfaction, empowerment, awe, urgency, hope.

AUDIENCE-CENTRIC LANGUAGE:
Write to ONE person. Use "you"/"你". Every scene: "Why should I care about this NOW?"
Short sentences. Active voice. Connect abstract concepts to the viewer's life.

CHARACTER-DRIVEN STORYTELLING:
Include at least ONE relatable character who represents the viewer. They must:
- Face the problem → struggle with it → discover/benefit from the solution
Track their emotional journey. Tell information through THEIR story, not a lecture.
The character can be: historical figure, hypothetical person, "someone like you", every-person archetype.

=== PER-SCENE FIELDS ===
- scene_number: 1 to {max_scenes}
- summary: 1-line purpose in the narrative arc
- narration: FULL spoken script — THE MOST IMPORTANT FIELD
- description: What to DRAW (subject, action, composition)
- visual_setup: Composition, key elements, focal points, visual strategy for this scene type
- visual_strategy: "hook"|"problem"|"explanation"|"data"|"turning_point"|"resolution"|"cta"
{veo_instruction}
- search_query (optional): Real-world person/landmark/subject reference
- text_overlay (REQUIRED): 1-2 key phrases/numbers as handwritten overlay.
  Hook scene → provocative question/number. Data scene → key statistic. Payoff → takeaway.
- key_information: The ONE most critical fact/insight
- emotional_beat: One valid value from above

=== VISUAL STRATEGY GUIDE ===
hook → bold provocative image, dynamic composition, strongest color accent
problem → character frustration/confusion, muted tones, visual tension
explanation → clear diagram/flowchart/comparison/analogy, structured layout
data → large numbers, trend arrows, before/after comparison
turning_point → dramatic contrast (before vs after), warm color breakthrough
resolution → harmonious balanced composition, character at peace/empowered
cta → single focal point, clean confident, one clear action symbol

=== CRITICAL RULES ===
- LANGUAGE: All values in {language}. JSON keys in English.
- DURATION: Within {total_budget} {budget_unit} budget. Trim ruthlessly.
- SCENES: Exactly {min_scenes}-{max_scenes}. No more, no less.
- HOOK: Scene 1 grabs attention in first 2 sentences.
- CURIOSITY GAPS: Every scene except last ends with one.
- TEXT OVERLAY: Every scene has non-empty text_overlay.
- EMOTIONAL BEAT: Every scene distinct. No consecutive repeats.

Output strict JSON. All string values in {language}, JSON keys in English:
{{{{
  "global_plan": {{{{
    "title": "...",
    "tone": "dramatic"|"suspenseful"|"playful"|"educational"|"urgent"|"awe-inspiring",
    "narrative_persona": "e.g. Wise Insider Who Was There",
    "visual_style": "e.g. Clean Whiteboard Animation with selective color accents",
    "pacing": "e.g. fast hook → steady build → climactic reveal → reflective close",
    "narrative_arc": "Describe: hook → tension → climax → payoff",
    "target_audience": "e.g. Curious professionals who feel overwhelmed by...",
    "total_scenes": {max_scenes}
  }}}},
  "scenes": [
    {{{{
      "scene_number": 1,
      "summary": "...",
      "narration": "...",
      "description": "...",
      "visual_setup": "...",
      "visual_strategy": "hook",{veo_schema_field}
      "search_query": "...",
      "text_overlay": "KEY PHRASE OR NUMBER",
      "key_information": "...",
      "emotional_beat": "curiosity"
    }}}},
    ...
  ]
}}}}
"""

    max_retries = 2
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            from ai_gateway import generate

            _emit(logger, "info",
                  f"Calling Director LLM (attempt {attempt}/{max_retries})...",
                  extra={"language": language, "research_provided": bool(research_material and research_material != user_instructions)})

            response = generate(
                task="story",
                prompt=prompt,
                options={"response_format": "json", "max_tokens": 16384, "temperature": 0.7},
            )
            raw_content = response.content

            # --- Multi-layer JSON parse with progressive repair ---
            result = None
            parse_errors = []

            # Layer 1: direct parse
            try:
                result = json.loads(raw_content)
            except json.JSONDecodeError as e1:
                parse_errors.append(f"direct: {e1}")

            # Layer 2: sanitize control chars + parse
            if result is None:
                try:
                    sanitized = _sanitize_json_control_chars(raw_content)
                    result = json.loads(sanitized)
                except json.JSONDecodeError as e2:
                    parse_errors.append(f"sanitized: {e2}")

            # Layer 3: repair truncated JSON + parse
            if result is None:
                try:
                    repaired = _repair_truncated_json(raw_content)
                    result = json.loads(repaired)
                    _emit(logger, "info", "Director JSON repaired successfully after truncation.")
                except json.JSONDecodeError as e3:
                    parse_errors.append(f"repaired: {e3}")

            # Layer 4: sanitize + repair + parse
            if result is None:
                try:
                    sanitized = _sanitize_json_control_chars(raw_content)
                    repaired = _repair_truncated_json(sanitized)
                    result = json.loads(repaired)
                    _emit(logger, "info", "Director JSON recovered after sanitize+repair.")
                except json.JSONDecodeError as e4:
                    parse_errors.append(f"sanitize+repair: {e4}")

            if result is not None:
                scene_count = len(result.get("scenes", []))
                _save_to_run_folder(json.dumps(result, indent=2), "video_plan.json")
                _emit(logger, "info", f"Director planned {scene_count} scenes",
                      extra={"scene_count": scene_count, "tone": result.get("global_plan", {}).get("tone"),
                             "attempt": attempt})
                return result

            # All parse layers failed — log and retry
            last_error = f"JSON parse failed after {len(parse_errors)} attempts: {'; '.join(parse_errors)}"
            _emit(logger, "warning", f"Director attempt {attempt}: {last_error}",
                  extra={"raw_preview": raw_content[:200]})

        except Exception as e:
            last_error = str(e)
            _emit(logger, "warning", f"Director attempt {attempt} failed: {e}",
                  extra={"error": str(e)})

    # All retries exhausted — abort, do NOT silently fallback
    raise RuntimeError(
        f"Director failed after {max_retries} attempts. "
        f"All JSON parse layers exhausted. Last error: {last_error}"
    )
