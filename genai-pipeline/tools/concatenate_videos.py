import subprocess
import os
from typing import Optional

from .utils import _emit

try:
    from log_utils import ContextLogger
except ImportError:
    ContextLogger = None  # type: ignore


def concatenate_videos_tool_fn(
    video_paths: list,
    output_path: str = "concatenated_output.mp4",
    logger: Optional["ContextLogger"] = None,
) -> str:
    """
    Concatenates multiple video files (with audio) sequentially into a single video.

    Args:
        video_paths: A list of paths to video files to concatenate. Must have at least 2 videos.
        output_path: Path for the concatenated output file.
        logger: Optional ContextLogger for structured logging.

    Returns:
        Path to the output file if successful, or an error message.
    """
    if not isinstance(video_paths, list) or len(video_paths) < 2:
        return "Error: video_paths must be a list with at least 2 video file paths."

    for i, vp in enumerate(video_paths):
        if not os.path.exists(vp):
            return f"Error: Video file not found at index {i}: {vp}"

    n = len(video_paths)

    _emit(logger, "info", f"Concatenating {n} videos into final output...",
          extra={"input_count": n, "output_path": output_path})

    # Build ffmpeg command with filter_complex for concat
    cmd = ["ffmpeg", "-y"]

    # Add inputs
    for vp in video_paths:
        cmd.extend(["-i", vp])

    # Build filter_complex string
    filter_inputs = "".join([f"[{i}:v][{i}:a]" for i in range(n)])
    filter_complex = f"{filter_inputs}concat=n={n}:v=1:a=1[v][a]"

    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-map", "[a]",
        "-pix_fmt", "yuv420p",
        output_path
    ])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            _emit(logger, "error", "FFmpeg concatenation failed",
                  extra={"stderr": result.stderr[:300], "return_code": result.returncode})
            return f"Error during ffmpeg execution: {result.stderr}"
        else:
            _emit(logger, "info", f"Successfully concatenated {n} videos", extra={"path": output_path})
            return output_path
    except Exception as e:
        _emit(logger, "error", f"Video concatenation error: {e}", extra={"error": str(e)})
        return f"Error in concatenate_videos_tool: {str(e)}"
