import json
import os
import sqlite3
import tempfile
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from .config import ChannelProfile
from .domain import Blocked, Channel, identifier, string


class State(StrEnum):
    CREATED = "CREATED"
    RENDERING = "RENDERING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


def write_json(path: Path, value: object) -> None:
    file = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
    )
    temporary = Path(file.name)
    try:
        with file:
            json.dump(value, file, ensure_ascii=False, indent=2, allow_nan=False)
            file.write("\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def project_lock(directory: Path) -> Iterator[None]:
    # The OS releases the lock when a crashed process closes its handles.
    with (directory / ".production.lock").open("a+b") as lock:
        try:
            if os.name == "nt":
                import msvcrt

                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise Blocked("Another production command is running for this project") from exc
        yield


@dataclass(frozen=True)
class Project:
    id: str
    topic: str
    channel: Channel
    directory: Path
    state: State
    message: str | None
    updated_at: str
    run_id: str | None
    input_digest: str | None
    output_hashes: dict[str, str] | None

    @property
    def run_directory(self) -> Path:
        if self.run_id is None:
            raise Blocked("This project has no completed render")
        return self.directory / "runs" / self.run_id


class ProjectStore:
    def __init__(self, root: Path) -> None:
        self.data = root / "data"
        self.data.mkdir(parents=True, exist_ok=True)
        self.database = self.data / "content_engine.db"
        with closing(self.connect()) as db, db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    manifest TEXT NOT NULL,
                    state TEXT NOT NULL,
                    message TEXT,
                    updated_at TEXT NOT NULL,
                    run_id TEXT,
                    input_digest TEXT,
                    output_hashes TEXT
                )
            """)

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database)
        db.row_factory = sqlite3.Row
        return db

    def create(self, profile: ChannelProfile, topic: str) -> Project:
        topic = string(topic, "topic")
        project_id = f"{profile.channel.id}_{uuid4().hex[:12]}"
        directory = self.data / "projects" / project_id
        directory.mkdir(parents=True)
        for name in ("media", "audio", "runs"):
            (directory / name).mkdir()
        timestamp = datetime.now(UTC).isoformat()
        manifest = {
            "schema_version": 1,
            "project_id": project_id,
            "topic": topic,
            "channel": profile.document,
            "created_at": timestamp,
        }
        # JSON remains the on-disk interchange format even for extra YAML profile fields.
        try:
            serialized = json.dumps(manifest, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise Blocked("Channel settings must be JSON-compatible") from exc
        write_json(directory / "project.json", manifest)
        write_json(directory / "assets.json", {"schema_version": 1, "assets": []})
        write_json(directory / "timeline.json", {
            "schema_version": 1,
            "project_id": project_id,
            "format": asdict(profile.channel.format),
            "scenes": [],
        })
        # Publish the project in SQLite only once all starter files exist.
        with closing(self.connect()) as db, db:
            db.execute(
                "INSERT INTO projects (id, manifest, state, updated_at) VALUES (?, ?, ?, ?)",
                (project_id, serialized, State.CREATED, timestamp),
            )
        return self.get(project_id)

    def get(self, project_id: str) -> Project:
        project_id = identifier(project_id, "project id")
        with closing(self.connect()) as db:
            row = db.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        if row is None:
            raise Blocked(f"Unknown project: {project_id}")
        manifest = json.loads(row["manifest"])
        return Project(
            project_id,
            manifest["topic"],
            Channel.parse(manifest["channel"]),
            self.data / "projects" / project_id,
            State(row["state"]),
            row["message"],
            row["updated_at"],
            row["run_id"],
            row["input_digest"],
            json.loads(row["output_hashes"]) if row["output_hashes"] is not None else None,
        )

    def set_state(self, project: Project, state: State, message: str | None = None) -> None:
        with closing(self.connect()) as db, db:
            db.execute(
                "UPDATE projects SET state = ?, message = ?, updated_at = ? WHERE id = ?",
                (state, message, datetime.now(UTC).isoformat(), project.id),
            )

    def finish(self, project: Project, run_id: str, digest: str, hashes: dict[str, str]) -> None:
        with closing(self.connect()) as db, db:
            db.execute("""
                UPDATE projects SET state = ?, message = NULL, updated_at = ?,
                    run_id = ?, input_digest = ?, output_hashes = ?
                WHERE id = ?
            """, (State.AWAITING_APPROVAL, datetime.now(UTC).isoformat(),
                  run_id, digest, json.dumps(hashes), project.id))
