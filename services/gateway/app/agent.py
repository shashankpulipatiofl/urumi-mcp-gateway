"""The dashboard chat agent: an LLM tool loop that reaches tools *through the gateway*.

How it acts as the logged-in user
---------------------------------
1. The dashboard calls POST /api/chat with the user's session cookie.
2. We mint a 5-minute internal token (JWT, audience "mcp-internal") for that user.
3. The agent opens a real MCP client session to this gateway's own /mcp endpoint with that
   token. The gateway resolves it to the same user, so tools/list is filtered by the user's
   role and every tools/call is audited under the user (source = "chat").
So the chat agent takes exactly the same path as Claude Code or Cursor: no back door.

The tool loop
-------------
  messages + tool list --> LLM
     LLM answers with text?           --> done, return it
     LLM asks for function calls?     --> run each through the gateway, append the results,
                                          ask the LLM again (at most LLM_MAX_STEPS rounds)
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import httpx
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client

from .config import Settings
from .security import create_internal_token
from .upstreams import SEPARATOR

log = logging.getLogger("gateway.agent")

ExecuteFn = Callable[[str, dict[str, Any]], Awaitable[str]]
MAX_RESULT_CHARS = 12_000


_sleep = asyncio.sleep   # indirection so tests can skip real waiting


class AgentError(Exception):
    pass


def system_prompt(user_name: str, role: str) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"You are the team assistant inside the Urumi MCP Gateway dashboard. You are acting for {user_name} "
        f"(role: {role}). Current time: {now}.\n"
        "Answer by calling the available tools; never invent store data. Tool names look like "
        f"'<server>{SEPARATOR}<tool>' and the prefix is the upstream server that runs it. If a tool returns an "
        "error, explain it plainly. Keep answers short and use bullet points or small tables for lists."
    )


# ------------------------------------------------------------------------- JSON schema helpers
def _resolve_refs(schema: Any, defs: dict[str, Any]) -> Any:
    if isinstance(schema, dict):
        if "$ref" in schema:
            name = schema["$ref"].split("/")[-1]
            return _resolve_refs(copy.deepcopy(defs.get(name, {})), defs)
        return {k: _resolve_refs(v, defs) for k, v in schema.items() if k not in ("$defs", "definitions")}
    if isinstance(schema, list):
        return [_resolve_refs(v, defs) for v in schema]
    return schema


_GEMINI_KEYS = {"type", "format", "description", "nullable", "enum", "properties", "required", "items",
                "minimum", "maximum", "minItems", "maxItems", "minLength", "maxLength", "anyOf"}


def to_gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Gemini accepts an OpenAPI-style subset of JSON Schema. Convert Pydantic-style schemas."""
    defs = {**schema.get("$defs", {}), **schema.get("definitions", {})}
    resolved = _resolve_refs(schema, defs)

    def clean(node: Any) -> Any:
        if not isinstance(node, dict):
            return node
        node = dict(node)
        if "anyOf" in node:  # Optional[X] -> X with nullable; several string enums -> one enum
            options = [o for o in node["anyOf"] if not (isinstance(o, dict) and o.get("type") == "null")]
            nullable = len(options) < len(node["anyOf"])
            if options and all(isinstance(o, dict) and ("enum" in o or "const" in o) for o in options):
                values = [v for o in options for v in (o.get("enum") or [o.get("const")])]
                node = {k: v for k, v in node.items() if k != "anyOf"} | {"type": "string", "enum": values}
                if nullable:
                    node["nullable"] = True
            elif len(options) == 1:
                merged = {**options[0], **{k: v for k, v in node.items() if k not in ("anyOf",)}}
                merged["nullable"] = True
                node = merged
        if isinstance(node.get("type"), list):  # ["string","null"]
            types_ = [t for t in node["type"] if t != "null"]
            node["type"] = types_[0] if types_ else "string"
            node["nullable"] = True
        if "const" in node:
            node["enum"] = [node.pop("const")]
        if "enum" in node:
            node["enum"] = [str(v) for v in node["enum"]]
            node["type"] = "string"
        out = {k: v for k, v in node.items() if k in _GEMINI_KEYS}
        if "properties" in out:
            out["properties"] = {k: clean(v) for k, v in out["properties"].items()}
            out.setdefault("type", "object")
        if "items" in out:
            out["items"] = clean(out["items"])
        if "anyOf" in out:
            out["anyOf"] = [clean(o) for o in out["anyOf"]]
        return out

    result = clean(resolved)
    if result.get("type") == "object" and not result.get("properties"):
        return {}  # Gemini rejects empty object schemas: omit parameters entirely
    return result


def to_openai_schema(schema: dict[str, Any]) -> dict[str, Any]:
    defs = {**schema.get("$defs", {}), **schema.get("definitions", {})}
    resolved = _resolve_refs(schema, defs)
    resolved.pop("$schema", None)
    resolved.setdefault("type", "object")
    resolved.setdefault("properties", {})
    return resolved


