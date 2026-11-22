from __future__ import annotations

import wave
from pathlib import Path

import pytest

from channel.config import TTSPrice
from channel.scripts.pipeline import Context, generate
from channel.store import Budget, BudgetExceeded, Store
from channel.voice import (
    CachedTTS,
    EspeakTTS,
    GoogleCloudTTS,
    TTSError,
    build_narration,
    speech_text,
    split_sentences,
    tts_cost_usd,
)
from channel.voice.narration import DurationError, fit_duration
from tests.fakes import ToneTTS

HI = {
    "mAh": " एमएएच",
    "Hz": " हर्ट्ज़",
    "W": " वॉट",
    "GB": " जीबी",
    "IP": "आई पी ",
    "AMOLED": "एमोलेड",
    "OIS": "ओ आई एस",
}
FREE = TTSPrice(usd_per_mchar=0, free_chars_per_month=0)
PAID = TTSPrice(usd_per_mchar=16, free_chars_per_month=1_000_000)


def test_speech_text() -> None:
    out = speech_text("5,000mAh, 67W, 120Hz, IP64, 8 GB, AMOLED, OIS, कीमत 18,999 रुपये", HI)
    assert out == ("5000 एमएएच, 67 वॉट, 120 हर्ट्ज़, आई पी 64, 8 जीबी, एमोलेड, ओ आई एस, कीमत 18999 रुपये")


def test_speech_text_leaves_words_alone() -> None:
    assert speech_text("Wow, OISHI and GBX", HI) == "Wow, OISHI and GBX"


def test_split_sentences_keeps_decimals() -> None:
    assert split_sentences("डिस्प्ले 6.67 इंच है। सच? हाँ! Price ₹18,999. Done.") == [
        "डिस्प्ले 6.67 इंच है।",
        "सच?",
        "हाँ!",
        "Price ₹18,999.",
        "Done.",
    ]


@pytest.mark.parametrize(
    ("chars", "used", "usd"),
    [(5000, 0, 0.0), (5000, 999_000, 4000 * 16 / 1e6), (5000, 2_000_000, 5000 * 16 / 1e6)],
)
def test_tts_free_tier(chars, used, usd) -> None:
    assert tts_cost_usd(chars, used, PAID) == pytest.approx(usd)


