import hashlib
import json
import wave
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path, PureWindowsPath
from typing import Any

from .domain import Asset, Blocked, EngineError, Scene, VideoFormat, integer, mapping, sequence
from .ffmpeg import Toolchain
from .storage import Project

# Change this when render or subtitle behavior changes to invalidate prior runs.
RENDER_VERSION = 1


def sha256(path: Path) -> str:
    try:
        with path.open("rb") as file:
            return hashlib.file_digest(file, "sha256").hexdigest()
    except OSError as exc:
        raise Blocked(f"Cannot read {path.name}: {exc}") from exc


def read_manifest(path: Path) -> tuple[dict[str, Any], str]:
    try:
        content = path.read_bytes()
        data = mapping(json.loads(content.decode("utf-8-sig")), path.name)
    except (OSError, ValueError) as exc:
        raise Blocked(f"Cannot read {path.name}: {exc}") from exc
    integer(data.get("schema_version", 1), f"{path.name}.schema_version", 1, 1)
    return data, hashlib.sha256(content).hexdigest()


def media_path(project: Project, category: str, relative: str) -> Path:
    local = Path(relative)
    windows = PureWindowsPath(relative)
    if local.is_absolute() or windows.anchor or ":" in relative or ".." in windows.parts or "\\" in relative:
        raise Blocked(f"Use a relative path under {category}/ for {relative}")
    try:
        path = (project.directory / local).resolve()
        if path.is_relative_to(project.directory.resolve() / category) and path.is_file():
            return path
    except (OSError, ValueError) as exc:
        raise Blocked(f"Invalid media path {relative}: {exc}") from exc
    raise Blocked(f"File missing or outside {category}/: {relative}")


@dataclass(frozen=True)
class SceneInput:
    scene: Scene
    asset: Asset
    video: Path
    audio: Path


@dataclass(frozen=True)
class Timeline:
    format: VideoFormat
    scenes: list[SceneInput]
    digest: str


def load_timeline(project: Project) -> Timeline:
    timeline, timeline_hash = read_manifest(project.directory / "timeline.json")
    registry, asset_hash = read_manifest(project.directory / "assets.json")
    if timeline.get("project_id") != project.id:
        raise Blocked("timeline.project_id must match this project")
    fmt = VideoFormat.parse(timeline.get("format"))
    if fmt != project.channel.format:
        raise Blocked("timeline.format must match the project's channel profile")
    assets: dict[str, Asset] = {}
    for entry in sequence(registry.get("assets"), "assets"):
        asset = Asset.parse(entry)
        if asset.id in assets:
            raise Blocked(f"Duplicate asset id: {asset.id}")
        assets[asset.id] = asset
    entries = sequence(timeline.get("scenes"), "scenes")
    if not entries:
        raise Blocked("Add at least one scene to timeline.json")
    inputs: list[SceneInput] = []
    scene_ids: set[str] = set()
    hashes: dict[str, str] = {}
    for entry in entries:
        scene = Scene.parse(entry)
        if scene.id in scene_ids:
            raise Blocked(f"Duplicate scene id: {scene.id}")
        scene_ids.add(scene.id)
        asset = assets.get(scene.asset_id)
        if asset is None:
            raise Blocked(f"Scene {scene.id} refers to unknown asset {scene.asset_id}")
        if asset.license_status != "verified":
            raise Blocked(f"Asset {asset.id} requires a verified license record")
        video = media_path(project, "media", asset.local_path)
        audio = media_path(project, "audio", scene.audio)
        if audio.suffix.lower() != ".wav":
            raise Blocked(f"Scene {scene.id} requires a PCM WAV narration")
        for path in (video, audio):
            key = path.relative_to(project.directory.resolve()).as_posix()
            if key not in hashes:
                hashes[key] = sha256(path)
        inputs.append(SceneInput(scene, asset, video, audio))
    fingerprint = json.dumps([RENDER_VERSION, asdict(fmt), timeline_hash, asset_hash, hashes], sort_keys=True)
    return Timeline(fmt, inputs, hashlib.sha256(fingerprint.encode()).hexdigest())


def stream_duration(stream: dict[str, Any]) -> Fraction | None:
    try:
        return int(stream["duration_ts"]) * Fraction(stream["time_base"])
    except (KeyError, ValueError, ZeroDivisionError):
        try:
            return Fraction(stream["duration"])
        except (KeyError, ValueError, ZeroDivisionError):
            return None


def audio_duration(path: Path) -> Fraction:
    try:
        with wave.open(str(path), "rb") as audio:
            frames = audio.getnframes()
            if frames > 0:
                audio.setpos(frames - 1)
                if len(audio.readframes(1)) == audio.getsampwidth() * audio.getnchannels():
                    return Fraction(frames, audio.getframerate())
    except (OSError, EOFError, wave.Error, ZeroDivisionError) as exc:
        raise Blocked(f"Invalid PCM WAV narration {path.name}: {exc}") from exc
    raise Blocked(f"Narration WAV {path.name} is empty or incomplete")


@dataclass(frozen=True)
class PlannedScene:
    source: SceneInput
    video_stream: int
    frames: int
    narration_duration: Fraction


@dataclass(frozen=True)
class RenderPlan:
    format: VideoFormat
    scenes: list[PlannedScene]

    @property
    def frames(self) -> int:
        return sum(scene.frames for scene in self.scenes)

    @property
    def duration(self) -> Fraction:
        return Fraction(self.frames, self.format.fps)


def plan_render(timeline: Timeline, tools: Toolchain) -> RenderPlan:
    videos: dict[Path, tuple[int, Fraction]] = {}
    audio_lengths: dict[Path, Fraction] = {}
    planned: list[PlannedScene] = []
    for item in timeline.scenes:
        scene = item.scene
        if item.video not in videos:
            try:
                metadata = tools.probe(item.video, asset=True)
            except EngineError as exc:
                raise Blocked(f"Cannot inspect asset {item.asset.id}: {exc}") from exc
            video = next((stream for stream in metadata.get("streams", [])
                          if stream.get("codec_type") == "video"
                          and not stream.get("disposition", {}).get("attached_pic")), None)
            if video is None:
                raise Blocked(f"Asset {item.asset.id} has no video stream")
            duration = stream_duration(video)
            if duration is None:
                # Matroska/WebM may only report a container duration.
                duration = stream_duration(metadata.get("format", {}))
            if duration is None or duration <= 0:
                raise Blocked(f"Asset {item.asset.id} has no usable video duration")
            videos[item.video] = (video["index"], duration)
        stream, duration = videos[item.video]
        if scene.end > duration:
            raise Blocked(f"Scene {scene.id} ends at {float(scene.end):g}s but the asset lasts {float(duration):g}s")
        frames = int((scene.end - scene.start) * timeline.format.fps)
        if frames == 0:
            raise Blocked(f"Scene {scene.id} must last at least one output frame")
        if item.audio not in audio_lengths:
            audio_lengths[item.audio] = audio_duration(item.audio)
        narration = audio_lengths[item.audio]
        available = Fraction(frames, timeline.format.fps)
        if narration > available:
            raise Blocked(
                f"Scene {scene.id} narration lasts {float(narration):.3f}s but the cut holds "
                f"{float(available):.3f}s. Extend the cut or supply shorter narration."
            )
        planned.append(PlannedScene(item, stream, frames, narration))
    return RenderPlan(timeline.format, planned)
