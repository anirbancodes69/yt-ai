"""Test doubles for TTS: deterministic tones, length proportional to the text."""

from __future__ import annotations

import io
import math
import wave


class ToneTTS:
    """Fake TTS: a 220 Hz tone, ``seconds_per_char`` long per character. Free."""

    name = "espeak"  # priced like espeak (free) in settings

    def __init__(self, seconds_per_char: float = 0.06) -> None:
        self.seconds_per_char = seconds_per_char
        self.calls: list[str] = []

    def identity(self):
        return {"provider": "tone", "spc": self.seconds_per_char}

    def synthesize(self, text: str) -> bytes:
        self.calls.append(text)
        rate = 16000
        n = max(1, int(len(text) * self.seconds_per_char * rate))
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(
                b"".join(
                    int(8000 * math.sin(2 * math.pi * 220 * i / rate)).to_bytes(
                        2, "little", signed=True
                    )
                    for i in range(n)
                )
            )
        return buf.getvalue()
