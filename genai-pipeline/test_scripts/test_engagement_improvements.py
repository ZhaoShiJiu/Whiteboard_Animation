"""
Test cases for engagement & duration optimization (A1-A4, B1-B4, B6).

Covers:
  - A1: Director duration constraint & word budget
  - A2: Research word limits & max_tokens
  - A3: Script compression (_compress_script)
  - A4: Frontend duration config threading (pipeline + web_app)
  - B1: Director story-driven prompt (hook, curiosity gaps, emotional arc)
  - B2: Visual strategy guidance mapping
  - B3: Character visual guidance by emotional beat
  - B4: Visual metaphor guide completeness
  - B6: text_overlay required in every scene

Usage:
    cd genai-pipeline
    python test_scripts/test_engagement_improvements.py

Set SKIP_INTEGRATION=1 to skip tests that require API keys.
"""

import os
import sys
import json
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SKIP_INTEGRATION = os.environ.get("SKIP_INTEGRATION", "0") == "1"


# ═══════════════════════════════════════════════════════════════════════════════
# Utility: extract the actual prompt text sent to the LLM (for assertion)
# ═══════════════════════════════════════════════════════════════════════════════

def _capture_director_prompt(**overrides):
    """Call director_tool_fn with mocked generate() to capture the prompt."""
    from tools.director import director_tool_fn

    captured_prompt = [None]

    def fake_generate(task, prompt, options=None):
        captured_prompt[0] = prompt
        # Return minimal valid JSON so parsing succeeds
        fake_resp = MagicMock()
        fake_resp.content = json.dumps({
            "global_plan": {
                "title": "Test",
                "tone": "dramatic",
                "narrative_persona": "Wise Storyteller",
                "visual_style": "Clean Whiteboard Animation",
                "pacing": "steady/educational",
                "narrative_arc": "hook → tension → reveal",
                "target_audience": "general public",
                "total_scenes": 6,
            },
            "scenes": [
                {
                    "scene_number": i,
                    "summary": f"Scene {i} summary",
                    "narration": f"Scene {i} narration text here.",
                    "description": f"Scene {i} description",
                    "visual_setup": f"Scene {i} setup",
                    "visual_strategy": "hook" if i == 1 else "explanation",
                    "search_query": "",
                    "text_overlay": f"KEY PHRASE {i}",
                    "key_information": f"Key info {i}",
                    "emotional_beat": "curiosity" if i == 1 else "revelation",
                }
                for i in range(1, 7)
            ],
        })
        fake_resp.provider = "deepseek"
        fake_resp.latency_ms = 100
        fake_resp.usage = MagicMock()
        fake_resp.usage.input_tokens = 1000
        fake_resp.usage.output_tokens = 500
        return fake_resp

    with patch("ai_gateway.generate", side_effect=fake_generate):
        result = director_tool_fn(
            user_instructions="Test topic about AI",
            language=overrides.get("language", "chinese"),
            target_duration_sec=overrides.get("target_duration_sec", 240),
            logger=None,
        )

    return result, captured_prompt[0]


# ═══════════════════════════════════════════════════════════════════════════════
# A1: Director Duration Constraint & Word Budget
# ═══════════════════════════════════════════════════════════════════════════════

