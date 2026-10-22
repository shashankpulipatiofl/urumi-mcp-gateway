import { FormEvent, useState } from "react";
import { api } from "../api";

export default function Login({ onLogin }: { onLogin: () => Promise<void> }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post("/api/auth/login", { email, password });
      await onLogin();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-page">
      <div className="auth-card">
        <div className="brand brand-center">
          <div className="logo">M</div>
          <div>
            <div className="brand-name dark">Urumi MCP Gateway</div>
            <div className="brand-sub dark">One endpoint for every MCP server your team uses</div>
          </div>
        </div>
        <form onSubmit={submit} className="form">
          <label>
            Email
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoFocus required />
          </label>
          <label>
            Password
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
          </label>
          {error && <div className="alert alert-error">{error}</div>}
          <button className="btn btn-primary btn-block" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
        <p className="muted small center">New team member? Open the invite link your admin sent you.</p>
      </div>
    </div>
  );
}
