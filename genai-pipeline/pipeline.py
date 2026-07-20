import os
import time
import datetime
import concurrent.futures
import subprocess
import sys
import uuid
import threading
import traceback
from typing import Optional

# Reconfigure stdout/stderr to UTF-8 to support Unicode/Hindi character printing on Windows
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

from config import SAM_API_URL
from tools import (
    research_tool_fn,
    web_grounded_research_tool_fn,
    director_tool_fn,
    prompt_tool_fn,
    image_gen_tool_fn,
    generate_tts_audio_tool_fn,
    segmentation_tool_fn,
    merge_audio_video_tool_fn,
    concatenate_videos_tool_fn,
    burn_subtitles_to_video_tool_fn,
    merge_srt_files_tool_fn,
    refine_narration_tool_fn,
    draw_animation_tool_fn,
    set_output_dir,
    get_video_duration,
    get_media_duration,
    reference_search_tool_fn,
    generate_video_seedance_tool_fn,
    generate_video_happyhorse_tool_fn,
)
from log_utils import (
    ContextLogger,
    setup_logging,
    teardown_logging,
    ffmpeg_log_capture,
)

# --- Helper functions for robustness ---

def _is_valid_path(path: str) -> bool:
    """Check if a tool returned a valid file path (not an error string)."""
    if not path:
        return False
    if "Error" in path or "error" in path:
        return False
    return os.path.exists(path)


class PipelineRuntime:
    """Holds synchronisation primitives for staged pipeline execution with
    frontend review checkpoints.  The background pipeline thread blocks on
    ``pause_event``; the web frontend signals it via the /approve endpoint."""

    def __init__(self, run_id: str, job_id: Optional[str] = None):
        self.run_id = run_id
        self.job_id = job_id
        self.pause_event = threading.Event()
        self.abort_event = threading.Event()
        self.regenerate = False
        self.feedback = ""
        self.edited_video_plan: Optional[dict] = None


def _wait_for_approval(runtime: PipelineRuntime) -> bool:
    """Block the pipeline thread until the frontend approves or cancels.

    Returns:
        ``True`` if the frontend approved (continue pipeline).
        ``False`` if the frontend cancelled (abort pipeline).
    """
    while True:
        if runtime.abort_event.is_set():
            return False
        if runtime.pause_event.wait(timeout=1.0):
            runtime.pause_event.clear()
            return True


def _compress_script(
    scenes: list,
    language: str,
    budget: int,
    is_cjk: bool = False,
    logger: Optional[ContextLogger] = None,
) -> list:
    """
    Compress an over-budget script to fit the target duration.

    Merges all scene narrations, sends them to the LLM with an aggressive
    compression prompt, then redistributes the compressed text back across
    scenes proportionally.

    Args:
        scenes: Director's scenes list.
        language: Target language.
        budget: Max word/character count.
        is_cjk: True if CJK language (character-based counting).
        logger: Optional logger.
    Returns:
        Scenes list with compressed narrations.
    """
    unit = "characters" if is_cjk else "words"

    # Build the full script for compression
    script_parts = []
    for s in scenes:
        script_parts.append(f"[Scene {s.get('scene_number', '?')} — {s.get('emotional_beat', '')}]\n{s.get('narration', '')}")
    full_script = "\n\n---\n\n".join(script_parts)

    compress_prompt = f"""You are an expert script editor. The following whiteboard animation script
is TOO LONG for a {budget}-{unit} budget. COMPRESS IT AGGRESSIVELY.

RULES:
1. CUT entire sentences that are not essential to the core narrative. Surgery, not sanding.
2. MERGE similar points. One clear idea beats three fuzzy ones.
3. KEEP the most emotionally compelling and story-worthy content.
4. PRESERVE the hook, the curiosity gaps between scenes, and the payoff/CTA.
5. PRESERVE the [Scene N] markers and scene structure.
6. PRESERVE all key facts, numbers, and dates that are essential.
7. DELETE: filler phrases, redundant explanations, throat-clearing, over-explaining.
8. The compressed script MUST feel tighter, punchier, and MORE engaging — not worse.
9. Everything MUST remain in {language}.

Output the compressed script with the SAME [Scene N] markers and structure.
Each scene's narration should still be distinct. No meta-commentary."""

    try:
        from ai_gateway import generate

        if logger:
            logger.info(f"Calling LLM for script compression ({unit} budget: {budget})...")
        response = generate(
            task="story",
            prompt=f"{compress_prompt}\n\nORIGINAL SCRIPT:\n\n{full_script}",
            options={"max_tokens": 4096, "temperature": 0.5},
        )
        compressed_text = response.content

        # Parse scene narrations back from compressed output
        import re
        pattern = r'\[Scene (\d+)[^\]]*\](.*?)(?=\[Scene \d+\]|\Z)'
        matches = re.findall(pattern, compressed_text, re.DOTALL)

        if matches and len(matches) >= len(scenes) * 0.5:
            # Build a map from scene number to compressed narration
            compressed_map = {}
            for scene_num_str, narration in matches:
                try:
                    sn = int(scene_num_str)
                    compressed_map[sn] = narration.strip()
                except ValueError:
                    continue

            # Apply compressed narrations back to scenes
            for s in scenes:
                sn = s.get("scene_number", 0)
                if sn in compressed_map and len(compressed_map[sn]) > 10:
                    s["narration"] = compressed_map[sn]

            new_total = sum(
                len(s.get("narration", "")) if is_cjk else len(s.get("narration", "").split())
                for s in scenes
            )
            if logger:
                logger.info(
                    f"Script compressed: {new_total} {unit} (budget: {budget})",
                    extra={"new_total": new_total, "budget": budget})
        else:
            # Fallback: trim each scene proportionally
            if logger:
                logger.warning("LLM compression parsing failed — falling back to proportional trim.")
            for s in scenes:
                narration = s.get("narration", "")
                if is_cjk:
                    if len(narration) > 0:
                        # Keep first ~70% of characters (trim from the end)
                        trim_len = int(len(narration) * 0.7)
                        # Find a sentence boundary near trim_len
                        for sep in ['。', '！', '？', '\n', '；']:
                            pos = narration.rfind(sep, 0, trim_len)
                            if pos > trim_len * 0.6:
                                trim_len = pos + 1
                                break
                        s["narration"] = narration[:trim_len]
                else:
                    words = narration.split()
                    if len(words) > 0:
                        trim_count = int(len(words) * 0.7)
                        # Find sentence boundary
                        trimmed = ' '.join(words[:trim_count])
                        for sep in ['. ', '! ', '? ', '\n']:
                            pos = trimmed.rfind(sep)
                            if pos > trim_count * 0.6:
                                trimmed = trimmed[:pos + 1]
                                break
                        s["narration"] = trimmed

        return scenes

    except Exception as e:
        if logger:
            logger.warning(f"Script compression failed: {e}. Using original script.",
                           extra={"error": str(e)})
        return scenes


