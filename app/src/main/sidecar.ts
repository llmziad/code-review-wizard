/**
 * Sidecar process manager.
 *
 * Owns the lifecycle of the Python `reviewer serve` process for the lifetime
 * of the Electron app. Speaks the same line-delimited JSON-RPC protocol the
 * sidecar exposes: requests get a unique id, the response comes back tagged
 * with that id, and streaming events arrive with `request_id` set.
 *
 * Concurrency: one process, many in-flight requests. The sidecar runs each
 * request in its own asyncio task and writes responses interleaved with
 * events; we route everything through a `pending` map keyed by id.
 */

import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { createInterface, type Interface as ReadlineInterface } from "node:readline";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));

// In dev: app/out/main/index.js → ../../sidecar. In a packaged build we'll
// ship the sidecar alongside; that path lands in task 35.
const SIDECAR_DIR = resolve(__dirname, "../../sidecar");

export type SidecarEvent = {
  event: string;
  request_id?: string;
  [k: string]: unknown;
};

const REQUEST_TIMEOUT_MS = 30_000;

type PendingRequest = {
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
  onEvent?: (event: SidecarEvent) => void;
  timeout: NodeJS.Timeout;
};

export class Sidecar {
  private proc: ChildProcessWithoutNullStreams | null = null;
  private rl: ReadlineInterface | null = null;
  private pending = new Map<string, PendingRequest>();
  private starting: Promise<void> | null = null;
  private stopped = false;

  /** Idempotent. Resolves once the sidecar process is up. */
  async start(): Promise<void> {
    if (this.proc) return;
    if (this.starting) return this.starting;
    this.starting = this.doStart();
    try {
      await this.starting;
    } finally {
      this.starting = null;
    }
  }

  private async doStart(): Promise<void> {
    // PATH for GUI-launched Electron sometimes omits user-local installs
    // (uv lives in ~/.local/bin). Ensure those locations are searchable.
    const homeBin = `${process.env["HOME"]}/.local/bin`;
    const augmentedPath = [
      homeBin,
      "/usr/local/bin",
      "/opt/homebrew/bin",
      process.env["PATH"] ?? "",
    ].join(":");

    console.log(`[sidecar] spawning: cwd=${SIDECAR_DIR}`);
    console.log(`[sidecar] PATH=${augmentedPath}`);

    const proc = spawn("uv", ["run", "reviewer", "serve"], {
      cwd: SIDECAR_DIR,
      stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env, PATH: augmentedPath },
    });

    // Surface stderr to the Electron console so failures aren't silent.
    proc.stderr.on("data", (chunk) => {
      process.stderr.write(`[sidecar stderr] ${chunk}`);
    });

    proc.on("error", (err) => {
      console.error("[sidecar] spawn error:", err);
      this.rejectAllPending(err);
    });

    proc.on("spawn", () => {
      console.log(`[sidecar] spawned pid=${proc.pid}`);
    });

    proc.on("exit", (code, signal) => {
      console.error(`[sidecar] exited code=${code} signal=${signal}`);
      this.proc = null;
      this.rl?.close();
      this.rl = null;
      this.rejectAllPending(
        new Error(`Sidecar exited unexpectedly (code=${code} signal=${signal})`),
      );
      // Auto-restart on unexpected crash. We don't restart if `stop()` was called.
      if (!this.stopped && code !== 0) {
        setTimeout(() => {
          this.start().catch((err) =>
            console.error("[sidecar] restart failed:", err),
          );
        }, 1000);
      }
    });

    this.rl = createInterface({ input: proc.stdout });
    this.rl.on("line", (line) => {
      console.log(`[sidecar →] ${line.slice(0, 200)}${line.length > 200 ? "…" : ""}`);
      this.handleLine(line);
    });

    this.proc = proc;
  }

  private handleLine(line: string): void {
    if (!line.trim()) return;
    let msg: { id?: string; result?: unknown; error?: { message: string; type: string }; event?: string; request_id?: string };
    try {
      msg = JSON.parse(line);
    } catch (err) {
      console.error("[sidecar] non-JSON line:", line, err);
      return;
    }

    // Response (has `id`): resolve or reject the pending promise.
    if (typeof msg.id === "string") {
      const pending = this.pending.get(msg.id);
      if (!pending) return; // stale
      this.pending.delete(msg.id);
      clearTimeout(pending.timeout);
      if (msg.error) {
        const err = Object.assign(new Error(msg.error.message), {
          type: msg.error.type,
        });
        pending.reject(err);
      } else {
        pending.resolve(msg.result);
      }
      return;
    }

    // Streaming event (has `event` and `request_id`).
    if (msg.event && typeof msg.request_id === "string") {
      const pending = this.pending.get(msg.request_id);
      pending?.onEvent?.(msg as SidecarEvent);
    }
  }

  private rejectAllPending(err: Error): void {
    for (const [, p] of this.pending) {
      clearTimeout(p.timeout);
      p.reject(err);
    }
    this.pending.clear();
  }

  /**
   * Send a JSON-RPC request. If `id` is supplied, it's reused as the request
   * id (the renderer passes its own id so events can be routed to it).
   */
  async request<T>(
    method: string,
    params: object = {},
    onEvent?: (event: SidecarEvent) => void,
    id?: string,
  ): Promise<T> {
    if (!this.proc) await this.start();
    if (!this.proc) throw new Error("Sidecar failed to start");
    const reqId = id ?? cryptoRandomId();
    return new Promise<T>((resolveFn, rejectFn) => {
      const timeout = setTimeout(() => {
        this.pending.delete(reqId);
        rejectFn(
          new Error(
            `Sidecar request '${method}' (id=${reqId}) timed out after ${REQUEST_TIMEOUT_MS}ms. ` +
              "Check the main-process terminal for [sidecar stderr] lines.",
          ),
        );
      }, REQUEST_TIMEOUT_MS);

      this.pending.set(reqId, {
        resolve: resolveFn as (v: unknown) => void,
        reject: rejectFn,
        onEvent,
        timeout,
      });
      const payload = JSON.stringify({ id: reqId, method, params }) + "\n";
      console.log(`[sidecar ←] ${payload.trim().slice(0, 200)}`);
      this.proc!.stdin.write(payload);
    });
  }

  async stop(): Promise<void> {
    this.stopped = true;
    if (!this.proc) return;
    const proc = this.proc;
    // Closing stdin asks the sidecar to drain and exit. Give it 2s, then SIGTERM.
    proc.stdin.end();
    await new Promise<void>((resolveFn) => {
      const t = setTimeout(() => {
        proc.kill("SIGTERM");
        setTimeout(() => {
          if (!proc.killed) proc.kill("SIGKILL");
          resolveFn();
        }, 1000);
      }, 2000);
      proc.once("exit", () => {
        clearTimeout(t);
        resolveFn();
      });
    });
  }
}

function cryptoRandomId(): string {
  // Node 22 has crypto.randomUUID in the global; main process is privileged so it's fine.
  return globalThis.crypto.randomUUID();
}

export const sidecar = new Sidecar();
