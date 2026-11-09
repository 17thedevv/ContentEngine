import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .domain import EngineError

LOCAL_INPUT = ("-protocol_whitelist", "file")
VIDEO_INPUT = (*LOCAL_INPUT, "-format_whitelist", "mov,matroska,webm,avi,mpegts")


def find_tool(root: Path, name: str) -> str:
    override = os.environ.get(f"CONTENT_ENGINE_{name.upper()}")
    executable = name + (".exe" if os.name == "nt" else "")
    if override:
        path = shutil.which(override) or (Path(override).resolve() if Path(override).is_file() else None)
        if path is not None:
            return str(Path(path).resolve())
        raise EngineError(f"CONTENT_ENGINE_{name.upper()} does not resolve to an executable")
    bundled = sorted((root / ".tools").glob(f"ffmpeg-*/bin/{executable}"), reverse=True)
    if bundled:
        return str(bundled[0])
    path = shutil.which(name)
    if path:
        return str(Path(path).resolve())
    raise EngineError(f"{name} is required. Install FFmpeg or set CONTENT_ENGINE_{name.upper()}.")


def invoke(executable: str, args: list[str], cwd: Path | None = None) -> str:
    try:
        result = subprocess.run(
            [executable, *args],
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip()[-3000:] or f"exit code {exc.returncode}"
        raise EngineError(f"{Path(executable).stem} failed: {detail}") from exc
    except OSError as exc:
        raise EngineError(f"Cannot run {executable}: {exc}") from exc
    return result.stdout


@dataclass(frozen=True)
class Toolchain:
    ffmpeg: str
    ffprobe: str

    @classmethod
    def locate(cls, root: Path) -> "Toolchain":
        return cls(find_tool(root, "ffmpeg"), find_tool(root, "ffprobe"))

    def probe(self, path: Path, *, asset: bool = False) -> dict[str, Any]:
        options = VIDEO_INPUT if asset else LOCAL_INPUT
        output = invoke(self.ffprobe, [
            "-v", "error", *options, "-show_streams", "-show_format", "-of", "json", str(path),
        ])
        try:
            return json.loads(output)
        except ValueError as exc:
            raise EngineError(f"FFprobe returned invalid JSON for {path.name}") from exc

    def encode(self, args: list[str], cwd: Path | None = None) -> None:
        invoke(self.ffmpeg, ["-hide_banner", "-loglevel", "error", "-nostdin", "-xerror", *args], cwd)