def test_cached_tts_caches_and_logs(tmp_path) -> None:
    store = Store(tmp_path / "db.sqlite")
    fake = ToneTTS()
    tts = CachedTTS(
        fake,
        cache_dir=tmp_path / "tts",
        store=store,
        budget=Budget(3000, 150),
        price=FREE,
        usd_to_inr=88,
    )
    p1 = tts.synthesize("नमस्ते", video_id="v")
    p2 = tts.synthesize("नमस्ते", video_id="v")
    assert p1 == p2 and len(fake.calls) == 1
    with wave.open(str(p1)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (24000, 1, 2)
    rows = store.video_costs("v")
    assert [r["cached"] for r in rows] == [0, 1]
    assert rows[0]["units"] == len("नमस्ते")


def test_paid_tts_respects_budget(tmp_path) -> None:
    store = Store(tmp_path / "db.sqlite")
    tts = CachedTTS(
        ToneTTS(),
        cache_dir=tmp_path / "tts",
        store=store,
        budget=Budget(0.0001, 150),
        price=PAID,
        usd_to_inr=88,
    )
    from channel.store import CostEntry

    store.log_cost(
        CostEntry(
            video_id="x", provider="anthropic", operation="script", usd=1, inr=88, cached=False
        )
    )
    with pytest.raises(BudgetExceeded):
        tts.synthesize("नमस्ते", video_id="v")


def test_google_needs_credentials(monkeypatch) -> None:
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    with pytest.raises(TTSError, match="GOOGLE_APPLICATION_CREDENTIALS"):
        GoogleCloudTTS(voice="hi-IN-Neural2-B", speaking_rate=1.0).synthesize("नमस्ते")


def test_google_request_shape() -> None:
    class FakeClient:
        def synthesize_speech(self, *, input, voice, audio_config):
            self.req = (input.text, voice.language_code, voice.name, audio_config.sample_rate_hertz)
            return type("R", (), {"audio_content": b"RIFF"})()

    client = FakeClient()
    tts = GoogleCloudTTS(voice="hi-IN-Neural2-B", speaking_rate=1.1, client=client)
    assert tts.synthesize("नमस्ते") == b"RIFF"
    assert client.req == ("नमस्ते", "hi-IN", "hi-IN-Neural2-B", 24000)


def test_espeak_produces_wav() -> None:
    data = EspeakTTS(voice="hi", wpm=165).synthesize("नमस्ते")
    assert data[:4] == b"RIFF"


@pytest.fixture
def short_script(tmp_settings):
    ctx = Context.load(tmp_settings)
    brief = ctx.brief("deal_short", {"product": "ostra-o2"}, tmp_settings.demo.as_of, "hi")
    script, _r, d = generate(ctx, brief, writer="template")
    return ctx, script, d


def test_build_narration_timings(short_script, tmp_path) -> None:
    _ctx, script, d = short_script
    store = Store(tmp_path / "db.sqlite")
    tts = CachedTTS(
        ToneTTS(0.05),
        cache_dir=tmp_path / "tts",
        store=store,
        budget=Budget(3000, 150),
        price=FREE,
        usd_to_inr=88,
    )
    tl = build_narration(
        script,
        tts,
        d / "n.wav",
        pronounce=HI,
        sentence_gap=0.25,
        section_gap=0.6,
        lead_in=0.4,
        tail=1.0,
    )
    starts = [s.start for s in tl.sentences]
    assert starts == sorted(starts) and tl.sentences[0].start == pytest.approx(0.4)
    assert all(s.end > s.start for s in tl.sentences)
    assert tl.duration == pytest.approx(tl.sentences[-1].end + 1.0, abs=0.01)
    with wave.open(str(d / "n.wav")) as w:
        assert w.getnframes() / w.getframerate() == pytest.approx(tl.duration, abs=0.01)
    assert [s.section_id for s in tl.sections][:2] == ["hook", "sys-disclosure"]
    # Claims are attached to the sentence that speaks them.
    price_sentence = next(s for s in tl.sentences if s.section_id == "price")
    assert "prices.ostra-o2.current" in price_sentence.refs


def test_fit_duration(short_script, tmp_path) -> None:
    _ctx, script, d = short_script
    store = Store(tmp_path / "db.sqlite")
    tts = CachedTTS(
        ToneTTS(0.07),
        cache_dir=tmp_path / "tts",
        store=store,
        budget=Budget(3000, 150),
        price=FREE,
        usd_to_inr=88,
    )
    tl = build_narration(
        script,
        tts,
        d / "n.wav",
        pronounce=HI,
        sentence_gap=0.25,
        section_gap=0.6,
        lead_in=0.4,
        tail=1.0,
    )
    before = tl.duration
    target = (30.0, before / 1.1)
    tempo = fit_duration(d / "n.wav", tl, target=target, max_speedup=1.2, max_slowdown=0.9)
    assert 1.1 < tempo <= 1.2
    assert tl.duration <= target[1] + 0.05
    assert tl.sentences[-1].end <= tl.duration
    with pytest.raises(DurationError, match="Shorten"):
        fit_duration(d / "n.wav", tl, target=(5.0, 10.0), max_speedup=1.2, max_slowdown=0.9)


def test_fit_duration_noop_inside_window(short_script, tmp_path) -> None:
    _ctx, script, d = short_script
    store = Store(tmp_path / "db.sqlite")
    tts = CachedTTS(
        ToneTTS(0.05),
        cache_dir=tmp_path / "tts",
        store=store,
        budget=Budget(3000, 150),
        price=FREE,
        usd_to_inr=88,
    )
    tl = build_narration(
        script,
        tts,
        d / "n.wav",
        pronounce=HI,
        sentence_gap=0.25,
        section_gap=0.6,
        lead_in=0.4,
        tail=1.0,
    )
    assert (
        fit_duration(Path(d / "n.wav"), tl, target=(1, 10_000), max_speedup=1.2, max_slowdown=0.9)
        == 1.0
    )
