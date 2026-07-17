"""
Test case for the subtitle refactor: SRT-only export + per-scene SRT merge.

Verifies:
  1. burn_subtitles_to_video_tool_fn exports SRT (not VTT) and copies video as-is.
  2. merge_srt_files_tool_fn produces a correctly offset, sequentially-numbered
     combined SRT file.

Usage:
    cd genai-pipeline
    python test_scripts/test_subtitle_refactor.py
"""

import os
import sys
import json
import subprocess
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.video_subtitle import (
    burn_subtitles_to_video_tool_fn,
    merge_srt_files_tool_fn,
)
from tools.utils import set_output_dir, get_media_duration


# ── Helpers ──────────────────────────────────────────────────────────────

def _generate_test_video(output_path: str, duration: float = 3.0,
                         width: int = 320, height: int = 240, fps: int = 10) -> str:
    """Generate a simple solid-color video via FFmpeg."""
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c=gray:s={width}x{height}:d={duration}:r={fps}",
        "-pix_fmt", "yuv420p",
        output_path,
    ], capture_output=True, text=True, check=True)
    return output_path


def _make_subtitles_json(output_path: str, subtitles: list) -> str:
    """Write a minimal subtitles JSON file."""
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({"subtitles": subtitles}, f, ensure_ascii=False, indent=2)
    return output_path


def _file_exists(path: str) -> bool:
    return os.path.isfile(path) and os.path.getsize(path) > 0


