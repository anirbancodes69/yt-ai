"""Animated burned-in captions (ASS, rendered by libass with HarfBuzz shaping)
plus a plain SRT for YouTube upload.

Each sentence is split into short caption chunks; chunk timing is proportional
to its length. Each chunk pops in (scale + fade) and words fill with the accent
colour in time with the speech (karaoke ``\\kf``).
"""

from __future__ import annotations

from channel.render.theme import Theme
from channel.voice.narration import Timeline


def _ts_ass(t: float) -> str:
    cs = round(max(0.0, t) * 100)
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ts_srt(t: float) -> str:
    ms = round(max(0.0, t) * 1000)
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _bgr(rgb: tuple[int, int, int]) -> str:
    r, g, b = rgb
    return f"&H00{b:02X}{g:02X}{r:02X}"


def chunk_words(text: str, max_chars: int) -> list[str]:
    chunks: list[str] = []
    cur = ""
    for w in text.split():
        trial = f"{cur} {w}".strip()
        if cur and len(trial) > max_chars:
            chunks.append(cur)
            cur = w
        else:
            cur = trial
    if cur:
        chunks.append(cur)
    return chunks


def caption_events(timeline: Timeline, max_chars: int) -> list[tuple[float, float, str]]:
    events = []
    for s in timeline.sentences:
        chunks = chunk_words(s.text, max_chars)
        total = sum(len(c) for c in chunks) or 1
        t = s.start
        for c in chunks:
            dur = (s.end - s.start) * len(c) / total
            events.append((t, t + dur, c))
            t += dur
    return events


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


def write_ass(timeline: Timeline, theme: Theme, *, width: int, height: int, portrait: bool) -> str:
    size = int(height * (0.040 if portrait else 0.052))
    margin_v = int(height * (0.26 if portrait else 0.13))
    max_chars = 26 if portrait else 44
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Noto Sans Devanagari,{size},{_bgr(theme.accent)},&H00FFFFFF,&H00101010,&H96000000,1,0,0,0,100,100,0,0,1,{max(2, size // 14)},{max(1, size // 30)},2,{int(width * 0.06)},{int(width * 0.06)},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for start, end, text in caption_events(timeline, max_chars):
        words = text.split()
        total = sum(len(w) for w in words) or 1
        dur_cs = max(1, int((end - start) * 100))
        karaoke = "".join(
            f"{{\\kf{max(1, int(dur_cs * len(w) / total))}}}{_escape(w)} " for w in words
        ).rstrip()
        anim = r"{\fad(60,40)\fscx92\fscy92\t(0,140,\fscx100\fscy100)}"
        lines.append(f"Dialogue: 0,{_ts_ass(start)},{_ts_ass(end)},Cap,,0,0,0,,{anim}{karaoke}\n")
    return "".join(lines)


def write_srt(timeline: Timeline, max_chars: int = 60) -> str:
    out = []
    for i, (start, end, text) in enumerate(caption_events(timeline, max_chars), start=1):
        out.append(f"{i}\n{_ts_srt(start)} --> {_ts_srt(end)}\n{text}\n")
    return "\n".join(out)
