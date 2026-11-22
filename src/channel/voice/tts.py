"""Text-to-speech providers behind one interface, plus a cached, costed wrapper.

* ``GoogleCloudTTS`` — primary (Neural2 voices; needs GOOGLE_APPLICATION_CREDENTIALS).
* ``EspeakTTS`` — offline espeak-ng, robotic but free; used by `make demo` and tests.

Every provider returns WAV bytes. ``CachedTTS`` normalises them to 24 kHz mono
16-bit PCM with ffmpeg, caches by (provider identity, text) on disk, and logs
characters and cost to the ledger (free monthly allowance applied).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Protocol

from channel.config import TTSPrice
from channel.store import Budget, CostEntry, Store

SAMPLE_RATE = 24_000


class TTSError(RuntimeError):
    pass


class TTSProvider(Protocol):
    name: str

    def identity(self) -> dict[str, Any]: ...

    def synthesize(self, text: str) -> bytes:
        """Return WAV bytes for ``text``."""
        ...


class EspeakTTS:
    name = "espeak"

    def __init__(self, *, voice: str, wpm: int) -> None:
        self.voice = voice
        self.wpm = wpm

    def identity(self) -> dict[str, Any]:
        return {"provider": self.name, "voice": self.voice, "wpm": self.wpm}

    def synthesize(self, text: str) -> bytes:
        with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
            proc = subprocess.run(
                ["espeak-ng", "-v", self.voice, "-s", str(self.wpm), "-w", tmp.name, text],
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0:
                raise TTSError(f"espeak-ng failed: {proc.stderr.strip()}")
            return Path(tmp.name).read_bytes()


class GoogleCloudTTS:
    name = "google"

    def __init__(self, *, voice: str, speaking_rate: float, client: Any = None) -> None:
        self.voice = voice
        self.speaking_rate = speaking_rate
        self.language_code = "-".join(voice.split("-")[:2])  # hi-IN-Neural2-B -> hi-IN
        self._client = client

    def identity(self) -> dict[str, Any]:
        return {"provider": self.name, "voice": self.voice, "rate": self.speaking_rate}

    def _get_client(self) -> Any:
        if self._client is None:
            if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
                raise TTSError(
                    "no Google credentials: set GOOGLE_APPLICATION_CREDENTIALS in .env to a "
                    "service-account JSON inside the repo (e.g. /app/secrets/gcp.json), "
                    "or use --tts espeak"
                )
            from google.cloud import texttospeech

            self._client = texttospeech.TextToSpeechClient()
        return self._client

    def synthesize(self, text: str) -> bytes:
        from google.cloud import texttospeech

        resp = self._get_client().synthesize_speech(
            input=texttospeech.SynthesisInput(text=text),
            voice=texttospeech.VoiceSelectionParams(
                language_code=self.language_code, name=self.voice
            ),
            audio_config=texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.LINEAR16,
                sample_rate_hertz=SAMPLE_RATE,
                speaking_rate=self.speaking_rate,
            ),
        )
        return resp.audio_content


def normalize_wav(data: bytes) -> bytes:
    """Any WAV -> 24 kHz mono s16le WAV (one format for the whole narration)."""
    proc = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            "pipe:0",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-sample_fmt",
            "s16",
            "-f",
            "wav",
            "pipe:1",
        ],
        input=data,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise TTSError(f"ffmpeg could not normalise TTS audio: {proc.stderr.decode()[:500]}")
    return proc.stdout


def tts_cost_usd(chars: int, used_this_month: int, price: TTSPrice) -> float:
    """Cost of ``chars`` new characters given what was already used this month."""
    free = price.free_chars_per_month
    before = max(0, used_this_month - free)
    after = max(0, used_this_month + chars - free)
    return (after - before) * price.usd_per_mchar / 1_000_000


class CachedTTS:
    def __init__(
        self,
        inner: TTSProvider,
        *,
        cache_dir: Path,
        store: Store,
        budget: Budget,
        price: TTSPrice,
        usd_to_inr: float,
    ) -> None:
        self.inner = inner
        self.cache_dir = cache_dir
        self.store = store
        self.budget = budget
        self.price = price
        self.usd_to_inr = usd_to_inr
        cache_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, text: str) -> Path:
        key = json.dumps(
            {"id": self.inner.identity(), "text": text}, sort_keys=True, ensure_ascii=False
        )
        return self.cache_dir / f"{hashlib.sha256(key.encode()).hexdigest()}.wav"

    def synthesize(self, text: str, *, video_id: str) -> Path:
        """Return the path of a normalised WAV for ``text`` (from cache when possible)."""
        path = self.path_for(text)
        provider = self.inner.name
        if path.exists():
            self.store.log_cost(
                CostEntry(
                    video_id=video_id,
                    provider=provider,
                    operation="tts",
                    usd=0.0,
                    inr=0.0,
                    cached=True,
                    note="cache hit",
                )
            )
            return path
        if self.price.usd_per_mchar > 0:
            self.budget.check(self.store, video_id)
        used = self.store.month_units(provider)
        wav = normalize_wav(self.inner.synthesize(text))
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(wav)
        tmp.replace(path)
        usd = tts_cost_usd(len(text), used, self.price)
        self.store.log_cost(
            CostEntry(
                video_id=video_id,
                provider=provider,
                operation="tts",
                model=str(self.inner.identity().get("voice")),
                units=len(text),
                usd=usd,
                inr=usd * self.usd_to_inr,
                cached=False,
            )
        )
        return path


def make_provider(name: str, lang: str, tts_cfg: Any) -> TTSProvider:
    if name == "google":
        return GoogleCloudTTS(
            voice=tts_cfg.google.voices[lang], speaking_rate=tts_cfg.google.speaking_rate
        )
    if name == "espeak":
        return EspeakTTS(voice=tts_cfg.espeak.voices[lang], wpm=tts_cfg.espeak.wpm)
    raise TTSError(f"unknown TTS provider {name!r}")
