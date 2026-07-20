from typing import Optional

from .utils import _save_to_run_folder, _emit

try:
    from log_utils import ContextLogger
except ImportError:
    ContextLogger = None  # type: ignore


def _get_visual_strategy_guidance(strategy: str, emotional_beat: str, tone: str) -> dict:
    """Return color and composition guidance based on the scene's visual strategy."""
    strategies = {
        "hook": {
            "color": "Use the STRONGEST, boldest color accent in the entire video — fiery red, electric blue, or vivid orange on the focal point. This scene must POP visually.",
            "composition": "Dynamic, unexpected composition. Diagonal movement. The focal subject should be large, close, and impossible to ignore. Use unusual perspective — extreme close-up or dramatic angle.",
        },
        "problem": {
            "color": "Use muted, cool tones (grey-blue, desaturated). The color accent should feel subdued — the world is a bit drained. Only 1 small accent of color, like a tiny warning red.",
            "composition": "Character-centric composition showing struggle. Visual tension — elements leaning, unbalanced. Use negative space to convey isolation or overwhelm.",
        },
        "explanation": {
            "color": "Clean, bright accent on the KEY concept element only. Use a calming blue or green. The accent color should GUIDE the eye through the information flow.",
            "composition": "Clear, structured layout. Flow from left to right or top to bottom. Use arrows, connectors, flow lines. Diagrams should be immediately readable.",
        },
        "data": {
            "color": "Bold accent on the KEY NUMBER. Use red, orange, or gold for the statistic. Everything else black. The number should be the largest, most colorful element.",
            "composition": "The key number/data point DOMINATES the frame. Use large-scale text. Supporting data smaller below. Strong before/after or comparison layout.",
        },
        "turning_point": {
            "color": "Dramatic shift in color — left side muted/cool, right side warm/bright. OR: a single breakthrough beam of golden/yellow light cutting through.",
            "composition": "Split composition: 'Before' on one side, 'After' on the other. OR: a central breakthrough moment — walls crumbling, doors opening, light bursting through.",
        },
        "resolution": {
            "color": "Warm, satisfying colors — golden, soft green, warm amber. The most harmonious color palette in the video. Feels like sunrise or home.",
            "composition": "Balanced, harmonious composition. The character at peace. Open space, wide framing. Everything feels resolved, in its right place.",
        },
        "cta": {
            "color": "One bold, confident accent — brand-appropriate color. Clean and unambiguous. No competing colors.",
            "composition": "Single, uncluttered focal point. Maximum negative space. One clear action symbol (arrow, button, star, checkmark). Nothing else competes.",
        },
    }

    default_strategy = {
        "color": "1-2 KEY objects or focal areas should have VIBRANT selective color",
        "composition": "Engaging and clear composition that supports the narration.",
    }

    guidance = strategies.get(strategy, default_strategy)

    # Augment with emotional beat nuance
    if emotional_beat in ("tension", "concern", "urgency"):
        guidance["color"] += " Slightly more dramatic — push the contrast."
    elif emotional_beat in ("relief", "satisfaction", "hope", "empowerment"):
        guidance["color"] += " Warmer, brighter accent tones."
    elif emotional_beat in ("curiosity", "surprise", "revelation", "awe"):
        guidance["color"] += " More vivid, eye-catching accent."

    return guidance


