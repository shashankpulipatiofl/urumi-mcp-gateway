import { Fragment, useCallback, useEffect, useState } from "react";
import { api, AuditEntry, formatTime, Server, User } from "../api";
import { useSession } from "../App";
import { Empty, PageHeader } from "../components/ui";

export default function Audit() {
  const { user } = useSession();
  const isAdmin = user.role === "admin";
  const [rows, setRows] = useState<AuditEntry[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [servers, setServers] = useState<Server[]>([]);
  const [filters, setFilters] = useState({ user_id: "", server: "", outcome: "", source: "" });
  const [live, setLive] = useState(true);
  const [open, setOpen] = useState<number | null>(null);

  const load = useCallback(() => {
    const qs = new URLSearchParams({ limit: "200" });
    Object.entries(filters).forEach(([k, v]) => v && qs.set(k, v));
    api.get<AuditEntry[]>(`/api/audit?${qs}`).then(setRows).catch(() => undefined);
  }, [filters]);

  useEffect(() => {
    load();
    if (!live) return;
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [load, live]);

  useEffect(() => {
    if (isAdmin) api.get<User[]>("/api/users").then(setUsers).catch(() => undefined);
    api.get<Server[]>("/api/servers").then(setServers).catch(() => undefined);
  }, [isAdmin]);

  const set = (k: keyof typeof filters, v: string) => setFilters({ ...filters, [k]: v });

  return (
    <div>
      <PageHeader
        title="Audit Log"
        subtitle={isAdmin ? "Every tool call through the gateway, from MCP clients and the chat agent." : "Your tool calls through the gateway."}
        actions={
          <label className="check">
            <input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} /> Live
          </label>
        }
      />
      <div className="filters">
        {isAdmin && (
          <select value={filters.user_id} onChange={(e) => set("user_id", e.target.value)}>
            <option value="">All users</option>
            {users.map((u) => (
              <option key={u.id} value={u.id}>
                {u.email}
              </option>
            ))}
          </select>
        )}
        <select value={filters.server} onChange={(e) => set("server", e.target.value)}>
          <option value="">All servers</option>
          {servers.map((s) => (
            <option key={s.id} value={s.slug}>
              {s.slug}
            </option>
          ))}
        </select>
        <select value={filters.outcome} onChange={(e) => set("outcome", e.target.value)}>
          <option value="">Any outcome</option>
          <option value="success">Success</option>
          <option value="error">Error</option>
        </select>
        <select value={filters.source} onChange={(e) => set("source", e.target.value)}>
          <option value="">Any source</option>
          <option value="mcp">MCP client</option>
          <option value="chat">Chat agent</option>
        </select>
      </div>
      <section className="card flush">
        {rows.length === 0 ? (
          <Empty title="No tool calls yet" />
        ) : (
          <table className="table table-audit">
            <thead>
              <tr>
                <th>When</th>
                <th>User</th>
                <th>Source</th>
                <th>Server</th>
                <th>Tool</th>
                <th className="num">Duration</th>
                <th>Outcome</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <Fragment key={r.id}>
                  <tr className="clickable" onClick={() => setOpen(open === r.id ? null : r.id)}>
                    <td className="nowrap">{formatTime(r.created_at)}</td>
                    <td>{r.user_email}</td>
                    <td>
                      <span className="pill">{r.source === "chat" ? "chat agent" : "MCP client"}</span>
                    </td>
                    <td className="mono">{r.server}</td>
                    <td className="mono">{r.tool}</td>
                    <td className="num">{r.duration_ms} ms</td>
                    <td>{r.success ? <span className="pill pill-ok">success</span> : <span className="pill pill-danger">error</span>}</td>
                  </tr>
                  {open === r.id && (
                    <tr className="detail-row">
                      <td colSpan={7}>
                        <div className="detail">
                          <div>
                            <div className="muted small">Arguments</div>
                            <pre>{r.arguments || "{}"}</pre>
                          </div>
                          {r.error && (
                            <div>
                              <div className="muted small">Error</div>
                              <pre className="text-danger">{r.error}</pre>
                            </div>
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