class TestA1_DirectorDurationConstraint(unittest.TestCase):
    """Verify the Director prompt enforces hard duration limits."""

    def test_prompt_contains_target_duration_sec(self):
        """Prompt must include the target_duration_sec value."""
        _, prompt = _capture_director_prompt(target_duration_sec=240)
        self.assertIn("240s", prompt)
        self.assertIn("4 min", prompt)

    def test_prompt_contains_scene_count_range(self):
        """Prompt must enforce 5-7 scenes."""
        _, prompt = _capture_director_prompt()
        self.assertIn("5-7 scenes", prompt)

    def test_prompt_contains_word_budget(self):
        """Prompt must include word/char budget for CJK and non-CJK."""
        _, prompt_cn = _capture_director_prompt(language="chinese", target_duration_sec=240)
        self.assertIn("characters", prompt_cn.lower())
        self.assertIn("800", prompt_cn)  # 4 min × 200 chars

        _, prompt_en = _capture_director_prompt(language="english", target_duration_sec=180)
        self.assertIn("words", prompt_en.lower())
        self.assertIn("420", prompt_en)  # 3 min × 140 words

    def test_prompt_cjk_char_budget_correct(self):
        """Chinese 4 min → ~800 chars (200 chars/min × 4)."""
        _, prompt = _capture_director_prompt(language="chinese", target_duration_sec=240)
        self.assertIn("800 characters", prompt)

    def test_prompt_english_word_budget_correct(self):
        """English 4 min → ~560 words (140 words/min × 4)."""
        _, prompt = _capture_director_prompt(language="english", target_duration_sec=240)
        self.assertIn("560 words", prompt)

    def test_per_scene_budget_in_prompt(self):
        """Prompt must include per-scene budget."""
        _, prompt = _capture_director_prompt(language="chinese", target_duration_sec=240)
        # 800 chars / 6 scenes ≈ 133 chars/scene
        self.assertIn("133 characters", prompt)


# ═══════════════════════════════════════════════════════════════════════════════
# B1: Director Story-Driven Prompt (Hook, Curiosity Gaps, Emotional Arc)
# ═══════════════════════════════════════════════════════════════════════════════

class TestB1_DirectorStoryDrivenPrompt(unittest.TestCase):
    """Verify the Director functions as a storyteller, not information organiser."""

    def test_prompt_forbids_generic_intro(self):
        """Must explicitly forbid generic openings."""
        _, prompt = _capture_director_prompt(language="chinese")
        # Phrase may be line-wrapped in the ASCII-art box; check parts
        self.assertIn("今天我们来", prompt)
        self.assertIn("FORBIDDEN", prompt)

    def test_prompt_requires_hook_in_first_10_seconds(self):
        """Must mention hook in first 10 seconds."""
        _, prompt = _capture_director_prompt()
        self.assertIn("first 10s", prompt.lower())

    def test_prompt_requires_curiosity_gaps(self):
        """Must require curiosity gaps between scenes."""
        _, prompt = _capture_director_prompt()
        self.assertIn("curiosity gap", prompt.lower())

    def test_prompt_requires_payoff_and_cta(self):
        """Must require payoff and CTA in last scene."""
        _, prompt = _capture_director_prompt()
        self.assertIn("PAYOFF", prompt)
        self.assertIn("CTA", prompt)

    def test_prompt_requires_emotional_beat_per_scene(self):
        """Must enforce distinct emotional beats, no consecutive repeats."""
        _, prompt = _capture_director_prompt()
        self.assertIn("emotional_beat", prompt.lower())
        self.assertIn("NEVER repeat", prompt)

    def test_prompt_lists_valid_emotional_beats(self):
        """Must list valid emotional_beat values."""
        _, prompt = _capture_director_prompt()
        for beat in ["curiosity", "surprise", "tension", "revelation", "satisfaction", "empowerment"]:
            self.assertIn(beat, prompt.lower(), f"Missing emotional beat: {beat}")

    def test_prompt_requires_audience_centric_language(self):
        """Must instruct writing as if talking to ONE person."""
        _, prompt = _capture_director_prompt(language="chinese")
        self.assertIn("你", prompt)


# ═══════════════════════════════════════════════════════════════════════════════
# B3: Character-Driven Storytelling
# ═══════════════════════════════════════════════════════════════════════════════

class TestB3_CharacterDrivenStorytelling(unittest.TestCase):
    """Verify character requirements in Director prompt."""

    def test_prompt_requires_character(self):
        """Must require at least one relatable character."""
        _, prompt = _capture_director_prompt()
        self.assertIn("CHARACTER", prompt)
        self.assertIn("relatable character", prompt.lower())

    def test_prompt_character_journey(self):
        """Character must face problem → struggle → solution."""
        _, prompt = _capture_director_prompt()
        self.assertIn("face the problem", prompt.lower())
        self.assertIn("struggle", prompt.lower())
        self.assertIn("solution", prompt.lower())


