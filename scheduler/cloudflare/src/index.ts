import { json, authenticate, isRecord, newToken, newUUID, hashToken, readJSON, uuid } from "./security";
import { DeviceStream } from "./device-stream";
import { DeliveryWorkflow } from "./delivery-workflow";
import type { DeviceJob, Env } from "./types";

export { DeviceStream, DeliveryWorkflow };

type StoredJob = {
  id: string;
  sessionScopeId: string;
  dueAt: number;
  expiresAt: number;
  state: DeviceJob["state"];
  retryCount: number;
  nextAttemptAt: number;
  deviceId: string;
};

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") return json({ ok: true });
    if (request.method === "POST" && url.pathname === "/v1/enroll") return enroll(request, env);

    const device = await authenticate(request, env);
    if (!device) return json({ error: "unauthorized" }, 401);

    if (request.method === "POST" && url.pathname === "/v1/jobs") {
      return createJob(request, env, device.id);
    }
    if (request.method === "GET" && url.pathname === "/v1/jobs") {
      const scope = url.searchParams.get("sessionScopeId");
      if (!uuid(scope)) return json({ error: "invalid sessionScopeId" }, 400);
      return listJobs(env, device.id, scope);
    }
    if (request.method === "GET" && url.pathname === "/v1/device/stream") {
      if (request.headers.get("Upgrade")?.toLowerCase() !== "websocket") {
        return json({ error: "websocket upgrade required" }, 426);
      }
      const id = env.DEVICE_STREAM.idFromName(device.id);
      const headers = new Headers(request.headers);
      headers.delete("Authorization");
      headers.set("X-Authenticated-Device", device.id);
      return env.DEVICE_STREAM.get(id).fetch(new Request("https://device.internal/stream", {
        method: "GET",
        headers,
      }));
    }

    const jobPath = /^\/v1\/jobs\/([0-9a-f-]{36})(?:\/(ack))?$/i.exec(url.pathname);
    if (jobPath && request.method === "DELETE" && !jobPath[2]) {
      return cancelJob(env, device.id, jobPath[1]);
    }
    if (jobPath && request.method === "POST" && jobPath[2] === "ack") {
      return acknowledge(request, env, device.id, jobPath[1]);
    }
    return json({ error: "not found" }, 404);
  },
} satisfies ExportedHandler<Env>;

async function enroll(request: Request, env: Env): Promise<Response> {
  // Cloudflare sets this header at the edge. The fixed fallback is useful for
  // local Wrangler development; deployments should always receive a client IP.
  const clientIP = request.headers.get("CF-Connecting-IP") ?? "local-development";
  const limit = await env.ENROLL_LIMITER.limit({ key: clientIP });
  if (!limit.success) return json({ error: "too many enrollment requests" }, 429);
  try {
    const body = await readJSON(request);
    if (!isRecord(body) || Object.keys(body).length !== 0) {
      return json({ error: "enrollment body must be empty" }, 400);
    }
    const userId = newUUID();
    const deviceId = newUUID();
    const token = newToken();
    const credentialHash = await hashToken(token);
    const now = Date.now();
    await env.DB.batch([
      env.DB.prepare("INSERT INTO users (id, created_at) VALUES (?, ?)").bind(userId, now),
      env.DB.prepare(`INSERT INTO devices
        (id, user_id, credential_hash, created_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?)`)
        .bind(deviceId, userId, credentialHash, now, now),
    ]);
    return json({ userId, deviceId, deviceToken: token }, 201);
  } catch {
    return json({ error: "enrollment failed" }, 500);
  }
}

async function createJob(request: Request, env: Env, deviceId: string): Promise<Response> {
  let body: Record<string, unknown>;
  try {
    const parsed = await readJSON(request);
    if (!isRecord(parsed)) return json({ error: "invalid request body" }, 400);
    body = parsed;
  } catch {
    return json({ error: "invalid request body" }, 400);
  }
  const allowed = ["id", "sessionScopeId", "dueAt", "expiresAt"];
  if (Object.keys(body).some((key) => !allowed.includes(key)) ||
    !uuid(body.id) || !uuid(body.sessionScopeId) ||
    typeof body.dueAt !== "string" || typeof body.expiresAt !== "string") {
    return json({ error: "invalid job metadata" }, 400);
  }
  const dueAt = Date.parse(body.dueAt);
  const expiresAt = Date.parse(body.expiresAt);
  const now = Date.now();
  // The connector may be offline when a locally saved job is first synced, so
  // accept past dueAt values while the delivery window remains open.
  if (!Number.isFinite(dueAt) || !Number.isFinite(expiresAt) || expiresAt <= dueAt || expiresAt <= now) {
    return json({ error: "expiresAt must be in the future and later than dueAt" }, 400);
  }

  const jobId = body.id;
  const sessionScopeId = body.sessionScopeId;
  const insert = await env.DB.prepare(`
    INSERT INTO scheduled_jobs
      (id, device_id, session_scope_id, due_at, expires_at, state, retry_count,
       next_attempt_at, created_at, updated_at)
    SELECT ?, ?, ?, ?, ?, 'scheduled', 0, ?, ?, ?
    WHERE (SELECT COUNT(*) FROM scheduled_jobs
      WHERE device_id = ? AND session_scope_id = ?
      AND state IN ('scheduled', 'waiting_for_device', 'delivering')) < 3
    ON CONFLICT(id) DO NOTHING
  `).bind(jobId, deviceId, sessionScopeId, dueAt, expiresAt, dueAt, now, now, deviceId, sessionScopeId).run();

  const stored = await env.DB.prepare(`
    SELECT id, device_id AS deviceId, session_scope_id AS sessionScopeId,
      due_at AS dueAt, expires_at AS expiresAt, state, retry_count AS retryCount,
      next_attempt_at AS nextAttemptAt
    FROM scheduled_jobs WHERE id = ? AND device_id = ?
  `).bind(jobId, deviceId).first<StoredJob>();
  if (!stored) {
    if (insert.meta.changes === 0) return json({ error: "session job limit reached or ID conflict" }, 409);
    return json({ error: "job could not be stored" }, 500);
  }
  if (stored.sessionScopeId !== sessionScopeId || stored.dueAt !== dueAt || stored.expiresAt !== expiresAt) {
    return json({ error: "job ID already belongs to different metadata" }, 409);
  }
  try {
    await ensureWorkflow(env, stored);
  } catch {
    return json({ error: "job saved but delivery schedule could not start; retry this request" }, 503);
  }
  return json(publicJob(stored), insert.meta.changes ? 201 : 200);
}

