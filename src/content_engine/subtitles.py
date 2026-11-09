from fractions import Fraction

from .timeline import RenderPlan


def ass_time(ticks: int) -> str:
    minutes, remainder = divmod(ticks, 6000)
    hours, minutes = divmod(minutes, 60)
    seconds, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02}:{seconds:02}.{centiseconds:02}"


def subtitle_text(text: str) -> str:
    # Keep timeline text literal instead of interpreting it as ASS override tags.
    text = text.translate(str.maketrans({"{": "｛", "}": "｝", "\\": "＼"}))
    return r"\N".join(" ".join(line.split()) for line in text.splitlines())


def make_ass(plan: RenderPlan) -> str:
    fmt = plan.format
    font_size = max(8, round(min(fmt.width, fmt.height) * 0.051))
    margin = max(2, round(fmt.width * 0.08))
    vertical_margin = max(2, round(fmt.height * 0.13))
    outline = max(1, round(font_size * 0.055))
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {fmt.width}",
        f"PlayResY: {fmt.height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Narration,Arial,{font_size},&H00FFFFFF,&H00FFFFFF,&H0011151A,&H8011151A,"
        f"-1,0,0,0,100,100,0,0,1,{outline},1,2,{margin},{margin},{vertical_margin},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    cursor = 0
    for item in plan.scenes:
        if item.source.scene.subtitle:
            start = Fraction(cursor, fmt.fps)
            start_ticks = int(start * 100)
            end_ticks = max(start_ticks + 1, int((start + item.narration_duration) * 100))
            lines.append(
                f"Dialogue: 0,{ass_time(start_ticks)},{ass_time(end_ticks)},Narration,,0,0,0,,"
                f"{subtitle_text(item.source.scene.narration)}"
            )
        cursor += item.frames
    return "\n".join(lines) + "\n"