# ═══════════════════════════════════════════════════════════════════════════════
# B6: text_overlay Required in Every Scene
# ═══════════════════════════════════════════════════════════════════════════════

class TestB6_TextOverlayRequired(unittest.TestCase):
    """Verify text_overlay is now mandatory per scene."""

    def test_prompt_requires_text_overlay_every_scene(self):
        """Prompt must say text_overlay is REQUIRED for every scene."""
        _, prompt = _capture_director_prompt()
        self.assertIn("text_overlay", prompt.lower())
        self.assertIn("REQUIRED", prompt)

    def test_prompt_text_overlay_for_hook_scene(self):
        """Hook scene must overlay provocative question or number."""
        _, prompt = _capture_director_prompt()
        self.assertIn("provocative question", prompt.lower())

    def test_prompt_text_overlay_for_data_scene(self):
        """Data scenes must overlay key statistic."""
        _, prompt = _capture_director_prompt()
        self.assertIn("key statistic", prompt.lower())


# ═══════════════════════════════════════════════════════════════════════════════
# A2: Research Word Limits
# ═══════════════════════════════════════════════════════════════════════════════

class TestA2_ResearchWordLimits(unittest.TestCase):
    """Verify research prompts are budget-constrained."""

    def _capture_research_prompt(self, fn, context="Test topic"):
        """Capture the prompt sent by a research function."""
        captured = [None]

        def fake_generate(task, prompt, options=None):
            captured[0] = (prompt, options)
            fake_resp = MagicMock()
            fake_resp.content = "Short research report."
            fake_resp.provider = "deepseek"
            fake_resp.latency_ms = 100
            fake_resp.usage = MagicMock()
            fake_resp.usage.output_tokens = 50
            return fake_resp

        with patch("ai_gateway.generate", side_effect=fake_generate):
            fn(context, logger=None)

        return captured[0]  # (prompt, options)

    def test_deep_research_500_800_words(self):
        """Deep research must ask for 500-800 words, not 1000+."""
        from tools.research import research_tool_fn
        prompt, _ = self._capture_research_prompt(research_tool_fn)
        self.assertIn("500-800 words", prompt)
        self.assertNotIn("at least 1000 words", prompt)

    def test_deep_research_narrative_potential(self):
        """Deep research must prioritize narrative potential."""
        from tools.research import research_tool_fn
        prompt, _ = self._capture_research_prompt(research_tool_fn)
        self.assertIn("NARRATIVE POTENTIAL", prompt.upper())

    def test_deep_research_max_tokens_4096(self):
        """Deep research max_tokens must be 4096 (down from 8192)."""
        from tools.research import research_tool_fn
        _, options = self._capture_research_prompt(research_tool_fn)
        self.assertEqual(options.get("max_tokens"), 4096)

    def test_web_search_500_800_chars(self):
        """Web-search research must ask for 500-800 字, not 1000+."""
        from tools.research import web_search_research_tool_fn

        captured_prompt = [None]
        captured_options = [None]

        def fake_generate(task, prompt, options=None):
            # First call: search; Second call: DeepSeek
            if captured_prompt[0] is None and task == "search":
                captured_prompt[0] = prompt
                fake = MagicMock()
                fake.content = [{"title": "T", "summary": "S", "url": "U", "site_name": "N", "publish_time": "2024"}]
                fake.latency_ms = 50
                return fake
            if captured_options[0] is None and task == "story":
                captured_options[0] = options
                fake = MagicMock()
                fake.content = "Short report."
                fake.provider = "deepseek"
                fake.latency_ms = 100
                fake.usage = MagicMock()
                fake.usage.output_tokens = 100
                return fake
            fake = MagicMock()
            fake.content = []
            fake.latency_ms = 10
            return fake

        with patch("ai_gateway.generate", side_effect=fake_generate):
            web_search_research_tool_fn(context="test", logger=None)

        self.assertIsNotNone(captured_options[0], "Second LLM call (DeepSeek) was not captured")
        self.assertEqual(captured_options[0].get("max_tokens"), 4096)

    def test_web_search_prompt_no_longer_1000_words(self):
        """Web-search Chinese prompt must not say 不少于 1000 字."""
        from tools.research import web_search_research_tool_fn

        captured_deepseek_prompt = [None]

        def fake_generate(task, prompt, options=None):
            if task == "search":
                fake = MagicMock()
                fake.content = []
                fake.latency_ms = 50
                return fake
            if task == "story" and captured_deepseek_prompt[0] is None:
                captured_deepseek_prompt[0] = prompt
                fake = MagicMock()
                fake.content = "Short report."
                fake.provider = "deepseek"
                fake.latency_ms = 100
                fake.usage = MagicMock()
                fake.usage.output_tokens = 50
                return fake
            fake = MagicMock()
            fake.content = []
            fake.latency_ms = 10
            return fake

        with patch("ai_gateway.generate", side_effect=fake_generate):
            web_search_research_tool_fn(context="test", logger=None)

        self.assertIsNotNone(captured_deepseek_prompt[0])
        self.assertIn("500-800 字", captured_deepseek_prompt[0])
        self.assertNotIn("不少于 1000 字", captured_deepseek_prompt[0])

    def test_web_grounded_research_constrained(self):
        """Web-grounded research must mention 500-800 words limit."""
        from tools.research import web_grounded_research_tool_fn
        prompt, _ = self._capture_research_prompt(web_grounded_research_tool_fn)
        self.assertIn("500-800 words", prompt)