# --- Main Pipeline ---

def run_pipeline(
    user_context: str,
    do_research: bool = True,
    do_web_search: bool = False,
    use_internet_image_search: bool = True,
    fast_mode: bool = False,
    language: str = "english",
    image_provider: str = "qwen",
    video_provider: Optional[str] = None,
    veo_direction_by_director: bool = False,
    target_duration_sec: int = 240,
    logger: Optional[ContextLogger] = None,
    run_id: Optional[str] = None,
    job_id: Optional[str] = None,
    skip_review: bool = True,
    runtime: Optional[PipelineRuntime] = None,
):
    """
    Run the full whiteboard animation ai pipeline.

    Args:
        user_context: The topic / context for the video.
        do_research: Perform deep research before planning.
        do_web_search: Perform web-grounded research (faster).
        use_internet_image_search: Download Wikipedia reference images.
        fast_mode: Process scenes in parallel via ThreadPoolExecutor.
        language: Narration language (e.g. 'english', 'hindi', 'chinese').
        image_provider: Image generation provider — "qwen" (default), "doubao_image".
        video_provider: Video generation provider — "seedance", "happyhorse", or None to skip.
        veo_direction_by_director: Let Director generate video prompts.
        target_duration_sec: Target video duration in seconds (default 240 = 4 min).
        logger: Optional pre-configured ContextLogger. If None, one is created.
        run_id: Optional run identifier. Auto-generated if None.
        job_id: Optional job identifier (links the run to a web-frontend job).
        skip_review: When True (default / CLI), the pipeline runs straight through.
            When False, the pipeline pauses at research_review and director_review
            checkpoints and waits for the frontend to call /approve.
        runtime: PipelineRuntime instance carrying pause/abort events.  Required
            when *skip_review* is False; ignored otherwise.

    Returns:
        Path to the final video, or None if all scenes failed.
    """
    # -- Initialise logging -------------------------------------------------
    if run_id is None:
        run_id = f"run_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    # 0. Setup Output Directory
    # In Docker: OUTPUT_DIR env var points to the mounted volume
    # Local dev: falls back to os.getcwd()/output
    # Use run_id as directory name so it matches the DB record (includes UUID suffix)
    output_base = os.environ.get("OUTPUT_DIR", os.path.join(os.getcwd(), "output"))
    output_dir = os.path.join(output_base, run_id)
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    if logger is None:
        logger = setup_logging(
            run_id=run_id,
            output_dir=output_dir,
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
        )

    set_output_dir(output_dir)

    # -- Persist run record to database ---------------------------------------
    from tools import db_utils as _db
    _db.create_run(
        run_id=run_id,
        job_id=job_id,
        context=user_context,
        language=language,
        settings={
            "do_research": do_research,
            "do_web_search": do_web_search,
            "use_internet_image_search": use_internet_image_search,
            "fast_mode": fast_mode,
            "image_provider": image_provider,
            "video_provider": video_provider,
            "veo_direction_by_director": veo_direction_by_director,
            "target_duration_sec": target_duration_sec,
        },
        output_dir=output_dir,
    )
    # Link the job record to this run so the frontend can find review data
    if job_id:
        _db.update_job(job_id, run_id=run_id)

    t_pipeline_start = time.perf_counter()

    logger.info(
        "Pipeline started",
        extra={
            "context": user_context[:200],
            "language": language,
            "do_research": do_research,
            "do_web_search": do_web_search,
            "use_internet_image_search": use_internet_image_search,
            "fast_mode": fast_mode,
            "image_provider": image_provider,
            "video_provider": video_provider,
            "veo_direction_by_director": veo_direction_by_director,
            "target_duration_sec": target_duration_sec,
            "output_dir": output_dir,
            "run_id": run_id,
        },
    )

    try:
        return _run_pipeline_impl(
            user_context=user_context,
            do_research=do_research,
            do_web_search=do_web_search,
            use_internet_image_search=use_internet_image_search,
            fast_mode=fast_mode,
            language=language,
            image_provider=image_provider,
            video_provider=video_provider,
            veo_direction_by_director=veo_direction_by_director,
            target_duration_sec=target_duration_sec,
            output_dir=output_dir,
            run_id=run_id,
            logger=logger,
            t_pipeline_start=t_pipeline_start,
            runtime=runtime,
        )
    except Exception:
        logger.exception("Pipeline crashed with unhandled exception")
        from tools import db_utils as _db_err
        _db_err.update_run(
            run_id,
            status="failed",
            error=traceback.format_exc(),
            completed_at=datetime.datetime.utcnow(),
        )
        return None
    finally:
        teardown_logging(run_id)


