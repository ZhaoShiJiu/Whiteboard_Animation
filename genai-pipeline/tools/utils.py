import os
from typing import Any, Dict, Optional

# Global Output Management
GLOBAL_OUTPUT_DIR = None


def set_output_dir(path: str):
    """Sets the directory where all tool outputs will be saved."""
    global GLOBAL_OUTPUT_DIR
    GLOBAL_OUTPUT_DIR = path
    if path and not os.path.exists(path):
        os.makedirs(path, exist_ok=True)


def _save_to_run_folder(content: str, filename: str, mode: str = "w"):
    """Helper to save content to the current run folder if enabled."""
    if GLOBAL_OUTPUT_DIR:
        full_path = os.path.join(GLOBAL_OUTPUT_DIR, filename)
        try:
            with open(full_path, mode, encoding="utf-8") as f:
                f.write(content)
            return full_path
        except Exception as e:
            print(f"Error saving to {filename}: {e}")
    return None


def _emit(logger, level: str, msg: str, extra: Optional[Dict[str, Any]] = None) -> None:
    """
    Emit a log message through a ContextLogger, falling back to print().

    All tool modules use this helper so they work both inside the full pipeline
    (where a structured logger is injected) and when called standalone.

    Args:
        logger: A ContextLogger instance or None.
        level: One of "debug", "info", "warning", "error", "critical", "exception".
        msg: The log message.
        extra: Optional dict of structured fields.
    """
    if logger is not None:
        log_method = getattr(logger, level, logger.info)
        try:
            log_method(msg, extra=extra)
        except TypeError:
            # Fallback for methods like .exception() that may require exc_info
            log_method(msg)
    else:
        # Standalone / legacy mode — strip context markers for clean output
        prefix_map = {
            "debug": "[DEBUG]",
            "info": "",
            "warning": "[!]",
            "error": "[X]",
            "critical": "[CRITICAL]",
        }
        prefix = prefix_map.get(level, "")
        full_msg = f"{prefix} {msg}".strip() if prefix else msg

        # Print extra as JSON if present
        if extra:
            import json
            try:
                compact = json.dumps(extra, ensure_ascii=False, default=str)
                full_msg += f"  {compact}"
            except Exception:
                pass

        print(full_msg)


def get_video_duration(video_path: str) -> float:
    """
    Returns the duration of a video file in seconds.
    """
    import cv2
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return 0.0
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        duration = frame_count / fps if fps > 0 else 0.0
        cap.release()
        return duration
    except Exception as e:
        print(f"Error getting video duration: {e}")
        return 0.0


def postprocess_ai_video(
    output_path: str,
    image_path: str,
    logger=None,
    provider_name: str = "AI",
) -> str:
    """
    Shared post-processing for AI-generated videos (Seedance / HappyHorse).

    Steps:
      1. Extract audio track → .wav (generate silent audio if none present)
      2. Strip audio from video (make it purely silent)
      3. Extract first frame → replace the original reference image

    Args:
        output_path: Path to the downloaded AI-generated MP4 video.
        image_path: Path to the original reference image (will be replaced).
        logger: Optional ContextLogger.
        provider_name: Provider label used in log messages and filenames.

    Returns:
        output_path (processed in-place).
    """
    import subprocess

    # 1. Extract audio channel → .wav
    audio_output_path = output_path.replace(".mp4", ".wav")
    _emit(logger, "debug", f"[{provider_name}] Extracting audio...")

    extract_audio_cmd = [
        "ffmpeg", "-y",
        "-i", output_path,
        "-vn",
        "-acodec", "pcm_s16le",
        audio_output_path,
    ]
    res = subprocess.run(extract_audio_cmd, capture_output=True, text=True)

    if res.returncode != 0 or not os.path.exists(audio_output_path) or os.path.getsize(audio_output_path) < 1000:
        _emit(logger, "info", f"[{provider_name}] No audio channel found. Generating silent track.")
        video_dur = get_video_duration(output_path) or 8.0
        silent_audio_cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"anullsrc=r=24000:cl=mono",
            "-t", str(video_dur),
            "-acodec", "pcm_s16le",
            audio_output_path,
        ]
        subprocess.run(silent_audio_cmd, capture_output=True)
    else:
        _emit(logger, "debug", f"[{provider_name}] Audio track extracted.")

    # 2. Strip audio from video
    temp_silent_video = output_path + "_silent.mp4"
    strip_audio_cmd = [
        "ffmpeg", "-y",
        "-i", output_path,
        "-an",
        "-vcodec", "copy",
        temp_silent_video,
    ]
    _emit(logger, "debug", f"[{provider_name}] Stripping audio from video...")
    subprocess.run(strip_audio_cmd, capture_output=True)

    if os.path.exists(temp_silent_video):
        try:
            os.remove(output_path)
            os.rename(temp_silent_video, output_path)
        except Exception as strip_err:
            _emit(logger, "warning", f"[{provider_name}] Error replacing with silent video",
                  extra={"error": str(strip_err)})
            try:
                import shutil
                shutil.copy2(temp_silent_video, output_path)
                os.remove(temp_silent_video)
            except Exception as cp_err:
                _emit(logger, "warning", f"[{provider_name}] Error copying silent video",
                      extra={"error": str(cp_err)})

    # 3. Extract first frame → replace original reference image
    ext = ".png"
    if image_path.lower().endswith((".jpg", ".jpeg")):
        ext = ".jpg"
    temp_first_frame = output_path + "_first_frame" + ext
    extract_frame_cmd = [
        "ffmpeg", "-y",
        "-i", output_path,
        "-vframes", "1",
        "-f", "image2",
        temp_first_frame,
    ]
    _emit(logger, "debug", f"[{provider_name}] Extracting first frame...")
    subprocess.run(extract_frame_cmd, capture_output=True)

    if os.path.exists(temp_first_frame):
        if os.path.exists(image_path):
            try:
                os.remove(image_path)
            except Exception as e:
                _emit(logger, "warning", f"[{provider_name}] Error removing original image",
                      extra={"path": image_path, "error": str(e)})
        try:
            os.rename(temp_first_frame, image_path)
            _emit(logger, "info", f"[{provider_name}] Replaced reference image with first frame")
        except Exception as e:
            _emit(logger, "warning", f"[{provider_name}] Error replacing image with first frame",
                  extra={"error": str(e)})
            try:
                import shutil
                shutil.copy2(temp_first_frame, image_path)
                os.remove(temp_first_frame)
            except Exception as cp_err:
                _emit(logger, "warning", f"[{provider_name}] Error copying first frame",
                      extra={"error": str(cp_err)})

    return output_path
