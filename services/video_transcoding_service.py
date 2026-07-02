import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional


logger = logging.getLogger(__name__)

ANDROID_SAFE_WIDTH = 1080
ANDROID_SAFE_HEIGHT = 1920
ANDROID_SAFE_PIX_FMT = "yuv420p"
ANDROID_SAFE_CODECS = {"h264"}
ANDROID_SAFE_PROFILES = {"Main", "Baseline"}
SKIP_DIR_NAMES = {"original", "backup", "backups", "transcoded", "__pycache__"}


class VideoTranscodingService:
    def __init__(self, ffmpeg_binary: str = "ffmpeg", ffprobe_binary: str = "ffprobe"):
        self.ffmpeg_binary = ffmpeg_binary
        self.ffprobe_binary = ffprobe_binary
        self._ffprobe_warning_logged = False

    def is_ffmpeg_available(self) -> bool:
        return shutil.which(self.ffmpeg_binary) is not None

    def is_ffprobe_available(self) -> bool:
        return shutil.which(self.ffprobe_binary) is not None

    def is_android_safe_video(self, path: Path) -> bool:
        path = Path(path)
        if path.suffix.lower() != ".mp4" or not path.is_file():
            return False

        probe = self._probe_video(path)
        if not probe:
            return False

        codec_name = (probe.get("codec_name") or "").lower()
        width = probe.get("width")
        height = probe.get("height")
        pix_fmt = probe.get("pix_fmt")
        profile = probe.get("profile")
        fps = self._parse_frame_rate(probe.get("avg_frame_rate")) or self._parse_frame_rate(
            probe.get("r_frame_rate")
        )

        return (
            codec_name in ANDROID_SAFE_CODECS
            and width == ANDROID_SAFE_WIDTH
            and height == ANDROID_SAFE_HEIGHT
            and pix_fmt == ANDROID_SAFE_PIX_FMT
            and profile in ANDROID_SAFE_PROFILES
            and fps is not None
            and abs(fps - 30.0) < 0.25
        )

    def transcode_to_android_safe(self, source_path: Path, output_path: Path) -> bool:
        source_path = Path(source_path)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            self.ffmpeg_binary,
            "-y",
            "-i",
            str(source_path),
            "-vf",
            "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30",
            "-c:v",
            "libx264",
            "-profile:v",
            "main",
            "-level:v",
            "4.0",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(output_path),
        ]

        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            return True
        except FileNotFoundError:
            logger.error("ffmpeg is not installed or not found in PATH")
            return False
        except subprocess.CalledProcessError as exc:
            logger.error(
                "ffmpeg failed for %s -> %s: %s",
                source_path,
                output_path,
                (exc.stderr or "").strip(),
            )
            return False

    def backup_original_if_needed(self, video_path: Path) -> Path:
        video_path = Path(video_path)
        original_dir = video_path.parent / "original"
        original_dir.mkdir(parents=True, exist_ok=True)
        backup_path = original_dir / video_path.name

        if not backup_path.exists():
            shutil.copy2(video_path, backup_path)
            logger.info("Created original backup: %s", backup_path)

        return backup_path

    def replace_video_with_safe_version(self, video_path: Path, force: bool = False) -> bool:
        video_path = Path(video_path)
        if video_path.suffix.lower() != ".mp4":
            logger.warning("Skipping non-mp4 file: %s", video_path)
            return False
        if self._is_temporary_video_file(video_path):
            logger.info("Skipping temporary video file: %s", video_path)
            return False
        if not video_path.is_file():
            logger.warning("Video file not found: %s", video_path)
            return False

        backup_path = self.backup_original_if_needed(video_path)

        if not force and self.is_ffprobe_available() and self.is_android_safe_video(video_path):
            logger.info("Video already Android-safe, skipping: %s", video_path)
            return False

        if not force and not self.is_ffprobe_available() and not self._ffprobe_warning_logged:
            logger.warning("ffprobe is not installed or not found in PATH; safe-check will be skipped")
            self._ffprobe_warning_logged = True

        tmp_path = self._build_temp_output_path(video_path)
        if tmp_path.exists():
            tmp_path.unlink()

        transcoded = self.transcode_to_android_safe(backup_path, tmp_path)
        if not transcoded:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            return False

        tmp_path.replace(video_path)
        logger.info("Replaced video with Android-safe version: %s", video_path)
        return True

    def _probe_video(self, path: Path) -> Optional[dict]:
        if not self.is_ffprobe_available():
            return None

        cmd = [
            self.ffprobe_binary,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height,pix_fmt,avg_frame_rate,r_frame_rate,profile",
            "-of",
            "json",
            str(path),
        ]
        try:
            completed = subprocess.run(cmd, check=True, capture_output=True, text=True)
        except FileNotFoundError:
            if not self._ffprobe_warning_logged:
                logger.warning("ffprobe is not installed or not found in PATH")
                self._ffprobe_warning_logged = True
            return None
        except subprocess.CalledProcessError as exc:
            logger.warning("ffprobe failed for %s: %s", path, (exc.stderr or "").strip())
            return None

        try:
            payload = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError:
            logger.warning("ffprobe returned invalid JSON for %s", path)
            return None

        streams = payload.get("streams") or []
        if not streams:
            return None
        return streams[0]

    @staticmethod
    def _parse_frame_rate(value: Optional[str]) -> Optional[float]:
        if not value:
            return None
        if "/" in value:
            numerator, denominator = value.split("/", 1)
            try:
                denominator_value = float(denominator)
                if denominator_value == 0:
                    return None
                return float(numerator) / denominator_value
            except (TypeError, ValueError):
                return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _build_temp_output_path(video_path: Path) -> Path:
        return video_path.with_name(f"{video_path.stem}.tmp{video_path.suffix}")

    @staticmethod
    def _is_temporary_video_file(path: Path) -> bool:
        lower_name = path.name.lower()
        return lower_name.endswith(".tmp.mp4") or lower_name.endswith("_tmp.mp4")
