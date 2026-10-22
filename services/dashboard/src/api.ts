// Thin wrapper around fetch for the gateway's dashboard API.
// - Auth is an httpOnly session cookie set by /api/auth/login (JavaScript never sees it).
// - Every request sends X-Requested-With, which the backend requires on state-changing
//   requests as CSRF protection.

export type Role = "admin" | "member";

export interface User {
  id: number;
  email: string;
  name: string;
  role: Role;
  is_active: boolean;
  created_at: string;
  active_keys: number;
}

export interface Tool {
  name: string;
  original_name: string;
  description: string;
  admin_only: boolean;
}

export interface Server {
  id: number;
  slug: string;
  display_name: string;
  description: string;
  url: string | null;
  enabled: boolean;
  auth_type: "none" | "bearer" | "header";
  auth_header_name: string;
  has_credential: boolean;
  admin_only_tools: string[];
  status: "connected" | "unreachable" | "disabled" | "unknown";
  error: string | null;
  checked_at: string | null;
  latency_ms: number | null;
  server_info: string | null;
  tools: Tool[];
}

export interface ApiKey {
  id: number;
  user_id: number;
  user_email: string;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
  key?: string;
}

export interface Invite {
  id: number;
  email: string;
  role: Role;
  created_at: string;
  expires_at: string;
  accepted_at: string | null;
  invite_url?: string | null;
}

export interface AuditEntry {
  id: number;
  created_at: string;
  user_id: number | null;
  user_email: string;
  source: "mcp" | "chat";
  server: string;
  tool: string;
  duration_ms: number;
  success: boolean;
  error: string;
  arguments: string;
}

export interface ToolCallTrace {
  name: string;
  server: string;
  arguments: Record<string, unknown>;
  ok: boolean;
  result: string;
  duration_ms: number;
}

export interface ChatTiming {
  total_ms: number;
  llm_ms: number;
  tools_ms: number;
  model: string;
}

export interface ChatResponse {
  reply: string;
  tool_calls: ToolCallTrace[];
  tools_available: number;
  timing?: ChatTiming;
}

export interface Stats {
  calls_24h: number;
  errors_24h: number;
  per_server: { server: string; calls: number; errors: number; avg_ms: number }[];
  servers: Record<string, number>;
  tools: number;
}

export interface PublicConfig {
  mcp_url: string;
  llm_provider: string;
  llm_model: string;
  chat_enabled: boolean;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: {
      "X-Requested-With": "dashboard",
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let message = res.statusText;
    try {
      const data = await res.json();
      message = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail ?? data);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, message);
  }
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
};

export function timeAgo(iso: string | null): string {
  if (!iso) return "never";
  const date = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
  const s = Math.round((Date.now() - date.getTime()) / 1000);
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return date.toLocaleDateString();
}

export function formatTime(iso: string): string {
  const date = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
  return date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
