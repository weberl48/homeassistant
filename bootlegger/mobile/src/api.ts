import Constants from "expo-constants";

const BASE: string =
  (Constants.expoConfig?.extra as { apiBase?: string } | undefined)?.apiBase ??
  "http://localhost:8484";

// Mirrors server/app/web/app.js's TOKEN (app.js:18/98): X-Bootlegger-Token is
// only attached when a token is configured. GET routes are unauthenticated
// server-side (require_token only guards MUTATES routes) — this exists so
// the app's mutating calls (approve/snooze/ignore/registerDevice) still work
// once BOOTLEGGER_API_TOKEN is set.
const TOKEN: string =
  (Constants.expoConfig?.extra as { apiToken?: string } | undefined)?.apiToken ?? "";

function withAuth(extra?: Record<string, string>): Record<string, string> | undefined {
  return TOKEN ? { ...extra, "X-Bootlegger-Token": TOKEN } : extra;
}

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`, { headers: withAuth() });
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json() as Promise<T>;
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: withAuth(body ? { "Content-Type": "application/json" } : undefined),
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json() as Promise<T>;
}

export const api = {
  base: BASE,
  board: () => get<any>("/api/draft/board"),
  // Server resolves the season clock itself — the app never hardcodes a week.
  weekCurrent: () => get<any>("/api/week/current"),
  week: (n = 1) => get<any>(`/api/week/${n}`),
  approve: (recId: number) => post<any>(`/api/recs/${recId}/approve`),
  snooze: (recId: number) => post<any>(`/api/recs/${recId}/snooze`),
  ignore: (recId: number) => post<any>(`/api/recs/${recId}/ignore`),
  registerDevice: (pushToken: string) =>
    post<any>("/api/devices", { push_token: pushToken, platform: "android" }),
  // The street: free-agent targets ranked by FA score (brain.waiver_targets).
  waivers: (week = 1) => get<any>(`/api/waivers?week=${week}`),
  // The parlor: mutually-beneficial trade suggestions (brain.suggest_trades).
  trades: (limit = 8) => get<any>(`/api/trades/suggest?limit=${limit}`),
  // The ledger: rule config + the recommendation audit trail. Read-only —
  // rule mutation lives behind X-Bootlegger-Token and isn't exposed here.
  rules: () => get<any>("/api/rules"),
  audit: (limit = 100) => get<any>(`/api/audit?limit=${limit}`),
};
