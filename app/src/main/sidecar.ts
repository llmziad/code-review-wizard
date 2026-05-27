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
import { accessSync, constants as fsConstants } from "node:fs";
import { createInterface, type Interface as ReadlineInterface } from "node:readline";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));

/**
 * Locate the sidecar directory by walking up from this file until we find
 * `sidecar/pyproject.toml`. Bulletproof across dev vs packaged builds and
 * any future `out/` directory reshuffling. (My previous `../../sidecar`
 * was off-by-one — landed in `app/sidecar`, which doesn't exist.)
 */
function findSidecarDir(): string {
  let dir = __dirname;
  for (let i = 0; i < 8; i++) {
    const candidate = resolve(dir, "sidecar");
    try {
      accessSync(resolve(candidate, "pyproject.toml"));
      return candidate;
    } catch {
      // not here; keep walking up
    }
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  throw new Error(
    `Could not find sidecar/pyproject.toml walking up from ${__dirname}`,
  );
}

const SIDECAR_DIR = findSidecarDir();

/**
 * Resolve `uv` to an absolute path.
 *
 * macOS GUI-launched apps (and Node's `spawn` on macOS) don't reliably honor
 * a custom PATH in `options.env` for command lookup — relying on it leaves
 * us at the mercy of whatever launchd-style PATH Electron inherited.
 *
 * Solution: probe the well-known install locations directly, return the
 * first executable hit. Bare "uv" is the last-resort fallback.
 */
function resolveUvPath(): string {
  const candidates = [
    `${process.env["HOME"]}/.local/bin/uv`, // standard uv installer
    "/opt/homebrew/bin/uv",                  // Apple Silicon Homebrew
    "/usr/local/bin/uv",                     // Intel Homebrew + manual installs
    `${process.env["HOME"]}/.cargo/bin/uv`,  // if installed via cargo
  ];
  for (const path of candidates) {
    try {
      accessSync(path, fsConstants.X_OK);
      return path;
    } catch {
      // not at this location
    }
  }
  return "uv"; // PATH fallback (will fail with the same ENOENT if missing)
}

const UV_PATH = resolveUvPath();

export type SidecarEvent = {
  event: string;
  request_id?: string;
  [k: string]: unknown;
};

/**
 * Idle timeout: if the sidecar emits nothing (no response, no event) for
 * this long, we assume the request is wedged and reject it. Every event
 * resets the clock — so a long-running review_pr that streams progress
 * keeps the timer alive, but a silently-hung sidecar still fails fast.
 *
 * 90s sized for the slowest single quiet stretch: an LLM call between
 * `pass_started` and `pass_completed` with no intermediate events. Sonnet
 * 4.6 is typically 10-30s on a medium PR; 90s gives headroom for slow days.
 */
const REQUEST_IDLE_TIMEOUT_MS = 90_000;

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

    console.log(`[sidecar] spawning: ${UV_PATH} run reviewer serve`);
    console.log(`[sidecar] cwd=${SIDECAR_DIR}`);

    const proc = spawn(UV_PATH, ["run", "reviewer", "serve"], {
      cwd: SIDECAR_DIR,
      stdio: ["pipe", "pipe", "pipe"],
      // PATH is also augmented in case the sidecar shells out to `gh` for token
      // resolution — same reason `uv` was hidden, `gh` could be too.
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
      if (pending) {
        // Reset the idle timeout — the sidecar is making progress.
        this.armTimeout(pending, msg.request_id, msg.event);
        pending.onEvent?.(msg as SidecarEvent);
      }
    }
  }

  /** (Re)arm the idle timeout for a pending request. */
  private armTimeout(pending: PendingRequest, reqId: string, lastEvent: string): void {
    clearTimeout(pending.timeout);
    pending.timeout = setTimeout(() => {
      this.pending.delete(reqId);
      pending.reject(
        new Error(
          `Sidecar request (id=${reqId}) idle for ${REQUEST_IDLE_TIMEOUT_MS}ms ` +
            `(last event: ${lastEvent}). Check the main-process terminal for [sidecar stderr].`,
        ),
      );
    }, REQUEST_IDLE_TIMEOUT_MS);
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
      // Initial timeout — overwritten by `armTimeout` immediately so the
      // same code path manages it from here on.
      const pending: PendingRequest = {
        resolve: resolveFn as (v: unknown) => void,
        reject: rejectFn,
        onEvent,
        timeout: setTimeout(() => {}, 0),
      };
      this.pending.set(reqId, pending);
      this.armTimeout(pending, reqId, `request '${method}'`);

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
