"""LLM writer: structured output parsing, refusals, cache, budget, cost ledger,
and the fix-and-retry loop — all with a fake client (no network, no cost)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from channel.config import Price
from channel.scripts.llm import (
    FALLBACK_BETA,
    AnthropicScriptLLM,
    CachedLLM,
    LLMResponse,
    ScriptGenerationError,
    Usage,
    cost_usd,
    strict_schema,
)
from channel.scripts.models import WriterScript
from channel.scripts.pipeline import Context, FactCheckFailed, generate
from channel.scripts.templates import write_from_template
from channel.store import Budget, BudgetExceeded, Store


class FakeStream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


class FakeClient:
    def __init__(self, *messages):
        self.messages_queue = list(messages)
        self.calls: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kwargs):
        self.calls.append(kwargs)
        return FakeStream(self.messages_queue.pop(0))


def fake_msg(text, stop="end_turn", model="claude-opus-5-5", inp=1000, out=500):
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
        stop_reason=stop,
        model=model,
        usage=SimpleNamespace(
            input_tokens=inp,
            output_tokens=out,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )


PRICING = {"claude-opus-5-5": Price(input=4, output=20, cache_read=0.2, cache_write=5)}


def _llm(client):
    return AnthropicScriptLLM(
        model="claude-opus-5-5", effort="medium", max_tokens=32000, client=client
    )


def test_request_shape_and_parse() -> None:
    client = FakeClient(fake_msg('{"title": "t", "sections": []}'))
    resp = _llm(client).complete(system="S", user="U", schema={"type": "object"})
    assert resp.text == '{"title": "t", "sections": []}'
    assert resp.usage == Usage(1000, 500, 0, 0)
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["betas"] == [FALLBACK_BETA] and call["fallbacks"] == "default"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["output_config"]["effort"] == "medium"
    assert "thinking" not in call and "temperature" not in call


@pytest.mark.parametrize(("stop", "msg"), [("refusal", "declined"), ("max_tokens", "max_tokens")])
def test_bad_stop_reasons_raise(stop, msg) -> None:
    with pytest.raises(ScriptGenerationError, match=msg):
        _llm(FakeClient(fake_msg("{}", stop=stop))).complete(system="", user="", schema={})


def test_missing_credentials_is_a_clear_error(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    llm = AnthropicScriptLLM(model="claude-opus-5-5", effort="medium", max_tokens=100)
    with pytest.raises(ScriptGenerationError, match="ANTHROPIC_API_KEY"):
        llm.complete(system="", user="", schema={})


def test_strict_schema_closes_objects() -> None:
    s = strict_schema(WriterScript.model_json_schema())
    claim = s["$defs"]["Claim"]
    assert claim["additionalProperties"] is False
    assert claim["required"] == ["ref", "text", "value"]
    assert "title" not in claim


def test_cost_known_and_unknown_model() -> None:
    u = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost_usd(u, "claude-opus-5-5", PRICING) == (24.0, None)
    usd, note = cost_usd(u, "mystery-model", PRICING)
    assert usd == 24.0 and "unpriced" in note


class CountingLLM:
    def __init__(self, *texts):
        self.texts = list(texts)
        self.calls = 0

    def identity(self):
        return {"provider": "fake", "model": "claude-opus-5-5"}

    def complete(self, *, system, user, schema):
        self.calls += 1
        return LLMResponse(
            text=self.texts.pop(0),
            model="claude-opus-5-5",
            usage=Usage(10_000, 6_000),
            stop_reason="end_turn",
        )


def _cached(inner, store, monthly=3000, per_video=150):
    return CachedLLM(
        inner, store=store, budget=Budget(monthly, per_video), pricing=PRICING, usd_to_inr=88.0
    )


def test_cache_hit_avoids_second_call_and_logs_zero(tmp_path) -> None:
    store = Store(tmp_path / "db.sqlite")
    inner = CountingLLM("A", "B")
    llm = _cached(inner, store)
    r1 = llm.complete(video_id="v1", operation="script", system="S", user="U", schema={})
    r2 = llm.complete(video_id="v1", operation="script", system="S", user="U", schema={})
    assert inner.calls == 1 and r1.text == r2.text == "A"
    rows = store.video_costs("v1")
    assert [r["cached"] for r in rows] == [0, 1]
    expected_usd = (10_000 * 4 + 6_000 * 20) / 1e6
    assert rows[0]["usd"] == pytest.approx(expected_usd)
    assert rows[0]["inr"] == pytest.approx(expected_usd * 88)
    assert rows[1]["inr"] == 0
    assert store.video_spend_inr("v1") == pytest.approx(expected_usd * 88)


def test_monthly_budget_blocks_paid_calls_but_not_cache(tmp_path) -> None:
    store = Store(tmp_path / "db.sqlite")
    inner = CountingLLM("A", "B")
    llm = _cached(inner, store, monthly=0.01)
    llm.complete(video_id="v1", operation="script", system="S", user="U", schema={})
    with pytest.raises(BudgetExceeded, match="monthly"):
        llm.complete(video_id="v2", operation="script", system="S", user="other", schema={})
    llm.complete(video_id="v1", operation="script", system="S", user="U", schema={})  # cached: ok
    assert inner.calls == 1


def test_per_video_budget(tmp_path) -> None:
    store = Store(tmp_path / "db.sqlite")
    llm = _cached(CountingLLM("A", "B"), store, per_video=0.5)
    llm.complete(video_id="v1", operation="script", system="S", user="U1", schema={})
    with pytest.raises(BudgetExceeded, match="per-video"):
        llm.complete(video_id="v1", operation="script", system="S", user="U2", schema={})


# ---------------------------------------------------------------- full pipeline with fake LLM


@pytest.fixture
def ctx(tmp_settings) -> Context:
    return Context.load(tmp_settings)


def _good_writer_json(ctx, brief) -> str:
    return write_from_template(
        brief, ctx.facts(brief.as_of), ctx.dataset, ctx.scoring
    ).model_dump_json()


def _bad_writer_json(good: str) -> str:
    data = json.loads(good)
    for s in data["sections"]:
        if s["id"] == "product-tarang-t9":
            s["narration"] += " इसकी बैटरी 2 दिन चलती है।"
    return json.dumps(data, ensure_ascii=False)


def test_llm_pipeline_fixes_errors_on_retry(ctx) -> None:
    brief = ctx.brief("best_under", {"band": 20000}, ctx.settings.demo.as_of, "hi")
    good = _good_writer_json(ctx, brief)
    store = Store(ctx.settings.paths.db)
    inner = CountingLLM(_bad_writer_json(good), good)
    script, report, d = generate(ctx, brief, writer="llm", llm=_cached(inner, store))
    assert inner.calls == 2 and report.status == "pass"
    assert script.writer == "llm:claude-opus-5-5"
    assert (d / "script.json").exists() and (d / "factcheck.json").exists()
    ops = [r["operation"] for r in store.video_costs(brief.video_id)]
    assert ops == ["script", "script_fix_1"]


def test_llm_pipeline_blocks_when_still_wrong(ctx) -> None:
    brief = ctx.brief("best_under", {"band": 20000}, ctx.settings.demo.as_of, "hi")
    bad = _bad_writer_json(_good_writer_json(ctx, brief))
    store = Store(ctx.settings.paths.db)
    with pytest.raises(FactCheckFailed) as exc:
        generate(ctx, brief, writer="llm", llm=_cached(CountingLLM(bad, bad), store))
    assert exc.value.report.status == "fail"
    saved = json.loads((exc.value.video_dir / "factcheck.json").read_text())
    assert saved["status"] == "fail"


def test_llm_pipeline_recovers_from_invalid_json(ctx) -> None:
    brief = ctx.brief("best_under", {"band": 20000}, ctx.settings.demo.as_of, "hi")
    store = Store(ctx.settings.paths.db)
    inner = CountingLLM('{"title": 1}', _good_writer_json(ctx, brief))
    _s, report, _d = generate(ctx, brief, writer="llm", llm=_cached(inner, store))
    assert report.status == "pass" and inner.calls == 2
