import type { DeviceStream } from "./device-stream";

export interface Env {
  DB: D1Database;
  DEVICE_STREAM: DurableObjectNamespace<DeviceStream>;
  DELIVERY_WORKFLOW: Workflow<DeliveryParams>;
  ENROLL_LIMITER: RateLimit;
}

export interface DeliveryParams {
  jobId: string;
  dueAt: number;
}

export interface DeviceJob {
  id: string;
  sessionScopeId: string;
  dueAt: string;
  expiresAt: string;
  state: string;
  retryCount: number;
  nextAttemptAt: string;
}

export type AuthenticatedDevice = {
  id: string;
  userId: string;
};

export type DeviceSignal = {
  type: "job_due";
  jobId: string;
  attempt: number;
  dueAt: string;
};
