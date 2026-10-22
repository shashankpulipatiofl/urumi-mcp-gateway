"""Gemini free-tier resilience: retries, model fallback, thinking-hint downgrade."""
import asyncio

import httpx
import pytest

from app import agent as agent_mod
from app.agent import AgentError, GeminiProvider
from app.config import Settings

OK = {"candidates": [{"content": {"parts": [{"text": "hello"}]}}]}


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    # Only inside this test module, and restored afterwards by pytest
    monkeypatch.setattr(agent_mod, "_sleep", _no_sleep)


def run_generate(handler, **cfg):
    calls = []

    def wrapped(request):
        calls.append(request)
        return handler(request)

    async def go():
        p = GeminiProvider(Settings(llm_api_key="k", **cfg))
        p.base = "http://gemini"
        p.models = [cfg.get("llm_model", "main")] + [m for m in cfg.get("llm_fallback_models", "lite").split(",")]
        p.thinking_ok = True
        async with httpx.AsyncClient(transport=httpx.MockTransport(wrapped)) as http:
            return await p._generate(http, {"contents": []})

    return asyncio.run(go()), calls


async def _no_sleep(_):
    return None


def test_overloaded_model_falls_back():
    def handler(r):
        if "/models/main:" in str(r.url):
            return httpx.Response(503, json={"error": {"message": "high demand"}})
        return httpx.Response(200, json=OK)
    data, calls = run_generate(handler, llm_model="main", llm_fallback_models="lite")
    assert data == OK
    assert sum("/models/main:" in str(c.url) for c in calls) == 1     # busy -> switch immediately
    assert "/models/lite:" in str(calls[-1].url)


def test_retired_model_is_skipped():
    def handler(r):
        if "/models/main:" in str(r.url):
            return httpx.Response(404, json={"error": {"message": "model not found"}})
        return httpx.Response(200, json=OK)
    data, calls = run_generate(handler)
    assert data == OK and len(calls) == 2


def test_thinking_hint_dropped_when_rejected():
    def handler(r):
        if b"thinking" in r.content:
            return httpx.Response(400, json={"error": {"message": "thinking_level is not supported"}})
        return httpx.Response(200, json=OK)
    data, calls = run_generate(handler)
    assert data == OK and b"thinking" not in calls[-1].content


def test_clear_error_when_everything_is_busy():
    with pytest.raises(AgentError, match="busy"):
        run_generate(lambda r: httpx.Response(503, json={"error": {"message": "high demand"}}))


# ---------------------------------------------------------------- OpenAI-compatible (Groq)
from app.agent import OpenAICompatibleProvider  # noqa: E402

OA_OK = {"choices": [{"message": {"role": "assistant", "content": "hi"}}]}


def run_complete(handler, **cfg):
    calls = []

    def wrapped(request):
        calls.append(request)
        return handler(request)

    async def go():
        p = OpenAICompatibleProvider(Settings(llm_provider="openai", llm_api_key="k", llm_base_url="http://groq/v1",
                                              **cfg))
        async with httpx.AsyncClient(transport=httpx.MockTransport(wrapped)) as http:
            return await p._complete(http, {"messages": []}, {}), p

    (data, provider), = [asyncio.run(go())]
    return data, provider, calls


def test_openai_fallback_skips_gemini_names_and_retired_models():
    import json as _json

    def handler(r):
        model = _json.loads(r.content)["model"]
        if model == "old-model":
            return httpx.Response(404, json={"error": {"message": "model does not exist"}})
        if model == "busy-model":
            return httpx.Response(429, json={"error": {"message": "rate limit"}})
        return httpx.Response(200, json=OA_OK)
    data, provider, calls = run_complete(
        handler, llm_model="old-model", llm_fallback_models="gemini-flash-latest,busy-model,openai/gpt-oss-120b")
    assert data == OA_OK and provider.last_model == "openai/gpt-oss-120b"
    assert all("gemini" not in _json.loads(c.content)["model"] for c in calls)


def test_openai_reasoning_hint_for_gpt_oss():
    import json as _json
    data, provider, calls = run_complete(lambda r: httpx.Response(200, json=OA_OK), llm_model="openai/gpt-oss-20b",
                                         llm_fallback_models="")
    assert _json.loads(calls[0].content)["reasoning_effort"] == "low"
