import os
import time
import datetime
import concurrent.futures
import subprocess
import sys
import uuid
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
    logger: Optional[ContextLogger] = None,
    run_id: Optional[str] = None,
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
        logger: Optional pre-configured ContextLogger. If None, one is created.
        run_id: Optional run identifier. Auto-generated if None.

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
        },
        output_dir=output_dir,
    )

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
            output_dir=output_dir,
            run_id=run_id,
            logger=logger,
            t_pipeline_start=t_pipeline_start,
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
    output_dir: str,
    run_id: str,
    logger: ContextLogger,
    t_pipeline_start: float,
):
    # 1. Research (Optional)
    research_report = None
    research_logger = logger.bind(step_tag="research")

    if do_research:
        research_logger.info("Step 1: Performing Deep Research...")
        try:
            research_report = research_tool_fn(user_context, logger=research_logger)
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
            research_report = web_grounded_research_tool_fn(user_context, logger=research_logger)
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

    # Step 2: Director Planning
    director_logger = logger.bind(step_tag="director")
    director_logger.info("Step 2: Director Planning & Scene Writing...")
    t_director_start = time.perf_counter()

    video_plan = director_tool_fn(
        user_context,
        research_material=research_report,
        language=language,
        enable_veo=(video_provider is not None),
        veo_direction_by_director=veo_direction_by_director,
        logger=director_logger,
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

    run_pipeline(
        context,
        do_research=do_research,
        do_web_search=do_web_search,
        use_internet_image_search=use_internet_image_search,
        fast_mode=fast_mode,
        language=language,
        video_provider=video_provider,
        veo_direction_by_director=veo_direction_by_director
    )