def _read_srt(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _parse_srt_timestamps(srt_text: str) -> list:
    """Extract (start_sec, end_sec, text_lines...) tuples from SRT text."""
    import re
    pattern = re.compile(
        r'(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})'
    )
    entries = []
    lines = srt_text.strip().split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        ts_match = pattern.match(line) if line else None
        if ts_match:
            def _to_s(g):
                return int(g[0]) * 3600 + int(g[1]) * 60 + int(g[2]) + int(g[3]) / 1000.0
            start = _to_s(ts_match.groups()[:4])
            end = _to_s(ts_match.groups()[4:])
            i += 1
            text_lines = []
            while i < len(lines) and lines[i].strip() and not pattern.match(lines[i].strip()):
                text_lines.append(lines[i].strip())
                i += 1
            entries.append((start, end, text_lines))
        else:
            i += 1
    return entries


# ── Tests ────────────────────────────────────────────────────────────────

PASS = 0
FAIL = 0


def _check(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ FAIL: {name}  -- {detail}")


def test_burn_subtitles_export_srt_only(output_dir: str):
    """burn_subtitles_to_video_tool_fn: exports SRT, no VTT, copies video."""
    print("\n" + "=" * 60)
    print("TEST 1: burn_subtitles_to_video_tool_fn — SRT export, no VTT, video copy")
    print("=" * 60)

    video_path = os.path.join(output_dir, "test1_input.mp4")
    subs_json = os.path.join(output_dir, "test1_subs.json")
    output_video = os.path.join(output_dir, "test1_output.mp4")

    _generate_test_video(video_path, duration=5.0)

    su = {"subtitles": [
        {"start": 0.0, "end": 2.5, "text": "第一段中文测试字幕。"},
        {"start": 2.5, "end": 5.0, "text": "第二段中文字幕内容。"},
    ]}

    # Sanity-check JSON keys
    _check("JSON uses 'subtitles' key",
           "subtitles" in su,
           f"keys present: {list(su.keys())}")

    _make_subtitles_json(subs_json, su["subtitles"])

    # ── Run ──
    result = burn_subtitles_to_video_tool_fn(video_path, subs_json, output_video)

    _check("returns a valid path (not error string)",
           not result.startswith("Error"),
           result)

    _check("output video file exists", _file_exists(output_video))

    # Video should be copied (not re-encoded by ffmpeg subtitles filter)
    _check("output video is a copy of input (same file size, ffmpeg not involved)",
           os.path.getsize(output_video) == os.path.getsize(video_path),
           f"in={os.path.getsize(video_path)} out={os.path.getsize(output_video)}")

    # ── SRT sidecar ──
    srt_path = os.path.join(output_dir, "test1_output.srt")
    _check("SRT sidecar file exists", _file_exists(srt_path))

    if _file_exists(srt_path):
        srt_content = _read_srt(srt_path)
        _check("SRT contains Chinese text from scene 1",
               "第一段中文测试字幕" in srt_content)
        _check("SRT contains Chinese text from scene 2",
               "第二段中文字幕内容" in srt_content)

    # ── VTT must NOT exist ──
    vtt_path = os.path.join(output_dir, "test1_output.vtt")
    _check("VTT file is NOT generated",
           not os.path.exists(vtt_path),
           f"found at {vtt_path}")


def test_merge_srt_basic(output_dir: str):
    """merge_srt_files_tool_fn: basic offset + sequential numbering."""
    print("\n" + "=" * 60)
    print("TEST 2: merge_srt_files_tool_fn — timestamp offset + sequential numbering")
    print("=" * 60)

    # Build 3 scene SRTs manually
    scene1_srt = os.path.join(output_dir, "test2_scene1.srt")
    scene2_srt = os.path.join(output_dir, "test2_scene2.srt")
    scene3_srt = os.path.join(output_dir, "test2_scene3.srt")
    merged_srt = os.path.join(output_dir, "test2_merged.srt")

    # Scene 1: 10s, two subs
    with open(scene1_srt, "w", encoding="utf-8") as f:
        f.write("1\n00:00:00,000 --> 00:00:03,000\n场景一第一句。\n\n"
                "2\n00:00:04,000 --> 00:00:09,000\n场景一第二句。\n\n")

    # Scene 2: 15s, one sub
    with open(scene2_srt, "w", encoding="utf-8") as f:
        f.write("1\n00:00:01,000 --> 00:00:05,000\n场景二第一句。\n\n")

    # Scene 3: 8s, two subs
    with open(scene3_srt, "w", encoding="utf-8") as f:
        f.write("1\n00:00:00,500 --> 00:00:04,000\n场景三第一句。\n\n"
                "2\n00:00:05,000 --> 00:00:07,500\n场景三第二句。\n\n")

    durations = [10.0, 15.0, 8.0]

    # ── Run ──
    result = merge_srt_files_tool_fn(
        [scene1_srt, scene2_srt, scene3_srt],
        durations,
        merged_srt,
    )

    _check("returns valid path", not result.startswith("Error"), result)
    _check("merged SRT file exists", _file_exists(merged_srt))

    if not _file_exists(merged_srt):
        print("  ⚠ Cannot continue — no merged SRT to inspect.")
        return

    merged_text = _read_srt(merged_srt)
    entries = _parse_srt_timestamps(merged_text)

    _check("total 5 subtitle entries", len(entries) == 5, f"got {len(entries)}")

    if len(entries) >= 5:
        # Scene 1, sub 1: offset 0, same as original
        _check("entry 1 start = 0.0s (scene 1, offset 0)",
               abs(entries[0][0] - 0.0) < 0.01,
               f"got {entries[0][0]:.3f}")

        # Scene 1, sub 2: offset 0
        _check("entry 2 start = 4.0s (scene 1, offset 0)",
               abs(entries[1][0] - 4.0) < 0.01,
               f"got {entries[1][0]:.3f}")

        # Scene 2, sub 1: offset = 10s → original 1s + 10 = 11s
        _check("entry 3 start ≈ 11.0s (scene 2, offset +10)",
               abs(entries[2][0] - 11.0) < 0.01,
               f"got {entries[2][0]:.3f}")

        # Scene 3, sub 1: offset = 10+15 = 25s → original 0.5s + 25 = 25.5s
        _check("entry 4 start ≈ 25.5s (scene 3, offset +25)",
               abs(entries[3][0] - 25.5) < 0.01,
               f"got {entries[3][0]:.3f}")

        # Scene 3, sub 2: offset = 25s → original 5s + 25 = 30s
        _check("entry 5 start ≈ 30.0s (scene 3, offset +25)",
               abs(entries[4][0] - 30.0) < 0.01,
               f"got {entries[4][0]:.3f}")

    # ── Sequential numbering check ──
    import re
    indices = [int(m) for m in re.findall(r'^(\d+)$', merged_text, re.MULTILINE)]
    expected = [1, 2, 3, 4, 5]
    _check("subtitle indices are sequential 1→5", indices == expected,
           f"got {indices}")

    # ── Text content preserved ──
    _check("all scene 1 Chinese text present",
           "场景一第一句" in merged_text and "场景一第二句" in merged_text)
    _check("all scene 2 Chinese text present",
           "场景二第一句" in merged_text)
    _check("all scene 3 Chinese text present",
           "场景三第一句" in merged_text and "场景三第二句" in merged_text)


def test_merge_srt_edge_cases(output_dir: str):
    """merge_srt_files_tool_fn: error cases."""
    print("\n" + "=" * 60)
    print("TEST 3: merge_srt_files_tool_fn — edge cases")
    print("=" * 60)

    # ── Length mismatch ──
    bad_result = merge_srt_files_tool_fn(
        ["a.srt", "b.srt"], [10.0],  # 2 paths, 1 duration
        os.path.join(output_dir, "test3_nonexistent.srt"),
    )
    _check("returns error on length mismatch",
           bad_result.startswith("Error"),
           bad_result)

    # ── Missing SRT file gracefully skipped ──
    srt_ok = os.path.join(output_dir, "test3_real.srt")
    with open(srt_ok, "w", encoding="utf-8") as f:
        f.write("1\n00:00:01,000 --> 00:00:03,000\n存在的字幕。\n\n")

    srt_missing = os.path.join(output_dir, "test3_does_not_exist.srt")
    merged_missing = os.path.join(output_dir, "test3_skip_missing.srt")

    skip_result = merge_srt_files_tool_fn(
        [srt_ok, srt_missing],
        [5.0, 10.0],
        merged_missing,
    )

    _check("succeeds when one SRT is missing (graceful skip)",
           not skip_result.startswith("Error"),
           skip_result)
    _check("merged file still created", _file_exists(merged_missing))

    if _file_exists(merged_missing):
        content = _read_srt(merged_missing)
        # Second scene has 10s offset but missing file → offset still accumulates
        # The existing file has +0 offset (first scene)
        entries = _parse_srt_timestamps(content)
        _check("only 1 entry (missing scene skipped)",
               len(entries) == 1,
               f"got {len(entries)}")
        _check("entry has 0 offset (first scene, not affected by missing second)",
               abs(entries[0][0] - 1.0) < 0.01,
               f"got {entries[0][0]:.3f}")


def test_single_scene_merge(output_dir: str):
    """merge_srt_files_tool_fn: single scene (zero offset)."""
    print("\n" + "=" * 60)
    print("TEST 4: merge_srt_files_tool_fn — single scene (zero offset)")
    print("=" * 60)

    srt = os.path.join(output_dir, "test4_single.srt")
    with open(srt, "w", encoding="utf-8") as f:
        f.write("1\n00:00:00,000 --> 00:00:02,000\n单场景测试。\n\n"
                "2\n00:00:02,500 --> 00:00:05,000\n第二句测试。\n\n")

    merged = os.path.join(output_dir, "test4_merged.srt")
    result = merge_srt_files_tool_fn([srt], [5.0], merged)

    _check("returns valid path", not result.startswith("Error"), result)
    _check("merged SRT exists", _file_exists(merged))

    if _file_exists(merged):
        merged_text = _read_srt(merged)
        entries = _parse_srt_timestamps(merged_text)
        _check("2 entries", len(entries) == 2, f"got {len(entries)}")
        if len(entries) >= 2:
            _check("entry 1 start unchanged (0s offset)",
                   abs(entries[0][0] - 0.0) < 0.01)
            _check("entry 2 start unchanged (0s offset)",
                   abs(entries[1][0] - 2.5) < 0.01)


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    # Setup
    output_dir = os.path.join(os.path.dirname(__file__), "..", "test_output")
    output_dir = os.path.abspath(output_dir)
    set_output_dir(output_dir)
    os.makedirs(output_dir, exist_ok=True)
    print(f"Output directory: {output_dir}")

    test_burn_subtitles_export_srt_only(output_dir)
    test_merge_srt_basic(output_dir)
    test_merge_srt_edge_cases(output_dir)
    test_single_scene_merge(output_dir)

    # ── Final report ──
    print("\n" + "=" * 60)
    total = PASS + FAIL
    print(f"RESULTS: {PASS}/{total} passed", end="")
    if FAIL > 0:
        print(f", {FAIL} FAILED")
    else:
        print(" — ALL GOOD ✓")

    print(f"\nArtifacts in: {output_dir}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
