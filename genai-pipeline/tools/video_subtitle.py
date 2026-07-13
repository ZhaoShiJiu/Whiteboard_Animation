import os
import json
import subprocess
from typing import Optional

from . import utils
from .utils import _emit

try:
    from log_utils import ContextLogger
except ImportError:
    ContextLogger = None  # type: ignore


def burn_subtitles_to_video_tool_fn(
    video_path: str,
    subtitles_json_path: str,
    output_path: str = None,
    logger: Optional["ContextLogger"] = None,
) -> str:
    """
    Burns subtitles into a video file using FFmpeg.

    Args:
        video_path: Path to the input video file.
        subtitles_json_path: Path to the JSON subtitle file (from TTS native subtitles).
        output_path: Optional path for the output video.
        logger: Optional ContextLogger for structured logging.

    Returns:
        Path to the video file with burned-in subtitles.
    """
    if not os.path.exists(video_path):
        return f"Error: Video file not found at {video_path}"
    if not os.path.exists(subtitles_json_path):
        return f"Error: Subtitle file not found at {subtitles_json_path}"

    try:
        # Load subtitles
        with open(subtitles_json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            subtitles = data.get("subtitles", [])

        if not subtitles:
            _emit(logger, "warning", "No subtitles found in JSON — copying original video.")
            return video_path

        _emit(logger, "info", f"Burning {len(subtitles)} subtitle segments into video...")

        # 1. Create a temporary SRT file
        srt_path = subtitles_json_path.replace(".json", ".srt")
        with open(srt_path, 'w', encoding='utf-8') as f:
            for i, sub in enumerate(subtitles):
                start = sub['start']
                end = sub['end']
                text = sub['text']

                # Format timestamps: HH:MM:SS,mmm
                def format_ts(seconds):
                    hrs = int(seconds // 3600)
                    mins = int((seconds % 3600) // 60)
                    secs = int(seconds % 60)
                    milli = int((seconds * 1000) % 1000)
                    return f"{hrs:02d}:{mins:02d}:{secs:02d},{milli:03d}"

                f.write(f"{i+1}\n")
                f.write(f"{format_ts(start)} --> {format_ts(end)}\n")
                f.write(f"{text}\n\n")

        # 2. Run FFmpeg to burn subtitles
        if not output_path:
            base, ext = os.path.splitext(video_path)
            output_path = f"{base}_with_subtitles{ext}"

        # Note: 'subtitles' filter requires the path to be escaped properly for FFmpeg
        # especially on Windows with backslashes.
        escaped_srt = srt_path.replace('\\', '/').replace(':', '\\:')

        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vf", f"subtitles='{escaped_srt}'",
            "-c:a", "copy",  # Keep original audio
            output_path
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            _emit(logger, "error", "FFmpeg subtitle burn failed",
                  extra={"stderr": result.stderr[:300], "return_code": result.returncode})
            return f"Error during FFmpeg subtitle burn: {result.stderr}"

        # Clean up SRT
        if os.path.exists(srt_path):
            os.remove(srt_path)

        _emit(logger, "info", "Subtitles burned successfully", extra={"path": output_path})
        return output_path

    except Exception as e:
        _emit(logger, "error", f"Subtitle burn error: {e}", extra={"error": str(e)})
        return f"Error in burn_subtitles_to_video_tool: {str(e)}"
