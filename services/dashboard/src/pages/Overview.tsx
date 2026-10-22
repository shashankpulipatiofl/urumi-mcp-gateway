import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, Server, Stats } from "../api";
import { useSession } from "../App";
import { CodeBlock, PageHeader, StatusBadge } from "../components/ui";

export default function Overview() {
  const { user, config } = useSession();
  const [stats, setStats] = useState<Stats | null>(null);
  const [servers, setServers] = useState<Server[]>([]);

  useEffect(() => {
    const load = () => {
      api.get<Stats>("/api/stats").then(setStats).catch(() => undefined);
      api.get<Server[]>("/api/servers").then(setServers).catch(() => undefined);
    };
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, []);

  const connected = servers.filter((s) => s.status === "connected").length;
  const errorRate = stats && stats.calls_24h ? Math.round((stats.errors_24h / stats.calls_24h) * 100) : 0;

  return (
    <div>
      <PageHeader title={`Welcome, ${user.name.split(" ")[0]}`} subtitle="Your team's MCP servers behind one authenticated endpoint." />
      <div className="stat-grid">
        <div className="stat">
          <div className="stat-label">Servers connected</div>
          <div className="stat-value">
            {connected}
            <span className="stat-of"> / {servers.length}</span>
          </div>
        </div>
        <div className="stat">
          <div className="stat-label">Tools available</div>
          <div className="stat-value">{stats?.tools ?? "–"}</div>
        </div>
        <div className="stat">
          <div className="stat-label">{user.role === "admin" ? "Tool calls (24h)" : "My tool calls (24h)"}</div>
          <div className="stat-value">{stats?.calls_24h ?? "–"}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Error rate (24h)</div>
          <div className={`stat-value ${errorRate > 20 ? "text-danger" : ""}`}>{stats ? `${errorRate}%` : "–"}</div>
        </div>
      </div>

      <div className="grid-2">
        <section className="card">
          <div className="card-head">
            <h2>Upstream servers</h2>
            <Link to="/servers" className="link">
              Manage →
            </Link>
          </div>
          <table className="table">
            <thead>
              <tr>
                <th>Server</th>
                <th>Status</th>
                <th className="num">Tools</th>
                <th className="num">Calls 24h</th>
                <th className="num">Avg</th>
              </tr>
            </thead>
            <tbody>
              {servers.map((s) => {
                const ps = stats?.per_server.find((p) => p.server === s.slug);
                return (
                  <tr key={s.id}>
                    <td>
                      <strong>{s.display_name}</strong>
                      <div className="muted small mono">{s.slug}</div>
                    </td>
                    <td>
                      <StatusBadge status={s.status} />
                    </td>
                    <td className="num">{s.tools.length}</td>
                    <td className="num">
                      {ps?.calls ?? 0}
                      {ps && ps.errors > 0 && <span className="text-danger small"> ({ps.errors} err)</span>}
                    </td>
                    <td className="num">{ps ? `${ps.avg_ms} ms` : "–"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>

        <section className="card">
          <div className="card-head">
            <h2>Connect an MCP client</h2>
          </div>
          <p className="muted">
            Add <strong>one URL</strong> and <strong>your own API key</strong> to Claude Code, Cursor or MCP Inspector. You get the tools of every
            enabled server without configuring any of them yourself.
          </p>
          <CodeBlock code={config.mcp_url} />
          <ol className="steps">
            <li>
              Create a key on the <Link to="/keys">API Keys</Link> page (it is shown once).
            </li>
            <li>Paste the generated snippet into your client.</li>
            <li>
              Tools appear as <code>server__tool</code>, e.g. <code>woocommerce__list_orders</code>.
            </li>
          </ol>
        </section>
      </div>
    </div>
  );
}
