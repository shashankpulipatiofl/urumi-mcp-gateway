import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, Server, timeAgo } from "../api";
import { useSession } from "../App";
import { Empty, ErrorBanner, Modal, PageHeader, StatusBadge } from "../components/ui";

type FormState = {
  slug: string;
  display_name: string;
  url: string;
  description: string;
  auth_type: "none" | "bearer" | "header";
  auth_header_name: string;
  credential: string;
  admin_only_tools: string[];
  enabled: boolean;
};

const EMPTY: FormState = {
  slug: "",
  display_name: "",
  url: "",
  description: "",
  auth_type: "none",
  auth_header_name: "",
  credential: "",
  admin_only_tools: [],
  enabled: true,
};

export default function Servers() {
  const { user } = useSession();
  const isAdmin = user.role === "admin";
  const [servers, setServers] = useState<Server[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Server | "new" | null>(null);
  const [open, setOpen] = useState<Record<number, boolean>>({});
  const [busy, setBusy] = useState<number | null>(null);

  const load = useCallback(() => {
    api.get<Server[]>("/api/servers").then(setServers).catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [load]);

  const act = async (id: number, fn: () => Promise<unknown>) => {
    setBusy(id);
    setError(null);
    try {
      await fn();
      load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div>
      <PageHeader
        title="MCP Servers"
        subtitle="Upstream servers the gateway connects to. Status is re-checked every 15 seconds."
        actions={
          isAdmin && (
            <button className="btn btn-primary" onClick={() => setEditing("new")}>
              + Add server
            </button>
          )
        }
      />
      <ErrorBanner error={error} />
      {servers.length === 0 && <Empty title="No servers registered yet" />}
      <div className="server-list">
        {servers.map((s) => (
          <section key={s.id} className={`card server ${!s.enabled ? "is-disabled" : ""}`}>
            <div className="server-head">
              <div className="server-title">
                <h2>{s.display_name}</h2>
                <span className="pill mono">{s.slug}__*</span>
                <StatusBadge status={s.status} />
              </div>
              {isAdmin && (
                <div className="actions">
                  <button className="btn btn-small" disabled={busy === s.id} onClick={() => act(s.id, () => api.post(`/api/servers/${s.id}/refresh`))}>
                    Re-check
                  </button>
                  <button className="btn btn-small" onClick={() => setEditing(s)}>
                    Edit
                  </button>
                  <button
                    className="btn btn-small"
                    disabled={busy === s.id}
                    onClick={() => act(s.id, () => api.patch(`/api/servers/${s.id}`, { enabled: !s.enabled }))}
                  >
                    {s.enabled ? "Disable" : "Enable"}
                  </button>
                  <button
                    className="btn btn-small btn-danger"
                    disabled={busy === s.id}
                    onClick={() => confirm(`Remove "${s.display_name}"? Its tools disappear for everyone.`) && act(s.id, () => api.del(`/api/servers/${s.id}`))}
                  >
                    Remove
                  </button>
                </div>
              )}
            </div>
            {s.description && <p className="muted">{s.description}</p>}
            <div className="meta-row">
              {s.url && (
                <span>
                  <span className="muted">URL</span> <code>{s.url}</code>
                </span>
              )}
              <span>
                <span className="muted">Auth</span>{" "}
                {s.auth_type === "none" ? "none" : `${s.auth_type === "bearer" ? "Bearer token" : `Header ${s.auth_header_name || ""}`}${s.has_credential ? " · stored encrypted" : " · missing!"}`}
              </span>
              {s.server_info && (
                <span>
                  <span className="muted">Reports</span> {s.server_info}
                </span>
              )}
              <span>
                <span className="muted">Checked</span> {timeAgo(s.checked_at)}
                {s.latency_ms != null && s.status === "connected" ? ` · ${s.latency_ms} ms` : ""}
              </span>
            </div>
            {s.error && s.status === "unreachable" && <div className="alert alert-error small">{s.error}</div>}
            {s.enabled && s.tools.length > 0 && (
              <div className="tools">
                <button className="link-btn" onClick={() => setOpen({ ...open, [s.id]: !open[s.id] })}>
                  {open[s.id] ? "▾" : "▸"} {s.tools.length} tools
                </button>
                {open[s.id] && (
                  <ul className="tool-list">
                    {s.tools.map((t) => (
                      <li key={t.name}>
                        <code>{t.name}</code>
                        {t.admin_only && <span className="pill pill-warn">admin only</span>}
                        <span className="muted"> {t.description}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </section>
        ))}
      </div>
      {editing && (
        <ServerForm
          server={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            load();
          }}
        />
      )}
    </div>
  );
}

function ServerForm({ server, onClose, onSaved }: { server: Server | null; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState<FormState>(
    server
      ? {
          slug: server.slug,
          display_name: server.display_name,
          url: server.url ?? "",
          description: server.description,
          auth_type: server.auth_type,
          auth_header_name: server.auth_header_name,
          credential: "",
          admin_only_tools: server.admin_only_tools,
          enabled: server.enabled,
        }
      : EMPTY,
  );
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => setF({ ...f, [k]: v });

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const body: Record<string, unknown> = { ...f };
      if (server) {
        delete body.slug;
        if (!f.credential) delete body.credential; // blank = keep the stored secret
        await api.patch(`/api/servers/${server.id}`, body);
      } else {
        await api.post("/api/servers", body);
      }
      onSaved();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title={server ? `Edit ${server.display_name}` : "Add MCP server"} onClose={onClose} wide>
      <form className="form" onSubmit={submit}>
        <div className="form-row">
          <label>
            Display name
            <input value={f.display_name} onChange={(e) => set("display_name", e.target.value)} required placeholder="GitHub" />
          </label>
          <label>
            Slug (tool prefix)
            <input
              value={f.slug}
              onChange={(e) => set("slug", e.target.value.toLowerCase())}
              required
              disabled={!!server}
              placeholder="github"
              pattern="[a-z][a-z0-9-]{1,30}[a-z0-9]"
              title="3-32 chars: lowercase letters, digits, dashes"
            />
          </label>
        </div>
        <label>
          Streamable HTTP URL
          <input value={f.url} onChange={(e) => set("url", e.target.value)} required placeholder="https://mcp.example.com/mcp" />
          <span className="hint">Private / cluster-internal addresses are blocked unless they belong to this platform's namespace (SSRF protection).</span>
        </label>
        <label>
          Description
          <input value={f.description} onChange={(e) => set("description", e.target.value)} />
        </label>
        <div className="form-row">
          <label>
            Credential type
            <select value={f.auth_type} onChange={(e) => set("auth_type", e.target.value as FormState["auth_type"])}>
              <option value="none">None</option>
              <option value="bearer">Bearer token (Authorization header)</option>
              <option value="header">Custom header</option>
            </select>
          </label>
          {f.auth_type === "header" && (
            <label>
              Header name
              <input value={f.auth_header_name} onChange={(e) => set("auth_header_name", e.target.value)} placeholder="X-API-Key" required />
            </label>
          )}
        </div>
        {f.auth_type !== "none" && (
          <label>
            Credential
            <input
              type="password"
              autoComplete="new-password"
              value={f.credential}
              onChange={(e) => set("credential", e.target.value)}
              placeholder={server?.has_credential ? "•••••••• stored (leave blank to keep)" : "secret value"}
            />
            <span className="hint">Encrypted at rest. Never sent back to the browser or to MCP clients.</span>
          </label>
        )}
        {server && server.tools.length > 0 && (
          <fieldset>
            <legend>Admin-only tools (hidden from members and rejected if called)</legend>
            <div className="checks">
              {server.tools.map((t) => (
                <label key={t.original_name} className="check">
                  <input
                    type="checkbox"
                    checked={f.admin_only_tools.includes(t.original_name)}
                    onChange={(e) =>
                      set(
                        "admin_only_tools",
                        e.target.checked ? [...f.admin_only_tools, t.original_name] : f.admin_only_tools.filter((x) => x !== t.original_name),
                      )
                    }
                  />
                  <code>{t.original_name}</code>
                </label>
              ))}
            </div>
          </fieldset>
        )}
        <label className="check">
          <input type="checkbox" checked={f.enabled} onChange={(e) => set("enabled", e.target.checked)} /> Enabled
        </label>
        {error && <div className="alert alert-error">{error}</div>}
        <div className="modal-actions">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={saving}>
            {saving ? "Connecting…" : server ? "Save" : "Add server"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
