"""
Sync historical output directories into the runs database table.

Scans genai-pipeline/output/ for run_* directories, checks whether each has a
final video, extracts metadata from run.log when available, and creates the
corresponding Run record in the database.

Also fixes any runs stuck in "running" status that have no final video on disk.

Usage::

    python sync_historical_runs.py          # dry-run (print what would be done)
    python sync_historical_runs.py --apply  # actually write to the database
"""

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

# Ensure genai-pipeline is on the path so db_utils imports work
_here = Path(__file__).resolve().parent
sys.path.insert(0, str(_here))

from tools import db_utils


OUTPUT_DIR = _here / "output"


def find_run_directories() -> list[Path]:
    """Return all run_* directories in output/, sorted by name."""
    if not OUTPUT_DIR.exists():
        return []
    dirs = [d for d in OUTPUT_DIR.iterdir() if d.is_dir() and d.name.startswith("run_")]
    dirs.sort(key=lambda d: d.name)
    return dirs


def find_final_video(run_dir: Path) -> tuple[str | None, str | None]:
    """Look for a final video file in the run directory.

    Returns (video_name, full_path) or (None, None) if none found.
    Handles both naming conventions:
      - whiteboard-animation-ai_final_video.mp4 (current)
      - storyboard_final_video.mp4 (older)
    """
    # Try current naming convention first
    candidates = [
        "whiteboard-animation-ai_final_video.mp4",
        "storyboard_final_video.mp4",
    ]
    for name in candidates:
        path = run_dir / name
        if path.exists() and path.stat().st_size > 0:
            return name, str(path)
    # Also try glob as last resort
    for p in sorted(run_dir.glob("*_final_video.mp4")):
        if p.stat().st_size > 0:
            return p.name, str(p)
    return None, None


def extract_metadata_from_log(run_dir: Path) -> dict:
    """Try to extract context, language, and full run_id from run.log."""
    log_file = run_dir / "run.log"
    result = {"context": "", "language": "", "full_run_id": None}

    if not log_file.exists():
        return result

    try:
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg = entry.get("msg", "")
                if "pipeline started" in msg.lower() or msg == "Pipeline started":
                    extra = entry.get("extra", {})
                    result["context"] = extra.get("context", "") or ""
                    result["language"] = extra.get("language", "") or ""
                    result["full_run_id"] = extra.get("run_id") or entry.get("run_id")
                    # Only need the first "pipeline started" entry
                    break
    except Exception:
        pass

    return result


def scene_count_from_dir(run_dir: Path) -> int:
    """Count scene_*_final.mp4 files to estimate scene count."""
    return len(list(run_dir.glob("scene_*_final.mp4")))


def get_existing_run_ids() -> set[str]:
    """Return the set of run_ids already in the database."""
    existing = db_utils.list_runs(limit=10000)
    return {r["run_id"] for r in existing}


