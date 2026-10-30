import type { AuthenticatedDevice, Env } from "./types";

export function json(value: unknown, status = 200): Response {
  return Response.json(value, {
    status,
    headers: { "Cache-Control": "no-store" },
  });
}

export function uuid(value: unknown): value is string {
  return typeof value === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}

export function newUUID(): string {
  return crypto.randomUUID();
}

export function newToken(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function hashToken(token: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(token));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function authenticate(request: Request, env: Env): Promise<AuthenticatedDevice | null> {
  const authorization = request.headers.get("Authorization") ?? "";
  const match = /^Bearer ([a-f0-9]{64})$/i.exec(authorization);
  if (!match) return null;
  const credentialHash = await hashToken(match[1]);
  const row = await env.DB.prepare(`
    SELECT id, user_id AS userId FROM devices
    WHERE credential_hash = ? AND revoked_at IS NULL
  `).bind(credentialHash).first<AuthenticatedDevice>();
  if (!row) return null;
  await env.DB.prepare("UPDATE devices SET last_seen_at = ? WHERE id = ?")
    .bind(Date.now(), row.id).run();
  return row;
}

export async function readJSON(request: Request, maxBytes = 8192): Promise<unknown> {
  const length = Number(request.headers.get("Content-Length") ?? 0);
  if (length > maxBytes) throw new Error("request body is too large");
  if (!request.body) throw new Error("request body is empty");
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > maxBytes) {
      await reader.cancel();
      throw new Error("request body is too large");
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  const text = new TextDecoder().decode(bytes);
  return JSON.parse(text);
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
