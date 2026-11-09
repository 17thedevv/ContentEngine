import argparse
import json
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from .config import load_channels
from .domain import Blocked, EngineError
from .production import current_output, project_status, quality_check, render, validate
from .storage import ProjectStore


def parser() -> argparse.ArgumentParser:
    main = argparse.ArgumentParser(description="Render a local video preview from a project timeline.")
    main.add_argument("--root", type=Path, default=Path.cwd(), help="workspace containing configs/ and data/")
    commands = main.add_subparsers(dest="group", required=True)

    channel = commands.add_parser("channel", help="inspect channel profiles")
    channel.add_subparsers(dest="action", required=True).add_parser("list")
    project = commands.add_parser("project", help="create and inspect a local project")
    project_actions = project.add_subparsers(dest="action", required=True)
    create = project_actions.add_parser("create")
    create.add_argument("--channel", required=True)
    create.add_argument("--topic", required=True)
    for name in ("status", "output"):
        project_actions.add_parser(name).add_argument("project_id")

    timeline = commands.add_parser("timeline", help="check a timeline and its inputs")
    timeline.add_subparsers(dest="action", required=True).add_parser("validate").add_argument("project_id")
    commands.add_parser("render", help="render and technically validate a preview").add_argument("project_id")
    quality = commands.add_parser("quality", help="recheck a completed preview")
    quality.add_subparsers(dest="action", required=True).add_parser("check").add_argument("project_id")
    return main


def dispatch(args: argparse.Namespace) -> tuple[object, int]:
    root = args.root.resolve()
    if args.group == "channel":
        return [asdict(profile.channel) for profile in load_channels(root).values()], 0

    store = ProjectStore(root)
    if args.group == "project" and args.action == "create":
        profile = load_channels(root).get(args.channel)
        if profile is None:
            raise Blocked(f"Unknown channel: {args.channel}")
        return project_status(store.create(profile, args.topic)), 0
    if args.group == "render":
        return render(store, args.project_id, root), 0
    if args.group == "quality":
        report = quality_check(store, args.project_id, root)
        return report, 0 if report["passed"] else 1
    project = store.get(args.project_id)
    if args.group == "timeline":
        return validate(project, root), 0
    if args.action == "output":
        return current_output(project), 0
    return project_status(project), 0


def main(argv: Sequence[str] | None = None) -> int:
    # A Windows caller can capture or redirect the same UTF-8 JSON as a terminal user.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        result, code = dispatch(args)
    except Blocked as exc:
        print(f"Blocked: {exc}", file=sys.stderr)
        return 2
    except (EngineError, OSError, sqlite3.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code