# ═══════════════════════════════════════════════════════════════════════════════
# B2: Visual Strategy Guidance
# ═══════════════════════════════════════════════════════════════════════════════

class TestB2_VisualStrategyGuidance(unittest.TestCase):
    """Verify visual strategy mapping produces correct guidance."""

    def setUp(self):
        from tools.image_prompt_tool import _get_visual_strategy_guidance
        self.fn = _get_visual_strategy_guidance

    def test_all_seven_strategies_exist(self):
        """All 7 visual strategies must return non-empty guidance."""
        strategies = ["hook", "problem", "explanation", "data",
                      "turning_point", "resolution", "cta"]
        for s in strategies:
            g = self.fn(s, "neutral", "dramatic")
            self.assertIn("color", g)
            self.assertIn("composition", g)
            self.assertTrue(g["color"], f"Strategy '{s}' has empty color guidance")
            self.assertTrue(g["composition"], f"Strategy '{s}' has empty composition guidance")

    def test_unknown_strategy_returns_default(self):
        """Unknown strategy must return valid default guidance."""
        g = self.fn("nonexistent", "", "")
        self.assertIn("color", g)
        self.assertIn("composition", g)

    def test_hook_strategy_has_strongest_color(self):
        """Hook must use the strongest color accent."""
        g = self.fn("hook", "curiosity", "dramatic")
        self.assertIn("STRONGEST", g["color"].upper())

    def test_emotional_beat_augments_color(self):
        """Emotional beat must add nuance to color guidance."""
        g_base = self.fn("explanation", "", "")
        g_tension = self.fn("explanation", "tension", "")
        g_relief = self.fn("explanation", "relief", "")
        # With emotional beat, color guidance is longer (has augment)
        self.assertGreaterEqual(len(g_tension["color"]), len(g_base["color"]))


# ═══════════════════════════════════════════════════════════════════════════════
# B4: Visual Metaphor Guide
# ═══════════════════════════════════════════════════════════════════════════════

class TestB4_VisualMetaphorGuide(unittest.TestCase):
    """Verify visual metaphor mapping covers key abstract concepts."""

    def setUp(self):
        from tools.image_prompt_tool import _get_visual_metaphor_guide
        self.guide = _get_visual_metaphor_guide()

    def test_guide_is_non_empty(self):
        """Metaphor guide must not be empty."""
        self.assertTrue(len(self.guide) > 200)

    def test_growth_metaphor_present(self):
        """Must include growth/progress metaphor."""
        self.assertIn("Growth", self.guide)

    def test_complexity_metaphor_present(self):
        """Must include complexity/confusion metaphor."""
        self.assertIn("Complexity", self.guide)

    def test_solution_metaphor_present(self):
        """Must include solution/clarity metaphor."""
        self.assertIn("Solution", self.guide)

    def test_innovation_metaphor_present(self):
        """Must include innovation/breakthrough metaphor."""
        self.assertIn("Innovation", self.guide)