def sync(dry_run: bool = True) -> dict:
    """Main sync logic.

    Returns summary dict with counts.
    """
    existing_ids = get_existing_run_ids()
    run_dirs = find_run_directories()

    summary = {
        "total_dirs": len(run_dirs),
        "already_in_db": 0,
        "created_completed": 0,
        "created_failed": 0,
        "fixed_stuck": 0,
        "skipped": 0,
        "details": [],
    }

    # -- Phase 1: Fix runs stuck in "running" status --
    for rid in existing_ids:
        # Check if this run_id corresponds to an existing directory
        matching_dir = OUTPUT_DIR / rid
        if not matching_dir.exists():
            # Try without UUID suffix — strip last _XXXXXX part
            # e.g. "run_20260717_095123_762b5e" → "run_20260717_095123"
            parts = rid.rsplit("_", 1)
            if len(parts) == 2 and len(parts[1]) == 6:
                matching_dir = OUTPUT_DIR / parts[0]

        if matching_dir.exists():
            _, final_path = find_final_video(matching_dir)
        else:
            final_path = None

        # Get current status from DB
        runs_list = db_utils.list_runs(limit=10000)
        run_info = next((r for r in runs_list if r["run_id"] == rid), None)
        if run_info is None:
            continue

        if run_info.get("status") == "running":
            if final_path:
                # Has a final video on disk but status is "running" → mark completed
                print(f"  [FIX STUCK] {rid}: has final video, marking completed")
                if not dry_run:
                    db_utils.update_run(
                        rid,
                        status="completed",
                        final_video=final_path,
                        completed_at=datetime.datetime.utcnow(),
                    )
                summary["fixed_stuck"] += 1
            else:
                # No final video → mark failed
                print(f"  [FIX STUCK] {rid}: no final video, marking failed")
                if not dry_run:
                    db_utils.update_run(
                        rid,
                        status="failed",
                        error="Pipeline terminated without producing a final video.",
                        completed_at=datetime.datetime.utcnow(),
                    )
                summary["fixed_stuck"] += 1

    # -- Phase 2: Import directories not yet in DB --
    for run_dir in run_dirs:
        dir_name = run_dir.name

        # Check if this directory is already tracked (by dir name or by full_run_id)
        is_tracked = dir_name in existing_ids

        # Also check if any DB record's full_run_id matches
        if not is_tracked:
            for rid in existing_ids:
                if rid.startswith(dir_name):
                    is_tracked = True
                    break

        if is_tracked:
            summary["already_in_db"] += 1
            continue

        # Look for final video
        video_name, final_path = find_final_video(run_dir)
        meta = extract_metadata_from_log(run_dir)
        scene_cnt = scene_count_from_dir(run_dir)

        if final_path:
            status = "completed"
            summary["created_completed"] += 1
        else:
            status = "failed"
            summary["created_failed"] += 1

        detail = {
            "run_id": dir_name,
            "status": status,
            "video_name": video_name,
            "context_preview": (meta["context"] or "")[:80],
            "language": meta["language"],
            "scene_count": scene_cnt,
        }
        summary["details"].append(detail)

        icon = "[OK]" if status == "completed" else "[FAIL]"
        print(f"  {icon} [{status.upper()}] {dir_name}  video={video_name or 'N/A'}  "
              f"lang={meta['language'] or '?'}  scenes={scene_cnt}")

        if not dry_run:
            db_utils.create_run(
                run_id=dir_name,
                context=meta["context"] or "",
                language=meta["language"] or "english",
                output_dir=str(run_dir),
            )
            db_utils.update_run(
                dir_name,
                status=status,
                final_video=final_path or "",
                scene_count=scene_cnt if scene_cnt > 0 else None,
                completed_at=datetime.datetime.utcnow() if status == "completed" else None,
                error=None if status == "completed" else "No final video found in output directory.",
            )

    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync historical runs into the database")
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually write changes to the database (default is dry-run)",
    )
    args = parser.parse_args()

    dry_run = not args.apply
    if dry_run:
        print("=" * 60)
        print("  DRY RUN -- no changes will be written to the database")
        print("  Run with --apply to actually sync")
        print("=" * 60)
    else:
        print("=" * 60)
        print("  APPLYING CHANGES to the database")
        print("=" * 60)

    print()

    result = sync(dry_run=dry_run)

    print()
    print("-" * 60)
    print(f"  Summary:")
    print(f"    Total directories scanned:  {result['total_dirs']}")
    print(f"    Already in database:        {result['already_in_db']}")
    print(f"    Stuck runs fixed:           {result['fixed_stuck']}")
    print(f"    New -> completed:           {result['created_completed']}")
    print(f"    New -> failed:              {result['created_failed']}")
    total_after = result['already_in_db'] + result['created_completed'] + result['created_failed']
    print(f"    Total runs in DB after sync: {total_after}")
    print("-" * 60)

    if dry_run:
        print()
        print("  This was a dry run. Use --apply to write to the database.")