def _run_pipeline_impl(
    user_context: str,
    do_research: bool,
    do_web_search: bool,
    use_internet_image_search: bool,
    fast_mode: bool,
    language: str,
    image_provider: str,
    video_provider: Optional[str],
    veo_direction_by_director: bool,
    target_duration_sec: int,
    output_dir: str,
    run_id: str,
    logger: ContextLogger,
    t_pipeline_start: float,
    runtime: Optional[PipelineRuntime] = None,
):
    # 1. Research (Optional)
    research_report = None
    research_logger = logger.bind(step_tag="research")
    research_was_run = do_research or do_web_search

    while True:  # loop allows regenerate if frontend requests it
        if do_research:
            research_logger.info("Step 1: Performing Deep Research...")
            try:
                research_report = research_tool_fn(user_context, logger=research_logger,
                                                   feedback=runtime.feedback if runtime else "")
                research_logger.info(
                    "Research completed",
                    extra={"report_length": len(research_report) if research_report else 0},
                )
            except Exception as e:
                research_logger.warning(
                    f"Deep Research failed: {e}. Continuing without research.",
                    extra={"error": str(e)},
                )
                research_report = None
        elif do_web_search:
            research_logger.info("Step 1: Performing Web-Grounded Research (Fast)...")
            try:
                research_report = web_grounded_research_tool_fn(user_context, logger=research_logger,
                                                                feedback=runtime.feedback if runtime else "")
                research_logger.info(
                    "Web-Grounded Research completed",
                    extra={"report_length": len(research_report) if research_report else 0},
                )
            except Exception as e:
                research_logger.warning(
                    f"Web-Grounded Research failed: {e}. Continuing without research.",
                    extra={"error": str(e)},
                )
                research_report = None
        else:
            research_logger.info("Step 1: Skipping Research — using provided context directly.")

        # Persist research to run record
        from tools import db_utils as _db_r1
        _db_r1.update_run(run_id, research_report=research_report)

        # ── Research review checkpoint ──
        if runtime is None or not research_was_run:
            break  # CLI mode or no research to review — continue straight through

        from tools import db_utils as _db_j1
        _db_j1.update_job(
            runtime.job_id,
            status="research_review",
            progress=20,
            message="等待评审研究报告…",
        )
        research_logger.info("Pausing for research review — waiting for frontend approval...")

        if not _wait_for_approval(runtime):
            _db_j1.update_job(runtime.job_id, status="cancelled", progress=0, message="已取消")
            research_logger.info("Pipeline cancelled by user during research review.")
            return None

        if runtime.regenerate:
            runtime.regenerate = False
            _db_j1.update_job(runtime.job_id, status="researching", progress=15,
                              message="重新研究中…")
            research_logger.info("Regenerating research per user request...")
            continue  # re-run the research loop

        break  # approved — move on

    # 审批通过后立刻更新状态，消除"状态真空"窗口
    if runtime is not None and research_was_run:
        from tools import db_utils as _db_j1b
        _db_j1b.update_job(runtime.job_id, status="directing", progress=25,
                           message="导演规划中…")

    # Step 2: Director Planning
    director_logger = logger.bind(step_tag="director")

    while True:  # loop allows regenerate if frontend requests it
        director_logger.info("Step 2: Director Planning & Scene Writing...")
        t_director_start = time.perf_counter()

        video_plan = director_tool_fn(
            user_context,
            research_material=research_report,
            language=language,
            enable_veo=(video_provider is not None),
            veo_direction_by_director=veo_direction_by_director,
            target_duration_sec=target_duration_sec,
            logger=director_logger,
            feedback=runtime.feedback if runtime else "",
        )

        global_plan = video_plan.get("global_plan", {})
        scenes = video_plan.get("scenes", [])
        director_elapsed = (time.perf_counter() - t_director_start) * 1000

        director_logger.info(
            f"Director planned {len(scenes)} scenes",
            extra={
                "scene_count": len(scenes),
                "tone": global_plan.get("tone"),
                "narrative_arc": global_plan.get("narrative_arc", "N/A"),
                "elapsed_ms": round(director_elapsed, 1),
            },
        )

        # -- Persist director output to DB --
        from tools import db_utils as _db2
        _db2.update_run(
            run_id,
            video_plan_json=video_plan,
            scene_count=len(scenes),
            research_report=research_report,
        )

        # ── Director review checkpoint ──
        if runtime is None:
            break  # CLI mode — continue straight through

        from tools import db_utils as _db_j2
        _db_j2.update_job(
            runtime.job_id,
            status="director_review",
            progress=30,
            message="等待评审导演方案…",
        )
        director_logger.info("Pausing for director review — waiting for frontend approval...")

        if not _wait_for_approval(runtime):
            _db_j2.update_job(runtime.job_id, status="cancelled", progress=0, message="已取消")
            director_logger.info("Pipeline cancelled by user during director review.")
            return None

        if runtime.regenerate:
            runtime.regenerate = False
            _db_j2.update_job(runtime.job_id, status="directing", progress=25,
                              message="重新规划中…")
            director_logger.info("Regenerating director plan per user request...")
            continue  # re-run the director loop

        # Check if frontend sent an edited video_plan
        if runtime.edited_video_plan:
            video_plan = runtime.edited_video_plan
            global_plan = video_plan.get("global_plan", {})
            scenes = video_plan.get("scenes", [])
            director_logger.info(
                f"Using frontend-edited video plan ({len(scenes)} scenes)",
                extra={"scene_count": len(scenes)},
            )
            from tools import db_utils as _db2e
            _db2e.update_run(
                run_id,
                video_plan_json=video_plan,
                scene_count=len(scenes),
            )

        break  # approved — move on

    # ── A3: Script Compression (if total narration exceeds budget) ──
    cjk_langs = {"chinese", "japanese", "korean", "中文", "日语", "韩语",
                 "mandarin", "cantonese", "zh", "ja", "ko", "zh-cn", "zh-tw"}
    is_cjk_lang = language.lower() in cjk_langs
    # Calculate budget: CJK ~200 chars/min, others ~140 words/min
    target_minutes = target_duration_sec / 60.0
    if is_cjk_lang:
        char_budget = int(target_minutes * 200)
        # Count total characters in all scene narrations
        total_chars = sum(len(s.get("narration", "")) for s in scenes)
        if total_chars > char_budget * 1.1:  # 10% tolerance
            compress_logger = logger.bind(step_tag="script_compress")
            compress_logger.info(
                f"Script too long ({total_chars} chars, budget {char_budget}) — compressing...",
                extra={"total_chars": total_chars, "char_budget": char_budget},
            )
            scenes = _compress_script(
                scenes, language, char_budget, is_cjk=True, logger=compress_logger
            )
        else:
            logger.info(
                f"Script within budget ({total_chars}/{char_budget} chars) — no compression needed.",
                extra={"total_chars": total_chars, "char_budget": char_budget},
            )
    else:
        word_budget = int(target_minutes * 140)
        total_words = sum(len(s.get("narration", "").split()) for s in scenes)
        if total_words > word_budget * 1.1:
            compress_logger = logger.bind(step_tag="script_compress")
            compress_logger.info(
                f"Script too long ({total_words} words, budget {word_budget}) — compressing...",
                extra={"total_words": total_words, "word_budget": word_budget},
            )
            scenes = _compress_script(
                scenes, language, word_budget, is_cjk=False, logger=compress_logger
            )
        else:
            logger.info(
                f"Script within budget ({total_words}/{word_budget} words) — no compression needed.",
                extra={"total_words": total_words, "word_budget": word_budget},
            )

    # 审批通过后立刻更新状态
    if runtime is not None:
        from tools import db_utils as _db_j2b
        _db_j2b.update_job(runtime.job_id, status="generating", progress=35,
                           message="生成场景中…")

    final_videos = []
    scene_srt_paths = []
    scene_video_durations = []
    first_image_path = None
    failed_scenes = []

    def process_scene_helper(scene_num, scene, local_prev_image_path):
        scene_logger = logger.bind(scene_id=scene_num)

        try:
            description = scene.get('description', 'No description')
            narration = scene.get('narration', 'No narration')
            visual_setup = scene.get('visual_setup', '')
            summary = scene.get('summary', '')
            emotional_beat = scene.get('emotional_beat', '')
            visual_strategy = scene.get('visual_strategy', '')
            search_query = scene.get('search_query', '')
            text_overlay = scene.get('text_overlay', '')

            scene_logger.info(
                f"Processing Scene {scene_num}/{len(scenes)}",
                extra={
                    "summary": summary[:150] if summary else None,
                    "emotional_beat": emotional_beat,
                    "narration_length": len(narration),
                },
            )

            # -- Persist scene record to DB --
            from tools import db_utils as _db3
            scene_db_id = _db3.create_scene(
                run_id=run_id,
                scene_index=scene_num,
                narration=narration,
                description=description,
                visual_setup=visual_setup,
                text_overlay=text_overlay,
            )

            # --- 3.a.0 Reference Search ---
            subject_image_path = None
            if use_internet_image_search and search_query:
                ref_logger = scene_logger.bind(step_tag="reference_search")
                ref_logger.info(f"Searching internet for reference image: '{search_query}'")
                res = reference_search_tool_fn(search_query, logger=ref_logger)
                if _is_valid_path(res):
                    subject_image_path = res
                    ref_logger.info("Reference image downloaded", extra={"path": subject_image_path})
                else:
                    ref_logger.warning(
                        "Reference search returned no valid image",
                        extra={"result": str(res)[:200]},
                    )
            elif not use_internet_image_search and search_query:
                scene_logger.debug(f"Internet image search disabled. Skipping reference for '{search_query}'.")

            # --- 3a. Generate Image Prompt ---
            prompt_logger = scene_logger.bind(step_tag="image_prompt")
            prompt_logger.info("Generating image prompt...")
            try:
                img_prompt = prompt_tool_fn(
                    description,
                    visual_setup=visual_setup,
                    text_overlay=text_overlay,
                    global_plan=global_plan,
                    emotional_beat=emotional_beat,
                    visual_strategy=visual_strategy,
                    logger=prompt_logger,
                )
            except Exception as e:
                scene_logger.error(
                    f"Skipping Scene {scene_num}: Image prompt generation failed",
                    extra={"error": str(e), "traceback": traceback.format_exc()},
                )
                return None
            if not img_prompt:
                scene_logger.error(
                    f"Skipping Scene {scene_num}: Image prompt generation returned empty."
                )
                _db3.update_scene(scene_db_id, status="failed", error="Image prompt generation returned empty.")
                return None

            # --- 3b. Generate Image ---
            img_gen_logger = scene_logger.bind(step_tag="image_gen")
            img_gen_logger.info("Generating image...")

            # Update scene with image prompt
            _db3.update_scene(scene_db_id, image_prompt=img_prompt)

            try:
                image_path = image_gen_tool_fn(
                    img_prompt,
                    reference_image_path=local_prev_image_path,
                    subject_reference_image_path=subject_image_path,
                    logger=img_gen_logger,
                    provider=image_provider,
                )
            except Exception as e:
                scene_logger.error(
                    f"Skipping Scene {scene_num}: Image generation failed",
                    extra={"error": str(e), "traceback": traceback.format_exc()},
                )
                return None

            if not _is_valid_path(image_path):
                scene_logger.error(
                    f"Skipping Scene {scene_num}: Image generation produced no valid image.",
                    extra={"image_path": str(image_path)[:200]},
                )
                return None

            current_image_path = image_path
            img_gen_logger.info("Image generated", extra={"path": current_image_path})

            # --- 3b.2. AI Video Generation (if enabled) ---
            veo_video_path = None
            veo_duration = 0.0
            if video_provider:
                veo_logger = scene_logger.bind(step_tag="ai_video")
                veo_prompt = scene.get('veo_prompt', '')
                if not veo_prompt:
                    veo_prompt = description
                veo_logger.info(f"Generating AI video via {video_provider}",
                                extra={"veo_prompt": veo_prompt[:200], "video_provider": video_provider})

                # Dispatch to the correct video generation tool
                if video_provider == "happyhorse":
                    veo_res = generate_video_happyhorse_tool_fn(
                        current_image_path, veo_prompt, logger=veo_logger,
                    )
                else:
                    veo_res = generate_video_seedance_tool_fn(
                        current_image_path, veo_prompt, logger=veo_logger,
                    )

                if _is_valid_path(veo_res):
                    veo_video_path = veo_res
                    veo_duration = get_media_duration(veo_video_path)
                    veo_logger.info("AI video generated", extra={
                        "path": veo_video_path, "provider": video_provider,
                        "duration_s": round(veo_duration, 1),
                    })
                else:
                    veo_logger.warning(
                        f"AI video generation ({video_provider}) failed — continuing without.",
                        extra={"result": str(veo_res)[:200]},
                    )

            # --- 3c. SAM Segmentation (non-critical, can fail gracefully) ---
            seg_json_path = None
            seg_logger = scene_logger.bind(step_tag="segmentation")
            if not SAM_API_URL:
                seg_logger.info("SAM_API_URL not configured — skipping SAM3 segmentation. Animation will run in single-pass mode.")
            else:
                seg_logger.info("Segmenting image objects via SAM3...")
                try:
                    seg_json_path = segmentation_tool_fn(current_image_path, logger=seg_logger)
                    if not _is_valid_path(seg_json_path):
                        seg_logger.warning("Segmentation returned no valid result — continuing without.")
                        seg_json_path = None
                    else:
                        seg_logger.info("Segmentation completed", extra={"seg_json": seg_json_path})
                except Exception as e:
                    seg_logger.warning(
                        f"Segmentation failed (non-critical): {e}. Continuing without.",
                        extra={"error": str(e)},
                    )

            # --- 3e. Narration Refinement (non-critical, can fallback to original) ---
            # Moved before animation: TTS happens first so animation can target audio duration.
            refine_logger = scene_logger.bind(step_tag="narration_refine")
            refine_logger.info("Refining narration...")
            try:
                refined = refine_narration_tool_fn(
                    narration,
                    current_image_path,
                    video_duration=None,  # animation not yet generated; no pacing constraint
                    global_plan=global_plan,
                    language=language,
                    logger=refine_logger,
                )
                if refined and "Error" not in refined:
                    narration = refined
                    refine_logger.info("Narration refined", extra={"new_length": len(narration)})
                else:
                    refine_logger.warning("Narration refinement returned error — using Director's original.")
            except Exception as e:
                refine_logger.warning(
                    f"Narration refinement failed (non-critical): {e}. Using Director's original.",
                    extra={"error": str(e)},
                )

            # --- 3f. TTS Generation (audio + native subtitles from MiniMax) ---
            tts_logger = scene_logger.bind(step_tag="tts")
            tts_logger.info("Generating narration audio with subtitles...")
            try:
                audio_path, tts_subtitles_json_path = generate_tts_audio_tool_fn(
                    narration, language=language, logger=tts_logger
                )
            except Exception as e:
                scene_logger.error(
                    f"Skipping Scene {scene_num}: TTS generation failed",
                    extra={"error": str(e), "traceback": traceback.format_exc()},
                )
                return None

            if not _is_valid_path(audio_path):
                scene_logger.error(
                    f"Skipping Scene {scene_num}: TTS generation produced no audio."
                )
                return None

            audio_duration = get_media_duration(audio_path)
            tts_logger.info("TTS audio generated", extra={
                "audio_path": audio_path,
                "duration_s": round(audio_duration, 1),
            })

            # -- Persist TTS output to DB --
            _db3.update_scene(scene_db_id, audio_path=audio_path, srt_content=None)
            _db3.create_media_asset(
                run_id=run_id,
                scene_id=scene_db_id,
                asset_type="audio",
                file_path=audio_path,
                is_temporary=True,
            )

            # --- 3d. Whiteboard Animation Generation ---
            # Target animation duration = audio duration minus Veo video duration (if any).
            # If Veo duration already exceeds audio, target a minimum 1s animation.
            # The merge step's setpts will handle any remaining gap downstream.
            wb_target = None
            if audio_duration > 0:
                remaining = audio_duration - veo_duration
                wb_target = max(1.0, remaining) if remaining > 0 else 1.0

            anim_logger = scene_logger.bind(step_tag="animation")
            anim_logger.info("Generating whiteboard animation...",
                             extra={"wb_target_s": round(wb_target, 1) if wb_target else None})
            anim_video_path = draw_animation_tool_fn(
                current_image_path,
                segmentation_results_path=seg_json_path,
                target_duration_sec=wb_target,
                logger=anim_logger,
            )

            if not _is_valid_path(anim_video_path):
                scene_logger.error(
                    f"Skipping Scene {scene_num}: Animation generation failed."
                )
                return None

            anim_logger.info("Animation generated", extra={"path": anim_video_path})

            # --- 3d.2. Concatenate Whiteboard Animation and Veo video (if enabled) ---
            combined_video_path = anim_video_path
            if veo_video_path:
                concat_logger = scene_logger.bind(step_tag="concat_wb_veo")
                concat_logger.info("Concatenating whiteboard animation and Veo video...")
                combined_output = os.path.join(output_dir, f"scene_{scene_num}_combined_silent.mp4")
                try:
                    filter_complex = (
                        "[0:v]scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=25[v0]; "
                        "[1:v]scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=25[v1]; "
                        "[v0][v1]concat=n=2:v=1:a=0[v]"
                    )
                    concat_cmd = [
                        "ffmpeg", "-y",
                        "-i", anim_video_path,
                        "-i", veo_video_path,
                        "-filter_complex", filter_complex,
                        "-map", "[v]",
                        "-pix_fmt", "yuv420p",
                        combined_output
                    ]
                    with ffmpeg_log_capture(concat_logger, "concat_wb_veo"):
                        subprocess.run(concat_cmd, capture_output=True, check=True)
                    if os.path.exists(combined_output):
                        combined_video_path = combined_output
                        concat_logger.info("Successfully concatenated", extra={"path": combined_video_path})
                    else:
                        concat_logger.warning("Concat output file not found. Falling back to whiteboard animation only.")
                except Exception as concat_err:
                    concat_logger.warning(
                        f"Concat failed: {concat_err}. Falling back to whiteboard animation only.",
                        extra={"error": str(concat_err)},
                    )

            # --- 3g. Audio-Video Merging ---
            merge_logger = scene_logger.bind(step_tag="merge_av")
            merged_output = os.path.join(output_dir, f"scene_{scene_num}_merged.mp4")
            merge_logger.info("Merging audio and video...")
            merged_video_path = merge_audio_video_tool_fn(
                combined_video_path, audio_path, merged_output, logger=merge_logger
            )

            if not _is_valid_path(merged_video_path):
                scene_logger.error(
                    f"Skipping Scene {scene_num}: Audio-Video merge failed."
                )
                return None

            merge_logger.info("Audio-Video merged", extra={"path": merged_video_path})

            # --- 3h. Subtitle SRT Export (subtitles come directly from TTS) ---
            final_scene_video = merged_video_path
            srt_path = None
            if tts_subtitles_json_path and _is_valid_path(tts_subtitles_json_path):
                sub_logger = scene_logger.bind(step_tag="subtitle_burn")
                sub_logger.info("Exporting SRT sidecar...")
                subtitled_output = os.path.join(output_dir, f"scene_{scene_num}_final.mp4")
                try:
                    final_sv = burn_subtitles_to_video_tool_fn(
                        merged_video_path, tts_subtitles_json_path, subtitled_output, logger=sub_logger
                    )
                    if _is_valid_path(final_sv):
                        final_scene_video = final_sv
                        srt_path = os.path.join(output_dir, f"scene_{scene_num}_final.srt")
                        sub_logger.info("SRT sidecar exported", extra={"path": final_scene_video, "srt": srt_path})
                    else:
                        sub_logger.warning("SRT export failed — using merged video without subtitles.")
                except Exception as e:
                    sub_logger.warning(
                        f"SRT export error: {e}. Using merged video without subtitles.",
                        extra={"error": str(e)},
                    )
            else:
                scene_logger.info("No subtitles from TTS — using merged video as-is.")

            # --- Cleanup intermediate files ---
            files_to_delete = []
            if seg_json_path and os.path.exists(seg_json_path):
                files_to_delete.append(seg_json_path)
            if anim_video_path and os.path.exists(anim_video_path):
                files_to_delete.append(anim_video_path)
            if veo_video_path and os.path.exists(veo_video_path):
                files_to_delete.append(veo_video_path)
            if combined_video_path and combined_video_path != anim_video_path and os.path.exists(combined_video_path):
                files_to_delete.append(combined_video_path)
            if audio_path and os.path.exists(audio_path):
                files_to_delete.append(audio_path)
            if merged_video_path and merged_video_path != final_scene_video and os.path.exists(merged_video_path):
                files_to_delete.append(merged_video_path)

            for fpath in files_to_delete:
                try:
                    os.remove(fpath)
                    scene_logger.debug(f"Cleaned up intermediate file: {os.path.basename(fpath)}")
                except Exception as cleanup_err:
                    scene_logger.warning(
                        f"Could not delete intermediate file {os.path.basename(fpath)}",
                        extra={"error": str(cleanup_err)},
                    )

            scene_logger.info(f"Scene {scene_num} completed successfully!", extra={
                "final_video": final_scene_video,
                "image_path": current_image_path,
            })

            # -- Mark scene done in DB --
            _db3.update_scene(scene_db_id, status="done")

            return {"scene_num": scene_num, "final_scene_video": final_scene_video,
                    "image_path": current_image_path, "srt_path": srt_path}

        except Exception as e:
            scene_logger.exception(
                f"UNEXPECTED ERROR in Scene {scene_num}",
                extra={"error": str(e), "traceback": traceback.format_exc()},
            )
            return None

    # 3. Asset Generation & Processing
    if fast_mode:
        logger.info(
            "Step 3: Processing Scenes in Parallel (Fast Mode)",
            extra={"scene_count": len(scenes), "max_workers": 5},
        )
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            future_to_scene = {
                executor.submit(process_scene_helper, i + 1, scene, None): i + 1
                for i, scene in enumerate(scenes)
            }
            results = []
            for future in concurrent.futures.as_completed(future_to_scene):
                scene_num = future_to_scene[future]
                try:
                    res = future.result()
                    if res:
                        results.append(res)
                    else:
                        failed_scenes.append(scene_num)
                except Exception as e:
                    logger.error(
                        f"UNEXPECTED ERROR in parallel Scene {scene_num}",
                        extra={"error": str(e), "traceback": traceback.format_exc()},
                    )
                    failed_scenes.append(scene_num)

            # Reconstruct final videos in correct order
            results.sort(key=lambda x: x["scene_num"])
            for res in results:
                final_videos.append(res["final_scene_video"])
                if res.get("srt_path"):
                    scene_srt_paths.append(res["srt_path"])
                    scene_video_durations.append(get_media_duration(res["final_scene_video"]))
    else:
        logger.info(
            "Step 3: Processing Scenes Sequentially",
            extra={"scene_count": len(scenes)},
        )
        for i, scene in enumerate(scenes):
            scene_num = i + 1
            res = process_scene_helper(scene_num, scene, first_image_path)
            if res:
                if first_image_path is None:
                    first_image_path = res["image_path"]
                final_videos.append(res["final_scene_video"])
                if res.get("srt_path"):
                    scene_srt_paths.append(res["srt_path"])
                    scene_video_durations.append(get_media_duration(res["final_scene_video"]))
            else:
                failed_scenes.append(scene_num)

    # --- Summary ---
    total_scenes = len(scenes)
    succeeded = len(final_videos)
    failed = len(failed_scenes)

    logger.info(
        f"Scene Processing Summary: {succeeded}/{total_scenes} succeeded, {failed} failed",
        extra={
            "total_scenes": total_scenes,
            "succeeded": succeeded,
            "failed": failed,
            "failed_scene_numbers": failed_scenes if failed_scenes else None,
        },
    )

    # 4. Final Merge
    if len(final_videos) >= 2:
        merge_logger = logger.bind(step_tag="final_merge")
        merge_logger.info("Step 4: Concatenating all scenes into final video...")
        final_video_path = os.path.join(output_dir, "whiteboard-animation-ai_final_video.mp4")
        result = concatenate_videos_tool_fn(final_videos, final_video_path, logger=merge_logger)

        # 5. Merge SRT subtitles
        if scene_srt_paths:
            srt_logger = logger.bind(step_tag="srt_merge")
            srt_logger.info("Step 5: Merging per-scene SRT files...")
            merged_srt_path = os.path.join(output_dir, "whiteboard-animation-ai_final_video.srt")
            merge_srt_files_tool_fn(
                scene_srt_paths, scene_video_durations, merged_srt_path, logger=srt_logger
            )

        total_elapsed = (time.perf_counter() - t_pipeline_start)
        logger.info(
            "Pipeline Complete!",
            extra={
                "final_video": result,
                "total_elapsed_s": round(total_elapsed, 1),
                "scenes_succeeded": succeeded,
                "scenes_failed": failed,
            },
        )

        # -- Mark run as completed in DB --
        from tools import db_utils as _db_final
        _db_final.update_run(
            run_id,
            status="completed",
            final_video=result,
            cost_total=None,  # aggregated from ai_usage later
            completed_at=datetime.datetime.utcnow(),
        )
        return result
    elif len(final_videos) == 1:
        # Merge SRT (single scene — copy with zero offset)
        if scene_srt_paths:
            srt_logger = logger.bind(step_tag="srt_merge")
            srt_logger.info("Step 5: Merging per-scene SRT files...")
            merged_srt_path = os.path.join(output_dir, "whiteboard-animation-ai_final_video.srt")
            merge_srt_files_tool_fn(
                scene_srt_paths, scene_video_durations, merged_srt_path, logger=srt_logger
            )

        total_elapsed = (time.perf_counter() - t_pipeline_start)
        logger.info(
            "Pipeline Complete (Single Scene)!",
            extra={
                "final_video": final_videos[0],
                "total_elapsed_s": round(total_elapsed, 1),
            },
        )
        from tools import db_utils as _db_final2
        _db_final2.update_run(
            run_id,
            status="completed",
            final_video=final_videos[0],
            completed_at=datetime.datetime.utcnow(),
        )
        return final_videos[0]
    else:
        total_elapsed = (time.perf_counter() - t_pipeline_start)
        logger.error(
            "Pipeline failed: No videos generated.",
            extra={
                "total_elapsed_s": round(total_elapsed, 1),
                "failed_scenes": failed_scenes,
            },
        )
        # -- Mark run as failed in DB --
        from tools import db_utils as _db_fail
        _db_fail.update_run(
            run_id,
            status="failed",
            error="No scenes produced a final video.",
            completed_at=datetime.datetime.utcnow(),
        )
        return None


