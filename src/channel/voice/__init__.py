"""Text-to-speech behind a provider interface, and timed narration assembly."""

from channel.voice.narration import (
    Sentence,
    Timeline,
    build_narration,
    speech_text,
    split_sentences,
)
from channel.voice.tts import (
    CachedTTS,
    EspeakTTS,
    GoogleCloudTTS,
    TTSError,
    TTSProvider,
    make_provider,
    tts_cost_usd,
)

__all__ = [
    "CachedTTS",
    "EspeakTTS",
    "GoogleCloudTTS",
    "Sentence",
    "TTSError",
    "TTSProvider",
    "Timeline",
    "build_narration",
    "make_provider",
    "speech_text",
    "split_sentences",
    "tts_cost_usd",
]