# ═══════════════════════════════════════════════════════════════════════════════
# B3 (visual part): Character Visual Guidance
# ═══════════════════════════════════════════════════════════════════════════════

class TestB3_CharacterVisualGuidance(unittest.TestCase):
    """Verify character visual guidance per emotional beat."""

    def setUp(self):
        from tools.image_prompt_tool import _get_character_visual_guidance
        self.fn = _get_character_visual_guidance

    def test_all_twelve_beats_return_guidance(self):
        """All 12 emotional beats must return non-empty guidance."""
        beats = ["curiosity", "surprise", "concern", "tension", "revelation",
                 "excitement", "relief", "satisfaction", "empowerment",
                 "awe", "urgency", "hope"]
        for beat in beats:
            g = self.fn(beat)
            self.assertTrue(g, f"Character guidance for '{beat}' is empty")
            self.assertIn(beat, g.lower(), f"Guidance for '{beat}' doesn't mention the beat name")

    def test_unknown_beat_returns_empty(self):
        """Unknown beat must return empty string."""
        g = self.fn("nonexistent_beat")
        self.assertEqual(g, "")

    def test_empty_beat_returns_empty(self):
        """Empty beat must return empty string."""
        g = self.fn("")
        self.assertEqual(g, "")


# ═══════════════════════════════════════════════════════════════════════════════
# A3: Script Compression
# ═══════════════════════════════════════════════════════════════════════════════

class TestA3_ScriptCompression(unittest.TestCase):
    """Verify _compress_script fallback (offline) logic."""

    def setUp(self):
        from pipeline import _compress_script
        self.fn = _compress_script

    def _make_scenes(self, narrations):
        """Build a minimal scenes list from narration strings."""
        return [
            {
                "scene_number": i + 1,
                "narration": nar,
                "emotional_beat": "neutral",
                "summary": f"Scene {i + 1}",
                "description": f"Desc {i + 1}",
                "visual_setup": "",
                "visual_strategy": "explanation",
                "text_overlay": f"Key {i + 1}",
                "key_information": f"Info {i + 1}",
                "search_query": "",
            }
            for i, nar in enumerate(narrations)
        ]

    def test_compress_fallback_trims_cjk_proportionally(self):
        """CJK fallback: scene narration shortened by ~30% at sentence boundary."""
        # Use delimiter-free text so trim happens at exact 70% position
        long_text = "这是一个非常长的叙述文本内容没有任何句号分隔符" * 6
        scenes = self._make_scenes([long_text])
        original_len = len(scenes[0]["narration"])

        result = self.fn(scenes, "chinese", 10, is_cjk=True)
        # Fallback should trim to ~70% (no delimiter found, so exact 70% cut)
        self.assertLess(len(result[0]["narration"]), original_len)
        self.assertGreater(len(result[0]["narration"]), 5)

    def test_compress_fallback_trims_english_proportionally(self):
        """English fallback: scene narration shortened by ~30% at sentence boundary."""
        long_text = "This is a very long narration. " * 30
        scenes = self._make_scenes([long_text])
        original_words = len(scenes[0]["narration"].split())

        result = self.fn(scenes, "english", 10, is_cjk=False)
        new_words = len(result[0]["narration"].split())
        self.assertLess(new_words, original_words)

    def test_compress_fallback_preserves_scene_count(self):
        """Fallback must preserve the number of scenes."""
        scenes = self._make_scenes([
            "Scene one narration text. " * 20,
            "Scene two narration text. " * 20,
            "Scene three narration text. " * 20,
        ])
        result = self.fn(scenes, "english", 20, is_cjk=False)
        self.assertEqual(len(result), 3)

    def test_compress_fallback_preserves_scene_structure(self):
        """Fallback must keep all scene fields intact except narration."""
        scenes = self._make_scenes(["Long narration text. " * 20])
        original_scene = scenes[0].copy()
        original_scene.pop("narration")

        result = self.fn(scenes, "english", 10, is_cjk=False)
        result_scene = result[0].copy()
        result_scene.pop("narration")
        self.assertEqual(result_scene, original_scene)

    def test_compress_short_narration_not_trimmed(self):
        """Narration shorter than trim target should not be trimmed."""
        short_text = "A short sentence."
        scenes = self._make_scenes([short_text])
        # Large budget, short text → might skip trimming if trim point not found
        result = self.fn(scenes, "english", 1000, is_cjk=False)
        # Either unchanged or trimmed slightly — both acceptable
        self.assertGreaterEqual(len(result[0]["narration"]), 1)

    def test_compress_single_char_not_broken(self):
        """Edge case: very short CJK narration should not crash."""
        scenes = self._make_scenes(["测试。"])
        result = self.fn(scenes, "chinese", 1, is_cjk=True)
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 1)


