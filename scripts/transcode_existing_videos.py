from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.video_transcoding_service import SKIP_DIR_NAMES, VideoTranscodingService


logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill exercise videos into Android-safe MP4 files.")
    parser.add_argument("--root", default="upload_exercises", help="Root directory with exercise videos")
    parser.add_argument("--force", action="store_true", help="Transcode even if video already looks safe")
    parser.add_argument("--dry-run", action="store_true", help="Show planned actions without changing files")
    return parser.parse_args()


def is_temporary_mp4(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith(".tmp.mp4") or name.endswith("_tmp.mp4")


def iter_mp4_files(root: Path):
    for current_root, dir_names, file_names in os.walk(root):
        dir_names[:] = [name for name in dir_names if name not in SKIP_DIR_NAMES]
        current_path = Path(current_root)

        for file_name in file_names:
            path = current_path / file_name
            if path.suffix.lower() != ".mp4":
                continue
            if is_temporary_mp4(path):
                continue
            try:
                path.resolve().relative_to(root)
            except ValueError:
                logger.warning("Skipping path outside root: %s", path)
                continue
            yield path


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()

    if not root.exists() or not root.is_dir():
        logger.error("Root directory does not exist or is not a directory: %s", root)
        return 1

    service = VideoTranscodingService()

    found = 0
    skipped = 0
    transcoded = 0
    failed = 0
    errors: list[str] = []

    ffprobe_available = service.is_ffprobe_available()

    if not args.dry_run and not service.is_ffmpeg_available():
        logger.error("ffmpeg is not installed or not found in PATH")
        return 1

    if not ffprobe_available:
        logger.warning("ffprobe is not installed or not found in PATH; safe-check will be skipped")

    for video_path in iter_mp4_files(root):
        found += 1
        relative_path = video_path.relative_to(root)

        should_skip = False
        if not args.force and ffprobe_available:
            try:
                should_skip = service.is_android_safe_video(video_path)
            except Exception as exc:
                logger.warning("Safe-check failed for %s: %s", relative_path, exc)

        if should_skip:
            skipped += 1
            logger.info("Skipping already safe video: %s", relative_path)
            continue

        if args.dry_run:
            logger.info("Would transcode: %s", relative_path)
            continue

        try:
            if service.replace_video_with_safe_version(video_path, force=args.force):
                transcoded += 1
            else:
                failed += 1
                errors.append(f"{relative_path}: transcoding returned false")
        except Exception as exc:
            failed += 1
            errors.append(f"{relative_path}: {exc}")
            logger.exception("Failed to transcode %s", relative_path)

    logger.info("Summary: found=%s skipped=%s transcoded=%s failed=%s", found, skipped, transcoded, failed)
    if errors:
        logger.error("Errors:")
        for item in errors:
            logger.error(" - %s", item)

    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