# ------------------------------------------------------------------------- providers
class GeminiProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base = (self.settings.llm_base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
        models = [self.settings.llm_model] + [m.strip() for m in self.settings.llm_fallback_models.split(",") if m.strip()]
        self.models = list(dict.fromkeys(models))   # de-duplicate, keep order
        self.thinking_ok = self.settings.llm_thinking == "low"
        self.dead_models: set[str] = set()
        self.last_model = self.settings.llm_model

    async def run(self, system: str, messages: list[dict[str, str]], tools: list[types.Tool],
                  execute: ExecuteFn) -> str:
        decls = []
        for t in tools:
            decl: dict[str, Any] = {"name": t.name, "description": (t.description or "")[:1000]}
            params = to_gemini_schema(t.inputSchema or {})
            if params:
                decl["parameters"] = params
            decls.append(decl)
        contents = [{"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]}
                    for m in messages]
        async with httpx.AsyncClient(timeout=self.settings.llm_timeout_seconds) as http:
            for _ in range(self.settings.llm_max_steps):
                body: dict[str, Any] = {"systemInstruction": {"parts": [{"text": system}]}, "contents": contents}
                if decls:
                    body["tools"] = [{"functionDeclarations": decls}]
                data = await self._generate(http, body)
                candidates = data.get("candidates") or []
                if not candidates:
                    raise AgentError(f"Gemini returned no answer ({data.get('promptFeedback', {})})")
                parts = (candidates[0].get("content") or {}).get("parts") or []
                calls = [p["functionCall"] for p in parts if "functionCall" in p]
                if not calls:
                    # Skip "thought" parts (internal reasoning summaries of thinking models)
                    text = "".join(str(p.get("text", "")) for p in parts if not p.get("thought"))
                    return text.strip() or "(no answer)"
                # Echo the model turn back unchanged (keeps Gemini "thought signatures" intact).
                contents.append({"role": "model", "parts": parts})
                responses = []
                for call in calls:
                    output = await execute(call["name"], call.get("args") or {})
                    fr: dict[str, Any] = {"name": call["name"], "response": {"result": output}}
                    if call.get("id"):
                        fr["id"] = call["id"]
                    responses.append({"functionResponse": fr})
                contents.append({"role": "user", "parts": responses})
        return "I stopped after the maximum number of tool-call rounds. Try a more specific question."

    async def _generate(self, http: httpx.AsyncClient, body: dict[str, Any]) -> dict[str, Any]:
        """One generateContent call, tuned for speed on the free tier:
        * busy/overloaded (429/5xx) or unknown model (404) -> switch to the next model IMMEDIATELY,
        * only when every model is busy, wait briefly once and go round again,
        * ask for no/minimal "thinking" and drop that hint if a model rejects it."""
        last_error = "no model available"
        for round_no in range(2):
            if round_no:
                await _sleep(1.0)
            for model in self.models:
                if model in self.dead_models:
                    continue
                for _ in range(2):  # second try only happens after dropping the thinking hint
                    payload = dict(body)
                    if self.thinking_ok:
                        thinking = {"thinkingBudget": 0} if "2.5" in model else {"thinkingLevel": "minimal"}
                        payload["generationConfig"] = {"thinkingConfig": thinking}
                    r = await http.post(f"{self.base}/models/{model}:generateContent", json=payload,
                                        headers={"x-goog-api-key": self.settings.llm_api_key})
                    if r.status_code < 400:
                        self.last_model = model
                        return r.json()
                    message = _api_message(r)
                    last_error = f"Gemini API error {r.status_code} ({model}): {message}"
                    if r.status_code == 400 and "think" in message.lower() and self.thinking_ok:
                        self.thinking_ok = False
                        continue
                    break
                if r.status_code == 404:
                    self.dead_models.add(model)        # retired/unknown model: never try it again
                    log.warning("%s; skipping this model", last_error)
                    continue
                if r.status_code in (429, 500, 502, 503, 504):
                    log.warning("%s; trying next model", last_error)
                    continue
                raise AgentError(last_error)
        raise AgentError(f"{last_error}. The free Gemini tier is busy; please try again in a minute.")


class OpenAICompatibleProvider:
    """OpenAI, Groq, OpenRouter, Ollama, vLLM, ... anything that speaks /chat/completions."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base = (settings.llm_base_url or "https://api.openai.com/v1").rstrip("/")
        self.last_model = settings.llm_model
        # Same resilience as Gemini: main model first, then fallbacks (Gemini names are skipped here).
        fallbacks = [m.strip() for m in settings.llm_fallback_models.split(",")
                     if m.strip() and not m.strip().startswith("gemini")]
        self.models = list(dict.fromkeys([settings.llm_model, *fallbacks]))
        self.dead_models: set[str] = set()
        # gpt-oss style reasoning models: ask for little hidden reasoning -> faster answers
        self.reasoning_ok = settings.llm_thinking == "low"

    async def _complete(self, http: httpx.AsyncClient, body: dict[str, Any], headers: dict[str, str]) -> dict:
        """POST /chat/completions with instant failover: busy (429/5xx), retired (404) or a
        malformed tool call from the model ("tool_use_failed") -> try the next model."""
        last_error = "no model available"
        for round_no in range(2):
            if round_no:
                await _sleep(1.0)
            for model in self.models:
                if model in self.dead_models:
                    continue
                for _ in range(2):  # second try only after dropping the reasoning hint
                    payload = {**body, "model": model}
                    if self.reasoning_ok and "gpt-oss" in model:
                        payload["reasoning_effort"] = "low"
                    r = await http.post(f"{self.base}/chat/completions", json=payload, headers=headers)
                    if r.status_code < 400:
                        self.last_model = model
                        return r.json()
                    message = _api_message(r)
                    last_error = f"LLM API error {r.status_code} ({model}): {message}"
                    if r.status_code == 400 and "reasoning" in message.lower() and self.reasoning_ok:
                        self.reasoning_ok = False
                        continue
                    break
                if r.status_code == 404:
                    self.dead_models.add(model)
                    log.warning("%s; skipping this model", last_error)
                    continue
                if r.status_code in (429, 500, 502, 503, 504) or "tool_use_failed" in message:
                    log.warning("%s; trying next model", last_error)
                    continue
                raise AgentError(last_error)
        raise AgentError(f"{last_error}. The LLM provider is busy or rate-limited; please try again in a minute.")

    async def run(self, system: str, messages: list[dict[str, str]], tools: list[types.Tool],
                  execute: ExecuteFn) -> str:
        oa_tools = [{"type": "function", "function": {"name": t.name, "description": (t.description or "")[:1000],
                                                      "parameters": to_openai_schema(t.inputSchema or {})}}
                    for t in tools]
        msgs: list[dict[str, Any]] = [{"role": "system", "content": system}, *messages]
        headers = {"Authorization": f"Bearer {self.settings.llm_api_key}"} if self.settings.llm_api_key else {}
        async with httpx.AsyncClient(timeout=self.settings.llm_timeout_seconds) as http:
            for _ in range(self.settings.llm_max_steps):
                body: dict[str, Any] = {"messages": msgs}
                if oa_tools:
                    body["tools"] = oa_tools
                    body["tool_choice"] = "auto"
                msg = (await self._complete(http, body, headers))["choices"][0]["message"]
                calls = msg.get("tool_calls") or []
                if not calls:
                    return str(msg.get("content") or "").strip() or "(no answer)"
                msgs.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
                for call in calls:
                    try:
                        args = json.loads(call["function"].get("arguments") or "{}")
                    except ValueError:
                        args = {}
                    output = await execute(call["function"]["name"], args)
                    msgs.append({"role": "tool", "tool_call_id": call["id"], "content": output})
        return "I stopped after the maximum number of tool-call rounds. Try a more specific question."


def _api_message(r: httpx.Response) -> str:
    try:
        data = r.json()
        err = data.get("error", data)
        return (err.get("message") if isinstance(err, dict) else str(err))[:300]
    except ValueError:
        return r.text[:300]


def make_provider(settings: Settings):
    if settings.llm_provider == "gemini":
        return GeminiProvider(settings)
    if settings.llm_provider == "openai":
        return OpenAICompatibleProvider(settings)
    raise AgentError(f"Unknown LLM_PROVIDER '{settings.llm_provider}'")


# ------------------------------------------------------------------------- entry point
async def run_agent(settings: Settings, user_id: int, user_name: str, role: str,
                    messages: list[dict[str, str]], provider=None) -> dict[str, Any]:
    if provider is None:
        if settings.llm_provider == "gemini" and not settings.llm_api_key:
            raise AgentError("The chat agent is not configured: set the LLM API key Secret (see README).")
        provider = make_provider(settings)

    token = create_internal_token(settings.secret_key, user_id)
    trace: list[dict[str, Any]] = []

    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"},
                                 timeout=httpx.Timeout(settings.call_timeout_seconds + 10, connect=5)) as http:
        async with streamable_http_client(settings.self_mcp_url, http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools

                async def execute(name: str, arguments: dict[str, Any]) -> str:
                    started = time.perf_counter()
                    result = await session.call_tool(name, arguments)
                    text = "\n".join(c.text for c in result.content if isinstance(c, types.TextContent))
                    if not text and result.structuredContent is not None:
                        text = json.dumps(result.structuredContent)
                    trace.append({
                        "name": name,
                        "server": name.split(SEPARATOR, 1)[0],
                        "arguments": arguments,
                        "ok": not result.isError,
                        "result": text[:4000],
                        "duration_ms": int((time.perf_counter() - started) * 1000),
                    })
                    return text[:MAX_RESULT_CHARS] if not result.isError else f"ERROR: {text[:2000]}"

                started = time.perf_counter()
                reply = await provider.run(system_prompt(user_name, role), messages, tools, execute)
                total_ms = int((time.perf_counter() - started) * 1000)

    tools_ms = sum(t["duration_ms"] for t in trace)
    timing = {"total_ms": total_ms, "tools_ms": tools_ms, "llm_ms": max(total_ms - tools_ms, 0),
              "model": getattr(provider, "last_model", settings.llm_model)}
    log.info("chat answered in %sms (llm %sms, tools %sms, model %s)", total_ms, timing["llm_ms"], tools_ms,
             timing["model"])
    return {"reply": reply, "tool_calls": trace, "tools_available": len(tools), "timing": timing}
