import { FormEvent, useEffect, useRef, useState } from "react";
import { api, ChatResponse, ChatTiming, ToolCallTrace } from "../api";
import { useSession } from "../App";
import Markdown from "../components/Markdown";
import { PageHeader } from "../components/ui";

interface Turn {
  role: "user" | "assistant";
  content: string;
  tools?: ToolCallTrace[];
  error?: boolean;
  timing?: ChatTiming;
}

const EXAMPLES = [
  "What orders came in today?",
  "Create a product called 'Urumi Hoodie' priced at 1499",
  "List today's orders and convert the total to USD",
  "Summarise today's orders and save it as a team note tagged 'daily'",
];

export default function Chat() {
  const { user, config } = useSession();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);

  // Block body on purpose: newer browsers return a Promise from scrollIntoView(), and React
  // would treat a returned value as a cleanup function and crash ("x is not a function").
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, busy]);

  const send = async (text: string) => {
    if (!text.trim() || busy) return;
    const history = [...turns.filter((t) => !t.error), { role: "user" as const, content: text.trim() }];
    setTurns([...turns, { role: "user", content: text.trim() }]);
    setInput("");
    setBusy(true);
    try {
      const res = await api.post<ChatResponse>("/api/chat", {
        messages: history.slice(-20).map(({ role, content }) => ({ role, content })),
      });
      // Be defensive: whatever comes back, render plain strings and a list.
      const reply = typeof res?.reply === "string" ? res.reply : JSON.stringify(res?.reply ?? res);
      const tools = Array.isArray(res?.tool_calls) ? res.tool_calls : [];
      setTurns((t) => [...t, { role: "assistant", content: reply || "(empty answer)", tools, timing: res?.timing }]);
    } catch (e) {
      setTurns((t) => [...t, { role: "assistant", content: `Error: ${String((e as Error)?.message ?? e)}`, error: true }]);
    } finally {
      setBusy(false);
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    send(input);
  };

  return (
    <div className="chat-page">
      <PageHeader
        title="Chat Agent"
        subtitle={`Answers with the same tools you get over MCP. Every call is audited as ${user.email}.`}
        actions={
          <>
            <span className="pill">{config.llm_model || "LLM"}</span>
            {turns.length > 0 && (
              <button className="btn btn-small" onClick={() => setTurns([])}>
                New chat
              </button>
            )}
          </>
        }
      />
      {!config.chat_enabled && (
        <div className="alert alert-warn">No LLM API key is configured. Set the LLM Secret (see README) to enable the agent.</div>
      )}
      <div className="chat-scroll card">
        {turns.length === 0 && (
          <div className="chat-empty">
            <h3>Ask about your store</h3>
            <div className="examples">
              {EXAMPLES.map((e) => (
                <button key={e} className="example" onClick={() => send(e)}>
                  {e}
                </button>
              ))}
            </div>
          </div>
        )}
        {turns.map((t, i) => (
          <div key={i} className={`msg msg-${t.role} ${t.error ? "msg-error" : ""}`}>
            {t.tools && t.tools.length > 0 && (
              <div className="tool-calls">
                {t.tools.map((c, j) => (
                  <details key={j} className={`tool-call ${c.ok ? "" : "failed"}`}>
                    <summary>
                      <span className="tc-icon">{c.ok ? "⚙" : "⚠"}</span>
                      <code>{String(c.name)}</code>
                      <span className="muted small">{c.duration_ms} ms</span>
                    </summary>
                    <div className="tc-body">
                      <div className="muted small">arguments</div>
                      <pre>{JSON.stringify(c.arguments ?? {}, null, 2)}</pre>
                      <div className="muted small">result</div>
                      <pre>{typeof c.result === "string" ? c.result : JSON.stringify(c.result, null, 2)}</pre>
                    </div>
                  </details>
                ))}
              </div>
            )}
            {t.role === "assistant" && !t.error ? (
              <div className="bubble bubble-md">
                <Markdown text={t.content} />
              </div>
            ) : (
              <div className="bubble">{t.content}</div>
            )}
            {t.timing && (
              <div className="timing">
                {(t.timing.total_ms / 1000).toFixed(1)} s · {t.timing.model} · AI {(t.timing.llm_ms / 1000).toFixed(1)} s · tools{" "}
                {(t.timing.tools_ms / 1000).toFixed(1)} s
              </div>
            )}
          </div>
        ))}
        {busy && (
          <div className="msg msg-assistant">
            <div className="bubble typing">
              <span />
              <span />
              <span />
              <em className="typing-label">thinking and calling tools through the gateway…</em>
            </div>
          </div>
        )}
        <div ref={bottom} />
      </div>
      <form className="chat-input" onSubmit={submit}>
        <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="Ask something… e.g. what orders came in today?" disabled={busy} />
        <button className="btn btn-primary" disabled={busy || !input.trim()}>
          Send
        </button>
      </form>
    </div>
  );
}
