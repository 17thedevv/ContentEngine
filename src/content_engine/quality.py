from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any

from .domain import Blocked, EngineError
from .ffmpeg import LOCAL_INPUT, Toolchain
from .render import AUDIO_RATE
from .storage import Project
from .subtitles import make_ass
from .timeline import RenderPlan, sha256, stream_duration


def within(duration: Fraction | None, expected: Fraction, tolerance: Fraction) -> bool:
    return duration is not None and abs(duration - expected) <= tolerance


def inspect_output(
    project: Project, plan: RenderPlan, tools: Toolchain, directory: Path
) -> dict[str, Any]:
    final = directory / "final.mp4"
    errors: list[str] = []
    checks: dict[str, bool] = {}
    metadata: dict[str, Any] = {}
    try:
        metadata = tools.probe(final)
    except EngineError as exc:
        errors.append(str(exc))
    streams = metadata.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), {})
    fmt = plan.format
    video_length, audio_length = stream_duration(video), stream_duration(audio)
    checks["mp4"] = "mp4" in metadata.get("format", {}).get("format_name", "").split(",")
    checks["video"] = (
        video.get("codec_name") == "h264" and video.get("pix_fmt") == "yuv420p"
        and (video.get("width"), video.get("height")) == (fmt.width, fmt.height)
    )
    checks["frame_rate"] = video.get("avg_frame_rate") == f"{fmt.fps}/1"
    checks["frame_count"] = video.get("nb_frames") == str(plan.frames)
    checks["video_duration"] = within(video_length, plan.duration, Fraction(1, fmt.fps))
    checks["audio"] = (
        audio.get("codec_name") == "aac" and audio.get("sample_rate") == str(AUDIO_RATE)
        and audio.get("channels") == 2
    )
    checks["audio_duration"] = within(audio_length, plan.duration, Fraction(1024, AUDIO_RATE))
    try:
        checks["subtitles"] = (directory / "subtitles.ass").read_text(encoding="utf-8") == make_ass(plan)
    except (OSError, UnicodeError):
        checks["subtitles"] = False
    try:
        tools.encode([*LOCAL_INPUT, "-i", str(final), "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"])
        checks["decode"] = True
    except EngineError as exc:
        checks["decode"] = False
        errors.append(str(exc))
    assets: dict[str, Any] = {}
    for item in plan.scenes:
        asset = item.source.asset
        assets[asset.id] = {
            "source": asset.source,
            "source_url": asset.source_url,
            "creator": asset.creator,
            "license_name": asset.license_name,
            "license_status": asset.license_status,
            "checked_at": asset.checked_at,
            "attribution": asset.attribution,
        }
    target = project.channel.target_duration
    return {
        "schema_version": 1,
        "project_id": project.id,
        "checked_at": datetime.now(UTC).isoformat(),
        "passed": all(checks.values()),
        "manual_review_required": True,
        "checks": checks,
        "errors": errors,
        "video": {
            "codec": video.get("codec_name"), "width": video.get("width"), "height": video.get("height"),
            "fps": video.get("avg_frame_rate"), "frames": video.get("nb_frames"),
            "duration_seconds": float(video_length) if video_length is not None else None,
        },
        "audio": {
            "codec": audio.get("codec_name"), "channels": audio.get("channels"),
            "sample_rate": audio.get("sample_rate"),
            "duration_seconds": float(audio_length) if audio_length is not None else None,
        },
        "subtitle_events": sum(item.source.scene.subtitle for item in plan.scenes),
        "target_duration_seconds": target,
        "timeline_duration_seconds": float(plan.duration),
        "duration_delta_seconds": float(plan.duration - target),
        "assets": assets,
    }


def output_hashes(directory: Path) -> dict[str, str]:
    return {name: sha256(directory / name) for name in ("final.mp4", "subtitles.ass")}


def matches_output(project: Project) -> bool:
    try:
        return (project.run_directory / "qa_report.json").is_file() and (
            output_hashes(project.run_directory) == project.output_hashes
        )
    except Blocked:
        return False
