import re
from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from typing import Any, Self
from urllib.parse import urlsplit


class EngineError(Exception):
    """A local tool or production step failed."""


class Blocked(EngineError):
    """Inputs need attention before production can proceed."""


def mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise Blocked(f"{label} must be an object")
    return value


def sequence(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise Blocked(f"{label} must be an array")
    return value


def string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Blocked(f"{label} must be non-empty text")
    return value.strip()


def identifier(value: object, label: str) -> str:
    result = string(value, label)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", result):
        raise Blocked(f"{label} must contain lowercase ASCII letters, digits, underscores or hyphens")
    return result


def integer(value: object, label: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise Blocked(f"{label} must be an integer from {low} to {high}")
    return value


def boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise Blocked(f"{label} must be true or false")
    return value


def seconds(value: object, label: str) -> Fraction:
    if type(value) not in (int, float):
        raise Blocked(f"{label} must be a non-negative number of seconds")
    try:
        result = Fraction(str(value))
    except (ValueError, ZeroDivisionError) as exc:
        raise Blocked(f"{label} must be finite") from exc
    if not 0 <= result <= 86400:
        raise Blocked(f"{label} must be between 0 and 86400 seconds")
    return result


@dataclass(frozen=True)
class VideoFormat:
    width: int
    height: int
    fps: int

    @classmethod
    def parse(cls, value: object) -> Self:
        data = mapping(value, "format")
        width = integer(data.get("width"), "format.width", 16, 4096)
        height = integer(data.get("height"), "format.height", 16, 4096)
        fps = integer(data.get("fps"), "format.fps", 1, 120)
        if width % 2 or height % 2:
            raise Blocked("H.264 output width and height must both be even")
        return cls(width, height, fps)


@dataclass(frozen=True)
class Channel:
    id: str
    name: str
    language: str
    format: VideoFormat
    target_duration: int

    @classmethod
    def parse(cls, value: object) -> Self:
        data = mapping(value, "channel")
        video = mapping(data.get("video"), "channel.video")
        resolution = sequence(video.get("resolution"), "channel.video.resolution")
        if len(resolution) != 2:
            raise Blocked("channel.video.resolution must contain width and height")
        fmt = VideoFormat.parse(dict(width=resolution[0], height=resolution[1], fps=video.get("fps", 30)))
        orientation = video.get("format")
        actual = "vertical" if fmt.height > fmt.width else "horizontal" if fmt.width > fmt.height else "square"
        if orientation != actual:
            raise Blocked(f"channel.video.format must be {actual} for this resolution")
        publishing = mapping(data.get("publishing", {}), "channel.publishing")
        if publishing.get("auto_publish", False) is not False:
            raise Blocked("channel.publishing.auto_publish must be false")
        channel_id = identifier(data.get("id"), "channel.id")
        if len(channel_id) > 60:
            raise Blocked("channel.id must be at most 60 characters")
        return cls(
            channel_id,
            string(data.get("name"), "channel.name"),
            string(data.get("language"), "channel.language"),
            fmt,
            integer(video.get("target_duration"), "channel.video.target_duration", 1, 3600),
        )


@dataclass(frozen=True)
class Asset:
    id: str
    local_path: str
    source: str
    source_url: str | None
    creator: str
    license_status: str
    license_name: str
    checked_at: str | None
    attribution: str | None

    @classmethod
    def parse(cls, value: object) -> Self:
        data = mapping(value, "asset")
        asset_id = identifier(data.get("asset_id"), "asset_id")
        label = f"asset {asset_id}"
        license_data = mapping(data.get("license"), f"{label}.license")
        source = identifier(data.get("source"), f"{label}.source")
        source_url = data.get("source_url")
        if source_url is not None or source not in {"original", "local"}:
            source_url = string(source_url, f"{label}.source_url")
            try:
                url = urlsplit(source_url)
                if url.scheme not in {"http", "https"} or not url.hostname:
                    raise ValueError
            except ValueError as exc:
                raise Blocked(f"{label}.source_url must be an HTTP(S) URL") from exc
        status = string(license_data.get("status"), f"{label}.license.status")
        if status not in {"verified", "unverified"}:
            raise Blocked(f"{label}.license.status must be verified or unverified")
        checked_at = license_data.get("checked_at")
        if status == "verified" or checked_at is not None:
            checked_at = string(checked_at, f"{label}.license.checked_at")
            try:
                if date.fromisoformat(checked_at).isoformat() != checked_at:
                    raise ValueError
            except ValueError as exc:
                raise Blocked(f"{label}.license.checked_at must use YYYY-MM-DD") from exc
        required = boolean(license_data.get("attribution_required"), f"{label}.license.attribution_required")
        attribution = license_data.get("attribution")
        if required or attribution is not None:
            attribution = string(attribution, f"{label}.license.attribution")
        return cls(
            asset_id,
            string(data.get("local_path"), f"{label}.local_path"),
            source,
            source_url,
            string(data.get("creator"), f"{label}.creator"),
            status,
            string(license_data.get("name"), f"{label}.license.name"),
            checked_at,
            attribution,
        )


@dataclass(frozen=True)
class Scene:
    id: str
    asset_id: str
    start: Fraction
    end: Fraction
    narration: str
    audio: str
    subtitle: bool

    @classmethod
    def parse(cls, value: object) -> Self:
        data = mapping(value, "scene")
        scene_id = identifier(data.get("id"), "scene.id")
        label = f"scene {scene_id}"
        start = seconds(data.get("source_start"), f"{label}.source_start")
        end = seconds(data.get("source_end"), f"{label}.source_end")
        if end <= start:
            raise Blocked(f"{label}: source_end must be greater than source_start")
        if data.get("transition", "cut") != "cut":
            raise Blocked(f"{label}: only cut transitions are supported")
        return cls(
            scene_id,
            identifier(data.get("asset_id"), f"{label}.asset_id"),
            start,
            end,
            string(data.get("narration"), f"{label}.narration"),
            string(data.get("narration_audio"), f"{label}.narration_audio"),
            boolean(data.get("subtitle", True), f"{label}.subtitle"),
        )