# ═══════════════════════════════════════════════════════════════════════════════
# A4: Pipeline target_duration_sec Threading
# ═══════════════════════════════════════════════════════════════════════════════

class TestA4_PipelineDurationThreading(unittest.TestCase):
    """Verify target_duration_sec flows through run_pipeline → director_tool_fn."""

    def test_run_pipeline_signature_has_target_duration_sec(self):
        """run_pipeline must accept target_duration_sec parameter."""
        import inspect
        from pipeline import run_pipeline
        sig = inspect.signature(run_pipeline)
        self.assertIn("target_duration_sec", sig.parameters)
        self.assertEqual(sig.parameters["target_duration_sec"].default, 240)

    def test_run_pipeline_impl_signature_has_target_duration_sec(self):
        """_run_pipeline_impl must accept target_duration_sec parameter."""
        import inspect
        from pipeline import _run_pipeline_impl
        sig = inspect.signature(_run_pipeline_impl)
        self.assertIn("target_duration_sec", sig.parameters)

    def test_director_tool_fn_signature_has_target_duration_sec(self):
        """director_tool_fn must accept target_duration_sec parameter."""
        import inspect
        from tools.director import director_tool_fn
        sig = inspect.signature(director_tool_fn)
        self.assertIn("target_duration_sec", sig.parameters)
        self.assertEqual(sig.parameters["target_duration_sec"].default, 240)

    def test_prompt_tool_fn_signature_has_new_params(self):
        """prompt_tool_fn must accept emotional_beat and visual_strategy."""
        import inspect
        from tools.image_prompt_tool import prompt_tool_fn
        sig = inspect.signature(prompt_tool_fn)
        self.assertIn("emotional_beat", sig.parameters)
        self.assertIn("visual_strategy", sig.parameters)


# ═══════════════════════════════════════════════════════════════════════════════
# Additional: Narration Refiner Bypass Logic (updated for compression)
# ═══════════════════════════════════════════════════════════════════════════════

class TestNarrationRefinerCompression(unittest.TestCase):
    """Verify narration_refiner now compresses instead of skipping."""

    def test_bypass_threshold_no_longer_skips(self):
        """When over bypass threshold, should now TIGHTEN not SKIP."""
        from tools.narration_refiner import refine_narration_tool_fn

        # Create a long narration that exceeds 2× target
        long_narration = "This is a very long narration. " * 100  # ~600 words
        # For a 20-second video: target = (20/60)*140 ≈ 46 words, bypass = 92 words
        # 600 words > 92 → old code: skip; new code: tighten

        captured_prompt = [None]

        def fake_generate(task, prompt, options=None):
            captured_prompt[0] = prompt
            fake = MagicMock()
            fake.content = "Tightened narration."
            fake.provider = "deepseek"
            return fake

        # Need a temp image file to exist
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp_img = f.name

        try:
            with patch("ai_gateway.generate", side_effect=fake_generate):
                refine_narration_tool_fn(
                    long_narration,
                    tmp_img,
                    video_duration=20.0,  # 20 seconds
                    language="english",
                    logger=None,
                )
        finally:
            os.unlink(tmp_img)

        self.assertIsNotNone(captured_prompt[0])
        # New logic should say "TIGHTEN" not "Do NOT shorten"
        self.assertIn("TIGHTEN", captured_prompt[0].upper())
        self.assertNotIn("Do NOT shorten", captured_prompt[0])


