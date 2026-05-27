/**
 * Preload script. Exposes a minimal, typed bridge to the renderer.
 *
 * The bridge is intentionally low-level: two methods, `invoke` and `onEvent`.
 * Higher-level typed wrappers (api.listPrs, api.reviewPr with streaming, etc.)
 * live in `src/renderer/lib/api.ts` — they compose these two primitives.
 *
 * Why this split: contextBridge can't proxy renderer-side callbacks back to
 * the main process. So `onEvent` registers a single global listener once;
 * the renderer routes events to the right consumer by `requestId`.
 */

import { contextBridge, ipcRenderer, type IpcRendererEvent } from "electron";

const CHANNEL_CALL = "reviewer:call";
const CHANNEL_EVENT = "reviewer:event";

export type SidecarEventPayload = {
  requestId: string;
  event: { event: string; request_id?: string; [k: string]: unknown };
};

const api = {
  /** Generate this in the renderer so events can be routed back to the caller. */
  newRequestId: (): string => globalThis.crypto.randomUUID(),

  /** Invoke a sidecar method. Returns the final result; events stream via onEvent. */
  invoke: <T = unknown>(
    method: string,
    params: object,
    requestId: string,
  ): Promise<T> => ipcRenderer.invoke(CHANNEL_CALL, { method, params, requestId }),

  /**
   * Register a single global event listener. The renderer is responsible for
   * routing by `payload.requestId` to the right consumer. Returns no
   * unsubscribe handle (contextBridge can't proxy function returns); the
   * renderer should only call this once at app startup.
   */
  onEvent: (callback: (payload: SidecarEventPayload) => void): void => {
    ipcRenderer.on(CHANNEL_EVENT, (_e: IpcRendererEvent, payload: SidecarEventPayload) => {
      callback(payload);
    });
  },
} as const;

contextBridge.exposeInMainWorld("reviewer", api);

declare global {
  interface Window {
    reviewer: typeof api;
  }
}
