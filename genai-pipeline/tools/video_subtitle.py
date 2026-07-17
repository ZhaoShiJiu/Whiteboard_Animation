import os
import json
import re
import shutil
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
    Export SRT subtitle sidecar file and copy video as-is.

    Subtitles are exported as an SRT sidecar file alongside the video.
    The video itself is copied unchanged (no subtitle burn-in).

    Args:
        video_path: Path to the input video file.
        subtitles_json_path: Path to the JSON subtitle file (from TTS native subtitles).
        output_path: Optional path for the output video.
        logger: Optional ContextLogger for structured logging.

    Returns:
        Path to the copied video file (with SRT sidecar alongside it).
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

        _emit(logger, "info", f"Exporting SRT for {len(subtitles)} subtitle segments...")

        # Decide output base name for subtitle sidecar files
        if not output_path:
            base, ext = os.path.splitext(video_path)
            output_path = f"{base}_with_subtitles{ext}"
        base_no_ext, _ = os.path.splitext(output_path)

        # Format timestamps: HH:MM:SS,mmm (SRT)
        def _format_srt_ts(seconds):
            hrs = int(seconds // 3600)
            mins = int((seconds % 3600) // 60)
            secs = int(seconds % 60)
            milli = int((seconds * 1000) % 1000)
            return f"{hrs:02d}:{mins:02d}:{secs:02d},{milli:03d}"

        # 1. Write persistent SRT sidecar file
        srt_path = f"{base_no_ext}.srt"
        with open(srt_path, 'w', encoding='utf-8') as f:
            for i, sub in enumerate(subtitles):
                f.write(f"{i+1}\n")
                f.write(f"{_format_srt_ts(sub['start'])} --> {_format_srt_ts(sub['end'])}\n")
                f.write(f"{sub['text']}\n\n")
        _emit(logger, "info", "SRT subtitles exported", extra={"path": srt_path})

        # 2. Copy video as-is (subtitles provided as SRT sidecar only)
        shutil.copy2(video_path, output_path)
        _emit(logger, "info", "Video copied (SRT sidecar exported)", extra={"path": output_path})
        return output_path

    except Exception as e:
        _emit(logger, "error", f"Subtitle export error: {e}", extra={"error": str(e)})
        return f"Error in burn_subtitles_to_video_tool: {str(e)}"


def merge_srt_files_tool_fn(
    srt_paths: list,
    video_durations: list,
    output_path: str,
    logger: Optional["ContextLogger"] = None,
) -> str:
    """
    Merge multiple per-scene SRT files into one combined SRT,
    offsetting timestamps by cumulative video durations.

    Args:
        srt_paths: Ordered list of per-scene SRT file paths.
        video_durations: Corresponding video durations in seconds (same order as srt_paths).
        output_path: Path for the merged SRT file.
        logger: Optional ContextLogger.

    Returns:
        Path to the merged SRT file, or an error string.
    """
    if len(srt_paths) != len(video_durations):
        return f"Error: srt_paths ({len(srt_paths)}) and video_durations ({len(video_durations)}) must have same length."

    # Pattern: HH:MM:SS,mmm --> HH:MM:SS,mmm
    ts_pattern = re.compile(
        r'(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})'
    )

    def _ts_to_seconds(h, m, s, ms):
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0

    def _seconds_to_srt_ts(seconds):
        hrs = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        milli = int((seconds * 1000) % 1000)
        return f"{hrs:02d}:{mins:02d}:{secs:02d},{milli:03d}"

    merged_lines = []
    cumulative_offset = 0.0
    global_seq = 1

    for i, (srt_path, dur) in enumerate(zip(srt_paths, video_durations)):
        if not os.path.exists(srt_path):
            _emit(logger, "warning", "SRT file not found, skipping",
                  extra={"path": srt_path})
            cumulative_offset += dur
            continue

        with open(srt_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Process each line: renumber indices, offset timestamps
        lines = content.strip().split('\n')
        new_lines = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            ts_match = ts_pattern.match(line)
            if ts_match:
                # Insert sequential index before timestamp
                new_lines.append(str(global_seq))
                global_seq += 1
                # Offset the timestamp
                start_s = _ts_to_seconds(
                    ts_match.group(1), ts_match.group(2),
                    ts_match.group(3), ts_match.group(4))
                end_s = _ts_to_seconds(
                    ts_match.group(5), ts_match.group(6),
                    ts_match.group(7), ts_match.group(8))
                start_s += cumulative_offset
                end_s += cumulative_offset
                new_lines.append(
                    f"{_seconds_to_srt_ts(start_s)} --> {_seconds_to_srt_ts(end_s)}")
            elif line.isdigit():
                # Skip original index lines — already renumbered above
                continue
            else:
                new_lines.append(line)

        merged_lines.extend(new_lines)
        cumulative_offset += dur

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(merged_lines) + '\n')

    _emit(logger, "info", "Merged SRT created",
          extra={"path": output_path, "scene_count": len(srt_paths)})
    return output_path
