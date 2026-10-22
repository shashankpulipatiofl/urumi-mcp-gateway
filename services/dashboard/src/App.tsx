import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { Navigate, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api, PublicConfig, User } from "./api";
import ErrorBoundary from "./components/ErrorBoundary";
import { RoleBadge } from "./components/ui";
import AcceptInvite from "./pages/AcceptInvite";
import Audit from "./pages/Audit";
import Chat from "./pages/Chat";
import Keys from "./pages/Keys";
import Login from "./pages/Login";
import Overview from "./pages/Overview";
import Servers from "./pages/Servers";
import Team from "./pages/Team";

interface Session {
  user: User;
  config: PublicConfig;
  refreshUser: () => Promise<void>;
}

const SessionContext = createContext<Session | null>(null);

export function useSession(): Session {
  const s = useContext(SessionContext);
  if (!s) throw new Error("useSession outside provider");
  return s;
}

const NAV = [
  { to: "/", label: "Overview", icon: "◎", end: true },
  { to: "/servers", label: "MCP Servers", icon: "⧉" },
  { to: "/chat", label: "Chat Agent", icon: "✦" },
  { to: "/keys", label: "API Keys", icon: "⚿" },
  { to: "/team", label: "Team", icon: "☺", admin: true },
  { to: "/audit", label: "Audit Log", icon: "≣" },
];

export default function App() {
  const [user, setUser] = useState<User | null | undefined>(undefined);
  const [config, setConfig] = useState<PublicConfig | null>(null);
  const navigate = useNavigate();
  const location = useLocation();

  const refreshUser = useCallback(async () => {
    try {
      setUser(await api.get<User>("/api/auth/me"));
    } catch {
      setUser(null);
    }
  }, []);

  useEffect(() => {
    refreshUser();
    api.get<PublicConfig>("/api/config").then(setConfig).catch(() => undefined);
  }, [refreshUser]);

  if (user === undefined) return <div className="boot">Loading…</div>;

  if (user === null) {
    return (
      <Routes>
        <Route path="/invite/:token" element={<AcceptInvite onDone={refreshUser} />} />
        <Route path="*" element={<Login onLogin={refreshUser} />} />
      </Routes>
    );
  }

  const logout = async () => {
    await api.post("/api/auth/logout");
    setUser(null);
    navigate("/");
  };

  return (
    <SessionContext.Provider value={{ user, config: config ?? { mcp_url: "/mcp", llm_provider: "", llm_model: "", chat_enabled: false }, refreshUser }}>
      <div className="shell">
        <aside className="sidebar">
          <div className="brand">
            <div className="logo">M</div>
            <div>
              <div className="brand-name">Urumi</div>
              <div className="brand-sub">MCP Gateway</div>
            </div>
          </div>
          <nav>
            {NAV.filter((n) => !n.admin || user.role === "admin").map((n) => (
              <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}>
                <span className="nav-icon">{n.icon}</span>
                {n.label}
              </NavLink>
            ))}
          </nav>
          <div className="sidebar-foot">
            <div className="me">
              <div className="avatar">{user.name.slice(0, 1).toUpperCase()}</div>
              <div className="me-text">
                <div className="me-name">{user.name}</div>
                <div className="me-email">{user.email}</div>
              </div>
            </div>
            <div className="me-actions">
              <RoleBadge role={user.role} />
              <button className="link-btn" onClick={logout}>
                Sign out
              </button>
            </div>
          </div>
        </aside>
        <main className="content">
          <ErrorBoundary key={location.pathname}>
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/servers" element={<Servers />} />
            <Route path="/chat" element={<Chat />} />
            <Route path="/keys" element={<Keys />} />
            <Route path="/team" element={user.role === "admin" ? <Team /> : <Navigate to="/" />} />
            <Route path="/audit" element={<Audit />} />
            <Route path="*" element={<Navigate to="/" />} />
          </Routes>
          </ErrorBoundary>
        </main>
      </div>
    </SessionContext.Provider>
  );
}
