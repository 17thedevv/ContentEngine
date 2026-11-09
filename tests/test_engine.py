import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

from content_engine.domain import EngineError
from content_engine.ffmpeg import Toolchain
from content_engine.storage import ProjectStore, State, project_lock


PROFILE = """\
id: test_vi
name: Test channel
language: vi
video:
  format: vertical
  resolution: [180, 320]
  fps: 30
  target_duration: 3
"""


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class CLIWorkspace(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="Engine thử ")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        profiles = self.root / "configs" / "channels"
        profiles.mkdir(parents=True)
        self.profile = profiles / "test.yaml"
        self.profile.write_text(PROFILE, encoding="utf-8")
        self.env = os.environ.copy()

    def cli(self, *args, code=0):
        result = subprocess.run(
            [sys.executable, "-m", "content_engine", "--root", str(self.root), *args],
            capture_output=True, encoding="utf-8", env=self.env,
        )
        self.assertEqual(result.returncode, code, result.stderr or result.stdout)
        return json.loads(result.stdout) if result.stdout else result.stderr

    def create(self, channel="test_vi"):
        return self.cli("project", "create", "--channel", channel, "--topic", "Tiếng Việt")


class CLITest(CLIWorkspace):
    def test_profiles_are_snapshotted_and_projects_are_isolated(self):
        self.profile.with_name("second.yaml").write_text(
            PROFILE.replace("test_vi", "science_vi").replace("180, 320", "360, 640"),
            encoding="utf-8",
        )
        self.assertEqual({item["id"] for item in self.cli("channel", "list")}, {"test_vi", "science_vi"})
        first = self.create()
        second = self.create("science_vi")
        self.profile.write_text(PROFILE.replace("fps: 30", "fps: 24"), encoding="utf-8")
        stored = self.cli("project", "status", first["project_id"])
        self.assertEqual(stored["state"], "CREATED")
        self.assertEqual(stored["format"], {"width": 180, "height": 320, "fps": 30})
        self.assertEqual(stored["topic"], "Tiếng Việt")
        self.assertEqual(second["format"]["width"], 360)
        self.assertNotEqual(first["directory"], second["directory"])
        self.assertIn("Add at least one scene", self.cli("render", first["project_id"], code=2))
        self.assertEqual(self.cli("project", "status", first["project_id"])["state"], "BLOCKED")

    def test_project_lock_prevents_concurrent_render(self):
        project = self.create()
        with project_lock(Path(project["directory"])):
            error = self.cli("render", project["project_id"], code=2)
            self.assertIn("Another production command", error)
        self.assertEqual(self.cli("project", "status", project["project_id"])["state"], "CREATED")


