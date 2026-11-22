"""Claude client for script writing, wrapped with a response cache and cost ledger.

* Structured output: the request sets ``output_config.format`` to the
  WriterScript JSON schema, so the reply is schema-valid JSON.
* Refusal fallback: server-side ``fallbacks="default"`` (beta) re-runs a
  safety-declined request on a fallback model inside the same call. The ledger
  prices whichever model actually served the request.
* Cache: identical requests (model, effort, prompts, schema) are served from
  SQLite and logged at ₹0. Nothing is ever sent twice.
* Budget: a new paid call is refused once the monthly or per-video cap is hit.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from channel.config import Price
from channel.store import Budget, CostEntry, Store

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ScriptGenerationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str  # model that served the request (may be a fallback)
    usage: Usage
    stop_reason: str


class ScriptLLM(Protocol):
    def identity(self) -> dict[str, Any]: ...

    def complete(self, *, system: str, user: str, schema: dict[str, Any]) -> LLMResponse: ...


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Make a pydantic JSON schema acceptable to structured outputs: every object
    gets additionalProperties=false and all properties required; drop titles and
    defaults."""
    if isinstance(schema, dict):
        out = {k: strict_schema(v) for k, v in schema.items() if k not in ("title", "default")}
        if out.get("type") == "object" and "properties" in out:
            out["additionalProperties"] = False
            out["required"] = sorted(out["properties"])
        return out
    if isinstance(schema, list):
        return [strict_schema(v) for v in schema]
    return schema


class AnthropicScriptLLM:
    def __init__(self, *, model: str, effort: str, max_tokens: int, client: Any = None) -> None:
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self._client = client

    def identity(self) -> dict[str, Any]:
        return {
            "provider": "anthropic",
            "model": self.model,
            "effort": self.effort,
            "max_tokens": self.max_tokens,
        }

    def _get_client(self) -> Any:
        if self._client is None:
            if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
                raise ScriptGenerationError(
                    "no Anthropic credentials: set ANTHROPIC_API_KEY in .env "
                    "(or use --writer template for an offline script)"
                )
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    def complete(self, *, system: str, user: str, schema: dict[str, Any]) -> LLMResponse:
        import anthropic

        try:
            with self._get_client().beta.messages.stream(
                model=self.model,
                max_tokens=self.max_tokens,
                betas=[FALLBACK_BETA],
                fallbacks="default",
                output_config={
                    "effort": self.effort,
                    "format": {"type": "json_schema", "schema": schema},
                },
                system=system,
                messages=[{"role": "user", "content": user}],
            ) as stream:
                msg = stream.get_final_message()
        except anthropic.AuthenticationError as exc:
            raise ScriptGenerationError(
                "Anthropic authentication failed: set ANTHROPIC_API_KEY in .env, or use "
                "--writer template"
            ) from exc

        if msg.stop_reason == "refusal":
            details = getattr(msg, "stop_details", None)
            raise ScriptGenerationError(f"model declined the request (refusal): {details}")
        if msg.stop_reason == "max_tokens":
            raise ScriptGenerationError(
                f"response hit max_tokens={self.max_tokens}; raise llm.max_tokens"
            )
        text = next((b.text for b in msg.content if b.type == "text"), None)
        if text is None:
            raise ScriptGenerationError("response contained no text block")
        u = msg.usage
        return LLMResponse(
            text=text,
            model=msg.model,
            usage=Usage(
                input_tokens=u.input_tokens or 0,
                output_tokens=u.output_tokens or 0,
                cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
                cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
            ),
            stop_reason=msg.stop_reason or "",
        )


def cost_usd(usage: Usage, model: str, pricing: dict[str, Price]) -> tuple[float, str | None]:
    """Cost of one call. Unknown models are charged at the highest listed rates."""
    note = None
    price = pricing.get(model)
    if price is None:
        note = f"unpriced model {model!r}: charged at highest listed rates"
        price = Price(
            input=max(p.input for p in pricing.values()),
            output=max(p.output for p in pricing.values()),
            cache_read=max(p.cache_read for p in pricing.values()),
            cache_write=max(p.cache_write for p in pricing.values()),
        )
    usd = (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_read_tokens * price.cache_read
        + usage.cache_write_tokens * price.cache_write
    ) / 1_000_000
    return usd, note


class CachedLLM:
    """Cache + budget + ledger around any ScriptLLM."""

    def __init__(
        self,
        inner: ScriptLLM,
        *,
        store: Store,
        budget: Budget,
        pricing: dict[str, Price],
        usd_to_inr: float,
    ) -> None:
        self.inner = inner
        self.store = store
        self.budget = budget
        self.pricing = pricing
        self.usd_to_inr = usd_to_inr

    @property
    def label(self) -> str:
        return str(self.inner.identity().get("model", "unknown"))

    def cache_key(self, system: str, user: str, schema: dict[str, Any]) -> str:
        blob = json.dumps(
            {"id": self.inner.identity(), "system": system, "user": user, "schema": schema},
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def complete(
        self, *, video_id: str, operation: str, system: str, user: str, schema: dict[str, Any]
    ) -> LLMResponse:
        key = self.cache_key(system, user, schema)
        hit = self.store.cache_get(key)
        if hit is not None:
            resp = LLMResponse(
                text=hit["text"],
                model=hit["model"],
                usage=Usage(**hit["usage"]),
                stop_reason=hit["stop_reason"],
            )
            self.store.log_cost(
                CostEntry(
                    video_id=video_id,
                    provider="anthropic",
                    operation=operation,
                    model=resp.model,
                    usd=0.0,
                    inr=0.0,
                    cached=True,
                    note="cache hit",
                )
            )
            return resp

        self.budget.check(self.store, video_id)
        resp = self.inner.complete(system=system, user=user, schema=schema)
        usd, note = cost_usd(resp.usage, resp.model, self.pricing)
        self.store.cache_put(
            key,
            "anthropic",
            {
                "text": resp.text,
                "model": resp.model,
                "usage": asdict(resp.usage),
                "stop_reason": resp.stop_reason,
            },
        )
        self.store.log_cost(
            CostEntry(
                video_id=video_id,
                provider="anthropic",
                operation=operation,
                model=resp.model,
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
                cache_read_tokens=resp.usage.cache_read_tokens,
                cache_write_tokens=resp.usage.cache_write_tokens,
                usd=usd,
                inr=usd * self.usd_to_inr,
                cached=False,
                note=note,
            )
        )
        return resp
