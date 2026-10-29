import { WorkflowEntrypoint } from "cloudflare:workers";
import type { WorkflowEvent, WorkflowStep } from "cloudflare:workers";
import type { DeviceSignal, DeliveryParams, Env } from "./types";

const retryMinuteOffsets = [0, 1, 3, 6, 10, 15];

type JobState = {
  id: string;
  deviceId: string;
  dueAt: number;
  expiresAt: number;
  state: string;
};

export class DeliveryWorkflow extends WorkflowEntrypoint<Env, DeliveryParams> {
  async run(event: WorkflowEvent<DeliveryParams>, step: WorkflowStep): Promise<void> {
    const { jobId, dueAt } = event.payload;
    for (let attempt = 0; attempt < retryMinuteOffsets.length; attempt += 1) {
      const scheduledAt = dueAt + retryMinuteOffsets[attempt] * 60_000;
      await step.sleepUntil(`wait-until-attempt-${attempt}`, scheduledAt);
      const state = await step.do(`dispatch-attempt-${attempt}`, async () => this.dispatch(jobId, attempt));
      if (state === "terminal") return;
      if (attempt === retryMinuteOffsets.length - 1) {
        await step.sleep("final-ack-window", "1 minute");
        await step.do("mark-final-outcome", async () => this.markFinal(jobId));
      }
    }
  }

  private async dispatch(jobId: string, attempt: number): Promise<"terminal" | "sent" | "offline"> {
    const job = await this.env.DB.prepare(`
      SELECT id, device_id AS deviceId, due_at AS dueAt, expires_at AS expiresAt, state
      FROM scheduled_jobs WHERE id = ?
    `).bind(jobId).first<JobState>();
    if (!job || ["delivered", "cancelled", "expired", "uncertain"].includes(job.state)) {
      return "terminal";
    }
    const now = Date.now();
    if (job.expiresAt <= now) {
      await this.env.DB.prepare(`UPDATE scheduled_jobs SET state = 'expired', updated_at = ? WHERE id = ?`)
        .bind(now, jobId).run();
      return "terminal";
    }

    const signal: DeviceSignal = {
      type: "job_due",
      jobId: job.id,
      attempt,
      dueAt: new Date(job.dueAt).toISOString(),
    };
    const objectId = this.env.DEVICE_STREAM.idFromName(job.deviceId);
    const response = await this.env.DEVICE_STREAM.get(objectId).fetch(
      new Request("https://device.internal/notify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(signal),
      }),
    );
    if (!response.ok) throw new Error(`device gateway returned ${response.status}`);
    const { sent } = await response.json() as { sent: number };
    const outcome = sent > 0 ? "sent" : "connection_unavailable";
    await this.env.DB.batch([
      this.env.DB.prepare(`INSERT INTO delivery_attempts
        (job_id, attempt_number, attempted_at, outcome)
        VALUES (?, ?, ?, ?) ON CONFLICT(job_id, attempt_number) DO UPDATE SET
        attempted_at = excluded.attempted_at, outcome = excluded.outcome`)
        .bind(jobId, attempt, now, outcome),
      this.env.DB.prepare(`UPDATE scheduled_jobs SET
        state = ?, retry_count = ?, next_attempt_at = ?, updated_at = ?
        WHERE id = ? AND state IN ('scheduled', 'waiting_for_device', 'delivering')`)
        .bind(sent > 0 ? "delivering" : "waiting_for_device", attempt,
          attempt + 1 < retryMinuteOffsets.length ? job.dueAt + retryMinuteOffsets[attempt + 1] * 60_000 : job.expiresAt,
          now, jobId),
    ]);
    return sent > 0 ? "sent" : "offline";
  }

  private async markFinal(jobId: string): Promise<void> {
    const now = Date.now();
    await this.env.DB.prepare(`UPDATE scheduled_jobs SET
      state = CASE WHEN expires_at <= ? THEN 'expired' ELSE 'uncertain' END,
      updated_at = ? WHERE id = ? AND state IN ('scheduled', 'waiting_for_device', 'delivering')`)
      .bind(now, now, jobId).run();
  }
}
