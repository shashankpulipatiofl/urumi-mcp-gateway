import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, ApiKey, formatTime, Invite, User } from "../api";
import { useSession } from "../App";
import { CopyButton, ErrorBanner, Modal, PageHeader, RoleBadge, SnippetTabs } from "../components/ui";

export default function Team() {
  const { user: me, config } = useSession();
  const [users, setUsers] = useState<User[]>([]);
  const [invites, setInvites] = useState<Invite[]>([]);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"member" | "admin">("member");
  const [newInvite, setNewInvite] = useState<Invite | null>(null);
  const [issued, setIssued] = useState<ApiKey | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.get<User[]>("/api/users").then(setUsers).catch((e) => setError(e.message));
    api.get<Invite[]>("/api/invites").then(setInvites).catch(() => undefined);
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  const run = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const invite = (e: FormEvent) => {
    e.preventDefault();
    run(async () => {
      setNewInvite(await api.post<Invite>("/api/invites", { email, role }));
      setEmail("");
    });
  };

  const pending = invites.filter((i) => !i.accepted_at);

  return (
    <div>
      <PageHeader title="Team" subtitle="Invite people, manage roles, and issue or revoke their gateway keys." />
      <ErrorBanner error={error} />
      <section className="card">
        <h2>Invite a team member</h2>
        <form className="inline-form" onSubmit={invite}>
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@company.com" required />
          <select value={role} onChange={(e) => setRole(e.target.value as "member" | "admin")}>
            <option value="member">Member (uses tools)</option>
            <option value="admin">Admin (manages servers & team)</option>
          </select>
          <button className="btn btn-primary">Create invite link</button>
        </form>
      </section>

      <section className="card">
        <h2>Members</h2>
        <table className="table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Email</th>
              <th>Role</th>
              <th className="num">Active keys</th>
              <th>Joined</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} className={!u.is_active ? "row-muted" : ""}>
                <td>
                  <strong>{u.name}</strong> {u.id === me.id && <span className="muted small">(you)</span>}
                </td>
                <td>{u.email}</td>
                <td>
                  {u.id === me.id ? (
                    <RoleBadge role={u.role} />
                  ) : (
                    <select value={u.role} onChange={(e) => run(() => api.patch(`/api/users/${u.id}`, { role: e.target.value }))}>
                      <option value="member">member</option>
                      <option value="admin">admin</option>
                    </select>
                  )}
                </td>
                <td className="num">{u.active_keys}</td>
                <td>{formatTime(u.created_at)}</td>
                <td className="right actions">
                  {u.is_active && (
                    <button className="btn btn-small" onClick={() => run(async () => setIssued(await api.post<ApiKey>(`/api/users/${u.id}/keys`, { name: "issued-by-admin" })))}>
                      Issue key
                    </button>
                  )}
                  {u.id !== me.id && (
                    <button
                      className={`btn btn-small ${u.is_active ? "btn-danger" : ""}`}
                      onClick={() =>
                        (!u.is_active || confirm(`Disable ${u.email}? All of their keys are revoked.`)) &&
                        run(() => api.patch(`/api/users/${u.id}`, { is_active: !u.is_active }))
                      }
                    >
                      {u.is_active ? "Disable" : "Enable"}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted small">To revoke an individual key, open API Keys → "Show every user's keys".</p>
      </section>

      {pending.length > 0 && (
        <section className="card">
          <h2>Pending invites</h2>
          <table className="table">
            <tbody>
              {pending.map((i) => (
                <tr key={i.id}>
                  <td>{i.email}</td>
                  <td>
                    <RoleBadge role={i.role} />
                  </td>
                  <td className="muted">expires {formatTime(i.expires_at)}</td>
                  <td className="right">
                    <button className="btn btn-small" onClick={() => run(() => api.del(`/api/invites/${i.id}`))}>
                      Cancel
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {newInvite?.invite_url && (
        <Modal title="Invite link created" onClose={() => setNewInvite(null)}>
          <p>
            Send this one-time link to <strong>{newInvite.email}</strong>. It expires {formatTime(newInvite.expires_at)}.
          </p>
          <div className="key-box">
            <code>{newInvite.invite_url}</code>
            <CopyButton text={newInvite.invite_url} />
          </div>
        </Modal>
      )}
      {issued?.key && (
        <Modal title={`Key issued for ${issued.user_email}`} onClose={() => setIssued(null)} wide>
          <div className="alert alert-warn">Share it securely. It is shown once and stored hashed.</div>
          <div className="key-box">
            <code>{issued.key}</code>
            <CopyButton text={issued.key} />
          </div>
          <SnippetTabs mcpUrl={config.mcp_url} apiKey={issued.key} />
        </Modal>
      )}
    </div>
  );
}
