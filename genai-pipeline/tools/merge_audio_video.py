import subprocess
import json
import os
from typing import Optional

from .utils import _emit

try:
    from log_utils import ContextLogger
except ImportError:
    ContextLogger = None  # type: ignore


def get_duration(file_path):
    """Get duration of a file in seconds using ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        file_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        return float(data['format']['duration'])
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"ffprobe failed: {e.stderr}")
    except (KeyError, json.JSONDecodeError) as e:
        raise RuntimeError(f"Failed to parse ffprobe output for {file_path}: {e}")


def merge_audio_video_tool_fn(
    video_path: str,
    audio_path: str,
    output_path: str = "output.mp4",
    logger: Optional["ContextLogger"] = None,
) -> str:
    """
    Merges audio and video files. If audio is longer than video, the last frame of the video
    is frozen to match the audio duration.

    Args:
        video_path: Path to the input video file.
        audio_path: Path to the input audio file.
        output_path: Path for the merged output file.
        logger: Optional ContextLogger for structured logging.

    Returns:
        Path to the output file if successful, or error message.
    """
    if not os.path.exists(video_path):
        return f"Error: Video file not found at {video_path}"
    if not os.path.exists(audio_path):
        return f"Error: Audio file not found at {audio_path}"

    try:
        video_dur = get_duration(video_path)
        audio_dur = get_duration(audio_path)

        _emit(logger, "info", "Merging audio and video",
              extra={"video_duration_s": round(video_dur, 2), "audio_duration_s": round(audio_dur, 2)})

        cmd = [
            "ffmpeg",
            "-y",  # Overwrite output if exists
            "-i", video_path,
            "-i", audio_path,
        ]

        if audio_dur > video_dur:
            # Pad the video at the end by cloning the last frame
            pad_seconds = audio_dur - video_dur
            _emit(logger, "debug", f"Audio longer than video — freezing last frame",
                  extra={"pad_seconds": round(pad_seconds, 2)})
            # Using tpad filter to clone the last frame
            filter_complex = f"[0:v]tpad=stop_mode=clone:stop_duration={pad_seconds}[v]"
            cmd.extend([
                "-filter_complex", filter_complex,
                "-map", "[v]",
                "-map", "1:a",
                "-pix_fmt", "yuv420p"  # Ensure compatibility
            ])
        else:
            # Video is longer or equal, just map them
            cmd.extend([
                "-map", "0:v",
                "-map", "1:a",
                "-pix_fmt", "yuv420p"
            ])

        cmd.append(output_path)

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            _emit(logger, "error", "FFmpeg merge failed",
                  extra={"stderr": result.stderr[:300], "return_code": result.returncode})
            return f"Error during ffmpeg execution: {result.stderr}"
        else:
            _emit(logger, "info", "Audio-Video merge successful", extra={"path": output_path})
            return output_path

    except Exception as e:
        _emit(logger, "error", f"Audio-Video merge error: {e}", extra={"error": str(e)})
        return f"Error in merge_audio_video_tool: {str(e)}"