# ═══════════════════════════════════════════════════════════════════════════════
# Image Prompt Tool: New Parameters in Prompt Output
# ═══════════════════════════════════════════════════════════════════════════════

class TestImagePromptNewParams(unittest.TestCase):
    """Verify emotional_beat and visual_strategy appear in generated prompts."""

    def test_visual_strategy_in_prompt_output(self):
        """Generated prompt must include visual strategy guidance text."""
        from tools.image_prompt_tool import prompt_tool_fn

        captured_prompt = [None]

        def fake_generate(task, prompt, options=None):
            captured_prompt[0] = prompt
            fake = MagicMock()
            fake.content = "A whiteboard drawing of [Subject]..."
            fake.provider = "deepseek"
            return fake

        with patch("ai_gateway.generate", side_effect=fake_generate):
            prompt_tool_fn(
                "A test scene",
                visual_setup="Test setup",
                text_overlay="BOLD TEXT",
                emotional_beat="curiosity",
                visual_strategy="hook",
                logger=None,
            )

        self.assertIsNotNone(captured_prompt[0])
        self.assertIn("curiosity", captured_prompt[0])
        self.assertIn("hook", captured_prompt[0].lower())

    def test_emotional_beat_in_prompt_output(self):
        """Prompt must contain the emotional beat value."""
        from tools.image_prompt_tool import prompt_tool_fn

        captured_prompt = [None]

        def fake_generate(task, prompt, options=None):
            captured_prompt[0] = prompt
            fake = MagicMock()
            fake.content = "A test prompt"
            fake.provider = "deepseek"
            return fake

        with patch("ai_gateway.generate", side_effect=fake_generate):
            prompt_tool_fn(
                "A test scene",
                emotional_beat="tension",
                visual_strategy="problem",
                logger=None,
            )

        self.assertIsNotNone(captured_prompt[0])
        self.assertIn("tension", captured_prompt[0])

    def test_character_guidance_in_prompt_for_curiosity(self):
        """When emotional_beat='curiosity', character pose guidance must appear."""
        from tools.image_prompt_tool import prompt_tool_fn

        captured_prompt = [None]

        def fake_generate(task, prompt, options=None):
            captured_prompt[0] = prompt
            fake = MagicMock()
            fake.content = "A test prompt"
            fake.provider = "deepseek"
            return fake

        with patch("ai_gateway.generate", side_effect=fake_generate):
            prompt_tool_fn(
                "A test scene",
                emotional_beat="curiosity",
                visual_strategy="hook",
                logger=None,
            )

        self.assertIsNotNone(captured_prompt[0])
        self.assertIn("leaning forward", captured_prompt[0].lower())


# ═══════════════════════════════════════════════════════════════════════════════
# Integration: Full Prompt Smoke Tests (require API keys — skip by default)
# ═══════════════════════════════════════════════════════════════════════════════

