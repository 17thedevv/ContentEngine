from fractions import Fraction
from pathlib import Path

from .ffmpeg import LOCAL_INPUT, VIDEO_INPUT, Toolchain
from .subtitles import make_ass
from .timeline import RenderPlan

AUDIO_RATE = 48000


def render_video(plan: RenderPlan, tools: Toolchain, directory: Path) -> None:
    fmt = plan.format
    args = ["-filter_complex_threads", "2"]
    graph: list[str] = []
    frame_cursor = 0
    for index, item in enumerate(plan.scenes):
        video_index, audio_index = index * 2, index * 2 + 1
        scene = item.source.scene
        args.extend([
            *VIDEO_INPUT, "-threads", "2", "-ss", f"{float(scene.start):.9f}", "-i", str(item.source.video),
            *LOCAL_INPUT, "-f", "wav", "-i", str(item.source.audio),
        ])
        span = f"{float(scene.end - scene.start):.9f}"
        graph.append(
            f"[{video_index}:{item.video_stream}]setpts=PTS-STARTPTS,trim=duration={span},"
            f"fps={fmt.fps}:start_time=0,trim=end_frame={item.frames},"
            f"scale={fmt.width}:{fmt.height}:force_original_aspect_ratio=decrease:"
            "force_divisible_by=2:reset_sar=1,"
            f"pad={fmt.width}:{fmt.height}:(ow-iw)/2:(oh-ih)/2,format=yuv420p[v{index}]"
        )
        start_sample = round(Fraction(frame_cursor * AUDIO_RATE, fmt.fps))
        frame_cursor += item.frames
        end_sample = round(Fraction(frame_cursor * AUDIO_RATE, fmt.fps))
        graph.append(
            f"[{audio_index}:a:0]aresample={AUDIO_RATE},aformat=channel_layouts=stereo,"
            f"apad,atrim=end_sample={end_sample - start_sample},asetpts=N/SR/TB[a{index}]"
        )
    count = len(plan.scenes)
    videos = "".join(f"[v{i}]" for i in range(count))
    audios = "".join(f"[a{i}]" for i in range(count))
    # Rebuild consecutive frame timestamps so segment rounding cannot accumulate.
    graph.append(f"{videos}concat=n={count}:v=1:a=0,settb=1/{fmt.fps},setpts=N,ass=subtitles.ass[outv]")
    graph.append(f"{audios}concat=n={count}:v=0:a=1,asetpts=N/SR/TB[outa]")

    (directory / "subtitles.ass").write_text(make_ass(plan), encoding="utf-8", newline="\n")
    script = directory / "render.ffgraph"
    script.write_text(";\n".join(graph), encoding="utf-8")
    tools.encode([
        *args, "-/filter_complex", script.name, "-map", "[outv]", "-map", "[outa]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-threads:v", "4",
        "-pix_fmt", "yuv420p", "-r", str(fmt.fps), "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart", "final.mp4",
    ], cwd=directory)
    script.unlink()
