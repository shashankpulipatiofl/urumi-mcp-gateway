import { FormEvent, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";

export default function AcceptInvite({ onDone }: { onDone: () => Promise<void> }) {
  const { token = "" } = useParams();
  const navigate = useNavigate();
  const [info, setInfo] = useState<{ email: string; role: string } | null>(null);
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.get<{ email: string; role: string }>(`/api/auth/invite/${token}`).then(setInfo).catch((e) => setError(e.message));
  }, [token]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try {
      await api.post("/api/auth/accept-invite", { token, name, password });
      await onDone();
      navigate("/keys");
    } catch (err) {
      setError((err as Error).message);
    }
  };

  return (
    <div className="auth-page">
      <div className="auth-card">
        <h2>Join the team</h2>
        {info ? (
          <p className="muted">
            You were invited as <strong>{info.role}</strong> with <strong>{info.email}</strong>.
          </p>
        ) : null}
        {error && <div className="alert alert-error">{error}</div>}
        {info && (
          <form onSubmit={submit} className="form">
            <label>
              Your name
              <input value={name} onChange={(e) => setName(e.target.value)} required autoFocus />
            </label>
            <label>
              Choose a password (min 8 characters)
              <input type="password" minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} required />
            </label>
            <button className="btn btn-primary btn-block">Create account</button>
          </form>
        )}
      </div>
    </div>
  );
}
