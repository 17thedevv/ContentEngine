from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .domain import Blocked, Channel, mapping


@dataclass(frozen=True)
class ChannelProfile:
    channel: Channel
    document: dict[str, Any]


def load_channels(root: Path) -> dict[str, ChannelProfile]:
    directory = root / "configs" / "channels"
    if not directory.is_dir():
        raise Blocked(f"Channel configuration directory is missing: {directory}")
    profiles: dict[str, ChannelProfile] = {}
    for path in sorted(directory.glob("*.yaml")):
        try:
            document = mapping(yaml.safe_load(path.read_text(encoding="utf-8-sig")), "channel")
            channel = Channel.parse(document)
        except (OSError, UnicodeError, yaml.YAMLError, Blocked) as exc:
            raise Blocked(f"Invalid channel file {path.name}: {exc}") from exc
        if channel.id in profiles:
            raise Blocked(f"Duplicate channel id: {channel.id}")
        profiles[channel.id] = ChannelProfile(channel, document)
    return profiles