def _get_visual_metaphor_guide() -> str:
    """Return visual metaphor mapping for abstract concepts."""
    return """
- Growth / Progress → growing plant with visible roots, rising sun on horizon,
  climbing stairs, expanding network of connected dots
- Complexity / Confusion → tangled knot, maze from above, fog bank,
  scattered puzzle pieces on a table
- Solution / Clarity → untied smooth rope, glowing light bulb, straight open path,
  completed puzzle with one piece clicking in
- Data / Information → flowing river with tributaries, connected constellation nodes,
  stacked building blocks forming a structure
- Security / Protection → shield with inner glow, strong wall with arch,
  closed umbrella in rain, sturdy lock
- Speed / Efficiency → streamlined arrow in flight, cheetah mid-stride,
  racing line through curves, stopwatch with wings
- Teamwork / Collaboration → interlocking gears, rowing team in sync,
  bridge being built from both sides meeting in the middle
- Innovation / Breakthrough → egg cracking with light spilling out,
  seedling breaking through concrete, match striking in darkness
- Time / History → flowing hourglass, winding path through landscape,
  tree rings expanding outward, clock face with sweeping hand
- Money / Value → growing coin stack, scale tipping favorably,
  seed turning into coin-bearing tree
- Competition / Conflict → chess pieces facing off, two arrows converging,
  mountain peaks side by side with one slightly higher
USE THESE METAPHORS whenever the scene concept is abstract. Pick the closest
match and adapt it to the whiteboard line-drawing style.
"""


def _get_character_visual_guidance(emotional_beat: str) -> str:
    """Return character-focused visual guidance based on emotional beat."""
    if not emotional_beat:
        return ""

    char_states = {
        "curiosity": "Draw a simple relatable figure leaning forward, eyes wide, one hand on chin — the universal 'I need to know more' pose.",
        "surprise": "Draw a figure with hands slightly raised, eyebrows up, mouth slightly open — genuine surprise, not cartoonish shock.",
        "concern": "Draw a figure with furrowed brows, arms crossed or one hand on forehead — the universal 'this is a problem' stance.",
        "tension": "Draw a figure leaning back, posture rigid, hands gripping something — the body language of suspense.",
        "revelation": "Draw a figure with one hand raised, palm open, eyes looking upward — the 'aha!' moment captured in posture.",
        "excitement": "Draw a figure with arms spread wide or fist pump — the moment of breakthrough, celebration in body language.",
        "relief": "Draw a figure with shoulders dropped, gentle exhale visible, hand on chest — visible release of tension.",
        "satisfaction": "Draw a figure with gentle smile, relaxed posture, nodding slightly — quiet contentment, not over-the-top.",
        "empowerment": "Draw a figure standing tall, chin up, shoulders back, feet planted firmly — confident and ready.",
        "awe": "Draw a small figure looking up at something vast and impressive — scale contrast to show wonder.",
        "urgency": "Draw a figure mid-stride, leaning forward, one arm pumping — purposeful motion, time matters.",
        "hope": "Draw a figure facing forward, one hand reaching slightly out, face tilted up with a subtle smile — looking toward a better future.",
    }

    guidance = char_states.get(emotional_beat, "")
    if guidance:
        return f"""CHARACTER VISUAL GUIDANCE:
The emotional beat of this scene is "{emotional_beat}".
{guidance}
Keep the character simple — stick-figure or minimalist cartoon style with clear
body language. The whiteboard sketch aesthetic must be preserved. No realistic faces.
"""
    return ""