class RenderIntegrationTest(CLIWorkspace):
    @classmethod
    def setUpClass(cls):
        try:
            cls.tools = Toolchain.locate(Path.cwd())
        except EngineError as exc:
            raise unittest.SkipTest(str(exc)) from exc
        temp = tempfile.TemporaryDirectory(prefix="content-engine-fixtures-")
        cls.addClassCleanup(temp.cleanup)
        cls.fixtures = Path(temp.name)
        for index, (color, geometry) in enumerate([
            ("red", "90x160:r=24"), ("green", "120x160:r=25"), ("blue", "160x90:r=30"),
        ], start=1):
            cls.tools.encode([
                "-f", "lavfi", "-i", f"color=c={color}:s={geometry}:d=2",
                "-c:v", "libx264", "-threads:v", "2", str(cls.fixtures / f"{index}.mp4"),
            ])
        with wave.open(str(cls.fixtures / "speech.wav"), "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", ""))
            audio.writeframes(b"\x18\x08\xe8\xf7" * 3600)

    def setUp(self):
        super().setUp()
        self.env.update(CONTENT_ENGINE_FFMPEG=self.tools.ffmpeg, CONTENT_ENGINE_FFPROBE=self.tools.ffprobe)

    def prepare(self):
        project = self.create()
        directory = Path(project["directory"])
        manifest = {"assets": []}
        timeline = {"project_id": project["project_id"], "format": project["format"], "scenes": []}
        shutil.copy(self.fixtures / "speech.wav", directory / "audio" / "lời đọc.wav")
        for index in range(1, 4):
            clip = f"media/clip {index}.mp4"
            shutil.copy(self.fixtures / f"{index}.mp4", directory / clip)
            manifest["assets"].append({
                "asset_id": f"clip_{index}", "local_path": clip, "source": "original", "creator": "Fixture",
                "license": {
                    "status": "verified", "name": "Generated fixture", "checked_at": "2026-11-10",
                    "attribution_required": False,
                },
            })
            timeline["scenes"].append({
                "id": f"scene_{index}", "asset_id": f"clip_{index}", "source_start": 0.25,
                "source_end": 1.25, "narration": "Một cảnh thử.", "narration_audio": "audio/lời đọc.wav",
                "subtitle": True,
            })
        write_json(directory / "assets.json", manifest)
        write_json(directory / "timeline.json", timeline)
        return project["project_id"], directory, manifest, timeline

    def rgb_frame(self, preview, time):
        return subprocess.check_output([
            self.tools.ffmpeg, "-v", "error", "-ss", str(time), "-i", preview,
            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-threads:v", "1", "-",
        ])

    def test_render_qa_cache_and_stale_output(self):
        project_id, directory, _, timeline = self.prepare()
        self.assertEqual(self.cli("timeline", "validate", project_id)["frames"], 90)
        render = self.cli("render", project_id)
        self.assertEqual(render["state"], "AWAITING_APPROVAL")
        self.assertFalse(render["reused"])
        preview = render["final_mp4"]
        report = self.cli("quality", "check", project_id)
        self.assertTrue(report["passed"])
        self.assertEqual(report["subtitle_events"], 3)
        self.assertEqual(report["video"]["duration_seconds"], 3)
        self.assertTrue(report["manual_review_required"])
        # The frames use three different source colors, and the first has burned-in text.
        first = self.rgb_frame(preview, 0.1)
        after_subtitle = self.rgb_frame(preview, 0.7)
        center = (160 * 180 + 90) * 3
        for frame, channel in [
            (first, 0), (self.rgb_frame(preview, 1.2), 1), (self.rgb_frame(preview, 2.2), 2),
        ]:
            self.assertEqual(max(range(3), key=lambda color: frame[center + color]), channel)
        def white_pixels(frame):
            bottom = frame[240 * 180 * 3:]
            return sum(
                min(red, green, blue) > 220
                for red, green, blue in zip(bottom[0::3], bottom[1::3], bottom[2::3])
            )

        self.assertGreater(white_pixels(first), white_pixels(after_subtitle))
        self.assertEqual(self.cli("project", "output", project_id)["final_mp4"], preview)
        self.assertTrue(self.cli("render", project_id)["reused"])

        timeline["scenes"][0]["narration"] = "Nội dung mới."
        write_json(directory / "timeline.json", timeline)
        self.assertEqual(self.cli("project", "status", project_id)["state"], "NEEDS_RENDER")
        self.cli("project", "output", project_id, code=2)
        new_render = self.cli("render", project_id)
        self.assertNotEqual(new_render["final_mp4"], preview)
        self.assertIn("Nội dung mới.", Path(new_render["subtitles_ass"]).read_text(encoding="utf-8"))

    def test_invalid_footage_cut_and_narration_are_blocked(self):
        project_id, directory, manifest, timeline = self.prepare()
        license_data = manifest["assets"][0]["license"]
        license_data["status"] = "unverified"
        write_json(directory / "assets.json", manifest)
        self.assertIn("verified license record", self.cli("render", project_id, code=2))
        self.assertEqual(self.cli("project", "status", project_id)["state"], "BLOCKED")
        license_data["status"] = "verified"
        manifest["assets"][0]["local_path"] = "media/../audio/lời đọc.wav"
        write_json(directory / "assets.json", manifest)
        self.assertIn("relative path", self.cli("timeline", "validate", project_id, code=2))
        manifest["assets"][0]["local_path"] = "media/clip 1.mp4"
        write_json(directory / "assets.json", manifest)
        scene = timeline["scenes"][0]
        scene["source_end"] = 3.0
        write_json(directory / "timeline.json", timeline)
        self.assertIn("the asset lasts 2s", self.cli("timeline", "validate", project_id, code=2))
        scene["source_end"] = 0.5
        write_json(directory / "timeline.json", timeline)
        self.assertIn("Extend the cut", self.cli("render", project_id, code=2))
        scene["source_end"] = 1.25
        write_json(directory / "timeline.json", timeline)
        self.assertEqual(self.cli("render", project_id)["state"], "AWAITING_APPROVAL")

    def test_resume_and_corrupt_output(self):
        project_id, _, _, _ = self.prepare()
        store = ProjectStore(self.root)
        store.set_state(store.get(project_id), State.RENDERING)
        self.assertEqual(self.cli("project", "status", project_id)["state"], "RENDERING")
        output = self.cli("render", project_id)
        Path(output["final_mp4"]).write_bytes(b"corrupted video")
        self.assertEqual(self.cli("project", "status", project_id)["state"], "NEEDS_RENDER")
        report = self.cli("quality", "check", project_id, code=1)
        self.assertFalse(report["checks"]["decode"])
        self.assertFalse(report["checks"]["output_integrity"])
        self.assertEqual(self.cli("project", "status", project_id)["state"], "FAILED")
        self.assertFalse(self.cli("render", project_id)["reused"])


if __name__ == "__main__":
    unittest.main()