async function ensureWorkflow(env: Env, job: StoredJob): Promise<void> {
  try {
    await env.DELIVERY_WORKFLOW.create({
      id: job.id,
      params: { jobId: job.id, dueAt: job.dueAt },
    });
  } catch (error) {
    // A repeated idempotent POST may find that its durable workflow already exists.
    try {
      await env.DELIVERY_WORKFLOW.get(job.id);
    } catch {
      throw error;
    }
  }
}

async function listJobs(env: Env, deviceId: string, scopeId: string): Promise<Response> {
  const result = await env.DB.prepare(`
    SELECT id, session_scope_id AS sessionScopeId, due_at AS dueAt,
      expires_at AS expiresAt, state, retry_count AS retryCount,
      next_attempt_at AS nextAttemptAt
    FROM scheduled_jobs WHERE device_id = ? AND session_scope_id = ?
    ORDER BY created_at, id
  `).bind(deviceId, scopeId).all<StoredJob>();
  return json((result.results ?? []).map(publicJob));
}

async function cancelJob(env: Env, deviceId: string, jobId: string): Promise<Response> {
  await env.DB.prepare(`
    UPDATE scheduled_jobs SET state = 'cancelled', updated_at = ?
    WHERE id = ? AND device_id = ?
      AND state IN ('scheduled', 'waiting_for_device', 'delivering', 'uncertain')
  `).bind(Date.now(), jobId, deviceId).run();
  return new Response(null, { status: 204 });
}

async function acknowledge(
  request: Request,
  env: Env,
  deviceId: string,
  jobId: string,
): Promise<Response> {
  let body: Record<string, unknown>;
  try {
    const parsed = await readJSON(request);
    if (!isRecord(parsed)) return json({ error: "invalid acknowledgement" }, 400);
    body = parsed;
  } catch {
    return json({ error: "invalid acknowledgement" }, 400);
  }
  if (Object.keys(body).some((key) => !["attempt", "outcome"].includes(key)) ||
    !Number.isInteger(body.attempt) || Number(body.attempt) < 0 || Number(body.attempt) > 5 ||
    !["acknowledged", "connection_unavailable", "timeout", "rejected", "uncertain"].includes(String(body.outcome))) {
    return json({ error: "invalid acknowledgement" }, 400);
  }
  const job = await env.DB.prepare(`SELECT id FROM scheduled_jobs WHERE id = ? AND device_id = ?`)
    .bind(jobId, deviceId).first<{ id: string }>();
  if (!job) return new Response(null, { status: 204 });
  const now = Date.now();
  await env.DB.batch([
    env.DB.prepare(`INSERT INTO delivery_attempts (job_id, attempt_number, attempted_at, outcome)
      VALUES (?, ?, ?, ?) ON CONFLICT(job_id, attempt_number) DO NOTHING`)
      .bind(jobId, Number(body.attempt), now, body.outcome),
    env.DB.prepare(`UPDATE scheduled_jobs SET
      state = CASE
        WHEN ? = 'acknowledged' THEN 'delivered'
        WHEN ? = 'uncertain' THEN 'uncertain'
        ELSE state END,
      updated_at = ? WHERE id = ? AND device_id = ?
        AND state IN ('scheduled', 'waiting_for_device', 'delivering')`)
      .bind(body.outcome, body.outcome, now, jobId, deviceId),
  ]);
  return new Response(null, { status: 204 });
}

function publicJob(job: StoredJob): DeviceJob {
  return {
    id: job.id,
    sessionScopeId: job.sessionScopeId,
    dueAt: new Date(job.dueAt).toISOString(),
    expiresAt: new Date(job.expiresAt).toISOString(),
    state: job.state,
    retryCount: job.retryCount,
    nextAttemptAt: new Date(job.nextAttemptAt).toISOString(),
  };
}
