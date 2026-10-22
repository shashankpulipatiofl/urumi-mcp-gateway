import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, ApiKey, formatTime, timeAgo } from "../api";
import { useSession } from "../App";
import { CopyButton, Empty, ErrorBanner, Modal, PageHeader, SnippetTabs } from "../components/ui";

export default function Keys() {
  const { user, config } = useSession();
  const isAdmin = user.role === "admin";
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [showAll, setShowAll] = useState(false);
  const [name, setName] = useState("");
  const [created, setCreated] = useState<ApiKey | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.get<ApiKey[]>(`/api/keys${showAll ? "?all=true" : ""}`).then(setKeys).catch((e) => setError(e.message));
  }, [showAll]);
  useEffect(() => {
    load();
  }, [load]);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    try {
      setCreated(await api.post<ApiKey>("/api/keys", { name: name || "default" }));
      setName("");
      load();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const revoke = async (k: ApiKey) => {
    if (!confirm(`Revoke key "${k.name}" (${k.prefix}…)? Clients using it stop working immediately.`)) return;
    await api.del(`/api/keys/${k.id}`);
    load();
  };

  return (
    <div>
      <PageHeader title="API Keys" subtitle="Personal keys for the gateway's MCP endpoint. Keys are stored hashed and shown only once." />
      <ErrorBanner error={error} />
      <section className="card">
        <form className="inline-form" onSubmit={create}>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Key name, e.g. claude-code-laptop" maxLength={100} />
          <button className="btn btn-primary">Generate key</button>
        </form>
      </section>

      <section className="card">
        <div className="card-head">
          <h2>{showAll ? "All keys" : "My keys"}</h2>
          {isAdmin && (
            <label className="check">
              <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> Show every user's keys
            </label>
          )}
        </div>
        {keys.length === 0 ? (
          <Empty title="No keys yet">Generate one above, then paste it into your MCP client.</Empty>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                {showAll && <th>Owner</th>}
                <th>Key</th>
                <th>Created</th>
                <th>Last used</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {keys.map((k) => (
                <tr key={k.id} className={k.revoked_at ? "row-muted" : ""}>
                  <td>{k.name}</td>
                  {showAll && <td>{k.user_email}</td>}
                  <td className="mono">{k.prefix}…</td>
                  <td>{formatTime(k.created_at)}</td>
                  <td>{timeAgo(k.last_used_at)}</td>
                  <td>{k.revoked_at ? <span className="pill pill-danger">revoked</span> : <span className="pill pill-ok">active</span>}</td>
                  <td className="right">
                    {!k.revoked_at && (
                      <button className="btn btn-small btn-danger" onClick={() => revoke(k)}>
                        Revoke
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {created?.key && (
        <Modal title="Your new API key" onClose={() => setCreated(null)} wide>
          <div className="alert alert-warn">Copy it now. For security it is stored hashed and will never be shown again.</div>
          <div className="key-box">
            <code>{created.key}</code>
            <CopyButton text={created.key} />
          </div>
          <h4>Add the gateway to your MCP client</h4>
          <SnippetTabs mcpUrl={config.mcp_url} apiKey={created.key} />
        </Modal>
      )}
    </div>
  );
}
