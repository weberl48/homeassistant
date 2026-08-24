import Constants from "expo-constants";

const BASE: string =
  (Constants.expoConfig?.extra as { apiBase?: string } | undefined)?.apiBase ??
  "http://localhost:8484";

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`);
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json() as Promise<T>;
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json() as Promise<T>;
}

export const api = {
  base: BASE,
  board: () => get<any>("/api/draft/board"),
  week: (n = 1) => get<any>(`/api/week/${n}`),
  approve: (recId: number) => post<any>(`/api/recs/${recId}/approve`),
  snooze: (recId: number) => post<any>(`/api/recs/${recId}/snooze`),
  ignore: (recId: number) => post<any>(`/api/recs/${recId}/ignore`),
  registerDevice: (pushToken: string) =>
    post<any>("/api/devices", { push_token: pushToken, platform: "android" }),
};
