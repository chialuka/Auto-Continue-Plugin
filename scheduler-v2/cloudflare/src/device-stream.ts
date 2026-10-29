import { DurableObject } from "cloudflare:workers";
import type { DeviceSignal, Env } from "./types";

export class DeviceStream extends DurableObject<Env> {
  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/stream" && request.method === "GET") {
      if (request.headers.get("X-Authenticated-Device") === null) {
        return new Response("Unauthorized", { status: 401 });
      }
      const pair = new WebSocketPair();
      this.ctx.acceptWebSocket(pair[1]);
      return new Response(null, { status: 101, webSocket: pair[0] });
    }
    if (url.pathname === "/notify" && request.method === "POST") {
      let signal: DeviceSignal;
      try {
        signal = await request.json() as DeviceSignal;
      } catch {
        return Response.json({ error: "invalid signal" }, { status: 400 });
      }
      if (signal.type !== "job_due" || typeof signal.jobId !== "string" ||
        !Number.isInteger(signal.attempt) || typeof signal.dueAt !== "string") {
        return Response.json({ error: "invalid signal" }, { status: 400 });
      }
      let sent = 0;
      for (const socket of this.ctx.getWebSockets()) {
        try {
          socket.send(JSON.stringify(signal));
          sent += 1;
        } catch {
          // A closed socket is removed by the runtime; other sockets still get the signal.
        }
      }
      return Response.json({ sent });
    }
    return new Response("Not Found", { status: 404 });
  }

  webSocketMessage(socket: WebSocket, message: string | ArrayBuffer): void {
    if (typeof message !== "string") {
      socket.close(1003, "text messages only");
      return;
    }
    try {
      const value = JSON.parse(message) as { type?: unknown };
      if (value.type === "ping") {
        socket.send(JSON.stringify({ type: "pong" }));
        return;
      }
    } catch {
      // Reject malformed client frames below.
    }
    socket.close(1008, "unsupported device event");
  }
}
