"""Split a script into sentences, voice each one, and build a timed narration track.

Per-sentence synthesis gives exact timings for captions and shot changes, and
lets the cache reuse unchanged sentences when a script is edited.
"""

from __future__ import annotations

import re
import subprocess
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from channel.scripts.models import Script
from channel.voice.tts import SAMPLE_RATE, CachedTTS

# Split after Hindi danda, ?, ! or a full stop that is followed by whitespace
# (so decimals such as 6.67 stay intact).
SENTENCE_END = re.compile(r"(?<=[।?!])\s+|(?<=\.)\s+")
UNIT_KEYS = ("mAh", "Hz", "GB", "W")


@dataclass
class Sentence:
    section_id: str
    index: int
    text: str  # caption text (exactly as written)
    speech: str  # text sent to TTS (pronunciation-normalised)
    start: float = 0.0
    end: float = 0.0
    refs: list[str] = field(default_factory=list)


@dataclass
class SectionSpan:
    section_id: str
    kind: str
    start: float
    end: float


@dataclass
class Timeline:
    sentences: list[Sentence]
    sections: list[SectionSpan]
    duration: float

    def to_json(self) -> dict[str, Any]:
        return {
            "duration": self.duration,
            "sections": [asdict(s) for s in self.sections],
            "sentences": [asdict(s) for s in self.sentences],
        }


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE_END.split(text.strip()) if s.strip()]


def speech_text(text: str, pronounce: dict[str, str]) -> str:
    """Make text easier for TTS: drop digit-group commas, spell out units/acronyms."""
    out = re.sub(r"(?<=\d),(?=\d)", "", text)
    for unit in UNIT_KEYS:
        if unit in pronounce:
            out = re.sub(rf"(?<=\d)\s?{re.escape(unit)}\b", pronounce[unit], out)
    if "IP" in pronounce:
        out = re.sub(r"\bIP(?=\d)", pronounce["IP"], out)
    for token, spoken in pronounce.items():
        if token in UNIT_KEYS or token == "IP":
            continue
        out = re.sub(rf"(?<![\w-]){re.escape(token)}(?![\w-])", spoken, out)
    return re.sub(r"\s{2,}", " ", out).strip()


def plan_sentences(script: Script, pronounce: dict[str, str]) -> list[Sentence]:
    sentences: list[Sentence] = []
    for sec in script.sections:
        claims = list(sec.claims)
        for i, text in enumerate(split_sentences(sec.narration)):
            refs = []
            for c in list(claims):
                if c.text in text:
                    refs.append(c.ref)
                    claims.remove(c)
            sentences.append(Sentence(sec.id, i, text, speech_text(text, pronounce), refs=refs))
    return sentences


def _wav_frames(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (SAMPLE_RATE, 1, 2):
            raise ValueError(f"{path} is not 24 kHz mono s16")
        return w.readframes(w.getnframes())


def build_narration(
    script: Script,
    tts: CachedTTS,
    out_wav: Path,
    *,
    pronounce: dict[str, str],
    sentence_gap: float,
    section_gap: float,
    lead_in: float,
    tail: float,
) -> Timeline:
    sentences = plan_sentences(script, pronounce)
    bytes_per_s = SAMPLE_RATE * 2

    def silence(seconds: float) -> bytes:
        return b"\x00\x00" * round(seconds * SAMPLE_RATE)

    pcm = bytearray(silence(lead_in))
    spans: list[SectionSpan] = []
    kinds = {s.id: s.kind for s in script.sections}
    prev_section: str | None = None
    for s in sentences:
        if prev_section is not None:
            pcm += silence(section_gap if s.section_id != prev_section else sentence_gap)
        if s.section_id != prev_section:
            spans.append(SectionSpan(s.section_id, kinds[s.section_id], len(pcm) / bytes_per_s, 0))
        s.start = len(pcm) / bytes_per_s
        pcm += _wav_frames(tts.synthesize(s.speech, video_id=script.video_id))
        s.end = len(pcm) / bytes_per_s
        spans[-1].end = s.end
        prev_section = s.section_id
    pcm += silence(tail)

    out_wav.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(bytes(pcm))
    return Timeline(sentences=sentences, sections=spans, duration=len(pcm) / bytes_per_s)


class DurationError(RuntimeError):
    pass


def fit_duration(
    wav: Path,
    timeline: Timeline,
    *,
    target: tuple[float, float],
    max_speedup: float,
    max_slowdown: float,
) -> float:
    """Re-time the narration into ``target`` seconds with ffmpeg atempo (pitch is
    preserved). Rescales all timeline timestamps in place. Returns the tempo used
    (1.0 = untouched). Raises DurationError if the needed change exceeds the limits:
    the script should be edited instead."""
    lo, hi = target
    d = timeline.duration
    if lo <= d <= hi:
        return 1.0
    tempo = d / (hi * 0.98) if d > hi else d / (lo * 1.02)
    if tempo > max_speedup or tempo < max_slowdown:
        raise DurationError(
            f"narration is {d:.0f}s; fitting {lo:.0f}-{hi:.0f}s needs tempo {tempo:.2f} "
            f"(allowed {max_slowdown}-{max_speedup}). Shorten or lengthen the script."
        )
    tmp = wav.with_suffix(".fit.wav")
    proc = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(wav),
            "-filter:a",
            f"atempo={tempo:.5f}",
            "-ar",
            str(SAMPLE_RATE),
            "-ac",
            "1",
            "-sample_fmt",
            "s16",
            str(tmp),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise DurationError(f"atempo failed: {proc.stderr[-500:]}")
    tmp.replace(wav)
    with wave.open(str(wav), "rb") as w:
        new_duration = w.getnframes() / w.getframerate()
    for s in timeline.sentences:
        s.start, s.end = s.start / tempo, s.end / tempo
    for sec in timeline.sections:
        sec.start, sec.end = sec.start / tempo, sec.end / tempo
    timeline.duration = new_duration
    return tempo
