"""A scripted fake LLM that speaks both the Gemini and the OpenAI wire formats.

Turn 1: asks for woocommerce__list_orders(period=today) and fx-rates__convert_currency.
Turn 2: (after tool results arrive) answers with text that quotes the tool results.
Used by tests and by the local e2e check; no real model or API key needed.
"""
import json
from fastapi import FastAPI, Request

app = FastAPI()
CALLS = [("woocommerce__list_orders", {"period": "today"}),
         ("fx-rates__convert_currency", {"amount": 1998, "from_currency": "INR", "to_currency": "USD"})]


def _available(names):
    chosen = [(n, a) for n, a in CALLS if n in names]
    # In the unit-test stack the upstreams are "alpha"/"beta": call every *__echo tool instead.
    return chosen or [(n, {"text": "hi"}) for n in sorted(names) if n.endswith("__echo")]


@app.post("/v1beta/models/{model}:generateContent")
async def gemini(model: str, request: Request):
    body = await request.json()
    names = {d["name"] for t in body.get("tools", []) for d in t["functionDeclarations"]}
    last = body["contents"][-1]["parts"]
    results = [p["functionResponse"] for p in last if "functionResponse" in p]
    if results:
        text = "Here is what I found: " + " | ".join(json.dumps(r["response"]["result"])[:120] for r in results)
        return {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}}]}
    parts = [{"functionCall": {"name": n, "args": a}, "thoughtSignature": "sig"} for n, a in _available(names)]
    return {"candidates": [{"content": {"role": "model", "parts": parts or [{"text": "no tools"}]}}]}


@app.post("/v1/chat/completions")
async def openai(request: Request):
    body = await request.json()
    names = {t["function"]["name"] for t in body.get("tools", [])}
    if body["messages"][-1]["role"] == "tool":
        tool_msgs = [m["content"][:120] for m in body["messages"] if m["role"] == "tool"]
        return {"choices": [{"message": {"role": "assistant", "content": "Answer: " + " | ".join(tool_msgs)}}]}
    calls = [{"id": f"call_{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
             for i, (n, a) in enumerate(_available(names))]
    return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": calls}}]}
