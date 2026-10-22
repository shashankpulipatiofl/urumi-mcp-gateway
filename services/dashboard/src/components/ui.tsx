import { ReactNode, useEffect, useState } from "react";

export function StatusBadge({ status }: { status: string }) {
  const label: Record<string, string> = {
    connected: "Connected",
    unreachable: "Unreachable",
    disabled: "Disabled",
    unknown: "Checking…",
  };
  return (
    <span className={`badge badge-${status}`}>
      <span className="dot" />
      {label[status] ?? status}
    </span>
  );
}

export function RoleBadge({ role }: { role: string }) {
  return <span className={`pill ${role === "admin" ? "pill-accent" : ""}`}>{role}</span>;
}

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className={`modal ${wide ? "modal-wide" : ""}`} onMouseDown={(e) => e.stopPropagation()} role="dialog" aria-label={title}>
        <div className="modal-head">
          <h3>{title}</h3>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      className="btn btn-small"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
        } catch {
          const el = document.createElement("textarea");
          el.value = text;
          document.body.appendChild(el);
          el.select();
          document.execCommand("copy");
          el.remove();
        }
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
    >
      {copied ? "Copied ✓" : label}
    </button>
  );
}

export function CodeBlock({ code }: { code: string }) {
  return (
    <div className="code">
      <pre>{code}</pre>
      <div className="code-copy">
        <CopyButton text={code} />
      </div>
    </div>
  );
}

export function ErrorBanner({ error }: { error: string | null }) {
  if (!error) return null;
  return <div className="alert alert-error">{error}</div>;
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <strong>{title}</strong>
      {children && <div className="muted">{children}</div>}
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {subtitle && <p className="muted">{subtitle}</p>}
      </div>
      {actions && <div className="actions">{actions}</div>}
    </div>
  );
}

export function clientSnippets(mcpUrl: string, key: string) {
  return {
    claude: `claude mcp add --transport http urumi-gateway ${mcpUrl} \\\n  --header "Authorization: Bearer ${key}"`,
    cursor: JSON.stringify(
      { mcpServers: { "urumi-gateway": { url: mcpUrl, headers: { Authorization: `Bearer ${key}` } } } },
      null,
      2,
    ),
    inspector: `npx @modelcontextprotocol/inspector\n# Transport: Streamable HTTP   URL: ${mcpUrl}\n# Header: Authorization = Bearer ${key}`,
    curl: `curl -s ${mcpUrl} \\\n  -H "Authorization: Bearer ${key}" \\\n  -H "Content-Type: application/json" \\\n  -H "Accept: application/json, text/event-stream" \\\n  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'`,
  };
}

export function SnippetTabs({ mcpUrl, apiKey }: { mcpUrl: string; apiKey: string }) {
  const snippets = clientSnippets(mcpUrl, apiKey);
  const tabs: [keyof typeof snippets, string][] = [
    ["claude", "Claude Code"],
    ["cursor", "Cursor (mcp.json)"],
    ["inspector", "MCP Inspector"],
    ["curl", "curl"],
  ];
  const [tab, setTab] = useState<keyof typeof snippets>("claude");
  return (
    <div>
      <div className="tabs">
        {tabs.map(([id, label]) => (
          <button key={id} className={`tab ${tab === id ? "active" : ""}`} onClick={() => setTab(id)}>
            {label}
          </button>
        ))}
      </div>
      <CodeBlock code={snippets[tab]} />
    </div>
  );
}