def prompt_tool_fn(
    scene_description: str,
    visual_setup: str = "",
    text_overlay: str = "",
    global_plan: dict = None,
    emotional_beat: str = "",
    visual_strategy: str = "",
    logger: Optional["ContextLogger"] = None,
) -> str:
    """
    Generates an image prompt for a whiteboard animation frame using Nano Banana guidelines.

    Formula: [Subject] + [Action] + [Location/context] + [Composition] + [Style]

    Args:
        scene_description: Visual description of the scene.
        visual_setup: Specific instructions for this frame (from the Director).
        text_overlay: Specific impact text to render into the frame.
        global_plan: The global plan dictionary (from the Director).
        emotional_beat: The emotional tone of this scene (e.g., curiosity, tension, revelation).
        visual_strategy: Scene type strategy — "hook", "problem", "explanation", "data",
            "turning_point", "resolution", or "cta".
        logger: Optional ContextLogger for structured logging.
    Returns:
        The image generation prompt string.
    """
    tone = global_plan.get("tone", "dramatic") if global_plan else "dramatic"
    visual_style = global_plan.get("visual_style", "Clean Whiteboard Animation") if global_plan else "Clean Whiteboard Animation"

    # ── VISUAL STRATEGY guidance (B2) ──
    strategy_guidance = _get_visual_strategy_guidance(visual_strategy, emotional_beat, tone)

    # ── VISUAL METAPHOR guide (B4) ──
    metaphor_guidance = _get_visual_metaphor_guide()

    # ── CHARACTER scene handling (B3) ──
    character_guidance = _get_character_visual_guidance(emotional_beat)

    text_guidance = ""
    if text_overlay:
        text_guidance = f"""
TEXT OVERLAY HANDLING (CRITICAL!):
The scene requires this key text to be rendered on the whiteboard: "{text_overlay}"
Draw this text LARGE and BOLD as handwritten marker text directly on the whiteboard.
It should be the most prominent text element — as if the instructor wrote it and
then underlined it twice. Position it centrally or in the upper-third for maximum impact.
"""

    prompt = f"""
You are an expert whiteboard animation artist and creative director.

Your job: Create an image generation prompt using the Nano Banana optimal formula:
[Subject] + [Action] + [Location/context] + [Composition] + [Style]

WHAT WHITEBOARD ANIMATION LOOKS LIKE:
- Clean WHITE background (like a dry-erase whiteboard)
- Simple, quick LINE DRAWINGS using black lines (just the pure ink on the board)
- Hand-drawn aesthetic — not photorealistic, not heavily detailed
- NO shading, NO gradients — flat simple strokes only

NEGATIVE PROMPT / STRICT FORBIDDEN:
- DO NOT under any circumstances draw hands, human arms, markers, pens, or any person physically drawing the final picture!
- Erase any concept of the artist from the scene.
- DO NOT draw any picture frames, borders, decorative edges, wooden/metal frames, or boundaries around the artwork. The image must have NO frame of any kind — the artwork should fill the entire canvas edge-to-edge without any enclosing border.
- Provide ONLY the final completed artwork standing alone upon the white background.

COLOR ENHANCEMENT RULE (varies by scene strategy):
- The drawing is primarily BLACK lines on WHITE background
- {strategy_guidance["color"]}
- Everything else stays black-and-white line art

{strategy_guidance["composition"]}

{character_guidance}

VISUAL METAPHOR GUIDE — USE THESE FOR ABSTRACT CONCEPTS:
{metaphor_guidance}

SCENE DETAILS:
- Subject / Description: "{scene_description}"
- Action / Setup: "{visual_setup}"
- Global Tone: {tone}
- Emotional Beat: {emotional_beat or 'neutral'}
- Visual Strategy: {visual_strategy or 'explanation'}
{text_guidance}

CONSTRUCT the final image generation prompt starting directly with [Subject] and following the formula implicitly. Ensure the Style section strictly describes the whiteboard marker, flat strokes, selective color, and clean white background without blending styles or becoming overly complex. Make sure if text is included, to wrap it in exact double quotes "like this" and specify the font.

Output: ONLY the final prompt string. No explanations, no markdown blocks.
"""

    try:
        from ai_gateway import generate

        _emit(logger, "debug", "Generating image prompt via LLM...",
              extra={"scene_description": scene_description[:150], "tone": tone})

        response = generate(
            task="story",
            prompt=prompt,
            options={"temperature": 0.8},
        )
        result = response.content.strip()
        result = result.replace('\"', '"').replace('`', '').strip()

        _save_to_run_folder(f"Scene: {scene_description}\nText Overlay: {text_overlay}\nPrompt: {result}\n---\n", "prompts_log.txt", mode="a")
        _emit(logger, "info", "Image prompt generated",
              extra={"prompt_length": len(result)})
        return result
    except Exception as e:
        _emit(logger, "error", f"Image prompt generation failed: {e}", extra={"error": str(e)})
        return f"Error prompt: {e}"