if __name__ == "__main__":
    print("--- Whiteboard Animation AI Pipeline ---")
    print()
    context = input("Enter the context for your video: ")
    res_choice = input("Select research mode: [1] Deep Research, [2] Web Search (Fast), [3] None (default 2): ").strip()

    do_research = False
    do_web_search = False

    if res_choice == '1':
        do_research = True
    elif res_choice == '3':
        pass
    else:
        do_web_search = True

    image_search_choice = input("Enable internet image search for references? [Y/n] (default Y): ").strip().lower()
    use_internet_image_search = False if image_search_choice in ['n', 'no'] else True

    fast_mode_choice = input("Enable fast mode (parallel generation)? [Y/n] (default N): ").strip().lower()
    fast_mode = True if fast_mode_choice in ['y', 'yes'] else False

    language = input("Enter the narration language (default 'english'): ").strip()
    if not language:
        language = "english"

    veo_choice = input("Enable AI video generation? [1] Seedance, [2] HappyHorse, [N] None (default N): ").strip().lower()
    video_provider = None
    if veo_choice in ['1', 'seedance', 'y', 'yes']:
        video_provider = "seedance"
    elif veo_choice in ['2', 'happyhorse']:
        video_provider = "happyhorse"

    veo_direction_by_director = False
    if video_provider:
        veo_dir_choice = input("Let Director generate video prompts? [Y/n] (default Y): ").strip().lower()
        veo_direction_by_director = False if veo_dir_choice in ['n', 'no'] else True

    dur_choice = input("Target video duration in seconds [180/240/300] (default 240): ").strip()
    target_duration_sec = 240
    if dur_choice in ('180', '240', '300'):
        target_duration_sec = int(dur_choice)

    run_pipeline(
        context,
        do_research=do_research,
        do_web_search=do_web_search,
        use_internet_image_search=use_internet_image_search,
        fast_mode=fast_mode,
        language=language,
        video_provider=video_provider,
        veo_direction_by_director=veo_direction_by_director,
        target_duration_sec=target_duration_sec,
    )
