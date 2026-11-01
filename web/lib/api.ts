export const API = process.env.NEXT_PUBLIC_API || "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  constructor(status: number, msg: string) { super(msg); this.status = status; }
}

type Options = RequestInit & { json?: unknown; timeoutMs?: number };
export async function api<T = any>(path: string, init?: Options): Promise<T> {
  const { json, timeoutMs = 120000, ...rest } = init || {};
  const headers = new Headers(rest.headers);
  let body = rest.body;
  if (json !== undefined) { headers.set("content-type", "application/json"); body = JSON.stringify(json); }
  const controller = new AbortController();
  const abort = () => controller.abort(rest.signal?.reason);
  if (rest.signal?.aborted) abort(); else rest.signal?.addEventListener("abort", abort, { once: true });
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
  try {
    let res: Response;
    try {
      res = await fetch(`${API}${path}`, { ...rest, headers, body, signal: controller.signal });
    } catch (e) {
      if (timedOut) throw new ApiError(0, "The request timed out. Check the current status before retrying an action; it may still have completed on the server.");
      if (rest.signal?.aborted) throw e;
      throw new ApiError(0, `Cannot reach the Rex API at ${API}. Start it with ./start.sh.`);
    }
    const text = await res.text();
    let data: any = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = text; }
    if (!res.ok) {
      const d = data && typeof data === "object" ? data.detail ?? JSON.stringify(data) : String(data);
      const msg = Array.isArray(d) ? d.map((e: any) => `${(e.loc ?? []).filter((x: string) => x !== "body").join(".")}: ${e.msg}`).join("; ") : typeof d === "string" ? d : JSON.stringify(d);
      throw new ApiError(res.status, msg);
    }
    return data as T;
  } catch (e) {
    if (timedOut && !(e instanceof ApiError)) throw new ApiError(0, "The response timed out. Check current status before retrying.");
    throw e;
  } finally {
    clearTimeout(timer); rest.signal?.removeEventListener("abort", abort);
  }
}
export const get = <T = any>(p: string, init?: Options) => api<T>(p, init);
export const post = <T = any>(p: string, json?: unknown) => api<T>(p, { method: "POST", json: json ?? {} });
