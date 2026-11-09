import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import uuid4

from .domain import Blocked, EngineError
from .ffmpeg import Toolchain
from .quality import inspect_output, matches_output, output_hashes
from .render import render_video
from .storage import Project, ProjectStore, State, project_lock, write_json
from .timeline import load_timeline, plan_render


def paths(project: Project) -> dict[str, str]:
    return {
        "project_id": project.id,
        "state": project.state,
        "final_mp4": str(project.run_directory / "final.mp4"),
        "subtitles_ass": str(project.run_directory / "subtitles.ass"),
        "qa_report": str(project.run_directory / "qa_report.json"),
    }


def current_output(project: Project) -> dict[str, str]:
    if project.state != State.AWAITING_APPROVAL:
        raise Blocked(f"Project is {project.state}. Run render to prepare an output.")
    if load_timeline(project).digest != project.input_digest or not matches_output(project):
        raise Blocked("Project inputs or output files changed. Run render for an up-to-date preview.")
    return paths(project)


def project_status(project: Project) -> dict[str, Any]:
    result: dict[str, Any] = {
        "project_id": project.id,
        "channel_id": project.channel.id,
        "topic": project.topic,
        "state": project.state,
        "directory": str(project.directory),
        "format": asdict(project.channel.format),
        "target_duration_seconds": project.channel.target_duration,
        "updated_at": project.updated_at,
        "message": project.message,
    }
    if project.state == State.AWAITING_APPROVAL:
        try:
            result["output"] = current_output(project)
        except Blocked as exc:
            result.update(state="NEEDS_RENDER", message=str(exc))
    return result


def validate(project: Project, root: Path) -> dict[str, Any]:
    timeline = load_timeline(project)
    plan = plan_render(timeline, Toolchain.locate(root))
    return {
        "project_id": project.id,
        "valid": True,
        "frames": plan.frames,
        "duration_seconds": float(plan.duration),
        "scenes": [
            {
                "scene_id": item.source.scene.id,
                "frames": item.frames,
                "narration_seconds": float(item.narration_duration),
            }
            for item in plan.scenes
        ],
    }


def render(store: ProjectStore, project_id: str, root: Path) -> dict[str, Any]:
    project = store.get(project_id)
    with project_lock(project.directory):
        project = store.get(project_id)
        try:
            timeline = load_timeline(project)
            if (
                project.state == State.AWAITING_APPROVAL
                and project.input_digest == timeline.digest
                and matches_output(project)
            ):
                return {**paths(project), "reused": True}
            tools = Toolchain.locate(root)
            plan = plan_render(timeline, tools)
            store.set_state(project, State.RENDERING)
            run_id = uuid4().hex
            with tempfile.TemporaryDirectory(prefix=".render-", dir=project.directory) as temporary:
                stage = Path(temporary) / "preview"
                stage.mkdir()
                render_video(plan, tools, stage)
                report = inspect_output(project, plan, tools, stage)
                if not report["passed"]:
                    failed = ", ".join(key for key, ok in report["checks"].items() if not ok)
                    details = " ".join(report["errors"])
                    raise EngineError(f"Output QA failed ({failed}). {details}".strip())
                if load_timeline(project).digest != timeline.digest:
                    raise Blocked("Project inputs changed during render. Run render again.")
                write_json(stage / "qa_report.json", report)
                hashes = output_hashes(stage)
                stage.rename(project.directory / "runs" / run_id)
            store.finish(project, run_id, timeline.digest, hashes)
        except Blocked as exc:
            store.set_state(project, State.BLOCKED, str(exc))
            raise
        except (EngineError, OSError) as exc:
            store.set_state(project, State.FAILED, str(exc))
            raise
        except KeyboardInterrupt:
            store.set_state(project, State.CANCELLED, "Render interrupted. Run render to retry.")
            raise
        return {**paths(store.get(project_id)), "reused": False}


def quality_check(store: ProjectStore, project_id: str, root: Path) -> dict[str, Any]:
    project = store.get(project_id)
    with project_lock(project.directory):
        project = store.get(project_id)
        if project.state != State.AWAITING_APPROVAL:
            raise Blocked(f"Project is {project.state}. Run render before quality check.")
        timeline = load_timeline(project)
        if timeline.digest != project.input_digest:
            raise Blocked("Project inputs changed. Run render before quality check.")
        tools = Toolchain.locate(root)
        plan = plan_render(timeline, tools)
        report = inspect_output(project, plan, tools, project.run_directory)
        report["checks"]["output_integrity"] = matches_output(project)
        report["passed"] = all(report["checks"].values())
        write_json(project.run_directory / "qa_report.json", report)
        if not report["passed"]:
            store.set_state(project, State.FAILED, "Quality check failed. See qa_report.json or run render.")
        return report