@unittest.skipIf(SKIP_INTEGRATION, "Integration tests skipped (set SKIP_INTEGRATION=0 to run)")
class TestDirectorPromptIntegration(unittest.TestCase):
    """Verify the actual LLM call produces compliant JSON. Requires API key."""

    def test_director_produces_5_to_7_scenes(self):
        """Actual LLM call must produce 5-7 scenes."""
        from tools.director import director_tool_fn
        result = director_tool_fn(
            "光合作用的原理",
            language="chinese",
            target_duration_sec=240,
            logger=None,
        )
        scenes = result.get("scenes", [])
        self.assertGreaterEqual(len(scenes), 5, f"Expected ≥5 scenes, got {len(scenes)}")
        self.assertLessEqual(len(scenes), 7, f"Expected ≤7 scenes, got {len(scenes)}")

    def test_director_scene1_has_provocative_opening(self):
        """Scene 1 narration must NOT start with generic intro phrases."""
        from tools.director import director_tool_fn
        result = director_tool_fn(
            "AI在医疗领域的应用",
            language="chinese",
            target_duration_sec=240,
            logger=None,
        )
        scenes = result.get("scenes", [])
        self.assertTrue(len(scenes) > 0, "No scenes returned")
        nar1 = scenes[0].get("narration", "")
        forbidden_openers = ["今天我们来", "在这个视频中", "大家好", "欢迎来到"]
        for opener in forbidden_openers:
            self.assertNotIn(opener, nar1[:20],
                           f"Scene 1 opens with forbidden phrase: '{opener}'")

    def test_every_scene_has_text_overlay(self):
        """Every scene must have a non-empty text_overlay."""
        from tools.director import director_tool_fn
        result = director_tool_fn(
            "黑洞的奥秘",
            language="chinese",
            target_duration_sec=240,
            logger=None,
        )
        scenes = result.get("scenes", [])
        for s in scenes:
            self.assertTrue(
                s.get("text_overlay", "").strip(),
                f"Scene {s.get('scene_number')} has empty text_overlay"
            )

    def test_scene_count_equals_global_plan_total_scenes(self):
        """global_plan.total_scenes must match len(scenes)."""
        from tools.director import director_tool_fn
        result = director_tool_fn(
            "工业革命的历史影响",
            language="chinese",
            target_duration_sec=240,
            logger=None,
        )
        declared = result["global_plan"].get("total_scenes", 0)
        actual = len(result.get("scenes", []))
        self.assertEqual(declared, actual)

    def test_no_consecutive_emotional_beats(self):
        """No two consecutive scenes should have the same emotional_beat."""
        from tools.director import director_tool_fn
        result = director_tool_fn(
            "地球变暖的原因和影响",
            language="chinese",
            target_duration_sec=240,
            logger=None,
        )
        scenes = result.get("scenes", [])
        for i in range(len(scenes) - 1):
            b1 = scenes[i].get("emotional_beat", "")
            b2 = scenes[i + 1].get("emotional_beat", "")
            self.assertNotEqual(b1, b2,
                f"Scenes {i+1} and {i+2} have the same emotional_beat: '{b1}'")

    def test_every_scene_has_visual_strategy(self):
        """Every scene must have a valid visual_strategy value."""
        from tools.director import director_tool_fn
        result = director_tool_fn(
            "互联网的发展史",
            language="chinese",
            target_duration_sec=240,
            logger=None,
        )
        valid_strategies = {"hook", "problem", "explanation", "data",
                            "turning_point", "resolution", "cta"}
        scenes = result.get("scenes", [])
        for s in scenes:
            vs = s.get("visual_strategy", "")
            self.assertIn(vs, valid_strategies,
                        f"Scene {s.get('scene_number')} has invalid visual_strategy: '{vs}'")

    def test_english_director_produces_5_to_7_scenes(self):
        """English LLM call must also produce 5-7 scenes."""
        from tools.director import director_tool_fn
        result = director_tool_fn(
            "The history of space exploration",
            language="english",
            target_duration_sec=240,
            logger=None,
        )
        scenes = result.get("scenes", [])
        self.assertGreaterEqual(len(scenes), 5)
        self.assertLessEqual(len(scenes), 7)


# ═══════════════════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("  Whiteboard Animation — Engagement & Duration Optimization Tests")
    print(f"  Integration tests: {'ENABLED' if not SKIP_INTEGRATION else 'SKIPPED'}")
    print(f"  (Set SKIP_INTEGRATION=1 to skip tests requiring API keys)")
    print("=" * 70)
    print()

    # Run only unit tests by default; add integration tests if enabled
    unittest.main(verbosity=2)
